#!/usr/bin/env python3
"""
Live server stats display with simple bar charts.
Queries the server-stats-collector on UDP 25570 every 3 seconds.

LAN Discovery: Automatically finds the collector on the local network.
If no beacon is received within 30 seconds, falls back to 127.0.0.1:25570.
"""
import sys
import time
import socket
import json
import os
import signal
import threading
from collections import deque

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from engine.gui_api_client import GuiApiClient

gui = GuiApiClient('TOOLS/server-stats-display', 'Stats Display')
gui.register_tab([
    {'type': 'label', 'id': 'status', 'title': 'Status', 'default': 'Starting...'},
    {'type': 'meter', 'id': 'cpu', 'title': 'CPU Usage', 'default': 0},
    {'type': 'meter', 'id': 'ram', 'title': 'RAM Usage', 'default': 0},
    {'type': 'meter', 'id': 'disk', 'title': 'Disk Usage', 'default': 0},
    {'type': 'label', 'id': 'temp', 'title': 'Temperature', 'default': '--'},
    {'type': 'label', 'id': 'ram_details', 'title': 'RAM Details', 'default': '--'},
    {'type': 'label', 'id': 'uptime', 'title': 'Uptime', 'default': '0h'},
    {'type': 'label', 'id': 'last_poll', 'title': 'Last Poll', 'default': '--'},
    {'type': 'message', 'id': 'log', 'title': 'Activity Log', 'default': ''},
])

# ── Globals for live state ───────────────────────────────────────
_latest_stats = None
_latest_stats_lock = threading.Lock()
_start_time = time.time()
_log_buffer = deque(maxlen=50)

# ── LAN Discovery ──────────────────────────────────────────────────
_FINDER = None
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERED_HOST = "127.0.0.1"   # Default fallback
_DISCOVERED_PORT = 25570
_DISCOVERED_VIA_LAN = False

def _start_discovery():
    """Start LAN discovery for server_stats services. Falls back to 127.0.0.1:25570."""
    global _FINDER
    try:
        from engine.lan_discovery import ServiceFinder

        def _on_service(service_type, host, port, extra, is_new):
            global _DISCOVERED_HOST, _DISCOVERED_PORT, _DISCOVERED_VIA_LAN
            with _DISCOVERY_LOCK:
                _DISCOVERED_HOST = host
                _DISCOVERED_PORT = port
                _DISCOVERED_VIA_LAN = True
            msg = f"[discovery] Found server_stats at {host}:{port}"
            _log_buffer.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
            print(msg, flush=True)

        _FINDER = ServiceFinder(
            "server_stats",
            callback=_on_service,
            fallback={"host": "127.0.0.1", "port": 25570},
        )
        _FINDER.start()
        print(f"[discovery] LAN discovery started for 'server_stats' "
              f"(fallback: 127.0.0.1:25570)", flush=True)
    except ImportError:
        print("[discovery] engine.lan_discovery not available — using 127.0.0.1:25570", flush=True)
    except Exception as e:
        print(f"[discovery] Failed to start: {e} — using fallback", flush=True)

def _resolve_host_port():
    """Return (host, port) using LAN discovery if available, else fallback."""
    global _FINDER, _DISCOVERED_HOST, _DISCOVERED_PORT, _DISCOVERED_VIA_LAN
    if _FINDER:
        try:
            best = _FINDER.get_best()
            if best and best.get("host"):
                with _DISCOVERY_LOCK:
                    if (best["host"] != _DISCOVERED_HOST or 
                        best.get("port", _DISCOVERED_PORT) != _DISCOVERED_PORT):
                        old_host = _DISCOVERED_HOST
                        old_port = _DISCOVERED_PORT
                        _DISCOVERED_HOST = best["host"]
                        _DISCOVERED_PORT = best.get("port", _DISCOVERED_PORT)
                        _DISCOVERED_VIA_LAN = True
                        msg = (f"[discovery] Collector switched from {old_host}:{old_port} "
                               f"to {_DISCOVERED_HOST}:{_DISCOVERED_PORT}")
                        _log_buffer.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
                        print(msg, flush=True)
        except Exception:
            pass
    with _DISCOVERY_LOCK:
        return _DISCOVERED_HOST, _DISCOVERED_PORT

def _get_gui_data():
    with _latest_stats_lock:
        stats = _latest_stats
    if stats is None:
        host, port = _resolve_host_port()
        conn_status = f"LAN:{host}:{port}" if _DISCOVERED_VIA_LAN else f"Local:{host}:{port}"
        return {
            'status': f'No data yet ({conn_status})',
            'cpu': 0,
            'ram': 0,
            'disk': 0,
            'temp': '--',
            'ram_details': '--',
            'uptime': _format_uptime(),
            'last_poll': '--',
            'log': '\n'.join(_log_buffer),
        }

    cpu_percents = stats.get('cpu_percents', [])
    cpu_val = cpu_percents[0] if isinstance(cpu_percents, list) and cpu_percents else 0

    ram_percent = stats.get('ram_percent', 0)
    ram_total = stats.get('ram_total_mb', 0)
    ram_free = stats.get('ram_free_mb', 0)
    ram_used = ram_total - ram_free
    ram_details = f"{ram_used:.0f} MB / {ram_total:.0f} MB"

    disk_percent = stats.get('disk_percent', 0)
    disk_free = stats.get('disk_free_mb', 0)
    disk_total = stats.get('disk_total_mb', 0)

    temp_f = stats.get('temp_f', 0)
    temp_str = f"{temp_f:.0f}°F" if temp_f else '--'

    net_sent = stats.get('net_bytes_sent', 0)
    net_recv = stats.get('net_bytes_recv', 0)

    status_parts = []
    if cpu_val > 0:
        status_parts.append(f"CPU {cpu_val:.0f}%")
    if ram_percent > 0:
        status_parts.append(f"RAM {ram_percent:.0f}%")
    if disk_percent > 0:
        status_parts.append(f"Disk {disk_percent:.0f}%")
    status = ' | '.join(status_parts) if status_parts else 'Stats received'

    if net_sent or net_recv:
        status += f" | Net ↑{format_bytes(net_sent)} ↓{format_bytes(net_recv)}"

    return {
        'status': status,
        'cpu': cpu_val,
        'ram': ram_percent,
        'disk': disk_percent,
        'temp': temp_str,
        'ram_details': ram_details,
        'uptime': _format_uptime(),
        'last_poll': time.strftime('%H:%M:%S'),
        'log': '\n'.join(_log_buffer),
    }

def _format_uptime():
    elapsed = time.time() - _start_time
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"

REFRESH_INTERVAL = 3
BAR_WIDTH = 20
TEMP_MAX_C = 100.0

def clear_screen():
    """Clear terminal using ANSI escape sequences (cross-platform)."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

def get_stats():
    """Fetch latest stats from collector via LAN discovery, return dict or None."""
    host, port = _resolve_host_port()

    for attempt in range(3):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            sock.sendto(b"latest", (host, port))
            data, _ = sock.recvfrom(8192)

            stats = json.loads(data.decode())

            for field in ("cpu_percents", "process_aliases"):
                if field in stats and isinstance(stats[field], str):
                    try:
                        stats[field] = json.loads(stats[field])
                    except (json.JSONDecodeError, TypeError):
                        stats[field] = []

            return stats
        except socket.timeout:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
            continue
        except (json.JSONDecodeError, ConnectionResetError):
            if attempt < 2:
                time.sleep(0.5)
            continue
        except Exception:
            if attempt < 2:
                time.sleep(1.0)
            continue
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
    return None

def bar(percent: float, width: int = BAR_WIDTH) -> str:
    filled = int(round(percent / 100.0 * width))
    filled = max(0, min(width, filled))
    return "[" + "|" * filled + " " * (width - filled) + "]"

def format_bytes(b: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(b) < 1024.0:
            return f"{b:.1f} {unit}"
        b /= 1024.0
    return f"{b:.1f} PB"

def display(stats: dict) -> str:
    lines = []
    ts = stats.get("timestamp", "")
    lines.append(f"=== Server Stats ({ts}) ===")

    cpu_percents = stats.get("cpu_percents", [])
    if isinstance(cpu_percents, list) and cpu_percents:
        cpu_avg = sum(cpu_percents) / len(cpu_percents)
        lines.append(f"CPU  {bar(cpu_avg)} {cpu_avg:5.1f}%")
    else:
        lines.append("CPU  (no data)")

    ram_total = stats.get("ram_total_mb", 0)
    ram_used  = stats.get("ram_used_mb", 0)
    ram_percent = stats.get("ram_percent", 0)
    lines.append(f"RAM  {bar(ram_percent)} {ram_percent:5.1f}% ({ram_used:.0f} MB / {ram_total:.0f} MB)")

    disk_percent = stats.get("disk_percent", 0)
    disk_free = stats.get("disk_free_mb", 0)
    disk_total = stats.get("disk_total_mb", 0)
    lines.append(f"Disk {bar(disk_percent)} {disk_percent:5.1f}% ({disk_free:.0f} MB free / {disk_total:.0f} MB)")

    temp_f = stats.get("temp_f", 0)
    temp_c = (temp_f - 32) / 1.8
    temp_pct = min(100.0, (temp_c / TEMP_MAX_C) * 100.0)
    lines.append(f"Temp {bar(temp_pct)} {temp_f:.0f}°F ({temp_c:.1f}°C)")

    net_sent = stats.get("net_bytes_sent", 0)
    net_recv = stats.get("net_bytes_recv", 0)
    lines.append(f"Net  sent: {format_bytes(net_sent)}, recv: {format_bytes(net_recv)}")

    procs = stats.get("process_aliases", [])
    if isinstance(procs, list) and procs:
        lines.append(f"Procs: {', '.join(procs)}")
    else:
        lines.append("Procs: (none monitored)")

    return "\n".join(lines)

# ── Shutdown handler ─────────────────────────────────────────────
_shutdown_flag = False

def _shutdown(signum=None, frame=None):
    global _shutdown_flag
    if _shutdown_flag:
        return
    _shutdown_flag = True
    # Stop LAN discovery
    if _FINDER:
        try:
            _FINDER.stop()
        except Exception:
            pass
    print("SHUTDOWN_COMPLETE", flush=True)

def main():
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, OSError):
        pass  # SIGTERM not available on Windows
    signal.signal(signal.SIGINT, _shutdown)

    gui.on_data_request(_get_gui_data)

    # ── Start LAN discovery ──
    _start_discovery()

    print("Live Server Stats (refresh every 3s)")
    print("LAN discovery active — auto-connects to collector on the network.\n", flush=True)

    try:
        while not _shutdown_flag:
            global _latest_stats
            stats = get_stats()
            if stats is not None:
                with _latest_stats_lock:
                    _latest_stats = stats
                ts = stats.get("timestamp", time.strftime('%H:%M:%S'))
                _log_buffer.append(f"[{ts}] Stats updated")

            clear_screen()
            if stats is None:
                host, port = _resolve_host_port()
                via = "LAN" if _DISCOVERED_VIA_LAN else "fallback"
                print(f"[Error] Could not connect to collector at {host}:{port} ({via}).")
                _log_buffer.append(f"[{time.strftime('%H:%M:%S')}] Error: connection to {host}:{port} failed")
            elif "error" in stats:
                print(f"[Error] {stats['error']}")
                _log_buffer.append(f"[{time.strftime('%H:%M:%S')}] Error: {stats['error']}")
            else:
                print(display(stats))
            time.sleep(REFRESH_INTERVAL)
    except KeyboardInterrupt:
        _shutdown()
    except Exception as e:
        print(f"Fatal error: {e}", flush=True)
        _log_buffer.append(f"[{time.strftime('%H:%M:%S')}] Fatal error: {e}")
    finally:
        _shutdown()
        gui.close()
        print("SHUTDOWN_COMPLETE", flush=True)
        sys.exit(0)

if __name__ == "__main__":
    main()
