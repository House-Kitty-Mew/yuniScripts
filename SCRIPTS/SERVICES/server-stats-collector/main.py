#!/usr/bin/env python3
"""
Server Stats Collector – polls remote daemon, stores in SQLite,
exposes hooks and UDP command interface.
"""
import json
import socket
import time
import threading
import sqlite3
import os
import sys
import signal
from pathlib import Path
from collections import deque

# ---------- Project root for engine imports ----------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------- GUI Dashboard integration ----------
from engine.gui_api_client import GuiApiClient
from engine.config_loader import assert_valid_config, register_config_schema


gui = GuiApiClient("SERVICES/server-stats-collector", "Server Stats")
gui.register_tab([
    {"type": "label", "id": "status", "title": "Status", "default": "Collecting..."},
    {"type": "meter", "id": "cpu", "title": "CPU Usage", "default": 0},
    {"type": "meter", "id": "ram", "title": "RAM Usage", "default": 0},
    {"type": "meter", "id": "disk", "title": "Disk Usage", "default": 0},
    {"type": "label", "id": "temp", "title": "Temperature", "default": "N/A"},
    {"type": "label", "id": "ram_details", "title": "RAM Details", "default": ""},
    {"type": "label", "id": "uptime", "title": "Uptime", "default": "0s"},
    {"type": "label", "id": "last_poll", "title": "Last Poll", "default": "Never"},
    {"type": "label", "id": "poll_count", "title": "Poll Count", "default": "0"},
    {"type": "message", "id": "log", "title": "Poll Log", "default": ""},
])

_latest_stats = None  # holds the most recent collect_stats() result
_start_time = time.time()
_poll_count = 0
_poll_log = deque(maxlen=50)
_last_poll_time = 0.0  # timestamp of the last successful poll (FIXED BUG 2)

def _get_gui_data():
    """Return real stats for the GUI dashboard from the latest poll.
    
    FIXED BUG 1: 'log' is now a string (joined from deque) instead of a
    raw list, matching the 'message' widget type expected by the dashboard.
    FIXED BUG 2: last_poll now tracks actual poll time, not start + elapsed.
    """
    global _latest_stats, _start_time, _poll_count, _poll_log, _last_poll_time

    # Format uptime
    elapsed = int(time.time() - _start_time)
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    # Format last poll time (FIXED: use actual last poll timestamp)
    if _last_poll_time > 0:
        last_poll_str = time.strftime("%H:%M:%S", time.localtime(_last_poll_time))
    else:
        last_poll_str = "Never"

    # Build log as a string (FIXED: was returning a raw list — incompatible with 'message' widget)
    log_str = "\n".join(_poll_log) if _poll_log else "No polls yet."

    if _latest_stats is None:
        return {
            "uptime": uptime_str,
            "last_poll": last_poll_str,
            "poll_count": str(_poll_count),
            "log": log_str,
        }
    s = _latest_stats
    cpu_first = s.get("cpu_percents", [0])[0] if s.get("cpu_percents") else 0
    return {
        "status": "Online",
        "cpu": cpu_first,
        "ram": s.get("ram_percent", 0),
        "disk": s.get("disk_percent", 0),
        "temp": f"{s.get('temp_f', 0):.1f}°F",
        "ram_details": f"{s.get('ram_used_mb', 0):.0f} / {s.get('ram_total_mb', 0):.0f} MB used",
        "uptime": uptime_str,
        "last_poll": last_poll_str,
        "poll_count": str(_poll_count),
        "log": log_str,
    }

# ---------- Configuration ----------
from engine.config_loader import get_config_path
CONFIG_PATH = get_config_path("server_stats_collector")
DB_PATH = Path(__file__).parent / "DATA" / "server_stats.db3"
MAX_PACKET_SIZE = 60000
ACK_TIMEOUT = 5.0
PACKET_RETRIES = 3

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

config = load_config()
# ── Validate config at startup ──
register_config_schema("server_stats_collector",
    required=["remote_host", "remote_port", "auth_token", "poll_interval_seconds", "command_port"],
    types={"remote_port": int, "poll_interval_seconds": int, "command_port": int})
config = assert_valid_config("server_stats_collector", config)

REMOTE_HOST = config["remote_host"]
REMOTE_PORT = config["remote_port"]
AUTH_TOKEN = config["auth_token"]
POLL_INTERVAL = config["poll_interval_seconds"]
CMD_PORT = config["command_port"]

# ---------- Database ----------
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            temp_f REAL,
            ram_total_mb REAL,
            ram_used_mb REAL,
            ram_free_mb REAL,
            ram_percent REAL,
            cpu_percents TEXT,
            disk_total_mb REAL,
            disk_free_mb REAL,
            disk_percent REAL,
            net_bytes_sent INTEGER,
            net_bytes_recv INTEGER,
            process_aliases TEXT
        )
    ''')
    conn.commit()
    return conn

db = init_db()

# ---------- Local hook calling ----------
def call_local_hook(stats_data):
    """Call the on_stats_received hook defined in hooks.py (if available)."""
    try:
        import hooks
        if hasattr(hooks, 'on_stats_received_callback'):
            hooks.on_stats_received_callback({'stats': stats_data, 'timestamp': time.time()})
    except Exception as e:
        print(f"Hook error: {e}")

# ── LAN discovery listener & beacon broadcaster ────────────────────
_FINDER = None
_BEACON = None
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERED_VIA_LAN = False  # True when a LAN beacon was received

def _start_discovery():
    """Start LAN discovery for server_stats services. Falls back to config."""
    global _FINDER
    try:
        from engine.lan_discovery import ServiceFinder

        def _on_service(service_type, host, port, extra, is_new):
            global REMOTE_HOST, REMOTE_PORT, _FINDER, _DISCOVERED_VIA_LAN
            with _DISCOVERY_LOCK:
                REMOTE_HOST = host
                REMOTE_PORT = port
                _DISCOVERED_VIA_LAN = True
                if is_new:
                    lan_mode = extra.get("lan_mode", True)
                    mode_label = "LAN" if lan_mode else "SECURE"
                    print(f"[discovery] Found {service_type} at {host}:{port} ({mode_label} mode, auto-configured)")

        _FINDER = ServiceFinder(
            "server_stats",
            callback=_on_service,
            fallback={"host": REMOTE_HOST, "port": REMOTE_PORT},
        )
        _FINDER.start()
        print(f"[discovery] LAN discovery started for 'server_stats' "
              f"(fallback: {REMOTE_HOST}:{REMOTE_PORT})")
    except ImportError:
        print("[discovery] engine.lan_discovery not available — using static config")

# ── E8: LAN beacon broadcaster ────────────────────────────────────
def _start_beacon():
    """Broadcast a LAN beacon so displays and finders can locate this collector."""
    global _BEACON
    try:
        from engine.lan_discovery import ServiceBeacon
        _BEACON = ServiceBeacon(
            "server_stats",
            port=REMOTE_PORT,
            extra={
                "version": "2.0",
                "lan_mode": True,
                "cmd_port": CMD_PORT,       # E8: Expose command port for admin commands
            },
        )
        _BEACON.start()
        print(f"[discovery] LAN beacon broadcasting for 'server_stats' "
              f"(port {REMOTE_PORT}, cmd_port {CMD_PORT})")
    except ImportError:
        print("[discovery] engine.lan_discovery not available — no LAN beacon")
    except Exception as e:
        print(f"[discovery] Beacon start failed: {e}")


# ---------- Network client ----------
def collect_stats():
    # Re-check discovery for best host/port
    global REMOTE_HOST, REMOTE_PORT, _FINDER, _DISCOVERED_VIA_LAN
    if _FINDER:
        best = _FINDER.get_best()
        if best.get("host"):
            with _DISCOVERY_LOCK:
                if best["host"] != REMOTE_HOST or best["port"] != REMOTE_PORT:
                    print(f"[discovery] Switching to {best['host']}:{best['port']}")
                    REMOTE_HOST = best["host"]
                    REMOTE_PORT = best["port"]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)

    try:
        # LAN mode: send plain "check" (no token). Config fallback: use token.
        if _DISCOVERED_VIA_LAN:
            request = b"check"
        else:
            request = f"{AUTH_TOKEN}|check".encode()
        sock.sendto(request, (REMOTE_HOST, REMOTE_PORT))

        data, addr = sock.recvfrom(1024)
        if data.decode() != "PREPARE":
            raise RuntimeError(f"Expected PREPARE, got: {data.decode()}")
        sock.sendto(b"READY", addr)

        payload = b""
        _packet_timeout = time.time() + 5.0  # Packet reassembly timeout (reduced from 15s)
        while True:
            if time.time() > _packet_timeout:
                raise TimeoutError("Packet reassembly timed out")
            packet, _ = sock.recvfrom(MAX_PACKET_SIZE + 100)
            if packet == b"DONE":
                break
            # Validate packet format before processing
            if b"|" not in packet:
                continue  # malformed packet, skip
            header_end = packet.index(b"|", packet.index(b"|") + 1)
            header = packet[:header_end].decode()
            chunk = packet[header_end+1:]
            parts = header.split("|")
            if len(parts) < 2:
                continue  # malformed header, skip
            packet_id = int(parts[0].split("/")[0])
            ack = f"ACK|{packet_id}".encode()
            sock.sendto(ack, addr)
            payload += chunk

        stats = json.loads(payload.decode())

        db.execute('''
            INSERT INTO stats (
                temp_f, ram_total_mb, ram_used_mb, ram_free_mb, ram_percent,
                cpu_percents, disk_total_mb, disk_free_mb, disk_percent,
                net_bytes_sent, net_bytes_recv, process_aliases
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            stats['temp_f'], stats['ram_total_mb'], stats['ram_used_mb'],
            stats['ram_free_mb'], stats['ram_percent'],
            json.dumps(stats['cpu_percents']),
            stats['disk_total_mb'], stats['disk_free_mb'], stats['disk_percent'],
            stats['net_bytes_sent'], stats['net_bytes_recv'],
            json.dumps(stats['process_aliases'])
        ))
        db.commit()

        call_local_hook(stats)
        return stats

    except socket.timeout:
        print(f"Collection timed out connecting to {REMOTE_HOST}:{REMOTE_PORT}")
        return None
    except json.JSONDecodeError as e:
        print(f"Collection error: invalid JSON response: {e}")
        return None
    except Exception as e:
        print(f"Collection error: {e}")
        return None
    finally:
        try:
            sock.close()  # FIXED BUG 3: socket closed in finally, covers all paths
        except Exception:
            pass

# ---------- Command server ----------
def handle_command(cmd):
    parts = cmd.strip().split(maxsplit=1)
    if not parts:
        return "Bad command"
    verb = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if verb == "latest":
        cur = db.execute("SELECT * FROM stats ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return "No data"
        keys = ["id", "timestamp", "temp_f", "ram_total_mb", "ram_used_mb",
                "ram_free_mb", "ram_percent", "cpu_percents", "disk_total_mb",
                "disk_free_mb", "disk_percent", "net_bytes_sent",
                "net_bytes_recv", "process_aliases"]
        return json.dumps(dict(zip(keys, row)), default=str)

    elif verb == "history":
        try:
            minutes = int(arg) if arg else 10
        except Exception:
            minutes = 10
        cur = db.execute(
            "SELECT * FROM stats WHERE timestamp >= datetime('now', ?) ORDER BY id",
            (f'-{minutes} minutes',)
        )
        rows = cur.fetchall()
        keys = ["id", "timestamp", "temp_f", "ram_total_mb", "ram_used_mb",
                "ram_free_mb", "ram_percent", "cpu_percents", "disk_total_mb",
                "disk_free_mb", "disk_percent", "net_bytes_sent",
                "net_bytes_recv", "process_aliases"]
        return json.dumps([dict(zip(keys, row)) for row in rows], default=str)

    elif verb == "config":
        with open(CONFIG_PATH) as f:
            return f.read()

    elif verb == "reload-config":
        global config, REMOTE_HOST, REMOTE_PORT, AUTH_TOKEN, POLL_INTERVAL, CMD_PORT
        config = load_config()
        REMOTE_HOST = config["remote_host"]
        REMOTE_PORT = config["remote_port"]
        AUTH_TOKEN = config["auth_token"]
        POLL_INTERVAL = config["poll_interval_seconds"]
        CMD_PORT = config["command_port"]
        return "Config reloaded"

    else:
        return f"Unknown command: {verb}"

def command_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", CMD_PORT))
    print(f"[stats-collector] Command listener on UDP {CMD_PORT}")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            cmd = data.decode().strip()
            resp = handle_command(cmd)
            sock.sendto(resp.encode(), addr)
        except Exception as e:
            print(f"[stats-collector] Command error: {e}")

# ── Shutdown handler ─────────────────────────────────────────────
_shutdown_flag = False

def _shutdown(signum=None, frame=None):
    global _shutdown_flag
    if _shutdown_flag:
        return
    _shutdown_flag = True
    print("Stats collector shutting down...", flush=True)
    # ── E8: Stop LAN beacon ──
    global _BEACON
    if _BEACON:
        try:
            _BEACON.stop()
        except Exception:
            pass
    # Close database connection
    try:
        db.close()
    except Exception:
        pass
    print("SHUTDOWN_COMPLETE", flush=True)

# ---------- Main loop ----------
def main():
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except OSError:
        pass  # SIGTERM not available on Windows
    signal.signal(signal.SIGINT, _shutdown)

    gui.on_data_request(_get_gui_data)

    # ── Start LAN discovery ──
    _start_discovery()
    _start_beacon()

    threading.Thread(target=command_listener, daemon=True).start()
    print("Server stats collector started.")
    try:
        while not _shutdown_flag:
            global _latest_stats, _poll_count, _poll_log, _last_poll_time
            poll_ts = time.time()
            # Retry logic for collect_stats (PHASE 2 BUG 5: network resilience)
            result = None
            for retry in range(3):
                result = collect_stats()
                if result is not None:
                    break
                if retry < 2:
                    print(f"[stats-collector] Poll retry {retry+1}/3...")
                    time.sleep(1.0)
            _poll_count += 1
            if result is not None:
                _latest_stats = result
                _last_poll_time = poll_ts  # FIXED BUG 2: record actual poll time
                _poll_log.append(f"[{time.strftime('%H:%M:%S')}] Poll #{_poll_count}: OK ({result.get('temp_f', '?'):.1f}°F, "
                                 f"{result.get('ram_percent', 0):.0f}% RAM, {result.get('disk_percent', 0):.0f}% disk)")
            else:
                _poll_log.append(f"[{time.strftime('%H:%M:%S')}] Poll #{_poll_count}: FAILED")
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        _shutdown()
    except Exception as e:
        print(f"Fatal error: {e}", flush=True)
    finally:
        _shutdown()

if __name__ == "__main__":
    main()