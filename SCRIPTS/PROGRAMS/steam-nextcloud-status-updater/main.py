#!/usr/bin/env python3
"""
Nextcloud status updater – queries MC status relay on UDP QUERY_PORT (engine.ports).
"""
import time, requests, subprocess, sys, signal, socket
from requests.auth import HTTPBasicAuth
from pathlib import Path
from configparser import ConfigParser
from datetime import datetime
from collections import deque

# ── GUI Dashboard integration ──────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from engine.gui_api_client import GuiApiClient
from engine.ports import QUERY_PORT

gui = GuiApiClient("PROGRAMS/steam-nextcloud-status-updater", "Steam Status")
gui.register_tab([
    {"type": "label", "id": "status", "title": "Current Status", "default": "Starting..."},
    {"type": "label", "id": "mc_biome", "title": "MC Biome", "default": "-"},
    {"type": "label", "id": "mc_dimension", "title": "MC Dimension", "default": "-"},
    {"type": "label", "id": "nextcloud_message", "title": "Nextcloud Message", "default": "-"},
    {"type": "label", "id": "uptime", "title": "Uptime", "default": "0s"},
    {"type": "label", "id": "last_update", "title": "Last Update", "default": "Never"},
    {"type": "message", "id": "log", "title": "Activity Log", "default": "No activity yet"},
])

_gui_mc_biome = None
_gui_mc_dim = None
_start_time = time.time()
_last_sync_time = 0
_log = deque(maxlen=50)

def _format_uptime(seconds):
    """Format seconds into a human-readable uptime string."""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"

def _get_gui_data():
    """Return current widget data for the GUI dashboard."""
    uptime_seconds = time.time() - _start_time
    last_update_str = datetime.fromtimestamp(_last_sync_time).strftime("%Y-%m-%d %H:%M:%S") if _last_sync_time > 0 else "Never"
    log_text = "\n".join(reversed(list(_log))) if _log else "No activity yet"
    data = {
        "status": "Active (MC)" if last_mc_active else "Idle",
        "mc_biome": str(_gui_mc_biome) if _gui_mc_biome else "-",
        "mc_dimension": str(_gui_mc_dim) if _gui_mc_dim else "-",
        "nextcloud_message": PREVIOUS_STATUS.get("message", "-") if isinstance(PREVIOUS_STATUS, dict) else "-",
        "uptime": _format_uptime(uptime_seconds),
        "last_update": last_update_str,
        "log": log_text,
    }
    return data

# ── End GUI Dashboard integration ──────────────────────────────

DEBUG_LOG = Path(__file__).parent / "DATA" / "debug.log"

def dbg(msg):
    global _last_sync_time
    timestamp = datetime.now().isoformat()
    _log.appendleft(f"[{timestamp}] {msg}")
    _last_sync_time = time.time()
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")
    print(msg, flush=True)

DEFAULT_CONFIG = {
    "steam_api_key": "YOUR_STEAM_API_KEY",
    "steam_id": "76561198048401655",
    "nextcloud_url": "https://your.nextcloud.com",
    "nextcloud_user": "admin",
    "nextcloud_app_password": "YOUR_APP_PASSWORD",
    "in_game_interval": "5",
    "recent_interval": "15",
    "idle_interval": "60",
    "recent_window": "300",
}

from engine.config_loader import get_config_path, load_config

def load_config():
    config = ConfigParser()
    config_path = get_config_path("steam_nextcloud")
    if config_path.exists():
        config.read(str(config_path))
        if "settings" in config:
            return dict(config["settings"])
    dbg("Config file not found, using defaults.")
    return DEFAULT_CONFIG.copy()

config = load_config()
STEAM_API_KEY = config["steam_api_key"]
STEAM_ID = config["steam_id"]
NEXTCLOUD_URL = config["nextcloud_url"]
NEXTCLOUD_USER = config["nextcloud_user"]
NEXTCLOUD_APP_PASSWORD = config["nextcloud_app_password"]
IN_GAME_INTERVAL = int(config.get("in_game_interval", 5))
RECENT_INTERVAL = int(config.get("recent_interval", 15))
IDLE_INTERVAL = int(config.get("idle_interval", 60))
RECENT_WINDOW = int(config.get("recent_window", 300))

# MC relay config (from centralized config, with fallback defaults)
MC_RELAY_HOST = config.get("mc_relay_host", "127.0.0.1")
MC_RELAY_PORT = int(config.get("mc_relay_port", QUERY_PORT))

PREVIOUS_STATUS = None
last_mc_active = False

def udp_query(host, port, cmd):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.sendto(cmd.encode(), (host, port))
        data, _ = sock.recvfrom(1024)
        sock.close()
        return data.decode().strip()
    except Exception as e:
        dbg(f"UDP query error: {e}")
        return None

def get_mc_status():
    """Return (biome, dim, age_seconds) or (None,None,None)."""
    resp = udp_query(MC_RELAY_HOST, MC_RELAY_PORT, "status")
    if not resp or resp == "none":
        return None, None, None
    parts = resp.split()
    if len(parts) >= 3:
        biome, dim, age = parts[0], parts[1], int(parts[2])
        return biome, dim, age
    return None, None, None

def set_nextcloud_custom_message(message, icon=None):
    url = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/user_status/api/v1/user_status/message/custom"
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
    params = {"format": "json"}
    auth = HTTPBasicAuth(NEXTCLOUD_USER, NEXTCLOUD_APP_PASSWORD)
    data = {"message": message}
    if icon: data["statusIcon"] = icon
    try:
        resp = requests.put(url, headers=headers, params=params, auth=auth, data=data, timeout=10)
        dbg(f"Set NC message: '{message}' -> {resp.status_code}")
    except Exception as e:
        dbg(f"NC message failed: {e}")

def set_nextcloud_online_status(status_type):
    url = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/user_status/api/v1/user_status/status"
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
    params = {"format": "json"}
    auth = HTTPBasicAuth(NEXTCLOUD_USER, NEXTCLOUD_APP_PASSWORD)
    data = {"statusType": status_type}
    try:
        requests.put(url, headers=headers, params=params, auth=auth, data=data, timeout=10)
    except Exception as e:
        dbg(f"Online status error: {e}")

def save_previous_status():
    global PREVIOUS_STATUS
    url = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/user_status/api/v1/user_status"
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
    params = {"format": "json"}
    auth = HTTPBasicAuth(NEXTCLOUD_USER, NEXTCLOUD_APP_PASSWORD)
    try:
        resp = requests.get(url, headers=headers, params=params, auth=auth, timeout=10)
        data = resp.json()
        ocs = data.get('ocs', {})
        if ocs.get('meta', {}).get('status') == 'ok':
            d = ocs['data']
            PREVIOUS_STATUS = {
                'message': d.get('message'),
                'icon': d.get('icon'),
                'clearAt': d.get('clearAt'),
                'status': d.get('status')
            }
            dbg(f"Saved previous status: {PREVIOUS_STATUS}")
    except Exception as e:
        dbg(f"Failed to save previous status: {e}")

def restore_previous_status():
    global PREVIOUS_STATUS
    if PREVIOUS_STATUS:
        dbg(f"Restoring: {PREVIOUS_STATUS}")
        if PREVIOUS_STATUS['clearAt'] is not None:
            set_nextcloud_custom_message("")
            set_nextcloud_online_status("online")
        else:
            set_nextcloud_custom_message(PREVIOUS_STATUS['message'] or "", icon=PREVIOUS_STATUS['icon'])
            set_nextcloud_online_status(PREVIOUS_STATUS['status'] or "online")
        PREVIOUS_STATUS = None
    else:
        set_nextcloud_custom_message("")
        set_nextcloud_online_status("online")

# ── Shutdown handler ─────────────────────────────────────────────
_shutdown_flag = False

def _shutdown(signum=None, frame=None):
    global _shutdown_flag
    if _shutdown_flag:
        return
    _shutdown_flag = True
    dbg("Shutting down, saving current status...")
    try:
        save_previous_status()
    except Exception as e:
        dbg(f"Shutdown save error: {e}")
    print("SHUTDOWN_COMPLETE", flush=True)

def main():
    global last_mc_active
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except OSError:
        pass  # SIGTERM not available on Windows
    signal.signal(signal.SIGINT, _shutdown)

    dbg("=== Updater started (MC relay) ===")
    save_previous_status()
    gui.on_data_request(_get_gui_data)
    dbg("GUI dashboard data request handler registered")

    try:
        while not _shutdown_flag:
            mc_biome, mc_dim, mc_age = get_mc_status()
            mc_active = (mc_biome is not None and mc_age is not None and mc_age < 20)

            # Track MC status for GUI dashboard
            global _gui_mc_biome, _gui_mc_dim
            _gui_mc_biome = mc_biome
            _gui_mc_dim = mc_dim

            if mc_active:
                msg = f"Playing Minecraft in {mc_biome} ({mc_dim})"
                set_nextcloud_custom_message(msg, icon="🎮")
                last_mc_active = True
            elif last_mc_active:
                dbg("MC no longer active")
                restore_previous_status()
                last_mc_active = False
                # after restoring, save new status as "previous" so it doesn't keep restoring
                save_previous_status()

            time.sleep(IN_GAME_INTERVAL)
    except KeyboardInterrupt:
        _shutdown()
    except Exception as e:
        dbg(f"Fatal error: {e}")
    finally:
        gui.close()
        _shutdown()

if __name__ == "__main__":
    main()