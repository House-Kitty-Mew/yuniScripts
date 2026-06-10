"""
process_adoption.py — PID File Protocol & Process Adoption System

Allows long-running scripts (game servers, critical services) to survive
engine restarts by persisting their PIDs and adopting existing processes
when the engine restarts.

Architecture:
    PID File Protocol:
        <pid_dir>/<sanitized_script_id>.pid  →  contains integer PID

    Adoption Flow:
        Engine Start → check PID file → check process alive → check ports open
        → ADOPT (wraps existing process) or START FRESH

Cross-Platform:
    - Linux: os.kill(pid, 0) for process detection
    - Windows: ctypes.windll.kernel32.OpenProcess for process detection
    - TCP port check works identically on both platforms
"""

import logging
import os
import sys
import socket
import tempfile
import time
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────

DEFAULT_PORT_CHECK_TIMEOUT = 1.0  # seconds
PID_DIR_NAME = "pids"
STALE_CLEANUP_LOCK = threading.Lock()

# Status string constants (re-exported for convenience)
STATUS_RUNNING = "running"
STATUS_STARTING = "starting"
STATUS_STOPPED = "stopped"
STATUS_CRASHED = "crashed"


# ── PID Directory ──────────────────────────────────────────────────


def get_pid_dir() -> Path:
    """Return the absolute path to the PID file directory.

    Creates the directory if it doesn't exist.
    Cross-platform: uses pathlib.Path for OS-appropriate separator.

    Returns:
        Path to engine/pids/
    """
    pid_dir = Path(__file__).resolve().parent / PID_DIR_NAME
    pid_dir.mkdir(parents=True, exist_ok=True)
    return pid_dir


def _sanitize_script_id(script_id: str) -> str:
    """Sanitize a script ID for safe filename usage.

    Replaces path separators (/ \\) and other problematic characters
    with underscores. Caps total length to 200 chars to avoid
    filesystem limitations on Windows (MAX_PATH).

    Args:
        script_id: Raw script ID (e.g. "GAMES/minecraft_manager")

    Returns:
        Sanitized filename-safe string (e.g. "GAMES_minecraft_manager")
    """
    safe = script_id.replace("/", "_").replace("\\", "_").replace(":", "_")
    safe = safe.replace(" ", "_").replace("\t", "_")
    # Cap length to avoid MAX_PATH issues on Windows
    if len(safe) > 200:
        safe = safe[-200:]
    return safe


# ── PID File Operations (Atomic) ───────────────────────────────────


def pid_file_path(script_id: str) -> Path:
    """Return the PID file path for a given script ID.

    Args:
        script_id: Raw script ID (e.g. "GAMES/minecraft_manager")

    Returns:
        Path to the .pid file in the PID directory
    """
    return get_pid_dir() / f"{_sanitize_script_id(script_id)}.pid"


def write_pid_file(script_id: str, pid: int) -> bool:
    """Write a PID file atomically using write-then-rename.

    Uses a temporary file in the same directory to prevent partial
    writes from corrupting the PID file. The temp file is written,
    then renamed (atomic on most filesystems) to the final path.

    Args:
        script_id: Raw script ID
        pid: Process ID to write

    Returns:
        True if written successfully, False on error
    """
    if not isinstance(pid, int) or pid <= 0:
        return False

    final_path = pid_file_path(script_id)
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: tempfile in same directory, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(final_path.parent),
            prefix=f".{_sanitize_script_id(script_id)}_",
            suffix=".tmp",
        )
        try:
            os.write(fd, f"{pid}\n".encode("utf-8"))
            os.fsync(fd)  # Force flush to disk
        finally:
            os.close(fd)

        # Rename is atomic on same filesystem
        os.replace(tmp_path, str(final_path))
        return True

    except (OSError, PermissionError, IOError) as e:
        print(f"  [adoption] WARNING: Could not write PID file for '{script_id}': {e}")
        # Clean up temp file if rename failed
        tmp = Path(tmp_path) if 'tmp_path' in locals() else None
        if tmp and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return False


def read_pid_file(script_id: str) -> Optional[int]:
    """Read PID from file. Returns None if missing, malformed, or error.

    Args:
        script_id: Raw script ID

    Returns:
        PID integer, or None if file doesn't exist or has invalid content
    """
    path = pid_file_path(script_id)
    if not path.exists():
        return None

    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        pid = int(content)
        if pid <= 0:
            return None
        return pid
    except (ValueError, OSError, PermissionError):
        return None


def remove_pid_file(script_id: str) -> bool:
    """Remove the PID file if it exists.

    Args:
        script_id: Raw script ID

    Returns:
        True if file was removed or didn't exist, False on permission error
    """
    path = pid_file_path(script_id)
    if not path.exists():
        return True  # Already gone — success
    try:
        path.unlink()
        return True
    except (OSError, PermissionError) as e:
        print(f"  [adoption] WARNING: Could not remove PID file for '{script_id}': {e}")
        return False


# ── Cross-Platform Process Detection ───────────────────────────────


def is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is currently alive.

    Cross-platform implementation:
        Linux/macOS: os.kill(pid, 0) — sends null signal (no effect)
            - ProcessExists: returns None (no exception)
            - ProcessDead: raises ProcessLookupError
            - PermissionError: process exists but we can't signal it (still alive)

        Windows: Uses ctypes.windll.kernel32.OpenProcess
            - ProcessExists: returns non-None handle
            - ProcessDead: returns None

    Args:
        pid: Process ID to check

    Returns:
        True if the process exists (even if we can't signal it)
    """
    if not isinstance(pid, int) or pid <= 0:
        return False

    if sys.platform == "win32":
        return _is_process_alive_windows(pid)
    else:
        return _is_process_alive_unix(pid)


def _is_process_alive_windows(pid: int) -> bool:
    """Windows-specific process detection using kernel32.OpenProcess.

    Uses PROCESS_QUERY_INFORMATION (0x0400) to avoid modifying the process.
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except (ImportError, AttributeError, OSError):
        # Fallback: try os.kill for Windows Python
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    except Exception:
        return False


def _is_process_alive_unix(pid: int) -> bool:
    """Unix-specific process detection via os.kill(pid, 0)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False  # Process does not exist
    except PermissionError:
        return True  # Process exists but owned by another user
    except OSError:
        return False  # Other OS error — assume dead


# ── Port Detection ──────────────────────────────────────────────────


def check_port_in_use(host: str, port: int, timeout: float = DEFAULT_PORT_CHECK_TIMEOUT) -> bool:
    """Check if a TCP port is in use by attempting a connection.

    Args:
        host: Hostname or IP (e.g. "127.0.0.1")
        port: Port number
        timeout: Connection timeout in seconds (default 1.0)

    Returns:
        True if port appears to be in use (connection succeeded or actively refused)
        False if connection timed out or host unreachable

    Note:
        A "connection refused" response also indicates the port is occupied
        (something was listening briefly). We treat this as "in use" to be safe.
    """
    if port <= 0 or port > 65535:
        return False

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        # connect_ex returns 0 on success, 111 (ECONNREFUSED) if nothing listening,
        # 110 (ETIMEDOUT) if timeout, etc.
        # We consider both "connected" and "connection refused" as "in use"
        # because a port that recently closed may still be in TIME_WAIT.
        if result == 0:
            return True  # Active connection
        if result == 111:  # ECONNREFUSED — was recently in use
            return True
        return False  # Timeout or other error — port likely free
    except (socket.gaierror, OSError):
        return False
    except Exception:
        return False


# ── Combined Status Check ────────────────────────────────────────────


def check_script_status(script_instance: Dict) -> Dict:
    """Combined PID + port check for a script.

    Reads the PID file, checks if the process is alive, then checks
    if the script's ports are open.

    Args:
        script_instance: Script metadata dict (must have "id" and "meta" keys)

    Returns:
        Dict with keys:
            - pid_alive (bool): Whether the recorded PID is alive
            - ports_open (bool): Whether ALL configured ports are open
            - pid (Optional[int]): The PID from file (or None)
            - adoptable (bool): True if BOTH pid_alive AND ports_open
            - status (str): Human-readable description
    """
    try:
        script_id = script_instance["id"]

    except Exception as e:
        logger.error(f"check_script_status failed: {e}")
        return None
    pid = read_pid_file(script_id)
    ports = script_instance["meta"].get("ports", [])

    pid_alive = False
    ports_open = False

    # Check PID
    if pid is not None and pid > 0:
        pid_alive = is_process_alive(pid)

    # Check ports if any are configured
    if ports:
        open_ports = 0
        for port in ports:
            if check_port_in_use("127.0.0.1", port):
                open_ports += 1
        ports_open = (open_ports == len(ports))
    else:
        # No ports configured — can't verify via ports, rely on PID only
        ports_open = pid_alive  # If no ports, ports_open == pid_alive

    adoptable = pid_alive and ports_open

    # Build human-readable status
    status_parts = []
    if pid:
        status_parts.append(f"pid={pid}")
        if pid_alive:
            status_parts.append("alive")
        else:
            status_parts.append("dead")
    else:
        status_parts.append("no-pid-file")

    if ports:
        status_parts.append(f"ports={open_ports}/{len(ports)} open")

    return {
        "pid_alive": pid_alive,
        "ports_open": ports_open,
        "pid": pid,
        "adoptable": adoptable,
        "status": " | ".join(status_parts),
    }


# ── Process Adoption ────────────────────────────────────────────────


def adopt_existing_process(script_instance: Dict, pid: int, log_base_dir: Optional[Path] = None) -> Dict:
    """Create a proc_info dict for an EXISTING (already running) process.

    This does NOT spawn a new subprocess — it wraps an existing process
    so the engine can monitor and eventually stop it.

    Args:
        script_instance: Script metadata dict
        pid: Process ID of the existing process to adopt
        log_base_dir: Base log directory (default: engine/logs/)

    Returns:
        proc_info dict with:
            - pid: The adopted PID
            - process: None (we don't own the subprocess object)
            - status: STATUS_RUNNING
            - adopted: True
            - script_id, log_path, log_handle, etc.
    """
    if log_base_dir is None:
        log_base_dir = Path(__file__).resolve().parent.parent / "engine" / "logs"

    log_base_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_script_id(script_instance["id"])
    log_path = log_base_dir / f"{safe_name}.log"

    # Open log file in append mode
    try:
        log_handle = open(log_path, "a", buffering=1)
    except (OSError, PermissionError) as e:
        print(f"  [adoption] WARNING: Cannot open log for adopted script '{script_instance['id']}': {e}")
        log_handle = None

    return {
        "pid": pid,
        "process": None,          # No subprocess object — we adopted it
        "status": STATUS_RUNNING,
        "exit_code": None,
        "restart_count": 0,
        "last_start_time": time.monotonic(),
        "script_id": script_instance["id"],
        "log_handle": log_handle,
        "log_path": str(log_path),
        "stdout_thread": None,
        "stderr_thread": None,
        "adopted": True,
        # No shutdown_detected event — adopted processes don't have pipe monitoring
        "server_type": script_instance["meta"].get("server_type", "normal"),
        "shutdown_timeout": script_instance["meta"].get("shutdown_timeout", 5.0),
    }


# ── Stale PID Cleanup ────────────────────────────────────────────────


def cleanup_stale_pids() -> int:
    """Scan all PID files and remove those whose processes are dead.

    Thread-safe: uses STALE_CLEANUP_LOCK.

    Returns:
        Number of stale PID files removed
    """
    with STALE_CLEANUP_LOCK:
        pid_dir = get_pid_dir()
        if not pid_dir.exists():
            return 0

        cleaned = 0
        for pid_file in pid_dir.glob("*.pid"):
            if not pid_file.is_file():
                continue
            try:
                content = pid_file.read_text(encoding="utf-8").strip()
                if not content:
                    pid_file.unlink()
                    cleaned += 1
                    continue
                pid = int(content)
                if not is_process_alive(pid):
                    pid_file.unlink()
                    cleaned += 1
            except (ValueError, OSError, PermissionError):
                # Can't read or parse — remove the malformed file
                try:
                    pid_file.unlink()
                    cleaned += 1
                except OSError:
                    pass

        return cleaned


# ── High-Level Adoption Flow ─────────────────────────────────────────


def find_and_adopt(script_instance: Dict, log_base_dir: Optional[Path] = None) -> Optional[Dict]:
    """Attempt to find and adopt an existing process for the given script.

    This is the high-level entry point for the adoption flow:
        1. Read PID file
        2. No PID file → return None (needs fresh start)
        3. PID file exists, process dead → clean stale PID, return None
        4. PID file exists, process alive → check ports
        5. Ports open → adopt
        6. Ports closed → kill orphan (might be zombie), clean PID, return None

    Args:
        script_instance: Script metadata dict
        log_base_dir: Base log directory

    Returns:
        proc_info dict if adopted successfully, None if needs fresh start
    """
    try:
        script_id = script_instance["id"]

    except Exception as e:
        logger.error(f"find_and_adopt failed: {e}")
        return None
    server_type = script_instance["meta"].get("server_type", "normal")

    # Only attempt adoption for long-running or critical scripts
    if server_type not in ("long_running", "critical"):
        return None

    status = check_script_status(script_instance)

    if status["adoptable"]:
        # Perfect: PID alive AND ports open → adopt
        adopted = adopt_existing_process(script_instance, status["pid"], log_base_dir)
        print(f"  [adopt] {script_id}: adopted existing process (pid={status['pid']}, status={status['status']})")
        return adopted

    if status["pid_alive"] and not status["ports_open"]:
        # PID alive but ports are wrong → kill the orphan
        print(f"  [adopt] {script_id}: PID alive but ports closed — killing orphan (pid={status['pid']})")
        _kill_process(status["pid"])
        remove_pid_file(script_id)
        return None

    if status["pid"] and not status["pid_alive"]:
        # Stale PID → clean up
        print(f"  [adopt] {script_id}: stale PID {status['pid']} — cleaning up")
        remove_pid_file(script_id)
        return None

    # No PID file or process completely gone
    return None


def _kill_process(pid: int) -> bool:
    """Attempt to terminate a process by PID. Cross-platform.

    Args:
        pid: Process ID to terminate

    Returns:
        True if termination was attempted (doesn't guarantee success)
    """
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 15)  # SIGTERM
            # Give it a moment, then SIGKILL if still alive
            time.sleep(0.5)
            if is_process_alive(pid):
                os.kill(pid, 9)  # SIGKILL
            return True
    except (OSError, ProcessLookupError, PermissionError, ImportError):
        return False


# ── Convenience: PID-based monitoring ────────────────────────────────


def check_adopted_health(proc_info: Dict) -> Dict:
    """Health check for an adopted process (no subprocess object).

    Args:
        proc_info: The adopted process info dict

    Returns:
        Updated proc_info with fresh status
    """
    if not proc_info.get("adopted"):
        return proc_info  # Not adopted — use normal health check

    pid = proc_info.get("pid")
    if pid is None or not is_process_alive(pid):
        return {
            **proc_info,
            "status": STATUS_CRASHED,
            "exit_code": -1,
            "pid": None,
        }

    return proc_info

