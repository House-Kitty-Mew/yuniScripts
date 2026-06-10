"""Functional socket admin interface — Unix domain (Linux/macOS) or TCP (Windows).
Cross-platform: uses tempfile.gettempdir() for Unix socket path, not hardcoded /tmp."""
import os
import sys
import socket
import select
import threading
import tempfile
from typing import Dict, Callable
from pathlib import Path

from engine.ports import TCP_ADMIN_PORT

def _admin_socket_path():
    """Return a platform-appropriate socket path or TCP port.
    
    On Unix: uses tempfile.gettempdir() for the socket file location,
    which respects TMPDIR/TMP/TEMP environment variables.
    On Windows: returns TCP host/port tuple.
    """
    if sys.platform == "win32":
        return ("127.0.0.1", TCP_ADMIN_PORT)  # TCP on localhost
    return str(Path(tempfile.gettempdir()) / "yuniScripts.sock")

def _create_admin_socket():
    """Create either a Unix domain socket (Linux) or TCP socket (Windows)."""
    if sys.platform == "win32":
        host, port = _admin_socket_path()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port))
        sock.listen(5)
        sock.setblocking(False)
        print(f"[admin-socket] TCP listening on {host}:{port}")
        return sock, port
    else:
        path = _admin_socket_path()
        try:
            os.unlink(path)
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        sock.listen(5)
        sock.setblocking(False)
        print(f"[admin-socket] Unix socket listening on {path}")
        return sock, path

def _cleanup_admin_socket(addr):
    """Remove the socket file if it's a Unix socket path."""
    if sys.platform != "win32" and isinstance(addr, str):
        try:
            os.unlink(addr)
        except OSError:
            pass

def start_socket_server(command_handler: Callable, registry: Dict, running: Dict, watchers: Dict, engine_state: Dict) -> tuple:
    server, addr = _create_admin_socket()

    # Use mutable containers for the state so the closure can update them
    state_container = {
        "registry": registry,
        "running": running,
        "watchers": watchers,
        "engine_state": engine_state,
    }

    def client_thread(conn, addr):
        conn.setblocking(True)
        buffer = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    cmd = line.decode("utf-8", errors="replace").strip()
                    if not cmd:
                        continue
                    if cmd.lower() in ("exit", "quit"):
                        conn.sendall(b"bye\n")
                        conn.close()
                        return
                    reg = state_container["registry"]
                    run = state_container["running"]
                    wch = state_container["watchers"]
                    est = state_container["engine_state"]
                    # command_handler returns (registry, running, watchers, engine_state, response)
                    reg, run, wch, est, response = command_handler(reg, run, wch, est, cmd)
                    state_container["registry"] = reg
                    state_container["running"] = run
                    state_container["watchers"] = wch
                    state_container["engine_state"] = est
                    conn.sendall((response + "\n").encode("utf-8"))
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def accept_loop():
        while True:
            try:
                conn, addr = server.accept()
                t = threading.Thread(target=client_thread, args=(conn, addr), daemon=True)
                t.start()
            except BlockingIOError:
                pass
            except OSError:
                break

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()

    server_state = {
        "server": server,
        "thread": thread,
        "addr": addr,
    }
    return state_container["registry"], state_container["running"], state_container["watchers"], state_container["engine_state"], server_state

def stop_socket_server(server_state: Dict):
    server = server_state.get("server")
    if server:
        server.close()
    _cleanup_admin_socket(server_state.get("addr"))