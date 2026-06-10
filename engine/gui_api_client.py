"""
Gui API Client – helper for scripts to register tabs and push updates
to the GUI Dashboard via Phooks.

Usage:
    from engine.gui_api_client import GuiApiClient
    gui = GuiApiClient("GAMES/minecraft_manager", "Minecraft Manager")
    gui.register_tab([
        {"type": "label", "id": "status", "title": "Server Status", "default": "Offline"},
        {"type": "meter", "id": "cpu", "title": "CPU", "default": 0},
        {"type": "table", "id": "players", "title": "Online Players", "columns": ["Name","Score"]},
        {"type": "message", "id": "log", "title": "Activity", "default": ""},
    ])

    # Later, push updates:
    gui.update_widget("status", "Online - 12 players")
    gui.update_widget("cpu", 45)
    gui.update_widget("players", [["Steve",20],["Alex",18]])
    gui.update_widget("log", "Server started\nRCON connected")
"""
from engine.phooks_client import PhooksClient
import threading


class GuiApiClient:
    def __init__(self, script_id, tab_name):
        self.script_id = script_id
        self.tab_name = tab_name
        self._data_callback = None
        self._widget_specs: list[dict] = []  # saved for re-registration
        self._widget_specs_lock = threading.Lock()
        self._running = True
        self._errors: list[str] = []
        self._callback_error_count = 0
        self._stats_lock = threading.Lock()
        self._client = PhooksClient(
            script_id=f"{script_id}:gui",
            listen_events=["gui_data_request"],
            emit_events=["gui_tab_register", "gui_widget_update"],
        )
        try:
            self._client.register()
        except Exception as e:
            self._errors.append(f"Phooks register failed: {e}")
            self._safe_print(f"[gui-api-client] FATAL: Phooks register failed: {e}")
        self._listener = threading.Thread(target=self._listen_loop, daemon=True)
        self._listener.start()
        self._safe_print(f"[gui-api-client] {script_id} tab '{tab_name}' client ready")

    def on_data_request(self, callback):
        """Register a callback function(data_source) that returns dict of widget_id->value.
        Called when the GUI dashboard requests a data refresh."""
        self._data_callback = callback

    def register_tab(self, widget_specs):
        """Emit a gui_tab_register event to the dashboard."""
        with self._widget_specs_lock:
            self._widget_specs = widget_specs  # saved for re-registration
        self._emit_tab_register()

    def _emit_tab_register(self):
        """Emit (or re-emit) the tab registration event to the dashboard."""
        try:
            with self._widget_specs_lock:
                specs = list(self._widget_specs)
            self._client.emit("gui_tab_register", {
                "script_id": self.script_id,
                "tab_name": self.tab_name,
                "widgets": specs,
            })
            self._safe_print(f"[gui-api-client] registered tab '{self.tab_name}' with {len(specs)} widget(s)")
        except Exception as e:
            self._errors.append(f"Emit tab register failed: {e}")
            self._safe_print(f"[gui-api-client] FATAL: emit tab register: {e}")

    def emit_error(self, error_msg: str):
        """Emit an error widget to show on the dashboard for this script's tab."""
        try:
            self._client.emit("gui_widget_update", {
                "script_id": self.script_id,
                "widget_id": "_gui_error",
                "value": error_msg,
            })
        except Exception:
            pass

    def update_widget(self, widget_id, value):
        """Push an update for a single widget."""
        try:
            self._client.emit("gui_widget_update", {
                "script_id": self.script_id,
                "tab_name": self.tab_name,
                "widget_id": widget_id,
                "value": value,
            })
        except Exception as e:
            self._safe_print(f"[gui-api-client] update_widget error: {e}")

    def close(self):
        """Unregister from Phooks hub and stop listener."""
        self._running = False
        try:
            self._client.unregister()
        except Exception:
            pass
        self._safe_print(f"[gui-api-client] {self.script_id} closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _listen_loop(self):
        """Background thread: handle gui_data_request events from the GUI dashboard."""
        while self._running:
            try:
                event = self._client.receive(timeout=1.0)
                if event and isinstance(event, dict) and event.get("event") == "gui_data_request":
                    # Re-register tab on every data request to fix race condition:
                    # if the dashboard started *after* us, it never received our
                    # initial gui_tab_register and would reject widget updates.
                    with self._widget_specs_lock:
                        has_specs = bool(self._widget_specs)
                    if has_specs:
                        self._emit_tab_register()
                    if self._data_callback:
                        try:
                            data = self._data_callback()
                            if isinstance(data, dict):
                                for widget_id, value in data.items():
                                    self._client.emit("gui_widget_update", {
                                        "script_id": self.script_id,
                                        "widget_id": widget_id,
                                        "value": value,
                                    })
                            with self._stats_lock:
                                self._callback_error_count = 0  # reset on success
                        except Exception as cb_exc:
                            with self._stats_lock:
                                self._callback_error_count += 1
                                count = self._callback_error_count
                            err_msg = f"data callback error ({count}): {cb_exc}"
                            self._safe_print(f"[gui-api-client] {err_msg}")
                            if count <= 3:
                                with self._stats_lock:
                                    self._errors.append(err_msg)
                                # Emit an update to the _gui_error widget to show on the dashboard
                                try:
                                    self._client.emit("gui_widget_update", {
                                        "script_id": self.script_id,
                                        "widget_id": "_gui_error",
                                        "value": f"⚠️ Callback error: {cb_exc}"[:200],
                                    })
                                except Exception:
                                    pass
            except Exception as loop_exc:
                self._safe_print(f"[gui-api-client] listen loop error: {loop_exc}")
                self._errors.append(f"listen loop: {loop_exc}")

    @staticmethod
    def _safe_print(msg):
        try:
            print(msg)
        except OSError:
            pass
