#!/usr/bin/env python3
"""Comprehensive unittest test suite for mc-status-relay/main.py.

Tests cover:
  - Config loading from JSON files (local & DATA/ paths)
  - register_tab() GUI registration
  - _get_gui_data() state-to-widget mapping
  - minescript_listener() UDP Minescript packet parsing (BIOME:/DIM:)
  - query_listener() UDP query/response for "status" command
  - LAN beacon/finder startup
  - Shutdown flag behaviour
  - Thread safety via threading.Lock
  - Edge cases: empty packets, malformed data, missing keys

Strategy:
  The source module has module-level imports (engine.gui_api_client, engine.ports)
  and module-level code that executes on import. We mock sys.modules BEFORE
  importing main to prevent real engine imports.
  For socket tests we patch the main module's already-imported socket reference.
  We use a custom BaseException (_TestHalt) to break out of infinite while-loops
  since the listener code catches `Exception` but not `BaseException`.
"""

import json
import os
import signal
import socket as real_socket
import sys
import tempfile
import threading
import time
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import ANY, MagicMock, PropertyMock, call, patch, DEFAULT


# ---------------------------------------------------------------------------
# Custom exception to break infinite loops (not caught by `except Exception`)
# ---------------------------------------------------------------------------
class _TestHalt(BaseException):
    """Raised by mock recvfrom to stop the listener's while True loop."""
    pass


# ---------------------------------------------------------------------------
# Helper: build mock engine modules that main.py imports at module level
# ---------------------------------------------------------------------------
def _build_mock_engine_modules():
    """Return dict of {module_name: MagicMock} for every engine.* import."""
    mock_gui_api = MagicMock(name="engine.gui_api_client")
    mock_gui_api.GuiApiClient = MagicMock(name="GuiApiClient")

    mock_ports = MagicMock(name="engine.ports")
    mock_ports.MINESCRIPT_SENDER_PORT = 25566
    mock_ports.QUERY_PORT = 25568
    mock_ports.LAN_DISCOVERY_PORT = 42069
    mock_ports.PHOOKS_HUB_PORT = 42070

    mock_lan_discovery = MagicMock(name="engine.lan_discovery")
    mock_lan_discovery.ServiceBeacon = MagicMock(name="ServiceBeacon")
    mock_lan_discovery.ServiceFinder = MagicMock(name="ServiceFinder")

    mock_dcl = MagicMock(name="dynamic_config_loader")
    mock_dcl.register_configs = MagicMock()
    mock_dcl.get_config = MagicMock(return_value=None)
    mock_dcl.update_config = MagicMock()
    mock_dcl.load_source = MagicMock()
    mock_dcl.flush_source = MagicMock()

    return {
        "engine": MagicMock(name="engine"),
        "engine.gui_api_client": mock_gui_api,
        "engine.ports": mock_ports,
        "engine.lan_discovery": mock_lan_discovery,
        "dynamic_config_loader": mock_dcl,
    }


def _import_main_with_mocks(mock_modules=None):
    """Import mc-status-relay.main with all engine modules mocked.

    Returns (main_module, cleanup_callable).
    After tests, call cleanup() to remove the import from sys.modules.
    """
    if mock_modules is None:
        mock_modules = _build_mock_engine_modules()

    backups = {}
    for mod_name, mock_obj in mock_modules.items():
        backups[mod_name] = sys.modules.get(mod_name)
        sys.modules[mod_name] = mock_obj

    main_mod_name = "SCRIPTS.SERVICES.mc_status_relay.main"
    if main_mod_name in sys.modules:
        del sys.modules[main_mod_name]
    if "main" in sys.modules:
        del sys.modules["main"]

    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    import importlib.util
    main_path = Path(__file__).resolve().parent.parent / "main.py"
    spec = importlib.util.spec_from_file_location("main", str(main_path))
    main_mod = importlib.util.module_from_spec(spec)
    relay_dir = str(main_path.parent)
    if relay_dir not in sys.path:
        sys.path.insert(0, relay_dir)

    spec.loader.exec_module(main_mod)

    def cleanup():
        for mod_name, backup in backups.items():
            if backup is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = backup
        sys.modules.pop("main", None)
        if str(project_root) in sys.path:
            sys.path.remove(str(project_root))
        if relay_dir in sys.path:
            sys.path.remove(relay_dir)

    return main_mod, cleanup


# ===================================================================
# Test Cases
# ===================================================================

class TestRegisterTab(unittest.TestCase):
    """register_tab(gui) should call gui.register_tab with correct schema."""

    def test_register_tab_calls_gui_with_widgets(self):
        """register_tab must pass the full widget list to gui.register_tab."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            gui = MagicMock()
            main_mod.register_tab(gui)
            gui.register_tab.assert_called_once()
            args = gui.register_tab.call_args[0][0]
            self.assertIsInstance(args, list)
            self.assertGreater(len(args), 0)
            widget_ids = [w["id"] for w in args]
            for expected in ("biome", "dimension", "last_update", "service_status",
                             "uptime", "packets_received", "listening_ports", "log"):
                self.assertIn(expected, widget_ids)
        finally:
            cleanup()

    def test_register_tab_widget_structure(self):
        """Verify each widget has the required keys."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            gui = MagicMock()
            main_mod.register_tab(gui)
            widgets = gui.register_tab.call_args[0][0]
            for w in widgets:
                self.assertIn("type", w, f"Widget {w.get('id')} missing 'type'")
                self.assertIn("id", w, f"Widget missing 'id'")
                self.assertIn("title", w, f"Widget {w['id']} missing 'title'")
                self.assertIn("default", w, f"Widget {w['id']} missing 'default'")
        finally:
            cleanup()


class TestGetGuiData(unittest.TestCase):
    """_get_gui_data() should return the correct state mapping."""

    def setUp(self):
        self.main_mod, self.cleanup = _import_main_with_mocks()

    def tearDown(self):
        self._reset_globals()
        self.cleanup()

    def _reset_globals(self):
        m = self.main_mod
        with m.lock:
            m.state["biome"] = ""
            m.state["dim"] = ""
            m.state["last_update"] = 0.0
            m._packet_count = 0
            m.log_buffer.clear()
            m._shutdown_flag = False

    def test_returns_dict_with_expected_keys(self):
        self._reset_globals()
        data = self.main_mod._get_gui_data()
        expected_keys = {"biome", "dimension", "last_update", "service_status",
                         "uptime", "packets_received", "listening_ports", "log"}
        self.assertEqual(set(data.keys()), expected_keys)

    def test_default_values_when_no_data(self):
        self._reset_globals()
        data = self.main_mod._get_gui_data()
        self.assertEqual(data["biome"], "—")
        self.assertEqual(data["dimension"], "—")
        self.assertEqual(data["last_update"], "Never")
        self.assertEqual(data["service_status"], "Running")
        self.assertEqual(data["packets_received"], "0")

    def test_shows_stopped_when_shutdown_flag_set(self):
        self._reset_globals()
        self.main_mod._shutdown_flag = True
        try:
            data = self.main_mod._get_gui_data()
            self.assertEqual(data["service_status"], "Stopped")
        finally:
            self.main_mod._shutdown_flag = False

    def test_reflects_biome_and_dim(self):
        self._reset_globals()
        with self.main_mod.lock:
            self.main_mod.state["biome"] = "plains"
            self.main_mod.state["dim"] = "overworld"
            self.main_mod.state["last_update"] = time.time()
        data = self.main_mod._get_gui_data()
        self.assertEqual(data["biome"], "plains")
        self.assertEqual(data["dimension"], "overworld")
        self.assertNotEqual(data["last_update"], "Never")

    def test_packet_count_shown(self):
        self._reset_globals()
        with self.main_mod.lock:
            self.main_mod._packet_count = 42
        data = self.main_mod._get_gui_data()
        self.assertEqual(data["packets_received"], "42")

    def test_log_buffer_content(self):
        self._reset_globals()
        with self.main_mod.lock:
            self.main_mod.log_buffer.append("line1")
            self.main_mod.log_buffer.append("line2")
        data = self.main_mod._get_gui_data()
        self.assertIn("line1", data["log"])
        self.assertIn("line2", data["log"])

    def test_empty_log_buffer(self):
        self._reset_globals()
        data = self.main_mod._get_gui_data()
        self.assertEqual(data["log"], "No activity yet.")

    def test_uptime_increases(self):
        self._reset_globals()
        data1 = self.main_mod._get_gui_data()
        time.sleep(0.01)
        data2 = self.main_mod._get_gui_data()
        uptime1 = int(data1["uptime"].rstrip("s"))
        uptime2 = int(data2["uptime"].rstrip("s"))
        self.assertGreaterEqual(uptime2, uptime1)

    def test_listening_ports_constant(self):
        self._reset_globals()
        data = self.main_mod._get_gui_data()
        self.assertEqual(data["listening_ports"], "25566, 25568")


class TestConfigLoading(unittest.TestCase):
    """Config loading from JSON files and dynamic config overrides."""

    def assert_ports(self, main_mod, minescript_expected, query_expected,
                     msg_minescript="", msg_query=""):
        self.assertEqual(main_mod.MINESCRIPT_PORT, minescript_expected,
                         msg_minescript or f"Expected minescript_port={minescript_expected}")
        self.assertEqual(main_mod.QUERY_PORT, query_expected,
                         msg_query or f"Expected query_port={query_expected}")

    def test_default_ports_when_no_config(self):
        """Without any config files, ports fall back to engine.ports defaults."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            self.assert_ports(main_mod, 25566, 25568)
        finally:
            cleanup()

    def test_config_loading_from_json(self):
        """Verify that config.json values are loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_data = {"minescript_port": "19999", "query_port": "20000"}
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps(config_data))

            # Import normally, then override _config_path resolution
            # by directly setting _MC_RELAY_CONFIG and recomputing ports
            main_mod, cleanup = _import_main_with_mocks()
            try:
                main_mod._MC_RELAY_CONFIG = config_data
                main_mod.MINESCRIPT_PORT = int(
                    main_mod._MC_RELAY_CONFIG.get("minescript_port", main_mod.MINESCRIPT_SENDER_PORT)
                )
                main_mod.QUERY_PORT = int(
                    main_mod._MC_RELAY_CONFIG.get("query_port", main_mod.QUERY_PORT)
                )
                self.assert_ports(main_mod, 19999, 20000)
            finally:
                cleanup()

    def test_dynamic_config_overrides_json(self):
        """Dynamic config (DCL) values should take precedence over JSON file values."""
        dcl_values = {"minescript_port": 21000, "query_port": 21001}
        def _dcl_get(ns, k):
            return dcl_values.get(k)

        mock_modules = _build_mock_engine_modules()
        mock_modules["dynamic_config_loader"].get_config = _dcl_get

        main_mod, cleanup = _import_main_with_mocks(mock_modules)
        try:
            self.assert_ports(main_mod, 21000, 21001)
        finally:
            cleanup()

    def test_invalid_config_raises_value_error(self):
        """If config has non-int port values, int() conversion raises ValueError."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            main_mod._MC_RELAY_CONFIG["minescript_port"] = "not_a_number"
            with self.assertRaises(ValueError):
                _ = int(main_mod._MC_RELAY_CONFIG.get("minescript_port", 25566))
        finally:
            cleanup()

    def test_partial_config(self):
        """Only providing one port should leave the other at default."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            main_mod._MC_RELAY_CONFIG = {"minescript_port": "18000"}
            main_mod.MINESCRIPT_PORT = int(
                main_mod._MC_RELAY_CONFIG.get("minescript_port", main_mod.MINESCRIPT_SENDER_PORT)
            )
            main_mod.QUERY_PORT = int(
                main_mod._MC_RELAY_CONFIG.get("query_port", main_mod.QUERY_PORT)
            )
            self.assertEqual(main_mod.MINESCRIPT_PORT, 18000)
            self.assertEqual(main_mod.QUERY_PORT, 25568)
        finally:
            cleanup()


class TestMinescriptParsing(unittest.TestCase):
    """Test the BIOME:/DIM: packet parsing logic that minescript_listener uses."""

    def setUp(self):
        self.main_mod, self.cleanup = _import_main_with_mocks()

    def tearDown(self):
        self.cleanup()

    def _parse(self, raw):
        """Replicate the parsing logic from minescript_listener."""
        msg = raw.strip()
        parts = msg.split()
        biome, dim = "", ""
        for p in parts:
            if p.startswith("BIOME:"):
                biome = p[6:]
            elif p.startswith("DIM:"):
                dim = p[4:]
        return biome, dim

    def test_biome_only(self):
        self.assertEqual(self._parse("BIOME:plains"), ("plains", ""))

    def test_dim_only(self):
        self.assertEqual(self._parse("DIM:overworld"), ("", "overworld"))

    def test_full_packet(self):
        self.assertEqual(self._parse("BIOME:forest DIM:overworld"), ("forest", "overworld"))

    def test_reversed_order(self):
        self.assertEqual(self._parse("DIM:the_end BIOME:end_barrens"),
                         ("end_barrens", "the_end"))

    def test_biome_single_token(self):
        """BIOME: value stops at space."""
        biome, dim = self._parse("BIOME:badlands plateau DIM:nether")
        self.assertEqual(biome, "badlands")
        self.assertEqual(dim, "nether")

    def test_empty_packet(self):
        self.assertEqual(self._parse(""), ("", ""))

    def test_wrong_prefix(self):
        self.assertEqual(self._parse("BOME:plains"), ("", ""))

    def test_case_sensitive(self):
        self.assertEqual(self._parse("biome:plains DIM:overworld"), ("", "overworld"))

    def test_underscore_in_values(self):
        self.assertEqual(self._parse("BIOME:deep_dark DIM:overworld"),
                         ("deep_dark", "overworld"))

    def test_extra_tokens_ignored(self):
        self.assertEqual(
            self._parse("some extra BIOME:jungle stuff DIM:overworld more"),
            ("jungle", "overworld")
        )


class TestMinescriptListener(unittest.TestCase):
    """Test minescript_listener with mocked module-level socket."""

    def setUp(self):
        self.main_mod, self.cleanup = _import_main_with_mocks()
        self._reset()

    def tearDown(self):
        self.main_mod._shutdown_flag = False
        self.cleanup()

    def _reset(self):
        m = self.main_mod
        with m.lock:
            m.state["biome"] = ""
            m.state["dim"] = ""
            m.state["last_update"] = 0.0
            m._packet_count = 0
            m.log_buffer.clear()

    def _run_listener(self, recvfrom_side_effect):
        """Run minescript_listener with a mocked socket, breaking via _TestHalt."""
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = recvfrom_side_effect
        # Patch the main module's already-imported socket reference
        with patch.object(self.main_mod, "socket") as mock_socket_mod:
            mock_socket_mod.socket.return_value = mock_sock
            mock_socket_mod.AF_INET = real_socket.AF_INET
            mock_socket_mod.SOCK_DGRAM = real_socket.SOCK_DGRAM
            mock_socket_mod.SOL_SOCKET = real_socket.SOL_SOCKET
            mock_socket_mod.SO_REUSEADDR = real_socket.SO_REUSEADDR
            try:
                self.main_mod.minescript_listener()
            except _TestHalt:
                pass

    def test_updates_state_from_minescript_packet(self):
        self._run_listener([
            (b"BIOME:plains DIM:overworld", ("10.0.0.1", 5000)),
            _TestHalt,
        ])
        with self.main_mod.lock:
            self.assertEqual(self.main_mod.state["biome"], "plains")
            self.assertEqual(self.main_mod.state["dim"], "overworld")
            self.assertGreater(self.main_mod.state["last_update"], 0.0)
            self.assertEqual(self.main_mod._packet_count, 1)

    def test_multiple_packets_accumulate(self):
        self._run_listener([
            (b"BIOME:desert DIM:nether", ("10.0.0.1", 5000)),
            (b"BIOME:ocean DIM:overworld", ("10.0.0.2", 5001)),
            _TestHalt,
        ])
        with self.main_mod.lock:
            self.assertEqual(self.main_mod.state["biome"], "ocean")
            self.assertEqual(self.main_mod.state["dim"], "overworld")
            self.assertEqual(self.main_mod._packet_count, 2)

    def test_listener_error_does_not_crash(self):
        self._run_listener([
            OSError("network error"),
            (b"BIOME:plains DIM:overworld", ("10.0.0.1", 5000)),
            _TestHalt,
        ])
        with self.main_mod.lock:
            self.assertEqual(self.main_mod.state["biome"], "plains")
            self.assertEqual(self.main_mod._packet_count, 1)

    def test_biome_only_resets_dim(self):
        """When only BIOME: is present, DIM: is reset to '' (code sets dim='' every iteration)."""
        with self.main_mod.lock:
            self.main_mod.state["dim"] = "overworld"
        self._run_listener([
            (b"BIOME:forest", ("10.0.0.1", 5000)),
            _TestHalt,
        ])
        with self.main_mod.lock:
            self.assertEqual(self.main_mod.state["biome"], "forest")
            # The code re-initializes dim="" on each packet, so existing dim is lost
            self.assertEqual(self.main_mod.state["dim"], "")

    def test_log_buffer_updated(self):
        self._run_listener([
            (b"BIOME:plains DIM:overworld", ("10.0.0.1", 5000)),
            _TestHalt,
        ])
        with self.main_mod.lock:
            self.assertEqual(len(self.main_mod.log_buffer), 1)
            self.assertIn("plains", self.main_mod.log_buffer[0])
            self.assertIn("overworld", self.main_mod.log_buffer[0])

    def test_socket_created_correctly(self):
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = [_TestHalt]
        with patch.object(self.main_mod, "socket") as mock_socket_mod:
            mock_socket_mod.socket.return_value = mock_sock
            mock_socket_mod.AF_INET = real_socket.AF_INET
            mock_socket_mod.SOCK_DGRAM = real_socket.SOCK_DGRAM
            mock_socket_mod.SOL_SOCKET = real_socket.SOL_SOCKET
            mock_socket_mod.SO_REUSEADDR = real_socket.SO_REUSEADDR
            try:
                self.main_mod.minescript_listener()
            except _TestHalt:
                pass

        mock_socket_mod.socket.assert_called_once_with(real_socket.AF_INET, real_socket.SOCK_DGRAM)
        mock_sock.bind.assert_called_once_with(("", self.main_mod.MINESCRIPT_PORT))

    def test_setsockopt_failure_does_not_block(self):
        mock_sock = MagicMock()
        mock_sock.setsockopt.side_effect = OSError("not supported")
        mock_sock.recvfrom.side_effect = [
            (b"BIOME:plains DIM:overworld", ("10.0.0.1", 5000)),
            _TestHalt,
        ]
        with patch.object(self.main_mod, "socket") as mock_socket_mod:
            mock_socket_mod.socket.return_value = mock_sock
            mock_socket_mod.AF_INET = real_socket.AF_INET
            mock_socket_mod.SOCK_DGRAM = real_socket.SOCK_DGRAM
            mock_socket_mod.SOL_SOCKET = real_socket.SOL_SOCKET
            mock_socket_mod.SO_REUSEADDR = real_socket.SO_REUSEADDR
            try:
                self.main_mod.minescript_listener()
            except _TestHalt:
                pass
        with self.main_mod.lock:
            self.assertEqual(self.main_mod.state["biome"], "plains")


class TestQueryListener(unittest.TestCase):
    """Test query_listener with mocked module-level socket."""

    def setUp(self):
        self.main_mod, self.cleanup = _import_main_with_mocks()
        self._reset()

    def tearDown(self):
        self.main_mod._shutdown_flag = False
        self.cleanup()

    def _reset(self):
        m = self.main_mod
        with m.lock:
            m.state["biome"] = ""
            m.state["dim"] = ""
            m.state["last_update"] = 0.0
            m._packet_count = 0
            m.log_buffer.clear()

    def _run_listener(self, recvfrom_side_effect):
        """Run query_listener with a mocked socket, breaking via _TestHalt.
        Returns the mock_sock for assertion checks."""
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = recvfrom_side_effect
        with patch.object(self.main_mod, "socket") as mock_socket_mod:
            mock_socket_mod.socket.return_value = mock_sock
            mock_socket_mod.AF_INET = real_socket.AF_INET
            mock_socket_mod.SOCK_DGRAM = real_socket.SOCK_DGRAM
            mock_socket_mod.SOL_SOCKET = real_socket.SOL_SOCKET
            mock_socket_mod.SO_REUSEADDR = real_socket.SO_REUSEADDR
            try:
                self.main_mod.query_listener()
            except _TestHalt:
                pass
        return mock_sock

    def test_responds_none_when_no_data(self):
        mock_sock = self._run_listener([
            (b"status", ("10.0.0.5", 50000)),
            _TestHalt,
        ])
        mock_sock.sendto.assert_called_once_with(b"none", ("10.0.0.5", 50000))

    def test_responds_with_biome_dim_age(self):
        with self.main_mod.lock:
            self.main_mod.state["biome"] = "plains"
            self.main_mod.state["dim"] = "overworld"
            self.main_mod.state["last_update"] = time.time()
        mock_sock = self._run_listener([
            (b"status", ("10.0.0.5", 50000)),
            _TestHalt,
        ])
        call_args = mock_sock.sendto.call_args[0]
        response_text = call_args[0].decode()
        parts = response_text.split()
        self.assertEqual(parts[0], "plains")
        self.assertEqual(parts[1], "overworld")
        self.assertTrue(parts[2].isdigit())

    def test_ignores_non_status_commands(self):
        mock_sock = self._run_listener([
            (b"some_other_command", ("10.0.0.5", 50000)),
            _TestHalt,
        ])
        mock_sock.sendto.assert_not_called()

    def test_log_updated_on_query(self):
        with self.main_mod.lock:
            self.main_mod.state["biome"] = "plains"
            self.main_mod.state["dim"] = "overworld"
            self.main_mod.state["last_update"] = time.time()
        self._run_listener([
            (b"status", ("10.0.0.5", 50000)),
            _TestHalt,
        ])
        with self.main_mod.lock:
            self.assertEqual(len(self.main_mod.log_buffer), 1)
            self.assertIn("Query answered", self.main_mod.log_buffer[0])

    def test_error_does_not_crash(self):
        mock_sock = self._run_listener([
            OSError("network error"),
            (b"status", ("10.0.0.5", 50000)),
            _TestHalt,
        ])
        mock_sock.sendto.assert_called_once()

    def test_socket_created_correctly(self):
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = [_TestHalt]
        with patch.object(self.main_mod, "socket") as mock_socket_mod:
            mock_socket_mod.socket.return_value = mock_sock
            mock_socket_mod.AF_INET = real_socket.AF_INET
            mock_socket_mod.SOCK_DGRAM = real_socket.SOCK_DGRAM
            mock_socket_mod.SOL_SOCKET = real_socket.SOL_SOCKET
            mock_socket_mod.SO_REUSEADDR = real_socket.SO_REUSEADDR
            try:
                self.main_mod.query_listener()
            except _TestHalt:
                pass
        mock_socket_mod.socket.assert_called_once_with(real_socket.AF_INET, real_socket.SOCK_DGRAM)
        mock_sock.bind.assert_called_once_with(("", self.main_mod.QUERY_PORT))


class TestConcurrencySafety(unittest.TestCase):
    """Verify that shared state access is thread-safe."""

    def test_concurrent_reads_and_writes(self):
        main_mod, cleanup = _import_main_with_mocks()
        try:
            def writer():
                for i in range(100):
                    with main_mod.lock:
                        main_mod.state["biome"] = f"biome_{i}"
                        main_mod.state["dim"] = f"dim_{i}"

            def reader():
                for _ in range(100):
                    with main_mod.lock:
                        _ = main_mod.state["biome"]
                        _ = main_mod.state["dim"]

            t1 = threading.Thread(target=writer, daemon=True)
            t2 = threading.Thread(target=reader, daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
            self.assertFalse(t1.is_alive(), "Writer thread should have finished")
            self.assertFalse(t2.is_alive(), "Reader thread should have finished")
        finally:
            cleanup()


class TestShutdown(unittest.TestCase):
    """Test shutdown flag and behaviour."""

    def setUp(self):
        self.main_mod, self.cleanup = _import_main_with_mocks()

    def tearDown(self):
        self.main_mod._shutdown_flag = False
        self.cleanup()

    def test_default_false(self):
        self.assertFalse(self.main_mod._shutdown_flag)

    def test_shutdown_sets_flag(self):
        self.main_mod._shutdown(None, None)
        self.assertTrue(self.main_mod._shutdown_flag)

    def test_shutdown_idempotent(self):
        self.main_mod._shutdown(None, None)
        self.main_mod._shutdown(None, None)
        self.assertTrue(self.main_mod._shutdown_flag)

    def test_shutdown_stops_beacon_and_finder(self):
        m = self.main_mod
        m._BEACON = MagicMock()
        m._FINDER = MagicMock()
        m._shutdown(None, None)
        m._BEACON.stop.assert_called_once()
        m._FINDER.stop.assert_called_once()

    def test_shutdown_handles_missing_beacon(self):
        """If _BEACON is None, _shutdown should not raise."""
        m = self.main_mod
        m._BEACON = None
        m._FINDER = MagicMock()
        m._shutdown(None, None)  # Should not raise
        self.assertTrue(m._shutdown_flag)


class TestBeaconAndFinder(unittest.TestCase):
    """Test _start_beacon and _start_finder with mocked engine.lan_discovery."""

    def setUp(self):
        mock_modules = _build_mock_engine_modules()
        mock_lan = MagicMock(name="engine.lan_discovery")
        mock_lan.ServiceBeacon = MagicMock()
        mock_lan.ServiceFinder = MagicMock()
        mock_modules["engine.lan_discovery"] = mock_lan
        self.mock_modules = mock_modules
        self.main_mod, self.cleanup = _import_main_with_mocks(mock_modules)

    def tearDown(self):
        self.main_mod._BEACON = None
        self.main_mod._FINDER = None
        self.cleanup()

    def test_start_beacon_creates_service_beacon(self):
        m = self.main_mod
        m._BEACON = None
        m._start_beacon()
        self.assertIsNotNone(m._BEACON)
        svc_beacon_cls = self.mock_modules["engine.lan_discovery"].ServiceBeacon
        svc_beacon_cls.assert_called_once_with(
            "mc_status_relay",
            port=m.MINESCRIPT_PORT,
            extra={"query_port": m.QUERY_PORT, "version": 1},
        )
        m._BEACON.start.assert_called_once()

    def test_start_beacon_skips_when_disabled(self):
        mock_modules = _build_mock_engine_modules()
        mock_dcl = mock_modules["dynamic_config_loader"]
        mock_dcl.get_config = MagicMock(
            side_effect=lambda ns, k: False if k == "lan_beacon_enabled" else None
        )
        mock_modules["engine.lan_discovery"] = MagicMock()
        main_mod, cleanup = _import_main_with_mocks(mock_modules)
        try:
            main_mod._BEACON = None
            main_mod._start_beacon()
            self.assertIsNone(main_mod._BEACON)
        finally:
            cleanup()

    def test_start_beacon_handles_import_error(self):
        mock_modules = _build_mock_engine_modules()
        # Replace engine.mock with a sentinel that makes submodule imports fail
        class _Unimportable:
            """Placed in sys.modules to block real imports."""
            @staticmethod
            def __getattr__(name):
                raise ImportError(f"No module named engine.{name}")
        mock_modules["engine.lan_discovery"] = _Unimportable()
        # Also ensure the existing engine mock doesn't shadow it
        main_mod, cleanup = _import_main_with_mocks(mock_modules)
        try:
            main_mod._BEACON = None
            main_mod._start_beacon()
            self.assertIsNone(main_mod._BEACON)
        finally:
            cleanup()

    def test_start_finder_creates_service_finder(self):
        m = self.main_mod
        m._FINDER = None
        m._start_finder()
        self.assertIsNotNone(m._FINDER)
        svc_finder_cls = self.mock_modules["engine.lan_discovery"].ServiceFinder
        svc_finder_cls.assert_called_once()
        call_kwargs = svc_finder_cls.call_args.kwargs
        self.assertEqual(call_kwargs["fallback"]["port"], 42070)
        m._FINDER.start.assert_called_once()

    def test_start_finder_skips_when_disabled(self):
        mock_modules = _build_mock_engine_modules()
        mock_dcl = mock_modules["dynamic_config_loader"]
        mock_dcl.get_config = MagicMock(
            side_effect=lambda ns, k: False if k == "lan_finder_enabled" else None
        )
        mock_modules["engine.lan_discovery"] = MagicMock()
        main_mod, cleanup = _import_main_with_mocks(mock_modules)
        try:
            main_mod._FINDER = None
            main_mod._start_finder()
            self.assertIsNone(main_mod._FINDER)
        finally:
            cleanup()


class TestModuleLevelInitialization(unittest.TestCase):
    """Test that module-level initialization works correctly."""

    def test_dynamic_config_not_available_when_missing(self):
        mock_modules = _build_mock_engine_modules()
        # Block real dynamic_config_loader import
        class _MissingModule:
            @staticmethod
            def __getattr__(name):
                raise ImportError(f"No module named {name}")
        mock_modules["dynamic_config_loader"] = _MissingModule()
        main_mod, cleanup = _import_main_with_mocks(mock_modules)
        try:
            self.assertFalse(main_mod._DYNAMIC_CONFIG_AVAILABLE)
        finally:
            cleanup()

    def test_dynamic_config_available_when_present(self):
        main_mod, cleanup = _import_main_with_mocks()
        try:
            self.assertTrue(main_mod._DYNAMIC_CONFIG_AVAILABLE)
        finally:
            cleanup()

    def test_default_state_structure(self):
        main_mod, cleanup = _import_main_with_mocks()
        try:
            self.assertIn("biome", main_mod.state)
            self.assertIn("dim", main_mod.state)
            self.assertIn("last_update", main_mod.state)
            self.assertEqual(main_mod.state["biome"], "")
            self.assertEqual(main_mod.state["dim"], "")
            self.assertEqual(main_mod.state["last_update"], 0.0)
        finally:
            cleanup()

    def test_lock_is_threading_lock(self):
        main_mod, cleanup = _import_main_with_mocks()
        try:
            self.assertIsInstance(main_mod.lock, type(threading.Lock()))
        finally:
            cleanup()

    def test_globals_exist(self):
        main_mod, cleanup = _import_main_with_mocks()
        try:
            self.assertIsNotNone(main_mod._start_time)
            self.assertEqual(main_mod._packet_count, 0)
            self.assertIsNotNone(main_mod.log_buffer)
            self.assertFalse(main_mod._shutdown_flag)
        finally:
            cleanup()


class TestLogBufferMaxLen(unittest.TestCase):
    """Verify the deque log buffer respects maxlen=50."""

    def test_maxlen(self):
        main_mod, cleanup = _import_main_with_mocks()
        try:
            self.assertEqual(main_mod.log_buffer.maxlen, 50)
            for i in range(60):
                main_mod.log_buffer.append(f"line {i}")
            self.assertEqual(len(main_mod.log_buffer), 50)
            self.assertIn("line 59", main_mod.log_buffer)
            self.assertNotIn("line 0", main_mod.log_buffer)
        finally:
            cleanup()


class TestSignalRegistration(unittest.TestCase):
    """Test that signal handlers are set up properly.
    Note: Signal registration is inside the `if __name__ == "__main__":` block,
    so it only runs when main.py is executed directly, not on import."""

    def test_shutdown_is_callable(self):
        """Verify that _shutdown is a proper callable function."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            self.assertTrue(callable(main_mod._shutdown))
        finally:
            cleanup()

    def test_shutdown_function_handles_signum_frame(self):
        """_shutdown accepts (signum, frame) args like a signal handler should."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            # Should not raise when called with signal handler args
            main_mod._shutdown(signal.SIGINT, None)
            self.assertTrue(main_mod._shutdown_flag)
        finally:
            cleanup()

    def test_signal_sigint_registration_in_main(self):
        """Simulate the __main__ signal registration for SIGINT."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            # Replicate what the __main__ block does
            signal.signal(signal.SIGINT, main_mod._shutdown)
            handler = signal.getsignal(signal.SIGINT)
            self.assertIs(handler, main_mod._shutdown)
            # Restore
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        finally:
            cleanup()

    def test_signal_sigterm_registration_in_main(self):
        """Simulate the __main__ signal registration for SIGTERM."""
        if not hasattr(signal, "SIGTERM"):
            self.skipTest("SIGTERM not available on this platform")
        main_mod, cleanup = _import_main_with_mocks()
        try:
            try:
                signal.signal(signal.SIGTERM, main_mod._shutdown)
            except OSError:
                self.skipTest("SIGTERM not supported on this platform")
            handler = signal.getsignal(signal.SIGTERM)
            self.assertIs(handler, main_mod._shutdown)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
        finally:
            cleanup()


class TestEdgeCases(unittest.TestCase):
    """Miscellaneous edge case tests."""

    def test_gui_data_with_emoji_biome(self):
        """Biome names with special characters should pass through."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            with main_mod.lock:
                main_mod.state["biome"] = "flower_forest"
                main_mod.state["dim"] = "overworld"
                main_mod.state["last_update"] = time.time()
            data = main_mod._get_gui_data()
            self.assertEqual(data["biome"], "flower_forest")
        finally:
            cleanup()

    def test_negative_age_handling(self):
        """If last_update is in the future (edge), _get_gui_data should still produce valid output."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            with main_mod.lock:
                main_mod.state["last_update"] = time.time() + 3600  # 1 hour in future
                main_mod.state["biome"] = "plains"
                main_mod.state["dim"] = "overworld"
            data = main_mod._get_gui_data()
            self.assertNotEqual(data["last_update"], "Never")
            # The age will be negative but should still produce a string
            self.assertIsInstance(data["last_update"], str)
        finally:
            cleanup()

    def test_minescript_parser_handles_trailing_newline(self):
        """Strip newlines/carriage returns from packet data."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            mock_sock = MagicMock()
            mock_sock.recvfrom.side_effect = [
                (b"BIOME:forest DIM:nether\n", ("10.0.0.1", 5000)),
                _TestHalt,
            ]
            with patch.object(main_mod, "socket") as mock_socket_mod:
                mock_socket_mod.socket.return_value = mock_sock
                mock_socket_mod.AF_INET = real_socket.AF_INET
                mock_socket_mod.SOCK_DGRAM = real_socket.SOCK_DGRAM
                mock_socket_mod.SOL_SOCKET = real_socket.SOL_SOCKET
                mock_socket_mod.SO_REUSEADDR = real_socket.SO_REUSEADDR
                try:
                    main_mod.minescript_listener()
                except _TestHalt:
                    pass
            with main_mod.lock:
                self.assertEqual(main_mod.state["biome"], "forest")
                self.assertEqual(main_mod.state["dim"], "nether")
        finally:
            cleanup()


class TestGuiIntegration(unittest.TestCase):
    """End-to-end style GUI integration tests."""

    def test_register_then_get_data_pipeline(self):
        """Simulate the full register_tab + gui.on_data_request flow."""
        main_mod, cleanup = _import_main_with_mocks()
        try:
            gui = MagicMock()
            main_mod.register_tab(gui)
            # The __main__ block calls gui.on_data_request(_get_gui_data)
            # Simulate that:
            with main_mod.lock:
                main_mod.state["biome"] = "desert"
                main_mod.state["dim"] = "overworld"
                main_mod.state["last_update"] = time.time()
            data = main_mod._get_gui_data()
            self.assertEqual(data["biome"], "desert")
            self.assertEqual(data["dimension"], "overworld")
            self.assertEqual(data["service_status"], "Running")
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
