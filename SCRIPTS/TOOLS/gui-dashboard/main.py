"""
YuniScripts GUI Dashboard – DearPyGui-based tabbed dashboard.

Listens for Phooks events to dynamically register tabs and update widgets.

Data pull protocol:
  Every 3 seconds the dashboard emits a "gui_data_request" event to all
  listening scripts. Scripts that wish to push data back should listen for
  this event and respond with a "gui_widget_update" event. This allows the
  dashboard to function as a polling consumer rather than requiring scripts
  to proactively emit updates.

Connection health:
  - Tracks when each script last sent a widget update
  - Shows stale (no update > 15s), dead (> 60s), connected status
  - Shows error widgets (_gui_error) as red text on each script tab
  - Dashboard tab shows a live "Connected Scripts" table
"""

import sys
import json
import threading
import queue
import time
from collections import deque, OrderedDict
from pathlib import Path

# ── project root ──────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ── safe-print wrapper (Windows pipe compat) ─────────────────────────────────
def _safe_print(*args, **kwargs):
    """print() guarded with OSError for Windows broken-pipe compat."""
    try:
        print(*args, **kwargs)
    except OSError:
        pass


# ── Imports ───────────────────────────────────────────────────────────────────
from engine.phooks_client import PhooksClient
from engine.ports import UDP_ADMIN_PORT

try:
    import dearpygui.dearpygui as dpg
except ImportError as _import_err:
    message = str(_import_err)
    _safe_print(f"[gui-dashboard] dearpygui import failed: {message}")
    _safe_print("[gui-dashboard] attempting auto-install via pip...")
    import subprocess
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "dearpygui>=1.0.0"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            _safe_print(
                "[gui-dashboard] pip install FAILED:\n"
                + result.stderr.strip()
            )
            _safe_print(
                "  Fix: open a terminal and run:\n"
                "  SCRIPTS\\TOOLS\\gui-dashboard\\venv\\Scripts\\pip install dearpygui>=1.0.0"
            )
            sys.exit(1)
    except Exception as exc:
        _safe_print(f"[gui-dashboard] pip install crashed: {exc}")
        sys.exit(1)
    _safe_print("[gui-dashboard] dearpygui installed, re-importing...")
    try:
        import dearpygui.dearpygui as dpg
    except ImportError as exc2:
        _safe_print(f"[gui-dashboard] re-import failed: {exc2}")
        sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────
SCRIPT_ID = "gui-dashboard"
LISTEN_EVENTS = ["gui_tab_register", "gui_widget_update"]
EMIT_EVENTS: list[str] = ["gui_data_request"]

POLL_INTERVAL_MS = 100

DASHBOARD_TAB = "Dashboard"

# ── Global state ──────────────────────────────────────────────────────────────
# Maps (script_id, widget_id) -> (wtype, dpg_tag)
_widget_map: dict[tuple[str, str], tuple[str, str | int]] = {}

# Script connection health: {script_id: last_update_time}
_script_health: dict[str, float] = {}

# Script tab mapping: {script_id: tab_tag}
_script_tabs: dict[str, str] = {}

# Error widget tags per script tab: {script_id: dpg_tag}
_error_widgets: dict[str, str | int] = {}

# Thread-safe event queue for cross-thread Phooks delegation
_event_queue: queue.Queue = queue.Queue()

# Reference to the Phooks client (set at runtime)
_client: PhooksClient | None = None

# Last time a gui_data_request poll was sent (epoch seconds)
_last_data_poll: float = 0.0

# Dashboard tab widget tags (stored so the poll functions can find them)
_CONNECTED_SCRIPTS_TABLE = "_dash_cxn_table"
CPU_METER = "_dash_cpu"
RAM_METER = "_dash_ram"
DISK_METER = "_dash_disk"
ENGINE_LOG = "_dash_engine_log"

# Live log buffer (ring buffer of latest events)
_log_buffer: deque = deque(maxlen=1000)

# ── Background poll thread state ────────────────────────────────────
# Results are fetched from a background thread so the GUI thread never
# blocks on socket I/O.  Updated by _background_poll_worker().
_background_engine_status: list | None = None
_background_system_resources: dict | None = None  # {"cpu":float,"ram":float,"disk":float}
_background_poll_lock: threading.Lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════
# Tab registration handler
# ═══════════════════════════════════════════════════════════════════════

def _handle_tab_register(data: dict) -> None:
    """Dynamically create a new tab with widgets from *data*."""
    script_id = data.get("script_id", "unknown")
    tab_name = data.get("tab_name", script_id)
    widget_specs = data.get("widgets", data.get("widget_specs", []))

    tab_tag = f"_tab_{script_id}"

    # If tab already exists, just update its last-seen timestamp and skip
    if dpg.does_item_exist(tab_tag):
        _script_health[script_id] = time.time()
        _script_tabs[script_id] = tab_tag
        return  # Don't re-create the tab — just refresh the health timestamp

    _append_log(f"Tab registered: '{tab_name}' ({len(widget_specs)} widgets)")
    _script_health[script_id] = time.time()
    _script_tabs[script_id] = tab_tag

    try:
        with dpg.tab(label=tab_name, tag=tab_tag, parent="main_tab_bar"):
            # Add a hidden error widget at the top (shown if errors occur)
            err_tag = f"{tab_tag}_err"
            _error_widgets[script_id] = err_tag
            dpg.add_text(tag=err_tag, default_value="", color=[255, 0, 0], show=False)

            for spec in widget_specs:
                wtype = spec.get("type", "label")
                wid = spec.get("id", "")
                title = spec.get("title", "")
                default = spec.get("default", "")
                columns = spec.get("columns", [])

                # Section header
                if title:
                    dpg.add_text(f"§ {title}")
                    dpg.add_spacer(height=2)

                if wtype == "label":
                    tag = f"{tab_tag}_{wid}"
                    dpg.add_text(tag=tag, default_value=str(default))
                    _widget_map[(script_id, wid)] = ("label", tag)

                elif wtype == "meter":
                    tag = f"{tab_tag}_{wid}"
                    dpg.add_progress_bar(tag=tag, default_value=0.0, width=400)
                    _widget_map[(script_id, wid)] = ("meter", tag)

                elif wtype == "table":
                    tag = f"{tab_tag}_{wid}"
                    with dpg.table(
                        tag=tag,
                        header_row=True,
                        borders_innerH=True,
                        borders_outerH=True,
                        row_background=True,
                    ):
                        for col in columns:
                            dpg.add_table_column(label=col)
                    _widget_map[(script_id, wid)] = ("table", tag)

                elif wtype == "message":
                    tag = f"{tab_tag}_{wid}"
                    dpg.add_input_text(
                        tag=tag,
                        multiline=True,
                        readonly=True,
                        default_value=str(default),
                        width=800,
                        height=200,
                    )
                    _widget_map[(script_id, wid)] = ("message", tag)

                else:
                    _safe_print(
                        f"[gui-dashboard] Unknown widget type '{wtype}' "
                        f"for tab '{tab_name}'"
                    )

            # ── Footer: Last update timestamp ──
            dpg.add_separator()
            ts_tag = f"{tab_tag}_ts"
            _widget_map[(script_id, "_last_update")] = ("label", ts_tag)
            dpg.add_text(tag=ts_tag, default_value="⏳ Awaiting data...")

    except Exception as exc:
        _safe_print(f"[gui-dashboard] Failed to create tab '{tab_name}': {exc}")
        _append_log(f"FAILED to create tab '{tab_name}': {exc}")


# ═══════════════════════════════════════════════════════════════════════
# Widget update handler
# ═══════════════════════════════════════════════════════════════════════

def _handle_widget_update(data: dict) -> None:
    """Update an existing widget with a new value."""
    script_id = data.get("script_id", "")
    widget_id = data.get("widget_id", "")
    tab_name = data.get("tab_name", "")

    # Track script health — any widget update means the script is alive
    _script_health[script_id] = time.time()

    # Handle _gui_error widget specially
    if widget_id == "_gui_error":
        error_value = str(data.get("value", ""))
        err_tag = _error_widgets.get(script_id)
        if err_tag and dpg.does_item_exist(err_tag):
            dpg.set_value(err_tag, f"⚠️ {error_value}")
            dpg.configure_item(err_tag, show=bool(error_value))
        else:
            _safe_print(f"[gui-dashboard] No error widget for script '{script_id}': {error_value}")
        return

    # Handle _last_update widget (built-in health timestamp)
    if widget_id == "_last_update":
        tab_tag = _script_tabs.get(script_id)
        if tab_tag:
            ts_tag = f"{tab_tag}_ts"
            if dpg.does_item_exist(ts_tag):
                dpg.set_value(ts_tag, str(data.get("value", "")))
        return

    # Normal widget lookup
    key = (script_id, widget_id)
    lookup = _widget_map.get(key)
    if lookup is None:
        _safe_print(
            f"[gui-dashboard] Unknown widget '{widget_id}' for script '{script_id}'"
            f" (known: {[k for k in _widget_map.keys() if k[0]==script_id][:5]})"
        )
        _append_log(f"Unknown widget '{widget_id}' for '{script_id}'")
        return

    wtype, tag = lookup
    value = data.get("value", "")

    try:
        if wtype == "label":
            dpg.set_value(tag, str(value))
        elif wtype == "meter":
            dpg.set_value(tag, float(value) / 100.0)
        elif wtype == "table":
            # HARDENED: safely clear existing table rows (BUG 3)
            try:
                child_dict = dpg.get_item_children(tag)
                if child_dict and isinstance(child_dict, dict):
                    existing_rows = list(child_dict.get(1, []))
                    for c in existing_rows:
                        try:
                            dpg.delete_item(c)
                        except Exception:
                            pass
            except Exception:
                pass
            for row in value:
                try:
                    with dpg.table_row(parent=tag):
                        for cell in row:
                            dpg.add_text(str(cell))
                except Exception:
                    pass
        elif wtype == "message":
            dpg.set_value(tag, str(value))
        else:
            _safe_print(f"[gui-dashboard] Unknown update type '{wtype}'")
    except Exception as exc:
        _safe_print(f"[gui-dashboard] Error updating widget '{tag}': {exc}")


# ═══════════════════════════════════════════════════════════════════════
# Event polling (runs on GUI thread via render loop)
# ═══════════════════════════════════════════════════════════════════════

def _poll_events() -> None:
    """Drain *event_queue* and dispatch handlers on the GUI thread."""
    while not _event_queue.empty():
        try:
            event = _event_queue.get_nowait()
        except queue.Empty:
            break

        event_name = event.get("event", "")
        data = event.get("data", {})

        if event_name == "gui_tab_register":
            _handle_tab_register(data)
        elif event_name == "gui_widget_update":
            _handle_widget_update(data)
        else:
            _safe_print(f"[gui-dashboard] Ignored unknown event '{event_name}'")


# ═══════════════════════════════════════════════════════════════════════
# Connection health & stale detection
# ═══════════════════════════════════════════════════════════════════════

def _update_health_status() -> None:
    """Update the Dashboard tab's connected-scripts table and detect stale tabs."""
    now = time.time()

    # Build connected-scripts table rows
    rows = []
    for script_id, last_seen in sorted(_script_health.items()):
        age = now - last_seen
        if age < 15:
            status = "✅ Connected"
        elif age < 60:
            status = f"⚠️ Stale ({age:.0f}s)"
        else:
            status = f"💀 Dead ({age:.0f}s)"
        rows.append([script_id, status])

    # Update the Dashboard tab table (HARDENED)
    if dpg.does_item_exist(_CONNECTED_SCRIPTS_TABLE):
        try:
            child_dict = dpg.get_item_children(_CONNECTED_SCRIPTS_TABLE)
            if child_dict and isinstance(child_dict, dict):
                existing_rows = list(child_dict.get(1, []))
                for c in existing_rows:
                    try:
                        dpg.delete_item(c)
                    except Exception:
                        pass
            for row in rows:
                try:
                    with dpg.table_row(parent=_CONNECTED_SCRIPTS_TABLE):
                        for cell in row:
                            dpg.add_text(str(cell))
                except Exception:
                    pass
        except Exception as exc:
            _safe_print(f"[gui-dashboard] Health table update error: {exc}")

    # Update per-tab health indicators
    for script_id, tab_tag in _script_tabs.items():
        last_seen = _script_health.get(script_id, 0)
        age = now - last_seen
        ts_tag = f"{tab_tag}_ts"
        if dpg.does_item_exist(ts_tag):
            if age < 15:
                dpg.set_value(ts_tag, f"🟢 Live — last update {age:.0f}s ago")
            elif age < 60:
                dpg.set_value(ts_tag, f"🟡 Stale — last update {age:.0f}s ago")
            else:
                dpg.set_value(ts_tag, f"🔴 No data — last seen {age:.0f}s ago")


# ═══════════════════════════════════════════════════════════════════════
# Log
# ═══════════════════════════════════════════════════════════════════════

def _append_log(msg: str) -> None:
    """Append a timestamped entry to the dashboard's internal log buffer."""
    ts = time.strftime("%H:%M:%S")
    _log_buffer.append(f"[{ts}] {msg}")


def _update_engine_log() -> None:
    """Flush the log buffer into the Engine Log DPG widget."""
    try:
        lines = "\n".join(_log_buffer)
        dpg.set_value(ENGINE_LOG, lines)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# Dashboard data poll (engine admin port)
# ═══════════════════════════════════════════════════════════════════════

def _background_poll_engine_status() -> list | None:
    """Query the engine's UDP admin port — safe to call from background thread.
    
    Returns a list of [script_id, status, pid, uptime] rows, or None on failure.
    Does NOT touch DPG or any GUI state — the caller applies results.
    Fixes BUG 2 (socket double-close) and BUG 6 (blocking on GUI thread).
    """
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    try:
        sock.sendto(b"status", ("127.0.0.1", UDP_ADMIN_PORT))
        resp, _ = sock.recvfrom(4096)
        text = resp.decode("utf-8")
        rows = []
        lines = text.strip().split("\n")
        for line in lines[1:]:
            line = line.strip()
            if not line or ": " not in line:
                continue
            sid, rest = line.split(": ", 1)
            sid = sid.strip()
            status = rest.strip()
            pid = "-"
            if "(pid=" in status:
                status = status.split("(")[0].strip()
                pid_part = rest.split("pid=")[1].rstrip(")")
                pid = pid_part.strip() if pid_part else "-"
            rows.append([sid, status[:8], pid, "-"])
        return rows
    except socket.timeout:
        # Don't print every cycle — just return None
        return None
    except Exception as exc:
        _safe_print(f"[gui-dashboard] Engine query error: {exc}")
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ── System resource polling (cross-platform) ──────────────────────────────────

def _background_poll_system_resources() -> dict:
    """Poll CPU, RAM, and disk usage.

    Cross-platform: tries /proc on Linux, falls back to os/try-based
    estimates on Windows/other platforms.  Returns a dict with keys
    'cpu', 'ram', 'disk' (all floats 0-100).

    Safe to call from background thread — does NOT touch DPG.
    Fixes BUG 4 (Linux-only /proc files crash on Windows).
    """
    cpu_pct = 0.0
    ram_pct = 0.0
    disk_pct = 0.0

    # ── CPU ──────────────────────────────────────────────────────
    if sys.platform == "linux" or sys.platform == "linux2":
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            if line.startswith("cpu "):
                fields = line.split()
                idle = int(fields[4])
                total = sum(int(v) for v in fields[1:])
                # Function attribute to store previous tick values
                if not hasattr(_background_poll_system_resources, "_prev"):
                    _background_poll_system_resources._prev = (total, idle)
                    cpu_pct = 0.0  # First call — no delta
                else:
                    prev_total, prev_idle = _background_poll_system_resources._prev
                    delta_total = total - prev_total
                    delta_idle = idle - prev_idle
                    _background_poll_system_resources._prev = (total, idle)
                    if delta_total > 0:
                        cpu_pct = 100.0 * (1.0 - delta_idle / delta_total)
        except (FileNotFoundError, IOError, OSError, IndexError, ValueError):
            pass
    else:
        # Windows/other: use psutil if available, otherwise report 0
        try:
            import psutil
            cpu_pct = psutil.cpu_percent(interval=0.1)
        except ImportError:
            pass  # No psutil — CPU stays 0

    # ── RAM ──────────────────────────────────────────────────────
    if sys.platform == "linux" or sys.platform == "linux2":
        try:
            mem_total = 0
            mem_avail = 0
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        mem_total = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        mem_avail = int(line.split()[1])
            if mem_total > 0:
                ram_pct = 100.0 * (1.0 - mem_avail / mem_total)
        except (FileNotFoundError, IOError, OSError, IndexError, ValueError):
            pass
    else:
        try:
            import psutil
            ram_pct = psutil.virtual_memory().percent
        except ImportError:
            pass

    # ── Disk ─────────────────────────────────────────────────────
    try:
        import shutil
        # Use platform-appropriate root path
        disk_root = "/" if sys.platform != "win32" else "C:\\"
        du = shutil.disk_usage(disk_root)
        disk_pct = 100.0 * (1.0 - du.free / du.total)
    except Exception:
        pass

    return {"cpu": cpu_pct, "ram": ram_pct, "disk": disk_pct}


def _apply_system_resources(res: dict) -> None:
    """Apply background-polled resource data to Dashboard tab DPG widgets.
    
    Must be called from the GUI thread (inside render loop).
    """
    try:
        cpu_pct = res.get("cpu", 0.0)
        ram_pct = res.get("ram", 0.0)
        disk_pct = res.get("disk", 0.0)
        for meter_id, pct in [(CPU_METER, cpu_pct), (RAM_METER, ram_pct), (DISK_METER, disk_pct)]:
            if dpg.does_item_exist(meter_id):
                dpg.set_value(meter_id, pct / 100.0)
            pct_tag = f"{meter_id}_pct"
            if dpg.does_item_exist(pct_tag):
                dpg.set_value(pct_tag, f"{pct:.1f}%")
    except Exception as exc:
        _safe_print(f"[gui-dashboard] Resource meter apply error: {exc}")


def _update_dash_engine_status(rows: list) -> None:
    """Replace the engine status table rows with *rows* data.
    
    HARDENED: all DPG operations wrapped in try/except (BUG 3).
    """
    table_tag = "_dash_engine_status"
    if not dpg.does_item_exist(table_tag):
        return
    try:
        # Safely clear existing rows
        child_dict = dpg.get_item_children(table_tag)
        if child_dict and isinstance(child_dict, dict):
            existing_rows = list(child_dict.get(1, []))
            for child in existing_rows:
                try:
                    dpg.delete_item(child)
                except Exception:
                    pass
        # Add new rows
        for row_data in rows:
            try:
                with dpg.table_row(parent=table_tag):
                    for cell in row_data:
                        dpg.add_text(str(cell))
            except Exception:
                pass
    except Exception as exc:
        _safe_print(f"[gui-dashboard] Table update error: {exc}")


# ═══════════════════════════════════════════════════════════════════════
# Background Phooks receiver
# ═══════════════════════════════════════════════════════════════════════

def _phooks_listener(client: PhooksClient) -> None:
    """Run in a daemon thread: receive Phooks events and enqueue them.
    
    HARDENED: If the hub is unreachable, attempt to re-register with
    exponential backoff so we recover automatically when the hub comes back.
    (PHASE 5: Phooks reconnection resilience)
    """
    _reconnect_attempt = 0
    while True:
        try:
            event = client.receive(timeout=1.0)
            if event is not None:
                _event_queue.put(event)
            _reconnect_attempt = 0  # Reset on successful receive
        except (ConnectionResetError, ConnectionRefusedError, OSError) as conn_err:
            _reconnect_attempt += 1
            delay = min(0.5 * (2 ** (_reconnect_attempt - 1)), 30.0)
            _safe_print(f"[gui-dashboard] Phooks connection lost (attempt {_reconnect_attempt}): {conn_err}")
            _append_log(f"Phooks disconnect, re-registering in {delay:.1f}s...")
            time.sleep(delay)
            # Re-register with hub
            try:
                client.register()
                _safe_print(f"[gui-dashboard] Phooks re-registered after disconnect")
                _append_log("Phooks re-registered")
                _reconnect_attempt = 0
            except Exception as reg_err:
                _safe_print(f"[gui-dashboard] Phooks re-register failed: {reg_err}")
        except Exception as exc:
            _safe_print(f"[gui-dashboard] Phooks listener error: {exc}")
            time.sleep(1)


# ═══════════════════════════════════════════════════════════════════════
# Build built-in Dashboard tab
# ═══════════════════════════════════════════════════════════════════════

def _build_dashboard_tab() -> None:
    """Create the default Dashboard tab with engine status, resources, and log."""
    ENGINE_STATUS_COLUMNS = ["Script ID", "Status", "PID", "Uptime"]
    ENGINE_STATUS_TABLE = "_dash_engine_status"
    CONNECTED_COLUMNS = ["Script ID", "Health"]

    with dpg.tab(label=DASHBOARD_TAB, tag="_dash_tab", parent="main_tab_bar"):
        # ─ Engine Status (from admin port) ─
        dpg.add_text("Engine Status")
        with dpg.table(
            tag=ENGINE_STATUS_TABLE,
            header_row=True,
            borders_innerH=True,
            borders_outerH=True,
            row_background=True,
        ):
            for col in ENGINE_STATUS_COLUMNS:
                dpg.add_table_column(label=col)
        _widget_map[("_dash", "engine_status")] = ("table", ENGINE_STATUS_TABLE)

        dpg.add_separator()

        # ─ System Resources ─
        dpg.add_text("System Resources")
        for meter_id, label in [
            (CPU_METER, "CPU"),
            (RAM_METER, "RAM"),
            (DISK_METER, "Disk"),
        ]:
            pct_tag = f"{meter_id}_pct"
            dpg.add_text(label)
            with dpg.group(horizontal=True):
                dpg.add_progress_bar(tag=meter_id, default_value=0.0, width=350)
                dpg.add_text(tag=pct_tag, default_value="—")
            dpg.add_spacer(height=6)
            _widget_map[("_dash", meter_id.replace("_dash_", ""))] = ("meter", meter_id)

        dpg.add_separator()

        # ─ Connected Scripts (Phooks health) ─
        dpg.add_text("Connected Scripts (Phooks)")
        with dpg.table(
            tag=_CONNECTED_SCRIPTS_TABLE,
            header_row=True,
            borders_innerH=True,
            borders_outerH=True,
            row_background=True,
        ):
            for col in CONNECTED_COLUMNS:
                dpg.add_table_column(label=col)
        _widget_map[("_dash", "connected_scripts")] = ("table", _CONNECTED_SCRIPTS_TABLE)

        dpg.add_separator()

        # ─ Engine Log ─
        dpg.add_text("Engine Log")
        dpg.add_input_text(
            tag=ENGINE_LOG,
            multiline=True,
            readonly=True,
            default_value="",
            width=800,
            height=200,
        )
        _widget_map[("_dash", "engine_log")] = ("message", ENGINE_LOG)


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    _append_log("Dashboard starting up...")

    # 1. Create Phooks client and register
    global _client
    _client = PhooksClient(
        script_id=SCRIPT_ID,
        listen_events=LISTEN_EVENTS,
        emit_events=EMIT_EVENTS,
    )
    _client.register()
    _safe_print(f"[gui-dashboard] Phooks client '{SCRIPT_ID}' registered.")
    _append_log("Phooks client registered.")

    # 2. Create DearPyGui context and viewport
    dpg.create_context()
    dpg.create_viewport(title="YuniScripts Dashboard", width=950, height=760)
    dpg.setup_dearpygui()

    # 3. Build main window with tab bar
    with dpg.window(
        label="YuniScripts Dashboard",
        tag="main_window",
        no_close=True,
    ):
        with dpg.tab_bar(tag="main_tab_bar"):
            _build_dashboard_tab()

    # 4. Start background Phooks listener thread
    listener_thread = threading.Thread(
        target=_phooks_listener,
        args=(_client,),
        daemon=True,
    )
    listener_thread.start()

    # 5. Show viewport and run render loop (polling events each frame)
    dpg.show_viewport()
    _append_log("Dashboard running — waiting for scripts to connect...")

    # Start background poll worker thread
    _poll_thread_running = True

    def _background_poll_worker():
        """Run engine status + system resource polling in a background thread
        so the GUI thread is never blocked by socket I/O."""
        nonlocal _poll_thread_running
        while _poll_thread_running:
            poll_start = time.time()
            # Engine status poll (UDP — may block up to 2s on timeout)
            eng_rows = _background_poll_engine_status()
            # System resources poll (file reads — fast, but may fail on Windows)
            sys_res = _background_poll_system_resources()
            with _background_poll_lock:
                global _background_engine_status, _background_system_resources
                _background_engine_status = eng_rows
                _background_system_resources = sys_res
            # Sleep for the remainder of the 3-second interval
            elapsed = time.time() - poll_start
            sleep_for = max(0.1, 3.0 - elapsed)
            time.sleep(sleep_for)

    poll_thread = threading.Thread(target=_background_poll_worker, daemon=True)
    poll_thread.start()

    try:
        while dpg.is_dearpygui_running():
            now = time.time()
            if now - _last_data_poll >= 3.0:
                _last_data_poll = now  # ← CRITICAL: was never updated, causing per-frame polling
                # Apply background poll results on GUI thread
                with _background_poll_lock:
                    eng_rows = _background_engine_status
                    sys_res = _background_system_resources
                    _background_engine_status = None
                    _background_system_resources = None
                if eng_rows is not None:
                    _update_dash_engine_status(eng_rows)
                    _append_log(f"Engine poll: {len(eng_rows)} script(s) running")
                if sys_res is not None:
                    _apply_system_resources(sys_res)
                _update_engine_log()
                _update_health_status()
                _client.emit("gui_data_request", {"target": ""})
            _poll_events()
            dpg.render_dearpygui_frame()
    except KeyboardInterrupt:
        _safe_print("[gui-dashboard] Shutdown requested.")

    # Cleanup — always runs, even on Ctrl+C
    _poll_thread_running = False
    if poll_thread.is_alive():
        poll_thread.join(timeout=3.0)
    dpg.destroy_context()
    _client.unregister()
    _safe_print("[gui-dashboard] Shutdown complete.")
    print("SHUTDOWN_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
