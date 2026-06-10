"""Subprocess management – functional, with logging and Phooks environment."""
import subprocess
import threading
import time
import os
import sys
from pathlib import Path
from typing import Dict, Tuple, Optional

from engine.ports import PHOOKS_HUB_PORT

# Restart backoff tracker: sid -> {"count": int, "last_start": float, "last_restart": float}
# Used by apply_restart_policy to prevent rapid restart loops.
# Uses time.monotonic() for wall-clock-independent timing.
_RESTART_LOG: Dict[str, dict] = {}

STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_CRASHED = "crashed"
STATUS_STOPPED = "stopped"

SHUTDOWN_MARKER = "SHUTDOWN_COMPLETE"


def _venv_python(venv_path: Path) -> Path:
    """Return the path to the Python executable inside a virtual environment.
    Platform-native only — Windows uses Scripts\\python.exe, Linux uses bin/python."""
    if sys.platform == "win32":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _get_python(script_instance: Dict) -> str:
    venv_path = script_instance.get("venv_path")
    if venv_path:
        python = _venv_python(venv_path)
        if python.exists():
            return str(python)
    return script_instance["meta"]["python_path"]


def _build_command(script_instance: Dict) -> list:
    python = _get_python(script_instance)
    script_path = script_instance["path"] / script_instance["meta"]["entry_point"]
    args = script_instance["meta"].get("args", [])
    return [python, str(script_path)] + args


def _drain_pipe(pipe, log_handle, prefix: str,
                shutdown_event: Optional[threading.Event] = None):
    """Read lines from a pipe, write them to the log, and detect SHUTDOWN_COMPLETE.

    When the SHUTDOWN_COMPLETE marker is seen in stdout (prefix==""), sets the
    shutdown_event so the engine can immediately recognize a clean shutdown.
    """
    try:
        for line in iter(pipe.readline, b""):
            decoded = line.decode("utf-8", errors="replace").rstrip()
            log_handle.write(f"{prefix}{decoded}\n")
            log_handle.flush()
            # Detect SHUTDOWN_COMPLETE marker from child stdout
            if not prefix and SHUTDOWN_MARKER in decoded:
                if shutdown_event:
                    shutdown_event.set()
    except (ValueError, IOError):
        pass
    finally:
        pipe.close()


def _create_proc_info(script_id, script_instance: Dict = None) -> Dict:
    """Create a process info dict.

    Args:
        script_id: Script identifier (string) or script_instance dict.
                   If a dict is passed, ``id`` and ``meta`` are extracted.
        script_instance: Full script instance dict (optional if script_id is a dict).

    Returns:
        Process info dict with default fields.
    """
    # Support both (script_id=str) and (script_id=dict) calling conventions
    if isinstance(script_id, dict):
        script_instance = script_id
        script_id = script_instance["id"]
    meta = (script_instance or {}).get("meta", {})
    return {
        "pid": None,
        "process": None,
        "status": STATUS_STOPPED,
        "exit_code": None,
        "restart_count": 0,
        "last_start_time": 0.0,
        "script_id": script_id,
        "server_type": meta.get("server_type", "normal"),
        "shutdown_timeout": meta.get("shutdown_timeout", 5.0),
        "log_handle": None,
        "log_path": None,
        "stdout_thread": None,
        "stderr_thread": None,
        # Event set when SHUTDOWN_COMPLETE is detected in child stdout
        "shutdown_detected": threading.Event(),
    }


def start_script(script_instance: Dict, log_base_dir: Path = None) -> Dict:
    sid = script_instance["id"]
    cmd = _build_command(script_instance)
    if log_base_dir is None:
        log_base_dir = Path(__file__).parent.parent / "engine" / "logs"
    log_base_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sid.replace("/", "_").replace("\\", "_")
    log_path = log_base_dir / f"{safe_name}.log"
    log_handle = open(log_path, "a", buffering=1)

    env = os.environ.copy()
    engine_dir = str(Path(__file__).parent.parent.resolve())
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{engine_dir}{os.pathsep}{existing_path}" if existing_path else engine_dir
    env["PHOOKS_HUB_PORT"] = str(PHOOKS_HUB_PORT)

    try:
        # HARDENED: Merge stderr into stdout to eliminate subprocess pipe
        # deadlock. When a child writes >64KB to a pipe that isn't being
        # drained, the process blocks forever. Using STDOUT for stderr
        # guarantees a single pipe that's always being drained.
        proc = subprocess.Popen(
            cmd,
            cwd=str(script_instance["path"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        info = _create_proc_info(sid, script_instance)
        info["pid"] = proc.pid
        info["process"] = proc
        info["status"] = STATUS_RUNNING
        info["last_start_time"] = time.monotonic()
        info["log_handle"] = log_handle
        info["log_path"] = str(log_path)

        # Create a fresh Event for this launch
        shutdown_event = threading.Event()
        info["shutdown_detected"] = shutdown_event

        # Single drain thread for merged stdout+stderr — no deadlock risk
        t_out = threading.Thread(
            target=_drain_pipe,
            args=(proc.stdout, log_handle, ""),
            kwargs={"shutdown_event": shutdown_event},
            daemon=True,
        )
        t_out.start()
        info["stdout_thread"] = t_out
        info["stderr_thread"] = None

        print(f"  [start] {sid} (pid={proc.pid}) log={log_path}")
        return info
    except Exception as e:
        print(f"  [start] ERROR launching {sid}: {e}")
        log_handle.close()
        info = _create_proc_info(sid, script_instance)
        info["status"] = STATUS_CRASHED
        info["exit_code"] = -1
        return info


def stop_script(proc_info: Dict, timeout: float = 5.0) -> Dict:
    proc = proc_info.get("process")
    if proc is None or proc_info.get("status") not in (STATUS_RUNNING, STATUS_STARTING):
        return proc_info
    print(f"  [stop] {proc_info['script_id']} (pid={proc.pid})")
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    except ProcessLookupError:
        pass

    for th in (proc_info.get("stdout_thread"), proc_info.get("stderr_thread")):
        if th and th.is_alive():
            th.join(timeout=2)

    log_handle = proc_info.get("log_handle")
    if log_handle:
        log_handle.close()

    return {
        **proc_info,
        "status": STATUS_STOPPED,
        "process": None,
        "pid": None,
        "exit_code": proc.returncode,
        "log_handle": None,
        "stdout_thread": None,
        "stderr_thread": None,
    }


def check_health(proc_info: Dict) -> Dict:
    """Check process health and detect SHUTDOWN_COMPLETE for clean termination."""
    if proc_info["status"] != STATUS_RUNNING:
        return proc_info

    # Check if the child printed SHUTDOWN_COMPLETE (clean shutdown detected)
    shutdown_detected = proc_info.get("shutdown_detected")
    if shutdown_detected and shutdown_detected.is_set():
        # Child reported clean shutdown — wait briefly for actual process exit
        proc = proc_info.get("process")
        if proc:
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't exit after SHUTDOWN_COMPLETE
                try:
                    proc.kill()
                    proc.wait(timeout=2.0)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
        print(f"  [shutdown-detect] {proc_info['script_id']} clean shutdown detected")
        return {
            **proc_info,
            "status": STATUS_STOPPED,
            "exit_code": 0,
            "process": None,
            "pid": None,
        }

    proc = proc_info.get("process")
    if proc is None:
        return {**proc_info, "status": STATUS_STOPPED}
    ret = proc.poll()
    if ret is not None:
        return {
            **proc_info,
            "status": STATUS_CRASHED,
            "exit_code": ret,
            "process": None,
            "pid": None,
        }
    return proc_info


def _restart_backoff(sid: str) -> float:
    """Return the backoff delay in seconds for a given script.

    Uses exponential backoff: 0.5s → 1s → 2s → 4s → 8s → 16s → 30s (cap).
    Resets to zero if the script's last run lasted >10 seconds (it was stable).
    Uses time.monotonic() to avoid NTP time-jump issues.

    Returns:
        Positive float: delay to wait before restarting (caller should proceed).
        Negative float: seconds remaining in the current backoff (caller should skip).
    """
    global _RESTART_LOG
    now = time.monotonic()
    log = _RESTART_LOG.get(sid, {"count": 0, "last_start": 0.0, "last_restart": 0.0, "last_run_duration": 999.0})

    # Runtime of the last run (before it crashed)
    runtime = log.get("last_run_duration", 999.0)

    # If it ran >10s, it was stable — reset the backoff counter
    if runtime > 10.0:
        log["count"] = 0

    # Calculate delay: 0.5 * 2^count, capped at 30s
    delay = min(0.5 * (2 ** log["count"]), 30.0)

    # Check if enough time has passed since the last restart attempt
    elapsed = now - log["last_restart"]
    if elapsed < delay:
        # Still in backoff — caller should skip this cycle
        return -1.0 * (delay - elapsed)  # negative = seconds remaining

    # Advance the counter and timestamps
    log["count"] += 1
    log["last_restart"] = now
    log["last_start"] = now
    log["last_run_duration"] = 0.0  # updated on next crash
    _RESTART_LOG[sid] = log
    return delay


def apply_restart_policy(script_instance: Dict, proc_info: Dict) -> Tuple[Dict, Dict]:
    """Apply restart policy with exponential backoff.

    When a script crashes:
      - "never": transition to STOPPED (removed from monitoring).
      - "on-failure": restart with backoff.
      - "always": restart with backoff.

    When a script cleanly shuts down (SHUTDOWN_COMPLETE detected):
      - All policies: transition to STOPPED and reset backoff counters.
    """
    policy = script_instance["meta"]["restart_policy"]

    # Clean shutdown (SHUTDOWN_COMPLETE detected) — always stop, reset backoff
    if proc_info["status"] == STATUS_STOPPED and proc_info.get("exit_code") == 0:
        sid = script_instance["id"]
        if sid in _RESTART_LOG:
            del _RESTART_LOG[sid]
        return script_instance, proc_info

    if proc_info["status"] != STATUS_CRASHED:
        return script_instance, proc_info

    if policy == "never":
        return script_instance, {**proc_info, "status": STATUS_STOPPED}
    elif policy in ("on-failure", "always"):
        sid = script_instance["id"]
        _RESTART_LOG[sid] = _RESTART_LOG.get(sid, {
            "count": 0, "last_start": 0.0, "last_restart": 0.0, "last_run_duration": 999.0
        })

        # Record how long the script actually ran before crashing
        now = time.monotonic()
        run_duration = now - _RESTART_LOG[sid]["last_start"]
        if run_duration > 0:
            _RESTART_LOG[sid]["last_run_duration"] = run_duration

        backoff = _restart_backoff(sid)

        if backoff < 0:
            # Still in backoff delay — remain CRASHED and wait for next cycle
            remaining = -backoff
            if remaining < 5.0:
                print(f"  [backoff] {sid} waiting {remaining:.1f}s before retry...")
            return script_instance, proc_info

        print(f"  [restart] {sid} (policy={policy}, backoff={backoff:.1f}s)")
        new_proc = start_script(script_instance)
        new_proc["restart_count"] = _RESTART_LOG.get(sid, {}).get("count", 0)
        return script_instance, new_proc

    return script_instance, {**proc_info, "status": STATUS_STOPPED}


def start_all_enabled(registry: Dict, log_base_dir: Path = None) -> Tuple[Dict, Dict]:
    running = {}
    for sid, inst in registry.items():
        if inst["meta"]["enabled"]:
            proc_info = start_script(inst, log_base_dir=log_base_dir)
            running[sid] = proc_info
    return registry, running


def monitor_scripts(registry: Dict, running: Dict) -> Tuple[Dict, Dict]:
    """Monitor all running scripts. Detects crashes, SHUTDOWN_COMPLETE, and applies restart policy.

    Scripts that cleanly shut down (SHUTDOWN_COMPLETE detected) are removed from
    the running set. Scripts that crash are restarted per their restart policy.
    """
    new_running = {}
    for sid, proc_info in running.items():
        inst = registry.get(sid)
        if inst is None:
            continue
        proc_info = check_health(proc_info)
        inst, proc_info = apply_restart_policy(inst, proc_info)
        status = proc_info["status"]
        policy = inst["meta"]["restart_policy"]
        if status == STATUS_STOPPED and policy == "never":
            continue
        if status == STATUS_STOPPED and policy == "on-failure":
            continue
        # Clean shutdown (SHUTDOWN_COMPLETE) results in STOPPED with exit_code 0
        if status == STATUS_STOPPED and proc_info.get("exit_code") == 0:
            continue
        new_running[sid] = proc_info
    return registry, new_running
