#!/usr/bin/env python3
"""YuniScripts Engine – core management, UDP admin, Phooks hub as managed script."""
from pathlib import Path
import sys
import select
import time
import importlib.util
import socket
import threading
from queue import Queue
import queue
from engine.manager import discover_scripts, apply_overrides, get_enabled_scripts, check_port_conflicts
from engine.venv_manager import prepare_environment
from engine.process_wrapper import (
    start_all_enabled,
    monitor_scripts,
    stop_script,
    start_script,
    STATUS_RUNNING,
)
from engine.watcher import create_watcher, start_watcher, reload_script
from engine.debug_utils import inject_debug_args
from engine.hooks import create_hook_registry
from engine.ports import UDP_ADMIN_PORT, DEBUG_BASE_PORT
import logging

logger = logging.getLogger(__name__)


SCRIPTS_ROOT = Path(__file__).resolve().parent / "SCRIPTS"
SCRIPTS_JSON = SCRIPTS_ROOT.parent / "scripts.json"
LOG_BASE_DIR = Path(__file__).resolve().parent / "engine" / "logs"


# ── Cross-platform stdin reader ──────────────────────────────────────
# On Windows, select.select() only works with sockets, not stdin pipes.
# We use a background thread to buffer stdin lines into a queue.
# This handles both interactive console (msvcrt.kbhit) and piped stdin.

_stdin_queue: "queue.Queue[str]" = Queue()
_stdin_reader_started = False
_stdin_stop_event = threading.Event()

def _start_stdin_reader():
    """Start a daemon thread that reads stdin into _stdin_queue."""
    global _stdin_reader_started
    if _stdin_reader_started:
        return
    _stdin_reader_started = True
    def _reader():
        try:
            for line in sys.stdin:
                if _stdin_stop_event.is_set():
                    break
                _stdin_queue.put(line)
        except (EOFError, OSError, ValueError):
            pass
    t = threading.Thread(target=_reader, daemon=True)
    t.start()


def _stop_stdin_reader():
    """Signal the stdin reader thread to stop."""
    _stdin_stop_event.set()


def _stdin_available(timeout: float = 0.1) -> bool:
    """Check if stdin has data available (cross-platform).
    
    On Linux/macOS: uses select.select on sys.stdin.
    On Windows: uses a background reader thread with a queue.
    Falls back to msvcrt.kbhit() for interactive console on Windows.
    """
    if sys.platform == "win32":
        # Interactive console: msvcrt.kbhit() works best
        try:
            import msvcrt
            if msvcrt.kbhit():
                return True
        except ImportError:
            pass
        # Piped stdin: check the reader queue (thread started on first call)
        if not sys.stdin.isatty():
            _start_stdin_reader()
            return not _stdin_queue.empty()
        return False
    # Linux/macOS: select works fine with pipes and terminals
    return bool(select.select([sys.stdin], [], [], timeout)[0])


def handle_admin_command(cmd_line, registry, running, watchers):
    try:
        parts = cmd_line.split()

    except Exception as e:
        logger.error(f"handle_admin_command failed: {e}")
        return None
    if not parts:
        return "Empty command"
    verb = parts[0].lower()
    if verb == "status":
        lines = ["Script status:"]
        for sid in sorted(registry):
            proc = running.get(sid)
            if proc:
                st = proc["status"]
                pid = proc.get("pid") or "-"
                sv_type = proc.get("server_type", "normal")
                timeout = proc.get("shutdown_timeout", 5.0)
                adopted = " [adopted]" if proc.get("adopted") else ""
                lines.append(f"  {sid}: {st} (pid={pid}, type={sv_type}, timeout={timeout}s){adopted}")
            else:
                meta = registry[sid]["meta"]
                sv_type = meta.get("server_type", "normal")
                lines.append(f"  {sid}: stopped (type={sv_type})")
        return "\n".join(lines)
    elif verb == "start":
        if len(parts) < 2: return "Usage: start <id>"
        sid = parts[1]
        if sid not in registry: return f"Unknown script: {sid}"
        if sid in running and running[sid]["status"] == STATUS_RUNNING:
            return f"{sid} is already running."
        proc = start_script(registry[sid], log_base_dir=LOG_BASE_DIR)
        running[sid] = proc
        return f"Started {sid} (pid={proc['pid']})"
    elif verb == "stop":
        if len(parts) < 2: return "Usage: stop <id>"
        sid = parts[1]
        if sid not in registry: return f"Unknown script: {sid}"
        if sid not in running or running[sid]["status"] not in (STATUS_RUNNING, "starting"):
            return f"{sid} is not running."
        running[sid] = stop_script(running[sid])
        return f"Stopped {sid}."
    elif verb == "reload":
        if len(parts) < 2: return "Usage: reload <id>"
        sid = parts[1]
        if sid not in registry: return f"Unknown script: {sid}"
        registry, running, watchers, ok = reload_script(registry, running, sid, watchers, log_base_dir=LOG_BASE_DIR)
        if ok: return f"Reloaded {sid}"
        return f"Failed to reload {sid}"
    elif verb == "reload-all":
        results = []
        for sid in list(running.keys()):
            registry, running, watchers, ok = reload_script(registry, running, sid, watchers, log_base_dir=LOG_BASE_DIR)
            results.append(f"{sid}: {'OK' if ok else 'failed'}")
        return "Reload all: " + ", ".join(results)
    elif verb == "help":
        return "Commands: status, start <id>, stop <id>, reload <id>, reload-all, help"
    else:
        return f"Unknown command: {verb}"

def udp_admin_server(registry, running, watchers):
    """UDP admin command server. HARDENED v2: socket timeout, reuse, graceful OSError recovery."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except (AttributeError, OSError):
        pass
    sock.settimeout(2.0)  # Periodic wake to check running state if needed
    sock.bind(("", UDP_ADMIN_PORT))
    print(f"[admin] UDP listening on port {UDP_ADMIN_PORT}")
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            cmd = data.decode().strip()
            if not cmd:
                continue
            resp = handle_admin_command(cmd, registry, running, watchers)
            try:
                sock.sendto(resp.encode(), addr)
            except OSError:
                pass  # Client disappeared — fire-and-forget
        except socket.timeout:
            continue  # Normal timeout wake-up
        except OSError as e:
            print(f"[admin] Socket error: {e}")
            # Recreate socket on fatal error
            try:
                sock.close()
            except OSError:
                pass
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(2.0)
            sock.bind(("", UDP_ADMIN_PORT))
            print(f"[admin] UDP socket recreated on port {UDP_ADMIN_PORT}")
        except Exception as e:
            print(f"[admin] Error: {e}")

def load_hooks_from_script(script_dir: Path, hook_registry: dict):
    try:
        hooks_file = script_dir / "hooks.py"

    except Exception as e:
        logger.error(f"load_hooks_from_scri failed: {e}")
        return None
    if not hooks_file.exists():
        main_file = script_dir / "main.py"
        spec = importlib.util.spec_from_file_location(f"script_{script_dir.name}_main", main_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "register_hooks"):
            hook_registry = module.register_hooks(hook_registry)
        return hook_registry
    spec = importlib.util.spec_from_file_location(f"script_{script_dir.name}_hooks", hooks_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "register_hooks"):
        hook_registry = module.register_hooks(hook_registry)
    return hook_registry

def main():
    print("Validating scripts...")
    registry = discover_scripts(SCRIPTS_ROOT)
    registry = apply_overrides(registry, SCRIPTS_JSON)

    check_port_conflicts(registry)

    hook_registry = create_hook_registry()
    for sid, inst in registry.items():
        hook_registry = load_hooks_from_script(inst["path"], hook_registry)

    print("\nPreparing virtual environments...")
    for sid in get_enabled_scripts(registry):
        inst = registry[sid]
        print(f"\n--- {sid} ---")
        inst = prepare_environment(inst)
        registry[sid] = inst

    debug_base_port = DEBUG_BASE_PORT
    debug_index = 0
    for sid in get_enabled_scripts(registry):
        inst = registry[sid]
        if inst["meta"].get("debug"):
            port = debug_base_port + debug_index
            inst = inject_debug_args(inst, port)
            registry[sid] = inst
            print(f"  [debug] {sid} will listen on port {port}")
            debug_index += 1

    print("\nStarting scripts...")
    registry, running = start_all_enabled(registry, log_base_dir=LOG_BASE_DIR)

    print("\nSetting up file watchers...")
    event_queue = Queue()
    watchers = {}
    for sid in running:
        inst = registry[sid]
        w = create_watcher(inst, event_queue)
        w = start_watcher(w)
        watchers[sid] = w
        print(f"  [watch] {sid}")

    # --- Phooks hub is now a separate managed script ---
    # No internal hub started here. The hub is expected to be in
    # SCRIPTS/SERVICES/phooks-hub/ and started by the engine above.

    threading.Thread(target=udp_admin_server, args=(registry, running, watchers), daemon=True).start()

    print(f"\nEngine running. Phooks hub expected as managed script (SERVICES/phooks-hub). "
          f"Type 'help' for commands. Ctrl+C to exit.\n")

    _last_status_print = 0.0  # throttle last status line timestamp

    try:
        while True:
            while not event_queue.empty():
                script_id, changed_file = event_queue.get()
                if script_id in watchers:
                    last = watchers[script_id].get("last_reload_time", 0.0)
                    if time.time() - last < 2.0:
                        continue
                print(f"\n  [hot-reload] change in {script_id}: {changed_file}")
                registry, running, watchers, _ = reload_script(registry, running, script_id, watchers, log_base_dir=LOG_BASE_DIR)

            registry, running = monitor_scripts(registry, running)

            if _stdin_available(0.1):
                # Read from stdin: direct on Linux/macOS, queue on Windows piped
                if sys.platform == "win32" and not sys.stdin.isatty():
                    try:
                        line = _stdin_queue.get_nowait()
                    except Exception:
                        line = ""
                else:
                    line = sys.stdin.readline()
                if not line:
                    break
                cmd = line.strip()
                if cmd in ("exit", "quit"):
                    break
                resp = handle_admin_command(cmd, registry, running, watchers)
                print(resp)

            # Throttle status line to once per second (was every 0.1s causing console spam)
            now = time.time()
            if now - _last_status_print >= 1.0:
                _last_status_print = now
                if running:
                    parts = [f"{sid}:{info['status'][:8]} pid={info.get('pid','-')}" for sid, info in running.items()]
                    line = " | ".join(parts) + " > "
                    print(f"\r{' ' * 80}\r{line}", end="", flush=True)
                else:
                    print(f"\r{' ' * 80}\rAll scripts stopped. > ", end="", flush=True)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        _stop_stdin_reader()
        # The hub (as a managed script) will be stopped like any other script
        for proc_info in running.values():
            stop_script(proc_info)

if __name__ == "__main__":
    main()
