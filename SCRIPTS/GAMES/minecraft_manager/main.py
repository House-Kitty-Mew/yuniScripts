#!/usr/bin/env python3
"""Minecraft Server Manager – Phooks + built‑in RCON + Multi-Server Engine."""
import json, sys, subprocess, time, threading, traceback, queue, socket, os, struct, hashlib, sqlite3, re, signal
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque

# ── GUI API Client ──────────────────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from engine.gui_api_client import GuiApiClient

# ── Dynamic Config Loader (Admin GUI API) ─────────────────────────
_DYNAMIC_CONFIG_AVAILABLE = False
try:
    _TOOLS_DIR = os.path.join(_PROJECT_ROOT, 'SCRIPTS', 'SERVICES', 'fastmcp_server', 'tools')
    if _TOOLS_DIR not in sys.path:
        sys.path.insert(0, _TOOLS_DIR)
    from dynamic_config_loader import (
        register_configs, get_config, update_config,
        load_source, flush_source,
    )
    _DYNAMIC_CONFIG_AVAILABLE = True

    # Register minecraft_manager configurable settings for Admin GUI
    register_configs("minecraft_manager", [
        {"key": "rcon_host", "type": "str", "default": "127.0.0.1",
         "description": "Legacy RCON host address",
         "category": "general"},
        {"key": "rcon_port", "type": "int", "default": 25575,
         "description": "Legacy RCON port",
         "valid_range": (1, 65535), "category": "network"},
        {"key": "rcon_password", "type": "str", "default": "",
         "description": "Legacy RCON password",
         "category": "security"},
        {"key": "multi_server_enabled", "type": "bool", "default": True,
         "description": "Enable multi-server engine",
         "category": "general"},
        {"key": "multi_server_keepalive_interval", "type": "int", "default": 30,
         "description": "RCON pool keepalive interval (seconds)",
         "valid_range": (5, 300), "category": "performance"},
    ])
except ImportError:
    pass

# ── Multi-Server Engine ──────────────────────────────────────────
from engine.multi_server import MultiServerManager, ensure_default_config

gui = GuiApiClient('GAMES/minecraft_manager', 'Minecraft Manager')
gui.register_tab([
    {'type': 'label', 'id': 'server_status', 'title': 'Server', 'default': 'Offline'},
    {'type': 'label', 'id': 'rcon', 'title': 'RCON', 'default': 'Disconnected'},
    {'type': 'label', 'id': 'players', 'title': 'Players', 'default': '0'},
    {'type': 'label', 'id': 'uptime', 'title': 'Uptime', 'default': '0s'},
    {'type': 'label', 'id': 'last_heartbeat', 'title': 'Heartbeat', 'default': 'never'},
])

def _format_uptime(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    else:
        return f"{s}s"

_log_buffer = []
_LOG_BUFFER_MAX = 50

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    _log_buffer.append(line)
    if len(_log_buffer) > _LOG_BUFFER_MAX:
        _log_buffer.pop(0)

# ── Config ───────────────────────────────────────────────────────
def load_config():
    config_path = Path(__file__).parent / "DATA" / "config.json"
    if not config_path.exists():
        log(f"config.json not found at {config_path}")
        sys.exit(1)
    with open(config_path) as f:
        config = json.load(f)
    if "rcon_port" in config:
        config["rcon_port"] = int(config["rcon_port"])

    # Merge in dynamic config values (admin GUI overrides take precedence)
    if _DYNAMIC_CONFIG_AVAILABLE:
        key_map = {
            "rcon_host": "rcon_host",
            "rcon_port": "rcon_port",
            "rcon_password": "rcon_password",
            "multi_server_enabled": ("multi_server", "enabled"),
            "multi_server_keepalive_interval": ("multi_server", "keepalive_interval_seconds"),
        }
        for config_key, dcl_key in key_map.items():
            if isinstance(dcl_key, tuple):
                # Nested config key
                dcl_val = get_config("minecraft_manager", dcl_key[0])
                if dcl_val is not None and config_key in config:
                    if isinstance(config[config_key], dict) and dcl_key[1] in config[config_key]:
                        config[config_key][dcl_key[1]] = dcl_val
            else:
                dcl_val = get_config("minecraft_manager", dcl_key)
                if dcl_val is not None:
                    config[config_key] = dcl_val
        # Load persisted config from DATA/
        try:
            load_source("minecraft_manager")
        except Exception:
            pass

    return config

config = load_config()
RCON_HOST = config["rcon_host"]
RCON_PORT = config["rcon_port"]
RCON_PASSWORD = config["rcon_password"]
log(f"Legacy RCON target: {RCON_HOST}:{RCON_PORT}")

# ── Multi-Server Manager initialization ──────────────────────────
_multi_config = config.get('multi_server', {})
_multi_enabled = _multi_config.get('enabled', True)
if _multi_enabled:
    try:
        ensure_default_config()
        _multi_mgr = MultiServerManager(_multi_config)
        discovered = _multi_mgr.discovery.server_count
        log(f"Multi-server: {discovered} server(s) discovered from mc-server-runner DB")
        _multi_mgr.rcon_pool.start_keepalive()
    except Exception as _m_err:
        log(f"Multi-server init error (non-fatal): {_m_err}")
        _multi_mgr = None
else:
    _multi_mgr = None

def _get_multi_mgr():
    return _multi_mgr if _multi_enabled else None

# ── Built-in RCON (legacy single-server) ─────────────────────────
def rcon_command(cmd: str, timeout: float = 5.0) -> str:
    port = int(RCON_PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((RCON_HOST, port))
        auth_packet = struct.pack('<ii', 0, 3) + RCON_PASSWORD.encode('utf-8') + b'\x00\x00'
        sock.sendall(struct.pack('<i', len(auth_packet)) + auth_packet)
        _ = sock.recv(4)
        resp_data = sock.recv(4096)
        if len(resp_data) < 10:
            raise ConnectionError("RCON authentication failed")
        cmd_bytes = cmd.encode('utf-8')
        cmd_packet = struct.pack('<ii', 2, 2) + cmd_bytes + b'\x00\x00'
        sock.sendall(struct.pack('<i', len(cmd_packet)) + cmd_packet)
        length_data = sock.recv(4)
        if len(length_data) < 4:
            return ""
        length = struct.unpack('<i', length_data)[0]
        response = b''
        while len(response) < length:
            chunk = sock.recv(length - len(response))
            if not chunk:
                break
            response += chunk
        if response:
            response = response[4:-2]
        return response.decode('utf-8', errors='replace')
    finally:
        sock.close()

def legacy_status():
    try:
        players = rcon_command("list")
        try:
            tps = rcon_command("tps")
        except Exception:
            tps = "N/A"
        return {"online": True, "players": players, "tps": tps}
    except Exception as e:
        return {"online": False, "error": str(e)}

# ── Phooks Client ────────────────────────────────────────────────
from engine.phooks_client import PhooksClient

# ── Multi-Server Phooks event handler ────────────────────────────
def _handle_multi_event(event: dict, phooks_client) -> None:
    """Handle multi-server Phooks events."""
    mgr = _get_multi_mgr()
    if not mgr:
        phooks_client.emit(event.get("event", "") + "_response", {
            "status": "error",
            "error": "Multi-server mode disabled",
        })
        return
    evt = event["event"]
    data = event.get("data", {})
    log(f"Multi-server event: {evt}")
    try:
        if evt == "multi_command":
            server = data.get("server", "")
            cmd = data.get("command", "")
            if not server or not cmd:
                phooks_client.emit("multi_command_response", {
                    "status": "error", "error": "server and command required"
                })
                return
            resp = mgr.send_command(server, cmd)
            phooks_client.emit("multi_command_response", {
                "status": "ok", "server": server, "command": cmd, "response": resp
            })
        elif evt == "multi_custom_command":
            server = data.get("server", "")
            cc_name = data.get("custom_command", "")
            if not server or not cc_name:
                phooks_client.emit("multi_custom_command_response", {
                    "status": "error", "error": "server and custom_command required"
                })
                return
            result = mgr.custom_commands.run(server, cc_name, mgr.send_command)
            phooks_client.emit("multi_custom_command_response", {
                "status": "ok", **result
            })
        elif evt == "multi_broadcast":
            cmd = data.get("command", "")
            if not cmd:
                phooks_client.emit("multi_broadcast_response", {
                    "status": "error", "error": "command required"
                })
                return
            results = mgr.broadcast_command(cmd)
            phooks_client.emit("multi_broadcast_response", {
                "status": "ok", "command": cmd, "results": results
            })
        elif evt == "multi_subsystem_toggle":
            server = data.get("server", "")
            subsystem = data.get("subsystem", "")
            enabled = data.get("enabled", False)
            if not server or not subsystem:
                phooks_client.emit("multi_subsystem_toggle_response", {
                    "status": "error", "error": "server and subsystem required"
                })
                return
            ok = mgr.subsystems.set_enabled(server, subsystem, enabled)
            phooks_client.emit("multi_subsystem_toggle_response", {
                "status": "ok", "server": server, "subsystem": subsystem,
                "enabled": enabled, "changed": ok
            })
        elif evt == "multi_query":
            action = data.get("action", "list").lower()
            server = data.get("server", "")
            if action == "list":
                servers = mgr.list_servers(with_status=True)
                phooks_client.emit("multi_query_response", {
                    "status": "ok", "action": "list", "servers": servers
                })
            elif action == "detail" and server:
                detail = mgr.get_server_detail(server)
                phooks_client.emit("multi_query_response", {
                    "status": "ok" if detail else "error",
                    "action": "detail", "server": detail or {"error": f"Server '{server}' not found"}
                })
            else:
                phooks_client.emit("multi_query_response", {
                    "status": "error", "error": f"Unknown query: {action} for {server}"
                })
    except Exception as e:
        log(f"Multi-server event error: {e}")
        phooks_client.emit(evt + "_response", {
            "status": "error", "error": str(e)
        })

# ── Main ─────────────────────────────────────────────────────────
def main():
    global _start_time, _last_heartbeat, _last_check_time
    _start_time = time.time()
    _last_heartbeat = time.time()
    _last_check_time = time.time()
    _shutdown_flag = [False]

    def _shutdown(signum=None, frame=None):
        if _shutdown_flag[0]:
            return
        _shutdown_flag[0] = True
        log("Shutting down...")

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    from engine.phooks_client import PhooksEvents
    # Include multi-server events in Phooks registration
    from Phooks import PHOOKS_EVENTS_LISTEN, PHOOKS_EVENTS_EMIT

    client = PhooksClient(
        script_id="mc_manager",
        listen_events=PHOOKS_EVENTS_LISTEN,
        emit_events=PHOOKS_EVENTS_EMIT,
    )
    client.register()
    log(f"Manager ready — {len(PHOOKS_EVENTS_LISTEN)} listen events, {len(PHOOKS_EVENTS_EMIT)} emit events")

    try:
        while True:
            event = client.receive(timeout=0.5)
            if event is not None:
                evt_name = event.get("event", "")
                log(f"Phooks event: {evt_name}")

                if evt_name == "sign_request":
                    # Minimal sign request handling
                    log(f"Sign request: {event.get('data', {})}")
                    client.emit("sign_response", {
                        "request_id": event["data"].get("request_id", "?"),
                        "result": {"status": "ok", "message": "Signed (stub)"}
                    })

                elif evt_name.startswith("ah_"):
                    log(f"AH event: {evt_name}")

                elif evt_name.startswith("multi_"):
                    _handle_multi_event(event, client)

            if _shutdown_flag[0]:
                break

            now = time.time()
            if now - _last_heartbeat > 30:
                log("Heartbeat")
                _last_heartbeat = now

            # Process stdin commands
            try:
                if sys.platform == "win32":
                    import msvcrt
                    has_input = msvcrt.kbhit()
                else:
                    import select
                    has_input = bool(select.select([sys.stdin], [], [], 0.1)[0])
            except (OSError, ValueError, ImportError):
                has_input = False

            if has_input:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                cmd = parts[0].lower()
                try:
                    if cmd == "start":
                        result = {"status": "started"}
                    elif cmd == "stop":
                        result = {"status": "stopping"}
                    elif cmd == "status":
                        result = legacy_status()
                    elif cmd == "command":
                        mc_cmd = " ".join(parts[1:])
                        result = {"response": rcon_command(mc_cmd)}

                    # ── Multi-Server commands ──────────────────────────
                    elif cmd == "servers":
                        mgr = _get_multi_mgr()
                        if not mgr:
                            result = {"error": "Multi-server mode disabled"}
                        else:
                            servers = mgr.list_servers(with_status=True)
                            result = {"servers": servers, "count": len(servers)}

                    elif cmd == "rcon":
                        mgr = _get_multi_mgr()
                        if not mgr:
                            result = {"error": "Multi-server mode disabled"}
                        elif len(parts) < 3:
                            result = {"error": "Usage: rcon <server> <mc_command>"}
                        else:
                            server_name = parts[1]
                            mc_cmd = " ".join(parts[2:])
                            try:
                                resp = mgr.send_command(server_name, mc_cmd)
                                result = {"server": server_name, "command": mc_cmd, "response": resp}
                            except Exception as e:
                                result = {"server": server_name, "error": str(e)}

                    elif cmd == "broadcast":
                        mgr = _get_multi_mgr()
                        if not mgr:
                            result = {"error": "Multi-server mode disabled"}
                        elif len(parts) < 2:
                            result = {"error": "Usage: broadcast <mc_command>"}
                        else:
                            mc_cmd = " ".join(parts[1:])
                            results = mgr.broadcast_command(mc_cmd)
                            result = {"command": mc_cmd, "results": results}

                    elif cmd == "custom-commands":
                        mgr = _get_multi_mgr()
                        if not mgr:
                            result = {"error": "Multi-server mode disabled"}
                        elif len(parts) < 2:
                            result = {"error": "Usage: custom-commands <server> <list|add|remove|run> ..."}
                        else:
                            server_name = parts[1]
                            if len(parts) < 3:
                                result = {"commands": mgr.custom_commands.list(server_name)}
                            else:
                                action = parts[2].lower()
                                if action == "list":
                                    result = {"commands": mgr.custom_commands.get_commands_for_server(server_name)}
                                elif action == "add" and len(parts) >= 5:
                                    cc_name = parts[3]
                                    cc_cmds = " ".join(parts[4:])
                                    ok = mgr.custom_commands.add(server_name, cc_name, cc_cmds)
                                    result = {"added": ok, "name": cc_name}
                                elif action == "remove" and len(parts) >= 4:
                                    cc_name = parts[3]
                                    ok = mgr.custom_commands.remove(server_name, cc_name)
                                    result = {"removed": ok, "name": cc_name}
                                elif action == "run" and len(parts) >= 4:
                                    cc_name = parts[3]
                                    result = mgr.custom_commands.run(server_name, cc_name, mgr.send_command)
                                else:
                                    result = {"error": "Usage: custom-commands <server> <list|add|remove|run> [args...]"}

                    elif cmd == "subsystems":
                        mgr = _get_multi_mgr()
                        if not mgr:
                            result = {"error": "Multi-server mode disabled"}
                        elif len(parts) < 2:
                            result = {"error": "Usage: subsystems <server> <list|enable|disable> [subsystem]"}
                        else:
                            server_name = parts[1]
                            if len(parts) < 3:
                                result = mgr.subsystems.get_server_subsystem_summary(server_name)
                            else:
                                action = parts[2].lower()
                                if action == "list":
                                    result = {"subsystems": mgr.subsystems.list_subsystems(server_name)}
                                elif action in ("enable", "disable") and len(parts) >= 4:
                                    sub_name = parts[3]
                                    enabled = (action == "enable")
                                    ok = mgr.subsystems.set_enabled(server_name, sub_name, enabled)
                                    result = {"changed": ok, "subsystem": sub_name, "enabled": enabled}
                                else:
                                    result = {"error": f"Usage: subsystems {server_name} <list|enable|disable> [subsystem]"}

                    elif cmd == "detail":
                        mgr = _get_multi_mgr()
                        if not mgr:
                            result = {"error": "Multi-server mode disabled"}
                        elif len(parts) < 2:
                            result = {"error": "Usage: detail <server>"}
                        else:
                            detail = mgr.get_server_detail(parts[1])
                            if detail:
                                result = detail
                            else:
                                result = {"error": f"Server '{parts[1]}' not found"}

                    elif cmd in ("exit", "quit"):
                        break
                    else:
                        result = {"error": f"unknown command: {cmd}"}
                except Exception as e:
                    result = {"error": str(e)}
                log(json.dumps(result))

    except KeyboardInterrupt:
        log("Shutting down.")
    finally:
        if _get_multi_mgr():
            _multi_mgr.shutdown()
        client.unregister()
        log("Manager shut down.")
        print("SHUTDOWN_COMPLETE", flush=True)
        sys.exit(0)

if __name__ == "__main__":
    main()