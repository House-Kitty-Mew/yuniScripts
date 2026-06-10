# GUI Dashboard API

## Overview

The GUI Dashboard is a DearPyGui-based tabbed window that other YuniScripts can
dynamically extend with their own tab pages and live-updating widgets.  It
functions as a **passive display surface**: scripts push data to the dashboard
via Phooks events, and the dashboard renders it inside DearPyGui GUI controls.

| Component              | Detail                                            |
|------------------------|---------------------------------------------------|
| **UI framework**       | DearPyGui with `tab_bar` + `tab` system          |
| **Event bus**          | Phooks hub on UDP `127.0.0.1:25573`               |
| **Listen events**      | `gui_tab_register`, `gui_widget_update`            |
| **Emit events**        | `gui_data_request`                                   |
| **Threading model**    | Main thread runs DearPyGui render loop             |
|                        | (`while dpg.is_dearpygui_running():                 |
|                        |  dpg.render_dearpygui_frame()`); background thread  |
|                        | runs Phooks `receive()` loop; the main thread       |
|                        | picks up pending updates from a thread-safe queue   |
|                        | on each frame.                                      |

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   GUI Dashboard (main.py)                │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  DearPyGui tab_bar + tab system                  │    │
│  │  ┌──────────┐ ┌────────────┐ ┌──────────────┐    │    │
│  │  │Dashboard │ │ Script A   │ │ Script B     │ ...│    │
│  │  │(built-in)│ │ tab        │ │ tab          │    │    │
│  │  └──────────┘ └────────────┘ └──────────────┘    │    │
│  └──────────────────────────────────────────────────┘    │
│                          ▲                                │
│    render loop dequeues │  (cross‑thread widget update)   │
│    from thread-safe Q   │                                │
│                          │                                │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Background Phooks Listener Thread               │    │
│  │  - receives gui_tab_register                     │    │
│  │  - receives gui_widget_update                    │    │
│  │  - enqueues update to thread-safe queue          │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
            ▲                          ▲
            │                          │
    ┌───────┴──────┐        ┌──────────┴──────────┐
    │ Script "foo" │  ...   │ Script "minecraft…" │
    │ emits events │        │ emits events        │
    └──────────────┘        └─────────────────────┘

                    Phooks Hub (UDP 25573)
```

### 1.1 Thread Safety

DearPyGui is **not** thread-safe.  All calls that create, modify, or destroy
GUI widgets **must** happen on the main (DearPyGui) thread.  The dashboard
uses a **thread-safe queue** (`queue.Queue`) to pass update requests from the
background Phooks listener thread to the main render loop.  On each frame,
the render loop checks the queue and processes all pending updates.

> **Important**: never call `dpg.set_value()`, `dpg.add_window()`, or any
> other DearPyGui method directly from the Phooks listener thread.  Always
> enqueue the work and let the main render loop apply it.

### 1.2 Listener Lifecycle

1. `main.py` starts the DearPyGui render loop in the main thread via
   `while dpg.is_dearpygui_running(): dpg.render_dearpygui_frame()`.
2. Before the render loop, it spawns a **daemon** background thread that
   runs the Phooks `receive()` loop.
3. When a Phooks event arrives, the listener thread enqueues an update
   request (with all necessary data) into a **thread-safe queue**.
4. The main render loop dequeues all pending updates on each frame and
   applies them.
5. On shutdown, the daemon thread is automatically killed; `dpg.stop_dearpygui()`
   is bound to the window close event.

---

## 2. Phooks Events

### 2.1 `gui_tab_register` (listen)

Sent by a script to **register a new tab page** in the dashboard.  The
dashboard creates a new tab inside the `TabbedFrame` and populates it with
the declared widgets.

**Payload**

```json
{
  "event": "gui_tab_register",
  "data": {
    "script_id": "minecraft_manager",
    "tab_name": "Minecraft",
    "widgets": [
      {
        "type": "label",
        "id": "status",
        "title": "Server Status",
        "default": "Offline"
      },
      {
        "type": "meter",
        "id": "cpu",
        "title": "CPU Load",
        "default": 0
      },
      {
        "type": "table",
        "id": "players",
        "title": "Online Players",
        "columns": ["Name", "UUID", "Ping"],
        "default": []
      },
      {
        "type": "message",
        "id": "log",
        "title": "Recent Log",
        "default": "Waiting for data…"
      }
    ]
  },
  "sender": "minecraft_manager"
}
```

**Fields**

| Field            | Type   | Description                                              |
|------------------|--------|----------------------------------------------------------|
| `script_id`      | string | Unique identifier for the script (used as namespace).    |
| `tab_name`       | string | Display name shown on the tab header.                    |
| `widgets`        | array  | List of widget definition objects (see §4 below).        |

**Behaviour**

- If a tab for `script_id` already exists, the dashboard **replaces** the
  entire tab contents with the new widget list (useful for re-initialisation
  after a script reload).
- Tab order is insertion order; the built-in Dashboard tab always remains
  first.
- A `tab_name` longer than ~20 characters will be truncated by DearPyGui.

---

### 2.2 `gui_widget_update` (listen)

Sent by a script to **update the value of a single widget** on its tab.

**Payload**

```json
{
  "event": "gui_widget_update",
  "data": {
    "script_id": "minecraft_manager",
    "widget_id": "status",
    "value": "Online – 4 players"
  },
  "sender": "minecraft_manager"
}
```

**Fields**

| Field       | Type             | Description                                |
|-------------|------------------|--------------------------------------------|
| `script_id` | string           | The script that owns the tab.              |
| `widget_id` | string           | Widget ID as declared in `gui_tab_register`. |
| `value`     | varies per type  | New value (see §5 for type-specific rules).|

**Behaviour**

- If `script_id` has no registered tab, the event is **silently ignored**.
- If `widget_id` does not exist on that tab, the event is **silently ignored**.
- The update is enqueued on a thread-safe queue and applied on the next
  DearPyGui render frame – updates are **not** immediate.

### 2.3 Error Widget (`_gui_error`)
A special widget with `widget_id = "_gui_error"` is automatically reserved on
every script tab.  Scripts can emit a `gui_widget_update` with this widget_id
to push a red error message to the top of their tab:

```json
{
  "event": "gui_widget_update",
  "data": {
    "script_id": "minecraft_manager",
    "widget_id": "_gui_error",
    "value": "Data callback error: could not connect to DB"
  }
}
```

The error message is visible as red text at the top of the tab.  An empty
value hides the error.  This is automatically used by the GuiApiClient when
its `_data_callback` throws an exception (up to 3 consecutive errors).

### 2.4 Connection Health
The dashboard tracks when each script last sent **any** `gui_widget_update`.
On the Dashboard tab, a **Connected Scripts** table shows:
- ✅ Connected (last update < 15s ago)
- ⚠️ Stale (15–60s)
- 💀 Dead (> 60s)

Each script tab also shows a footer 🟢 Live / 🟡 Stale / 🔴 No data indicator.
This health tracking is fully automatic — no extra Phooks events required.

---

## 3. Data Pull Protocol

### 3.1 Motivation

The GUI Dashboard uses a pull-based model to keep widgets up-to-date. Every 3 seconds,
the dashboard emits a `gui_data_request` event. Scripts that have registered a tab
should respond by emitting `gui_widget_update` events with their latest data.

### 3.2 Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `gui_data_request` | GUI → Script | Dashboard requests current data from all scripts. Emitted every 3 seconds. |
| `gui_widget_update` | Script → GUI | Script responds with updated widget values. |

### 3.3 Using GuiApiClient (recommended)

Scripts should use `GuiApiClient` from `engine/gui_api_client.py`:

```python
from engine.gui_api_client import GuiApiClient

gui = GuiApiClient("GAMES/minecraft_manager", "Minecraft Manager")
gui.register_tab([
    {"type": "label", "id": "status", "title": "Server", "default": "Offline"},
    {"type": "meter", "id": "cpu", "title": "CPU", "default": 0},
])

def get_data():
    return {
        "status": "Online - 12 players",
        "cpu": 45,
    }

gui.on_data_request(get_data)

# On shutdown:
gui.close()
```

### 3.4 Manual Implementation (without GuiApiClient)

Scripts that don't use GuiApiClient can still participate:

1. Create a PhooksClient with listen_events=["gui_data_request"]
2. Register it
3. In the receive loop, when gui_data_request arrives, emit gui_widget_update events

### 3.5 Required Implementation (all scripts)

ALL scripts that register a GUI tab MUST implement a data request handler.
The handler should return current values for ALL registered widgets.
Scripts without a data request handler will show stale/empty data.

### 3.6 Error Handling

- If a script fails to respond (timeout, crash), its widgets keep their last known values
- The GUI never blocks waiting for a response — it uses fire-and-forget
- Scripts should handle exceptions in their data callbacks

### 3.7 Polling Interval

The default polling interval is 3 seconds. This is controlled by the GUI dashboard
and is not configurable per-script (to prevent flooding).

---

## 4. Widget Types & Definitions

Every widget in a `gui_tab_register` payload has these common fields:

| Field     | Type   | Required | Description                    |
|-----------|--------|----------|--------------------------------|
| `type`    | string | yes      | One of: `label`, `meter`, `table`, `message` |
| `id`      | string | yes      | Unique widget ID within this script's tab.   |
| `title`   | string | yes      | Display label shown above the widget.        |
| `default` | varies | yes      | Initial value (type‑dependent).              |

### 4.1 `label`

A plain text label (read‑only).

| Field     | Type   | Description                   |
|-----------|--------|-------------------------------|
| `type`    | string | `"label"`                     |
| `default` | string | Initial text displayed.       |

**DPG equivalent**: `dpg.add_text(default=default, tag=widget_id)`;
update via `dpg.set_value(widget_id, new_text)`.

---

### 4.2 `meter`

A horizontal progress bar (0–100 %).

| Field     | Type   | Description                         |
|-----------|--------|-------------------------------------|
| `type`    | string | `"meter"`                           |
| `default` | number | Initial percentage (0–100).         |

**DPG equivalent**: `dpg.add_progress_bar(tag=widget_id, default_value=default/100)`;
update via `dpg.set_value(widget_id, value/100)`.  Internally, DPG expects
0.0–1.0; scripts send 0–100 which is divided by 100.

---

### 4.3 `table`

A multi-column table with header row.

| Field     | Type   | Description                                 |
|-----------|--------|---------------------------------------------|
| `type`    | string | `"table"`                                   |
| `columns` | array  | List of column header strings.              |
| `default` | array  | List of rows; each row is a list of cell values matching the column count. |

**DPG equivalent**: `dpg.add_table(tag=widget_id)` with
`dpg.add_table_column(label=...)` for headers, then `dpg.add_row(...)` /
`dpg.table_row(...)` for rows.

> Tables are **read‑only** in the dashboard – users cannot edit cells.

---

### 4.4 `message`

A scrollable multi-line text area (read‑only).

| Field     | Type   | Description                           |
|-----------|--------|---------------------------------------|
| `type`    | string | `"message"`                           |
| `default` | string | Initial text displayed in the area.   |

**DPG equivalent**: `dpg.add_input_text(tag=widget_id, multiline=True, readonly=True, default_value=default)`.

> This is ideal for log output, event feeds, or any text that exceeds a
> single line.

---

## 5. Value Types Per Widget (gui_widget_update)

When a `gui_widget_update` event arrives, the `value` field is interpreted
according to the widget's declared `type`:

| Widget type | Accepted `value` type   | Behaviour                                               |
|-------------|------------------------|---------------------------------------------------------|
| `label`     | string                 | Replaces the label text.                                |
| `meter`     | number (0–100)         | Sets the meter fill percentage.                         |
| `table`     | array of arrays        | Replaces the entire table. Each inner array is a row.   |
| `message`   | string                 | Replaces the entire message area content.               |

**Examples**

```json
// Update a label
{"script_id": "minecraft_manager", "widget_id": "status", "value": "Online"}

// Update a meter
{"script_id": "minecraft_manager", "widget_id": "cpu", "value": 67}

// Replace a table
{"script_id": "minecraft_manager", "widget_id": "players",
 "value": [["Notch", "abc-123", "1ms"], ["Jeb", "def-456", "2ms"]]}

// Update a message box
{"script_id": "minecraft_manager", "widget_id": "log",
 "value": "[10:32] Player joined\n[10:33] Backup started"}
```

---

## 6. Built-in Dashboard Tab

The first tab (always present) is the **Dashboard** tab, which shows
engine-level information contributed by the GUI Dashboard itself.

### 6.1 Engine Status Table

A `table` widget that lists all scripts currently registered with the
Phooks hub.  Columns:

| Column     | Description                                                   |
|------------|---------------------------------------------------------------|
| Script ID  | The `script_id` of the registered script.                    |
| Status     | `Running` if heartbeat received within timeout, else `Stale`. |
| Last Seen  | Timestamp of last Phooks heartbeat / event.                  |
| Tabs       | Number of tabs the script has registered in the dashboard.   |

The dashboard populates this table by querying the Phooks hub's in-memory
`script_registry` via a local function call (not over the wire).  Updates
occur every **5 seconds** via a timer callback on the DearPyGui render loop.

### 6.2 System Stats

The dashboard also listens for `gui_widget_update` events where
`script_id` equals `"server-stats-collector"` to display live system
metrics.  The `server-stats-collector` service is expected to emit:

| widget_id         | type    | Description                        |
|-------------------|---------|------------------------------------|
| `cpu_percent`     | meter   | Overall CPU usage %                |
| `ram_percent`     | meter   | RAM usage %                        |
| `ram_used`        | label   | Used RAM string (e.g. "3.2 GiB")   |
| `disk_percent`    | meter   | Disk usage %                       |
| `disk_free`       | label   | Free disk string (e.g. "120 GiB")  |
| `temp`            | label   | CPU temperature string             |
| `net_sent`        | label   | Total bytes sent                   |
| `net_recv`        | label   | Total bytes received               |

> The server-stats-collector must register a tab and use standard
> `gui_widget_update` events just like any other script.  It does not need
> special integration – it follows the same API described in this document.

---

## 7. Error Handling & Timeouts

### 7.1 Phooks Receiver Timeout

The background listener calls `client.receive(timeout=1.0)` in a loop.  If
a timeout occurs (no events for 1 second), the loop simply continues.
This prevents the listener from blocking indefinitely and allows clean
shutdown.

### 7.2 Unknown Events

Any Phooks event that is **not** `gui_tab_register` or `gui_widget_update`
is silently discarded by the listener.

### 7.3 Duplicate Tab Registration

If a script sends a second `gui_tab_register` with the same `script_id`,
the dashboard **removes the old tab and creates a new one** with the fresh
widget definitions.  This handles script restarts gracefully.

### 7.4 Missing Widget on Update

A `gui_widget_update` targeting a `widget_id` that does not exist (or whose
script tab does not exist) is **silently ignored**.  No error is emitted
to the sender.

### 7.5 Value Overflow / Out of Range

- **meter**: values are clamped to the [0, 100] range internally.
- **table**: rows with more cells than declared `columns` are truncated;
  rows with fewer cells are padded with empty strings.
- **label / message**: values longer than ~10 000 characters are truncated
  to prevent DearPyGui rendering issues.

### 7.6 Hub Unavailability

If the Phooks hub is not running when the dashboard starts, the
`PhooksClient.register()` call will fail silently (packet sent to a closed
port).  The dashboard will still open its window, but no external tabs
will appear and no updates will arrive.  The listener thread will continue
to poll `receive()` and will begin processing events once the hub comes
online – no reconnect logic is required because UDP is connectionless.

---

## 8. Example: Registering a Tab from minecraft_manager

The following example shows how `minecraft_manager` would register a tab
and push live updates.

### 8.1 Phooks.py Declaration

```python
# minecraft_manager/Phooks.py

PHOOKS_EVENTS_LISTEN = [
    "sign_request",
    # … existing events …
]

PHOOKS_EVENTS_EMIT = [
    "sign_response",
    "gui_tab_register",       # <-- allow emitting tab registration
    "gui_widget_update",      # <-- allow emitting widget updates
]
```

### 8.2 Registration on Startup

```python
from engine.phooks_client import PhooksClient

client = PhooksClient(
    script_id="minecraft_manager",
    listen_events=PHOOKS_EVENTS_LISTEN,
    emit_events=PHOOKS_EVENTS_EMIT,
)
client.register()

# Register the dashboard tab
client.emit("gui_tab_register", {
    "script_id": "minecraft_manager",
    "tab_name": "Minecraft",
    "widgets": [
        {"type": "label",   "id": "status",   "title": "Status",
         "default": "Starting…"},
        {"type": "meter",   "id": "cpu",      "title": "CPU",
         "default": 0},
        {"type": "meter",   "id": "ram",      "title": "RAM",
         "default": 0},
        {"type": "table",   "id": "players",  "title": "Online Players",
         "columns": ["Name", "Ping", "World"], "default": []},
        {"type": "message", "id": "activity", "title": "Activity Log",
         "default": "Awaiting events…"},
    ],
})
```

### 8.3 Updating a Widget

```python
# Later, whenever state changes:
client.emit("gui_widget_update", {
    "script_id": "minecraft_manager",
    "widget_id": "status",
    "value": "Online – 4 players",
})

client.emit("gui_widget_update", {
    "script_id": "minecraft_manager",
    "widget_id": "players",
    "value": [
        ["Notch",  "1ms", "overworld"],
        ["Jeb",    "2ms", "nether"],
        ["Dinnerbone", "3ms", "overworld"],
    ],
})
```

> **Tip**: batch rapid updates by coalescing state and emitting once per
> tick to avoid flooding the dashboard.

---

## 9. Guide: How Scripts Use the API

### 9.1 Quick Start

1. **Add emit permissions** to your script's `Phooks.py`:
   ```python
   PHOOKS_EVENTS_EMIT.append("gui_tab_register")
   PHOOKS_EVENTS_EMIT.append("gui_widget_update")
   ```

2. **Create a PhooksClient** (if you don't already have one):
   ```python
   from engine.phooks_client import PhooksClient
   client = PhooksClient("my-script-id", [...listen...], [...emit...])
   client.register()
   ```

3. **Call `client.emit("gui_tab_register", {...})`** once at startup to
   create your tab.  The dashboard will add a new tab page and populate it
   with the widgets you define.

4. **Call `client.emit("gui_widget_update", {...})`** whenever a value
   changes.  The dashboard updates the corresponding widget on your tab.

### 9.2 Widget Layout

The dashboard arranges widgets **vertically** in the order they appear in
the `widgets` array, each taking the full width of the tab.  For now there
is no custom grid or column configuration – each widget occupies a single
row, with its title label above it.

| Array position | Row index |
|---------------|------------------|
| widgets[0]    | row 0            |
| widgets[1]    | row 1            |
| widgets[n]    | row n            |

### 9.3 Best Practices

- **Register your tab once** on script startup.  Re-register only if you
  need to change the widget structure (e.g., add a new meter type).
- **Use descriptive but short `widget_id` values** (alphanumeric, no spaces).
  These are used as DPG widget tags internally.
- **Limit update frequency** to no more than once per second per widget.
  The dashboard uses a thread-safe queue which is drained on the main thread;
  excessive updates can make the UI unresponsive.
- **For high-frequency metrics** (e.g., CPU), consider aggregating values
  on the sender side and emitting the average every 1–2 seconds.
- **Tables should be compact**: aim for ≤100 rows.  Larger datasets should
  use the `message` widget with formatted text instead.

### 9.4 Phooks.py Convention

Every script that interacts with the GUI Dashboard should declare the
relevant events in its `Phooks.py` file, following the YuniScripts
convention:

```python
# Phooks.py – Declares events this script listens to and emits.

PHOOKS_EVENTS_LISTEN = [
    # … events this script listens for …
]

PHOOKS_EVENTS_EMIT = [
    "gui_tab_register",     # register a tab in the GUI Dashboard
    "gui_widget_update",    # push widget value updates
    # … other emitted events …
]
```

This serves as documentation and allows the engine to report on
inter-script dependencies without reading every `main.py`.

---

## Appendix: DearPyGui Control Reference

| Widget type | DPG creation call                                              | DPG update call                     |
|-------------|---------------------------------------------------------------|--------------------------------------|
| label       | `dpg.add_text(tag=id, default_value=text)`                    | `dpg.set_value(id, text)`            |
| meter       | `dpg.add_progress_bar(tag=id, default_value=val/100)`         | `dpg.set_value(id, val/100)`         |
| table       | `dpg.add_table(tag=id)` + `dpg.add_table_column(...)` + rows  | recreate rows via `dpg.delete_item` / `dpg.add_row` |
| message     | `dpg.add_input_text(tag=id, multiline=True, readonly=True)`   | `dpg.set_value(id, text)`            |

All updates are enqueued on a thread-safe queue and applied by the main
render loop on the next DearPyGui frame.

---

*Document version 1.0.0 — YuniScripts GUI Dashboard API*
