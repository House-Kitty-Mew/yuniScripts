#!/usr/bin/env python3
"""Minecraft Status Relay – receives Minescript UDP and answers queries."""
import socket
import threading
import time
import sys
import signal
from collections import deque
from pathlib import Path

# ── Project root & GUI API ────────────────────────────────────────
_PROJECT_ROOT = (Path(__file__).resolve().parent.parent.parent.parent)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from engine.gui_api_client import GuiApiClient
from engine.ports import MINESCRIPT_SENDER_PORT, QUERY_PORT, LAN_DISCOVERY_PORT, PHOOKS_HUB_PORT

# ── Dynamic Config Loader (Admin GUI API) ─────────────────────────
_DYNAMIC_CONFIG_AVAILABLE = False
try:
    _TOOLS_DIR = str(_PROJECT_ROOT / 'SCRIPTS' / 'SERVICES' / 'fastmcp_server' / 'tools')
    if _TOOLS_DIR not in sys.path:
        sys.path.insert(0, _TOOLS_DIR)
    from dynamic_config_loader import (
        register_configs, get_config, update_config,
        load_source, flush_source,
    )
    _DYNAMIC_CONFIG_AVAILABLE = True

    # Register mc-status-relay configurable settings
    register_configs("mc-status-relay", [
        {"key": "minescript_port", "type": "int", "default": MINESCRIPT_SENDER_PORT,
         "description": "UDP port for Minescript listener",
         "valid_range": (1024, 65535), "category": "network"},
        {"key": "query_port", "type": "int", "default": QUERY_PORT,
         "description": "UDP port for query listener",
         "valid_range": (1024, 65535), "category": "network"},
        {"key": "lan_beacon_enabled", "type": "bool", "default": True,
         "description": "Enable LAN discovery beacon",
         "category": "network"},
        {"key": "lan_finder_enabled", "type": "bool", "default": True,
         "description": "Enable LAN service finder",
         "category": "network"},
    ])
except ImportError:
    pass

# ── Load configurable ports (with defaults) ─────────────────────
import os, json
_MC_RELAY_CONFIG = {}
_config_path = Path(__file__).parent / "config.json"
if _config_path.exists():
    try:
        _MC_RELAY_CONFIG = json.loads(_config_path.read_text())
    except Exception:
        pass
# Also try centralized DATA/ path
_from_data = Path(__file__).resolve().parent.parent.parent.parent / "DATA" / "mc_status_relay_config.json"
if _from_data.exists():
    try:
        _MC_RELAY_CONFIG.update(json.loads(_from_data.read_text()))
    except Exception:
        pass

# Merge in dynamic config values (admin GUI overrides take precedence)
if _DYNAMIC_CONFIG_AVAILABLE:
    for key in ("minescript_port", "query_port"):
        dcl_val = get_config("mc-status-relay", key)
        if dcl_val is not None:
            _MC_RELAY_CONFIG[key] = dcl_val
    # Load persisted config from DATA/
    try:
        load_source("mc-status-relay")
    except Exception:
        pass

MINESCRIPT_PORT = int(_MC_RELAY_CONFIG.get("minescript_port", MINESCRIPT_SENDER_PORT))
QUERY_PORT = int(_MC_RELAY_CONFIG.get("query_port", QUERY_PORT))

# Shared state
lock = threading.Lock()
state = {"biome": "", "dim": "", "last_update": 0.0}

# Enhanced GUI globals
_start_time = time.time()
_packet_count = 0
log_buffer = deque(maxlen=50)


# ── GUI helpers ──────────────────────────────────────────────────
def register_tab(gui: GuiApiClient) -> None:
    """Register the MC Status Relay tab with the GUI dashboard."""
    gui.register_tab([
        {"type": "label", "id": "biome", "title": "Biome", "default": "—"},
        {"type": "label", "id": "dimension", "title": "Dimension", "default": "—"},
        {"type": "label", "id": "last_update", "title": "Last Update", "default": "Never"},
        {"type": "label", "id": "service_status", "title": "Service Status", "default": "Running"},
        {"type": "label", "id": "uptime", "title": "Uptime", "default": "0s"},
        {"type": "label", "id": "packets_received", "title": "Packets Received", "default": "0"},
        {"type": "label", "id": "listening_ports", "title": "Listening Ports", "default": "25566, 25568"},
        {"type": "message", "id": "log", "title": "Activity Log", "default": "No activity yet."},
    ])


def _get_gui_data() -> dict:
    """Return current relay state as widget_id -> value mapping."""
    with lock:
        biome = state["biome"] or "—"
        dim = state["dim"] or "—"
        last = state["last_update"]
        svc_status = "Stopped" if _shutdown_flag else "Running"
        pkt_count = _packet_count
        log_lines = list(log_buffer)
    if last == 0.0:
        age_str = "Never"
    else:
        age_sec = int(time.time() - last)
        age_str = f"{age_sec}s ago"
    uptime_sec = int(time.time() - _start_time)
    uptime_str = f"{uptime_sec}s"
    log_str = "\n".join(log_lines) if log_lines else "No activity yet."
    return {
        "biome": biome,
        "dimension": dim,
        "last_update": age_str,
        "service_status": svc_status,
        "uptime": uptime_str,
        "packets_received": str(pkt_count),
        "listening_ports": "25566, 25568",
        "log": log_str,
    }


# ── Core listeners ───────────────────────────────────────────────
def minescript_listener():
    global _packet_count, log_buffer
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except (AttributeError, OSError):
        pass
    sock.bind(("", MINESCRIPT_PORT))
    print(f"[relay] Listening for Minescript on UDP {MINESCRIPT_PORT}")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            msg = data.decode().strip()
            parts = msg.split()
            biome, dim = "", ""
            for p in parts:
                if p.startswith("BIOME:"):
                    biome = p[6:]
                elif p.startswith("DIM:"):
                    dim = p[4:]
            with lock:
                state["biome"] = biome
                state["dim"] = dim
                state["last_update"] = time.time()
                _packet_count += 1
                log_buffer.append(f"[{time.strftime('%H:%M:%S')}] Minescript packet from {addr[0]}:{addr[1]} — biome={biome}, dim={dim}")
        except Exception as e:
            print(f"[relay] Error: {e}")


def query_listener():
    global log_buffer
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except (AttributeError, OSError):
        pass
    sock.bind(("", QUERY_PORT))
    print(f"[relay] Query listener on UDP {QUERY_PORT}")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            cmd = data.decode().strip()
            if cmd == "status":
                with lock:
                    biome = state["biome"]
                    dim = state["dim"]
                    last = state["last_update"]
                if last == 0.0:
                    resp = "none"
                else:
                    age = int(time.time() - last)
                    resp = f"{biome} {dim} {age}"
                sock.sendto(resp.encode(), addr)
                with lock:
                    log_buffer.append(f"[{time.strftime('%H:%M:%S')}] Query answered from {addr[0]}:{addr[1]} — resp={resp}")
        except Exception as e:
            print(f"[relay-query] Error: {e}")


# ── Shutdown handler ─────────────────────────────────────────────
_shutdown_flag = False


def _shutdown(signum=None, frame=None):
    global _shutdown_flag
    if _shutdown_flag:
        return
    _shutdown_flag = True
    # Stop LAN beacon & finder
    if _BEACON:
        try:
            _BEACON.stop()
        except Exception:
            pass
    if _FINDER:
        try:
            _FINDER.stop()
        except Exception:
            pass
    print("Status relay shutting down...", flush=True)


# ── LAN Discovery Beacon & Finder ───────────────────────────────
_BEACON = None
_FINDER = None

def _start_beacon():
    """Start broadcasting a LAN beacon so finders can locate this relay."""
    # Check if beacon is enabled via dynamic config
    if _DYNAMIC_CONFIG_AVAILABLE:
        beacon_enabled = get_config("mc-status-relay", "lan_beacon_enabled")
        if beacon_enabled is False:
            print("[relay] LAN beacon disabled via config", flush=True)
            return
    global _BEACON
    try:
        from engine.lan_discovery import ServiceBeacon
        _BEACON = ServiceBeacon(
            "mc_status_relay",
            port=MINESCRIPT_PORT,
            extra={"query_port": QUERY_PORT, "version": 1},
        )
        _BEACON.start()
        print(f"[relay] LAN beacon broadcasting on UDP {LAN_DISCOVERY_PORT} for 'mc_status_relay'", flush=True)
    except ImportError:
        print("[relay] engine.lan_discovery not available — no LAN beacon", flush=True)
    except Exception as e:
        print(f"[relay] LAN beacon start failed: {e}", flush=True)


def _start_finder():
    """Start LAN discovery to find the Phooks hub and other services."""
    # Check if finder is enabled via dynamic config
    if _DYNAMIC_CONFIG_AVAILABLE:
        finder_enabled = get_config("mc-status-relay", "lan_finder_enabled")
        if finder_enabled is False:
            print("[relay] LAN finder disabled via config", flush=True)
            return
    global _FINDER
    try:
        from engine.lan_discovery import ServiceFinder

        def _on_service(service_type, host, port, extra, is_new):
            if is_new or True:
                print(f"[relay] Discovered service '{service_type}' at {host}:{port}", flush=True)

        _FINDER = ServiceFinder(
            "phooks_hub",
            callback=_on_service,
            fallback={"host": "127.0.0.1", "port": PHOOKS_HUB_PORT},
        )
        _FINDER.start()
        print(f"[relay] LAN finder started — discovering services", flush=True)
    except ImportError:
        print("[relay] engine.lan_discovery not available — no LAN finder", flush=True)
    except Exception as e:
        print(f"[relay] LAN finder start failed: {e}", flush=True)


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except OSError:
        pass  # SIGTERM not available on Windows
    signal.signal(signal.SIGINT, _shutdown)

    _start_beacon()
    _start_finder()

    gui = GuiApiClient("SERVICES/mc-status-relay", "MC Status Relay")
    register_tab(gui)
    gui.on_data_request(_get_gui_data)

    threading.Thread(target=minescript_listener, daemon=True).start()
    threading.Thread(target=query_listener, daemon=True).start()
    # Keep the script alive until shutdown signal
    try:
        while not _shutdown_flag:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()
    finally:
        # Flush dynamic configs on shutdown
        if _DYNAMIC_CONFIG_AVAILABLE:
            try:
                flush_source("mc-status-relay")
            except Exception:
                pass
        gui.close()
        print("SHUTDOWN_COMPLETE", flush=True)
        sys.exit(0)
