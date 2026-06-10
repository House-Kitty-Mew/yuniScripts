#!/usr/bin/env python3
"""
Log Viewer – accepts commands on a socket to open/close tail windows.
Cross-platform — uses TCP on Windows, Unix domain socket on Linux/macOS.
"""
import os
import sys
import signal
import socket
import subprocess
import time
import threading
import shutil
from pathlib import Path

_start_time = time.time()

# ---- GUI API Client ----
import sys, os
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from engine.gui_api_client import GuiApiClient
from engine.ports import TCP_ADMIN_PORT

gui = GuiApiClient('TOOLS/log-viewer', 'Log Viewer')
gui.register_tab([
    {'type': 'label', 'id': 'file_path', 'title': 'Watching', 'default': 'None'},
    {'type': 'label', 'id': 'lines_count', 'title': 'Lines', 'default': '0'},
    {'type': 'label', 'id': 'active', 'title': 'Active', 'default': 'No'},
    {'type': 'label', 'id': 'uptime', 'title': 'Uptime', 'default': '0s'},
    {'type': 'message', 'id': 'log_preview', 'title': 'Recent Log', 'default': ''},
])

def _get_gui_data():
    """Return live data from the watched dict and log buffers."""
    uptime_seconds = int(time.time() - _start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        uptime_str = f"{hours}h {minutes}m {seconds}s"
    elif minutes:
        uptime_str = f"{minutes}m {seconds}s"
    else:
        uptime_str = f"{seconds}s"

    with watched_lock:
        if not watched:
            return {
                'file_path': 'None',
                'lines_count': '0',
                'active': 'No',
                'uptime': uptime_str,
                'log_preview': 'No scripts being watched.',
            }
        # Show data for the most recently watched script
        last_sid = list(watched.keys())[-1]
        info = watched[last_sid]
        file_path = info.get('file_path', 'Unknown')
        lines_count = str(info.get('lines_count', 0))
        active = info.get('active', 'No')

    # Get log preview from buffer (outside watched_lock to avoid deadlock)
    with log_buffers_lock:
        buffer = log_buffers.get(last_sid, [])
        log_preview = '\n'.join(buffer[-20:]) if buffer else '(no log data)'

    return {
        'file_path': file_path,
        'lines_count': lines_count,
        'active': active,
        'uptime': uptime_str,
        'log_preview': log_preview,
    }

# ---- Configuration ----
if sys.platform == "win32":
    SOCKET_HOST = "127.0.0.1"
    SOCKET_PORT = 25570
    SOCKET_PATH = None  # not used
else:
    SOCKET_HOST = None
    SOCKET_PORT = None
    SOCKET_PATH = "/tmp/yuniScripts-logviewer.sock"

if sys.platform == "win32":
    ENGINE_SOCKET_HOST = "127.0.0.1"
    ENGINE_SOCKET_PORT = TCP_ADMIN_PORT
else:
    ENGINE_SOCKET_PATH = "/tmp/yuniScripts.sock"

LOG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "engine" / "logs"


def _create_listen_socket():
    """Create a listening socket — TCP on Windows, Unix domain on Linux."""
    if sys.platform == "win32":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((SOCKET_HOST, SOCKET_PORT))
        sock.listen(5)
        sock.settimeout(1)
        print(f"Log viewer listening on {SOCKET_HOST}:{SOCKET_PORT}")
        return sock
    else:
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(SOCKET_PATH)
        sock.listen(5)
        sock.settimeout(1)
        print(f"Log viewer listening on {SOCKET_PATH}")
        return sock


def _cleanup_listen_socket(sock):
    """Close the socket and optionally remove the Unix socket file."""
    sock.close()
    if sys.platform != "win32" and SOCKET_PATH:
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass


# ---- Terminal detection (best-effort, Linux only) ----
def find_terminal():
    """
    Return a working terminal command prefix.
    Only meaningful on Linux/macOS — returns None on Windows.
    """
    if sys.platform == "win32":
        return None  # Windows doesn't use X terminal emulators

    # 1. Generic X terminal wrapper (available on most Linux desktops, including SteamOS)
    wrapper = "/usr/bin/x-terminal-emulator"
    if os.path.isfile(wrapper) and os.access(wrapper, os.X_OK):
        return [wrapper, "-e"]

    # 2. Try well-known terminals with absolute paths
    known_terminals = [
        ["/usr/bin/konsole", "-e"],
        ["/usr/bin/xterm", "-hold", "-e"],
        ["/usr/bin/gnome-terminal", "--"],
        ["/usr/bin/xfce4-terminal", "--hold", "-e"],
        ["/usr/bin/terminator", "-e"],
        ["/usr/bin/alacritty", "-e"],
    ]
    for term_cmd in known_terminals:
        if os.path.isfile(term_cmd[0]) and os.access(term_cmd[0], os.X_OK):
            return term_cmd

    # 3. Search PATH using shutil.which (extended PATH if needed)
    extended_path = os.environ.get("PATH", "") + ":/usr/bin:/usr/local/bin"
    for term_name in ["konsole", "xterm", "gnome-terminal", "xfce4-terminal", "terminator", "alacritty"]:
        full_path = shutil.which(term_name, path=extended_path)
        if full_path:
            if term_name == "konsole":
                return [full_path, "-e"]
            elif term_name in ("xterm", "xfce4-terminal"):
                return [full_path, "-hold", "-e"]
            elif term_name == "gnome-terminal":
                return [full_path, "--"]
            else:
                return [full_path, "-e"]

    return None

# ---- Window spawning ----
def spawn_log_window(script_id):
    """Open a new terminal window running `tail -f` on the script's log."""
    safe_name = script_id.replace("/", "_").replace("\\", "_")
    log_file = LOG_DIR / f"{safe_name}.log"

    if not log_file.exists():
        return None, f"Log file not found: {log_file}"

    term_prefix = find_terminal()
    if not term_prefix:
        # Provide diagnostic info
        path_info = f"PATH={os.environ.get('PATH', 'not set')}"
        return None, f"No terminal emulator found. Tried x-terminal-emulator, konsole, xterm, etc. {path_info}"

    cmd = term_prefix + ["tail", "-f", str(log_file)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc, f"Opened log window for {script_id} (terminal PID={proc.pid})"
    except Exception as e:
        return None, f"Failed to launch terminal ({term_prefix[0]}): {e}"

# ---- Engine status check ----
def _connect_engine_socket():
    """Connect to the engine's admin socket (Unix on Linux, TCP on Windows)."""
    if sys.platform == "win32":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((ENGINE_SOCKET_HOST, ENGINE_SOCKET_PORT))
        return sock
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(ENGINE_SOCKET_PATH)
        return sock

def is_script_running(script_id):
    """Return True if the script is currently in 'running' state."""
    try:
        sock = _connect_engine_socket()
        sock.sendall(b"status\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        text = data.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.lstrip().startswith(script_id + ":"):
                return "running" in line
        return False
    except Exception as e:
        print(f"Error checking engine status: {e}", file=sys.stderr)
        return False

# ---- Watched windows management ----
watched = {}
watched_lock = threading.Lock()

log_buffers = {}      # script_id -> list of recent log lines (last 50)
log_buffers_lock = threading.Lock()

def _read_log_lines(file_path, max_lines=50):
    """Read the last max_lines from a log file."""
    try:
        with open(file_path, 'r', errors='replace') as f:
            lines = f.readlines()
            return lines[-max_lines:]
    except Exception:
        return []

def update_log_buffers():
    """Periodically read latest lines from watched log files into buffers."""
    while True:
        time.sleep(2)
        with watched_lock:
            current_watched = dict(watched)
        for sid, info in current_watched.items():
            file_path = info.get('file_path', '')
            if file_path and os.path.isfile(file_path):
                lines = _read_log_lines(file_path, 50)
                with log_buffers_lock:
                    log_buffers[sid] = lines

def close_window(script_id):
    with watched_lock:
        info = watched.pop(script_id, None)
        if info and info["terminal"]:
            try:
                info["terminal"].terminate()
                info["terminal"].wait(timeout=2)
            except Exception:
                try:
                    info["terminal"].kill()
                except Exception:
                    pass
            print(f"Closed log window for {script_id}")

def monitor_windows():
    """Periodically check watched windows and close if script stops or window dies."""
    while True:
        time.sleep(3)
        to_close = []
        with watched_lock:
            for sid, info in list(watched.items()):
                terminal = info["terminal"]
                if terminal.poll() is not None:
                    to_close.append(sid)
                elif not is_script_running(sid):
                    to_close.append(sid)
        for sid in to_close:
            close_window(sid)

# ---- Socket command handler ----
def handle_command(cmd):
    parts = cmd.strip().split()
    if not parts:
        return "Bad command"
    verb = parts[0].lower()
    if verb == "watch":
        if len(parts) < 2:
            return "Usage: watch <script_id>"
        sid = parts[1]
        safe_name = sid.replace("/", "_").replace("\\", "_")
        log_file = LOG_DIR / f"{safe_name}.log"
        with watched_lock:
            if sid in watched:
                return f"Already watching {sid}"
            proc, msg = spawn_log_window(sid)
            if proc:
                # Read initial lines
                init_lines = _read_log_lines(str(log_file), 50)
                watched[sid] = {
                    "terminal": proc,
                    "log_window_pid": proc.pid,
                    "file_path": str(log_file),
                    "lines_count": len(init_lines),
                    "active": "Yes",
                }
                with log_buffers_lock:
                    log_buffers[sid] = init_lines
                return msg
            else:
                return msg
    elif verb == "unwatch":
        if len(parts) < 2:
            return "Usage: unwatch <script_id>"
        sid = parts[1]
        close_window(sid)
        return f"Unwatched {sid}"
    elif verb == "list":
        with watched_lock:
            if not watched:
                return "No watched scripts."
            return "\n".join(f"{sid} (pid={info['log_window_pid']})" for sid, info in watched.items())
    elif verb in ("stop", "exit"):
        with watched_lock:
            sids = list(watched.keys())
        for sid in sids:
            close_window(sid)
        return "Log viewer shutting down."
    else:
        return f"Unknown command: {verb}"

# ---- Main ----
def main():
    server = _create_listen_socket()
    gui.on_data_request(_get_gui_data)

    monitor_thread = threading.Thread(target=monitor_windows, daemon=True)
    monitor_thread.start()

    buffer_thread = threading.Thread(target=update_log_buffers, daemon=True)
    buffer_thread.start()

    running = True
    def shutdown(signum=None, frame=None):
        nonlocal running
        running = False
    # SIGINT works on all platforms; SIGTERM is Unix-only
    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except OSError:
        pass  # SIGTERM not available on Windows

    while running:
        try:
            conn, addr = server.accept()
            with conn:
                conn.settimeout(5)
                try:
                    data = conn.recv(4096).decode("utf-8", errors="replace")
                    if not data:
                        continue
                    response = handle_command(data.strip())
                    conn.sendall((response + "\n").encode("utf-8"))
                except socket.timeout:
                    pass
                except Exception as e:
                    print(f"Connection error: {e}")
        except socket.timeout:
            continue
        except OSError:
            break

    with watched_lock:
        for sid in list(watched.keys()):
            close_window(sid)
    _cleanup_listen_socket(server)
    gui.close()
    print("Log viewer stopped.")
    print("SHUTDOWN_COMPLETE", flush=True)

if __name__ == "__main__":
    main()