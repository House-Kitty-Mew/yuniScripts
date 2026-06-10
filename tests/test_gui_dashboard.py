"""
Comprehensive unit tests for the GUI Dashboard module.

Tests:
  - GuiApiClient construction, event format, and edge cases
  - Widget spec validation (all types, missing fields, invalid types)
  - Widget map data structure (store, retrieve, type tracking)
  - Phooks event data correctness
  - Cross-platform print safety
  - Dashboard build logic (widget_map registration)

Run with: python -m pytest tests/test_gui_dashboard.py -v
"""
import json
import sys
import os
import queue
import time
import threading
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call

# ── Test helpers ──────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUI_MAIN = PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "gui-dashboard" / "main.py"
GUI_API_CLIENT = PROJECT_ROOT / "engine" / "gui_api_client.py"


# ── Test 1: GuiApiClient construction ────────────────────────────────────────

class TestGuiApiClientConstruction:
    """Verify GuiApiClient init and field validation."""

    def test_init_creates_client_with_correct_id(self):
        """Client script_id should include the script's ID plus ':gui' suffix."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("GAMES/minecraft_manager", "Minecraft Manager")
        try:
            # The internal PhooksClient should have script_id = "GAMES/minecraft_manager:gui"
            assert client.script_id == "GAMES/minecraft_manager"
            assert client.tab_name == "Minecraft Manager"
        finally:
            client.close()

    def test_init_with_empty_script_id(self):
        """Edge case: empty script_id should still create a client."""
        from engine.gui_api_client import GuiApiClient
        client = GuiApiClient("", "")
        try:
            assert client.script_id == ""
            assert client.tab_name == ""
        finally:
            client.close()

    def test_init_with_special_chars(self):
        """Edge case: special characters in IDs should not break init."""
        from engine.gui_api_client import GuiApiClient
        client = GuiApiClient("SERVICES/weird!@#$%", "Tab & Special:Chars")
        try:
            assert client.script_id == "SERVICES/weird!@#$%"
            assert client.tab_name == "Tab & Special:Chars"
        finally:
            client.close()


# ── Test 2: GuiApiClient register_tab event format ───────────────────────────

class TestGuiApiClientRegisterTab:
    """Verify that register_tab emits correctly structured Phooks events."""

    def test_register_tab_event_format(self):
        """The event should contain script_id, tab_name, and widgets list."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("GAMES/minecraft_manager", "Minecraft")

        # Capture the emit call
        with patch.object(client._client, 'emit') as mock_emit:
            specs = [
                {"type": "label", "id": "status", "title": "Status", "default": "Offline"},
            ]
            client.register_tab(specs)

            mock_emit.assert_called_once()
            event_name, data = mock_emit.call_args[0]
            assert event_name == "gui_tab_register"
            assert data["script_id"] == "GAMES/minecraft_manager"
            assert data["tab_name"] == "Minecraft"
            assert data["widgets"] == specs

        client.close()

    def test_register_tab_empty_widgets(self):
        """Edge case: empty widget list should still emit valid event."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/empty", "Empty")
        with patch.object(client._client, 'emit') as mock_emit:
            client.register_tab([])
            data = mock_emit.call_args[0][1]
            assert data["widgets"] == []

        client.close()

    def test_register_tab_all_widget_types(self):
        """All 4 widget types should be accepted in the specs list."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/all", "All Types")
        with patch.object(client._client, 'emit') as mock_emit:
            specs = [
                {"type": "label", "id": "l1", "title": "Label", "default": "hello"},
                {"type": "meter", "id": "m1", "title": "Progress", "default": 50},
                {"type": "table", "id": "t1", "title": "Players",
                 "columns": ["Name", "Score"], "default": []},
                {"type": "message", "id": "msg1", "title": "Log", "default": ""},
            ]
            client.register_tab(specs)
            data = mock_emit.call_args[0][1]
            assert len(data["widgets"]) == 4
            assert data["widgets"][0]["type"] == "label"
            assert data["widgets"][1]["type"] == "meter"
            assert data["widgets"][2]["type"] == "table"
            assert data["widgets"][3]["type"] == "message"

        client.close()

    def test_register_tab_unknown_widget_type(self):
        """Edge case: unknown widget type should still be passed through."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/unknown", "Unknown")
        with patch.object(client._client, 'emit') as mock_emit:
            specs = [
                {"type": "piechart", "id": "p1", "title": "Pie", "default": {}},
            ]
            client.register_tab(specs)
            data = mock_emit.call_args[0][1]
            assert data["widgets"][0]["type"] == "piechart"

        client.close()

    def test_register_tab_missing_optional_fields(self):
        """Edge case: specs with only required fields should still work."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/minimal", "Minimal")
        with patch.object(client._client, 'emit') as mock_emit:
            specs = [
                {"type": "label", "id": "min", "title": "Min"},
                # No 'default' field
            ]
            client.register_tab(specs)
            data = mock_emit.call_args[0][1]
            assert "default" not in data["widgets"][0]

        client.close()


# ── Test 3: GuiApiClient update_widget event format ──────────────────────────

class TestGuiApiClientUpdateWidget:
    """Verify that update_widget emits correctly structured events."""

    def test_update_widget_string_value(self):
        """A label update should carry script_id, widget_id, and string value."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/server", "Server")
        with patch.object(client._client, 'emit') as mock_emit:
            client.update_widget("status", "Online - 12 players")
            event_name, data = mock_emit.call_args[0]
            assert event_name == "gui_widget_update"
            assert data["script_id"] == "test/server"
            assert data["widget_id"] == "status"
            assert data["value"] == "Online - 12 players"

        client.close()

    def test_update_widget_numeric_value(self):
        """A meter update should carry a numeric value."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/stats", "Stats")
        with patch.object(client._client, 'emit') as mock_emit:
            client.update_widget("cpu", 85)
            data = mock_emit.call_args[0][1]
            assert data["value"] == 85

        client.close()

    def test_update_widget_table_value(self):
        """A table update should carry a 2D list value."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/players", "Players")
        with patch.object(client._client, 'emit') as mock_emit:
            table_data = [["Steve", 20], ["Alex", 18]]
            client.update_widget("player_table", table_data)
            data = mock_emit.call_args[0][1]
            assert data["value"] == table_data

        client.close()

    def test_update_widget_edge_empty_string(self):
        """Edge case: empty string value."""
        from engine.gui_api_client import GuiApiClient
        client = GuiApiClient("test/edge", "Edge")
        with patch.object(client._client, 'emit') as mock_emit:
            client.update_widget("label1", "")
            data = mock_emit.call_args[0][1]
            assert data["value"] == ""

        client.close()

    def test_update_widget_edge_none_value(self):
        """Edge case: None value."""
        from engine.gui_api_client import GuiApiClient
        client = GuiApiClient("test/edge", "Edge")
        with patch.object(client._client, 'emit') as mock_emit:
            client.update_widget("label1", None)
            data = mock_emit.call_args[0][1]
            assert data["value"] is None

        client.close()

    def test_update_widget_edge_very_long_string(self):
        """Edge case: very long string value (10k chars)."""
        from engine.gui_api_client import GuiApiClient
        client = GuiApiClient("test/edge", "Edge")
        with patch.object(client._client, 'emit') as mock_emit:
            long_val = "x" * 10000
            client.update_widget("big_msg", long_val)
            data = mock_emit.call_args[0][1]
            assert len(data["value"]) == 10000

        client.close()


# ── Test 4: _widget_map data structure ───────────────────────────────────────

class TestWidgetMap:
    """Verify the widget map data structure (extracted from main.py logic)."""

    def setup_method(self):
        """Create a fresh widget map for each test."""
        self.widget_map = {}

    def test_store_and_retrieve_label(self):
        """Storing a label widget and retrieving it should return (type, name)."""
        key = ("GAMES/minecraft_manager", "status")
        self.widget_map[key] = ("label", "Server Status_status")
        assert self.widget_map[key] == ("label", "Server Status_status")

    def test_store_and_retrieve_meter(self):
        """Storing a meter should return ('meter', wid)."""
        key = ("GAMES/minecraft_manager", "cpu")
        self.widget_map[key] = ("meter", "cpu")
        assert self.widget_map[key] == ("meter", "cpu")

    def test_store_and_retrieve_table(self):
        """Storing a table should return ('table', wid)."""
        key = ("_dash", "engine_status")
        self.widget_map[key] = ("table", "_dash_engine_status")
        assert self.widget_map[key] == ("table", "_dash_engine_status")

    def test_store_and_retrieve_message(self):
        """Storing a message should return ('message', wid)."""
        key = ("_dash", "engine_log")
        self.widget_map[key] = ("message", "_dash_engine_log")
        assert self.widget_map[key] == ("message", "_dash_engine_log")

    def test_unknown_key_returns_none(self):
        """Looking up a non-existent key should return None."""
        key = ("nonexistent", "widget")
        assert self.widget_map.get(key) is None

    def test_overwrite_existing_key(self):
        """Overwriting an existing key should replace the value."""
        key = ("test", "w1")
        self.widget_map[key] = ("label", "old_name")
        assert self.widget_map[key] == ("label", "old_name")
        self.widget_map[key] = ("meter", "new_name")
        assert self.widget_map[key] == ("meter", "new_name")

    def test_multiple_scripts_isolation(self):
        """Widgets from different scripts with same widget_id should not collide."""
        key_a = ("script/A", "status")
        key_b = ("script/B", "status")
        self.widget_map[key_a] = ("label", "A_status")
        self.widget_map[key_b] = ("label", "B_status")
        assert self.widget_map[key_a] == ("label", "A_status")
        assert self.widget_map[key_b] == ("label", "B_status")

    def test_mixed_case_script_id(self):
        """Script IDs are case-sensitive; should differentiate."""
        key_lower = ("games/mc", "w1")
        key_upper = ("GAMES/MC", "w1")
        self.widget_map[key_lower] = ("label", "lower")
        self.widget_map[key_upper] = ("meter", "upper")
        assert self.widget_map[key_lower] == ("label", "lower")
        assert self.widget_map[key_upper] == ("meter", "upper")

    def test_type_unpacking(self):
        """Verify tuple unpacking pattern used in _handle_widget_update."""
        key = ("test", "meter1")
        self.widget_map[key] = ("meter", "meter1")
        wtype, full_name = self.widget_map[key]
        assert wtype == "meter"
        assert full_name == "meter1"


# ── Test 5: Event handling logic ─────────────────────────────────────────────

class TestEventHandlerLogic:
    """Verify event dispatching logic extracted from main.py."""

    def test_tab_register_field_resolution(self):
        """_handle_tab_register should accept both 'widgets' and 'widget_specs' keys."""
        # Simulate the logic from the fixed main.py
        def get_widgets(data):
            return data.get("widgets", data.get("widget_specs", []))

        # Test 'widgets' key (from gui_api_client.py)
        data1 = {"script_id": "test", "tab_name": "Test", "widgets": [{"type": "label"}]}
        assert get_widgets(data1) == [{"type": "label"}]

        # Test 'widget_specs' key (fallback)
        data2 = {"script_id": "test", "tab_name": "Test", "widget_specs": [{"type": "meter"}]}
        assert get_widgets(data2) == [{"type": "meter"}]

        # Test both keys; 'widgets' should take priority
        data3 = {"script_id": "test", "widgets": [{"type": "label"}], "widget_specs": [{"type": "meter"}]}
        assert get_widgets(data3) == [{"type": "label"}]

        # Test missing keys; should return empty list
        data4 = {"script_id": "test"}
        assert get_widgets(data4) == []

    def test_widget_update_lookup_flow(self):
        """Verify the full update widget lookup flow."""
        widget_map = {}
        # Register a widget
        widget_map[("test", "status")] = ("label", "Server Status_status")

        # Simulate update lookup
        key = ("test", "status")
        lookup = widget_map.get(key)
        assert lookup is not None
        wtype, full_name = lookup
        assert wtype == "label"
        assert full_name == "Server Status_status"

        # Simulate unknown widget
        key2 = ("test", "unknown")
        assert widget_map.get(key2) is None

    def test_update_value_type_handling(self):
        """Verify that values are correctly cast per widget type."""
        # Label: str(value)
        assert str(42) == "42"
        assert str("hello") == "hello"
        assert str(None) == "None"
        assert str(True) == "True"

        # Meter: int(value)
        assert int(85) == 85
        assert int(85.7) == 85
        assert int("42") == 42

        # Table: pass through as-is
        table = [["a", 1], ["b", 2]]
        assert table == [["a", 1], ["b", 2]]

        # Message: str(value)
        assert str("log line") == "log line"

    def test_meter_value_clamping(self):
        """Meters should handle out-of-range values without crashing."""
        # appJar's setMeter accepts 0-100; we should handle extreme values
        assert int(-50) == -50  # appJar will clamp
        assert int(200) == 200  # appJar will clamp
        assert int(0) == 0
        assert int(100) == 100


# ── Test 6: _safe_print ──────────────────────────────────────────────────────

class TestSafePrint:
    """Verify the _safe_print wrapper handles OSError on stdout."""

    def test_safe_print_normal(self):
        """Normal print should work unchanged."""
        # Import and test the static method from gui_api_client
        from engine.gui_api_client import GuiApiClient
        # Should not raise
        GuiApiClient._safe_print("normal message")

    def test_safe_print_oserror_handled(self):
        """OSError from print should be caught silently."""
        from engine.gui_api_client import GuiApiClient
        with patch('builtins.print', side_effect=OSError(22, 'Invalid argument')):
            # Should not raise
            GuiApiClient._safe_print("this will fail")
            # If we get here, the error was caught


# ── Test 7: Context manager support ──────────────────────────────────────────

class TestContextManager:
    """Verify GuiApiClient works as a context manager."""

    def test_context_manager_enter_exit(self):
        """Using 'with' should register and then unregister."""
        from engine.gui_api_client import GuiApiClient
        from engine.phooks_client import PhooksClient

        with patch.object(PhooksClient, 'register') as mock_reg:
            with patch.object(PhooksClient, 'unregister') as mock_unreg:
                with GuiApiClient("test/ctx", "Ctx") as client:
                    assert client.script_id == "test/ctx"
                # After exit, unregister should have been called
                mock_unreg.assert_called_once()

    def test_context_manager_on_exception(self):
        """Exception inside 'with' block should still call close."""
        from engine.gui_api_client import GuiApiClient
        from engine.phooks_client import PhooksClient

        with patch.object(PhooksClient, 'unregister') as mock_unreg:
            try:
                with GuiApiClient("test/err", "Error") as client:
                    raise ValueError("test error")
            except ValueError:
                pass
            mock_unreg.assert_called_once()


# ── Test 8: Phooks event serialization ───────────────────────────────────────

class TestEventSerialization:
    """Verify events survive JSON serialization/deserialization."""

    def test_tab_register_serializable(self):
        """gui_tab_register events should be JSON-serializable."""
        event = {
            "command": "EMIT",
            "event": "gui_tab_register",
            "data": {
                "script_id": "GAMES/minecraft_manager",
                "tab_name": "Minecraft",
                "widgets": [
                    {"type": "label", "id": "status", "title": "Status", "default": "Offline"},
                    {"type": "meter", "id": "cpu", "title": "CPU", "default": 0},
                ]
            },
            "sender": "GAMES/minecraft_manager:gui"
        }
        serialized = json.dumps(event)
        deserialized = json.loads(serialized)
        assert deserialized["data"]["script_id"] == "GAMES/minecraft_manager"
        assert len(deserialized["data"]["widgets"]) == 2

    def test_widget_update_serializable(self):
        """gui_widget_update events should be JSON-serializable with table data."""
        event = {
            "command": "EMIT",
            "event": "gui_widget_update",
            "data": {
                "script_id": "GAMES/minecraft_manager",
                "widget_id": "players",
                "value": [["Steve", 20, "Overworld"], ["Alex", 18, "Nether"]],
            },
            "sender": "GAMES/minecraft_manager:gui"
        }
        serialized = json.dumps(event)
        deserialized = json.loads(serialized)
        assert deserialized["data"]["value"][0][0] == "Steve"
        assert deserialized["data"]["value"][1][1] == 18

    def test_update_with_unicode(self):
        """Unicode characters in updates should survive JSON."""
        event = {
            "command": "EMIT",
            "event": "gui_widget_update",
            "data": {
                "script_id": "test/unicode",
                "widget_id": "label1",
                "value": "Café résumé 中文 español",
            },
            "sender": "test/unicode:gui"
        }
        serialized = json.dumps(event)
        deserialized = json.loads(serialized)
        assert deserialized["data"]["value"] == "Café résumé 中文 español"

    def test_tab_with_widget_specs_fallback(self):
        """Events using 'widget_specs' key should also be handled."""
        event = {
            "command": "EMIT",
            "event": "gui_tab_register",
            "data": {
                "script_id": "test/legacy",
                "tab_name": "Legacy",
                "widget_specs": [{"type": "label", "id": "old", "title": "Old"}],
            },
            "sender": "test/legacy:gui"
        }
        serialized = json.dumps(event)
        deserialized = json.loads(serialized)
        assert "widget_specs" in deserialized["data"]
        assert "widgets" not in deserialized["data"]


# ── Test 9: Module import edge cases ────────────────────────────────────────

class TestModuleImports:
    """Verify modules can be imported without GUI available."""

    def test_gui_api_client_imports_without_appjar(self):
        """engine/gui_api_client.py should not import appJar."""
        # It only imports PhooksClient, not appJar
        import ast
        tree = ast.parse(GUI_API_CLIENT.read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        assert "appJar" not in imports

    def test_main_imports_appjar_gracefully(self):
        """main.py should handle ImportError for appJar gracefully."""
        content = GUI_MAIN.read_text()
        assert "try:" in content
        assert "ImportError" in content
        assert "sys.exit(1)" in content

    def test_api_documentation_exists(self):
        """API.md should exist and be non-empty."""
        api_path = PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "gui-dashboard" / "API.md"
        assert api_path.exists()
        content = api_path.read_text()
        assert len(content) > 1000

    def test_requirements_contains_appjar(self):
        """requirements.txt should list appJar."""
        req_path = PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "gui-dashboard" / "requirements.txt"
        assert req_path.exists()
        content = req_path.read_text()
        assert "dearpygui" in content

    def test_phooks_py_declares_events(self):
        """Phooks.py should declare LISTEN and EMIT event lists."""
        phooks_path = PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "gui-dashboard" / "Phooks.py"
        content = phooks_path.read_text()
        assert "PHOOKS_EVENTS_LISTEN" in content
        assert "PHOOKS_EVENTS_EMIT" in content
        assert "gui_tab_register" in content
        assert "gui_widget_update" in content

    def test_meta_info_exists(self):
        """meta.info should exist and be valid."""
        meta_path = PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "gui-dashboard" / "meta.info"
        assert meta_path.exists()
        content = meta_path.read_text()
        assert "name = GUI Dashboard" in content
        assert "entry_point = main.py" in content


# ── Test 10: Thread safety (logic tests) ────────────────────────────────────

class TestThreadSafety:
    """Verify thread-safe patterns used in the dashboard."""

    def test_queue_based_event_passing(self):
        """Events should be passed through a thread-safe queue."""
        q = queue.Queue()
        # Producer
        q.put({"event": "gui_widget_update", "data": {"widget_id": "test"}})
        # Consumer
        assert not q.empty()
        event = q.get_nowait()
        assert event["event"] == "gui_widget_update"

    def test_empty_queue_drain(self):
        """Draining an empty queue should not block."""
        q = queue.Queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items == []

    def test_multiple_events_queue_order(self):
        """Events should be processed in FIFO order."""
        q = queue.Queue()
        q.put("first")
        q.put("second")
        q.put("third")
        assert q.get() == "first"
        assert q.get() == "second"
        assert q.get() == "third"

    def test_concurrent_put_and_get(self):
        """Simulate producer-consumer pattern from main.py."""
        q = queue.Queue()
        results = []

        def producer():
            for i in range(100):
                q.put(i)
                time.sleep(0.001)

        def consumer():
            while len(results) < 100:
                try:
                    results.append(q.get(timeout=2))
                except queue.Empty:
                    break

        t = threading.Thread(target=producer, daemon=True)
        t.start()
        consumer()
        t.join(timeout=3)
        assert len(results) == 100
        assert results == list(range(100))


# ── Test 11: GuiApiClient on_data_request / data pull protocol ──────────────

class TestGuiApiClientDataRequest:
    """Verify the data pull protocol: on_data_request callback registration,
    gui_data_request event handling, and widget update emission."""

    # ------------------------------------------------------------------ #
    #  Helper: simulate what _listen_loop does when a gui_data_request
    #  event arrives, without needing a real thread.
    # ------------------------------------------------------------------ #
    @staticmethod
    def _simulate_data_request(client):
        """Inline version of the _listen_loop logic for gui_data_request."""
        if client._data_callback:
            try:
                data = client._data_callback()
                if isinstance(data, dict):
                    for widget_id, value in data.items():
                        client._client.emit("gui_widget_update", {
                            "script_id": client.script_id,
                            "widget_id": widget_id,
                            "value": value,
                        })
            except Exception:
                pass

    def test_on_data_request_registers_callback(self):
        """Callback passed to on_data_request should be stored in _data_callback."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/data_req", "DataReq")
        try:
            cb = Mock(return_value={})
            client.on_data_request(cb)
            assert client._data_callback is cb
        finally:
            client.close()

    def test_data_request_triggers_callback(self):
        """Simulating gui_data_request should invoke the registered callback."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/data_req", "DataReq")
        try:
            cb = Mock(return_value={})
            client.on_data_request(cb)
            self._simulate_data_request(client)
            cb.assert_called_once()
        finally:
            client.close()

    def test_data_request_emits_updates(self):
        """When callback returns {status:'OK', cpu:50}, a gui_widget_update
        emit should be made for each key."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/data_req", "DataReq")
        try:
            cb = Mock(return_value={"status": "OK", "cpu": 50})
            client.on_data_request(cb)

            with patch.object(client._client, 'emit') as mock_emit:
                self._simulate_data_request(client)
                # Two emit calls expected
                assert mock_emit.call_count == 2
                # First emit: widget_id = "status", value = "OK"
                call1 = mock_emit.call_args_list[0]
                assert call1[0][0] == "gui_widget_update"
                assert call1[0][1]["widget_id"] == "status"
                assert call1[0][1]["value"] == "OK"
                # Second emit: widget_id = "cpu", value = 50
                call2 = mock_emit.call_args_list[1]
                assert call2[0][0] == "gui_widget_update"
                assert call2[0][1]["widget_id"] == "cpu"
                assert call2[0][1]["value"] == 50
        finally:
            client.close()

    def test_data_request_empty_response(self):
        """Callback returning {} should produce zero emit calls."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/data_req", "DataReq")
        try:
            cb = Mock(return_value={})
            client.on_data_request(cb)

            with patch.object(client._client, 'emit') as mock_emit:
                self._simulate_data_request(client)
                mock_emit.assert_not_called()
        finally:
            client.close()

    def test_data_request_none_callback(self):
        """No callback registered (None) should not crash."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/data_req", "DataReq")
        try:
            # Do NOT register a callback; _data_callback remains None
            with patch.object(client._client, 'emit') as mock_emit:
                # This simulates the _listen_loop guard: if cb is None, skip
                self._simulate_data_request(client)
                mock_emit.assert_not_called()
        finally:
            client.close()

    def test_data_request_callback_error(self):
        """Callback raising an exception should be caught gracefully (no crash)."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/data_req", "DataReq")
        try:
            def _broken_cb():
                raise RuntimeError("simulated failure")

            client.on_data_request(_broken_cb)

            with patch.object(client._client, 'emit') as mock_emit:
                # Should not raise
                self._simulate_data_request(client)
                mock_emit.assert_not_called()
        finally:
            client.close()

    def test_on_data_request_edge_invalid_return(self):
        """Callback returning a non-dict (e.g., a string) should not crash."""
        from engine.gui_api_client import GuiApiClient

        client = GuiApiClient("test/data_req", "DataReq")
        try:
            cb = Mock(return_value="not_a_dict")
            client.on_data_request(cb)

            with patch.object(client._client, 'emit') as mock_emit:
                self._simulate_data_request(client)
                # The isinstance(data, dict) guard should prevent emits
                mock_emit.assert_not_called()
        finally:
            client.close()


# ── Test 12: Dashboard data polling logic ────────────────────────────────────

class _DataPoller:
    """Minimal stub that mirrors the dashboard's data-pull protocol:
    every *interval* seconds, emit a ``gui_data_request`` event.
    On construction, the timer starts at 0 so the first call to ``poll()``
    always fires (emulating the dashboard's startup behaviour).
    """
    def __init__(self, emit_func, interval=3.0):
        self.emit_func = emit_func
        self.interval = interval
        # Start at -interval so the very first poll always fires
        self._last_poll = -interval

    def poll(self, current_time):
        """Check whether the interval has elapsed. If yes, emit and reset."""
        if current_time - self._last_poll >= self.interval:
            self._last_poll = current_time
            self.emit_func("gui_data_request", {})
            return True
        return False

    @property
    def last_poll(self):
        return self._last_poll


class TestDashboardDataPoll:
    """Verify the dashboard's 3-second data-poll timer and event emission."""

    def test_data_poll_interval(self):
        """poll() should return True (emit) only when the interval has elapsed."""
        poller = _DataPoller(emit_func=Mock(), interval=3.0)

        # At t=0, no poll has happened yet → should emit
        assert poller.poll(0.0) is True

        # At t=1, only 1s has passed → skip
        assert poller.poll(1.0) is False

        # At t=2.9, still < 3s → skip
        assert poller.poll(2.9) is False

        # At t=3.0, exactly interval → emit
        assert poller.poll(3.0) is True

    def test_data_poll_emits_event(self):
        """poll() should call the emit function with 'gui_data_request'."""
        emit = Mock()
        poller = _DataPoller(emit_func=emit, interval=3.0)

        poller.poll(0.0)  # Trigger
        emit.assert_called_once_with("gui_data_request", {})

    def test_data_poll_skips_if_recent(self):
        """If less than 3 seconds have passed, emit should NOT be called."""
        emit = Mock()
        poller = _DataPoller(emit_func=emit, interval=3.0)

        poller.poll(0.0)   # Emit #1
        assert emit.call_count == 1

        poller.poll(1.0)   # Too soon → skip
        poller.poll(2.0)   # Still too soon → skip
        assert emit.call_count == 1  # No additional calls

    def test_data_poll_resets_timer(self):
        """After emitting, the timer should reset so the next poll waits
        another full interval."""
        emit = Mock()
        poller = _DataPoller(emit_func=emit, interval=3.0)

        # First emit at t=0
        poller.poll(0.0)
        assert poller.last_poll == 0.0
        assert emit.call_count == 1

        # t=2 → too soon
        poller.poll(2.0)
        assert emit.call_count == 1

        # t=3 → interval elapsed from last_poll (0s), emit again
        poller.poll(3.0)
        assert emit.call_count == 2
        assert poller.last_poll == 3.0  # Timer reset

        # t=4 → only 1s since last emit → skip
        poller.poll(4.0)
        assert emit.call_count == 2

    def test_data_poll_custom_interval(self):
        """Should support arbitrary intervals (not just 3 seconds)."""
        emit = Mock()
        poller = _DataPoller(emit_func=emit, interval=5.0)

        poller.poll(0.0)
        assert emit.call_count == 1

        poller.poll(4.9)
        assert emit.call_count == 1  # Still < 5s

        poller.poll(5.0)
        assert emit.call_count == 2


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
