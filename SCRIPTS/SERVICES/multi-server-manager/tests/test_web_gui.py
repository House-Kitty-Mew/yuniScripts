"""
Unit tests for the Web GUI (Phase 5).

Tests the async HTTP server, route matching, API dispatch,
SPA serving, and graceful degradation.

Run with:
    python3 -m unittest tests.test_web_gui -v
"""

import asyncio
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

SCRIPT_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "engine"))

from engine.web_gui import HTTPServer, WebGUI, SPA_HTML


# ═════════════════════════════════════════════════════════════════════════════
# Mock AdminCLI
# ═════════════════════════════════════════════════════════════════════════════

class MockCLIResult:
    def __init__(self, data):
        self._data = data
        self.data = data
    def to_dict(self):
        return self._data

class MockAdminCLI:
    def __init__(self):
        self.servers = [
            {"server_id": "test1", "display_name": "Test 1", "state": "running", "plugin_count": 2},
            {"server_id": "test2", "display_name": "Test 2", "state": "stopped", "plugin_count": 0},
        ]
        self.plugins = [
            {"name": "auction_house", "version": "1.0.0", "description": "Auction House"},
            {"name": "economy_bridge", "version": "1.0.0", "description": "Economy Bridge"},
        ]
        self.events = [{"timestamp": "2026-01-01", "event_type": "start", "server_id": "test1", "message": "Started"}]
        self.config = {"port": 8200, "log_level": "INFO"}
    
    async def global_status(self):
        return MockCLIResult({
            "servers_total": 2, "servers_healthy": 1,
            "plugins_total": 2, "plugins_registered": 2,
            "databases_total": 3, "databases_healthy": 3,
            "events_total": 1, "last_event_time": "2026-01-01T00:00:00",
            "plugins_list": [
                {"name": "auction_house", "servers": 2, "health": "healthy"},
                {"name": "economy_bridge", "servers": 1, "health": "healthy"},
            ],
        })
    
    async def list_servers(self):
        return MockCLIResult(self.servers)
    
    async def start_server(self, server_id):
        for s in self.servers:
            if s["server_id"] == server_id:
                s["state"] = "running"
                return MockCLIResult({"ok": True, "server_id": server_id, "message": "started"})
        return MockCLIResult({"ok": False, "error": "Not found"})
    
    async def stop_server(self, server_id):
        for s in self.servers:
            if s["server_id"] == server_id:
                s["state"] = "stopped"
                return MockCLIResult({"ok": True, "server_id": server_id, "message": "stopped"})
        return MockCLIResult({"ok": False, "error": "Not found"})
    
    async def restart_server(self, server_id):
        for s in self.servers:
            if s["server_id"] == server_id:
                s["state"] = "running"
                return MockCLIResult({"ok": True, "server_id": server_id, "message": "restarted"})
        return MockCLIResult({"ok": False, "error": "Not found"})
    
    async def server_info(self, server_id):
        for s in self.servers:
            if s["server_id"] == server_id:
                return MockCLIResult(s)
        return MockCLIResult({"error": "Not found"})
    
    async def list_plugins(self):
        return MockCLIResult(self.plugins)
    
    async def plugin_info(self, name):
        for p in self.plugins:
            if p["name"] == name:
                return MockCLIResult(p)
        return MockCLIResult({"error": "Not found"})
    
    async def list_databases(self):
        return MockCLIResult([
            {"subsystem": "ah", "server_id": "test1", "size": "32 KB", "table_count": 4, "is_healthy": True},
        ])
    
    async def show_database(self, subsystem, server_id):
        return MockCLIResult({"subsystem": subsystem, "server_id": server_id, "healthy": True})
    
    async def check_database_health(self):
        return MockCLIResult({"healthy": True, "total": 2, "unhealthy": []})
    
    async def show_events(self, limit=20):
        return MockCLIResult(self.events[:limit])
    
    async def show_config(self):
        return MockCLIResult(self.config)
    
    async def reload_config(self):
        return MockCLIResult({"ok": True, "message": "Config reloaded"})
    
    async def list_instances(self):
        return MockCLIResult([{"plugin": "ah", "server": "test1", "state": "running"}])


# ═════════════════════════════════════════════════════════════════════════════
# Test Base With Background Server
# ═════════════════════════════════════════════════════════════════════════════

class ServerTestBase(unittest.TestCase):
    """Base class that starts a Web GUI server in a background thread."""
    
    gui_class = WebGUI
    
    @classmethod
    def setUpClass(cls):
        cls.cli = MockAdminCLI()
    
    def setUp(self):
        self.gui = self.gui_class("127.0.0.1", 0)
        self._port = None
        self._stop = False
        self._loop = asyncio.new_event_loop()
        
        def _run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self.gui.start(self.cli))
            sock = self.gui.server._server.sockets[0]
            self._port = sock.getsockname()[1]
            while not self._stop:
                self._loop.run_until_complete(asyncio.sleep(0.1))
            self._loop.run_until_complete(self.gui.stop())
            self._loop.close()
        
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        for _ in range(50):
            if self._port is not None:
                break
            time.sleep(0.05)
        self.assertIsNotNone(self._port, "Server failed to start")
    
    def tearDown(self):
        self._stop = True
        if self._thread.is_alive():
            self._thread.join(timeout=5)
    
    def fetch(self, path, method="GET"):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            return response.status, body
        finally:
            conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# HTTPServer Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestHTTPServer(unittest.TestCase):
    """Tests for the core HTTPServer class."""
    
    def setUp(self):
        self.server = HTTPServer("127.0.0.1", 0)
    
    def test_route_registration(self):
        @self.server.route("GET", "/test")
        def handler(method, path, params, query, body):
            return {"ok": True}
        self.assertIn("GET:/test", self.server._routes)
    
    def test_route_with_path_params(self):
        @self.server.route("GET", "/api/servers/{id}")
        def handler(method, path, params, query, body):
            return {"id": params.get("id")}
        h, p = self.server._find_route("GET", "/api/servers/my_server")
        self.assertIsNotNone(h)
        self.assertEqual(p, {"id": "my_server"})
    
    def test_route_with_multi_params(self):
        @self.server.route("GET", "/api/databases/{subsys}/{sid}")
        def handler(method, path, params, query, body):
            return {"subsys": params["subsys"], "sid": params["sid"]}
        h, p = self.server._find_route("GET", "/api/databases/ah/test1")
        self.assertIsNotNone(h)
        self.assertEqual(p, {"subsys": "ah", "sid": "test1"})
    
    def test_route_not_found(self):
        h, p = self.server._find_route("GET", "/nonexistent")
        self.assertIsNone(h)
    
    def test_route_wrong_method(self):
        @self.server.route("POST", "/api/test")
        def handler(method, path, params, query, body):
            return {"ok": True}
        h, p = self.server._find_route("GET", "/api/test")
        self.assertIsNone(h)
    
    def test_route_different_length(self):
        @self.server.route("GET", "/api/servers/{id}/start")
        def handler(method, path, params, query, body):
            return {"ok": True}
        h, _ = self.server._find_route("GET", "/api/servers/test1")
        self.assertIsNone(h)
    
    def test_route_url_encoded_param(self):
        @self.server.route("GET", "/api/servers/{id}")
        def handler(method, path, params, query, body):
            return {"id": params.get("id")}
        h, p = self.server._find_route("GET", "/api/servers/test%20server")
        self.assertIsNotNone(h)
        self.assertEqual(p, {"id": "test server"})


# ═════════════════════════════════════════════════════════════════════════════
# WebGUI Tests (via background server)
# ═════════════════════════════════════════════════════════════════════════════

class TestWebGUI(ServerTestBase):
    """Tests for the WebGUI class using a background HTTP server."""
    
    # ── SPA Frontend ────────────────────────────────────────────────
    
    def test_spa_html_contains_required_elements(self):
        self.assertIn("Multi-Server Manager", SPA_HTML)
        self.assertIn("data-tab=", SPA_HTML)
        self.assertIn("renderers", SPA_HTML)
        self.assertIn("api", SPA_HTML)
    
    def test_get_root_serves_spa(self):
        status, body = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn("Multi-Server Manager", body)
        self.assertIn("<!DOCTYPE html>", body)
    
    # ── API Endpoints ───────────────────────────────────────────────
    
    def test_api_status(self):
        status, body = self.fetch("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertIn("data", data)
        self.assertIn("servers_total", data["data"])
    
    def test_api_servers(self):
        status, body = self.fetch("/api/servers")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["data"]), 2)
    
    def test_api_server_start(self):
        status, body = self.fetch("/api/servers/test2/start", method="POST")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
    
    def test_api_server_stop(self):
        status, body = self.fetch("/api/servers/test1/stop", method="POST")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
    
    def test_api_server_restart(self):
        status, body = self.fetch("/api/servers/test1/restart", method="POST")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
    
    def test_api_server_info(self):
        status, body = self.fetch("/api/servers/test1")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["server_id"], "test1")
    
    def test_api_server_not_found(self):
        status, body = self.fetch("/api/servers/nonexistent")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
    
    def test_api_plugins(self):
        status, body = self.fetch("/api/plugins")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["data"]), 2)
    
    def test_api_plugin_info(self):
        status, body = self.fetch("/api/plugins/auction_house")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["name"], "auction_house")
    
    def test_api_databases(self):
        status, body = self.fetch("/api/databases")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["data"], list)
    
    def test_api_logs(self):
        status, body = self.fetch("/api/logs")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
    
    def test_api_config(self):
        status, body = self.fetch("/api/config")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["port"], 8200)
    
    def test_api_config_reload(self):
        status, body = self.fetch("/api/config/reload", method="POST")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
    
    def test_api_instances(self):
        status, body = self.fetch("/api/instances")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
    
    def test_api_404(self):
        status, body = self.fetch("/api/nonexistent")
        self.assertEqual(status, 404)
        data = json.loads(body)
        self.assertFalse(data["ok"])
    
    # ── CORS ────────────────────────────────────────────────────────
    
    def test_cors_headers(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5)
        try:
            conn.request("GET", "/api/status")
            response = conn.getresponse()
            response.read()
            headers = {k.lower(): v for k, v in response.getheaders()}
            self.assertIn("access-control-allow-origin", headers)
            self.assertEqual(headers["access-control-allow-origin"], "*")
        finally:
            conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# Server Start/Stop Tests (standalone, not using background server)
# ═════════════════════════════════════════════════════════════════════════════

class TestServerLifecycle(unittest.TestCase):
    """Tests for server start/stop and graceful degradation."""
    
    def test_http_server_start_stop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            server = HTTPServer("127.0.0.1", 0)
            started = loop.run_until_complete(server.start())
            self.assertTrue(started)
            loop.run_until_complete(server.stop())
        finally:
            loop.close()
    
    def test_http_server_port_taken(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            s1 = HTTPServer("127.0.0.1", 0)
            started = loop.run_until_complete(s1.start())
            self.assertTrue(started)
            port = s1._server.sockets[0].getsockname()[1]
            
            s2 = HTTPServer("127.0.0.1", port)
            started = loop.run_until_complete(s2.start())
            self.assertFalse(started)  # Graceful degradation
            
            loop.run_until_complete(s1.stop())
        finally:
            loop.close()
    
    def test_gui_start_stop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            gui = WebGUI("127.0.0.1", 0)
            started = loop.run_until_complete(gui.start(MockAdminCLI()))
            self.assertTrue(started)
            loop.run_until_complete(gui.stop())
        finally:
            loop.close()
    
    def test_gui_graceful_degradation(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            g1 = WebGUI("127.0.0.1", 0)
            started = loop.run_until_complete(g1.start(MockAdminCLI()))
            self.assertTrue(started)
            port = g1.server._server.sockets[0].getsockname()[1]
            
            g2 = WebGUI("127.0.0.1", port)
            started = loop.run_until_complete(g2.start(MockAdminCLI()))
            self.assertFalse(started)  # Graceful degradation
            
            loop.run_until_complete(g1.stop())
        finally:
            loop.close()


# ═════════════════════════════════════════════════════════════════════════════
# Default Responses (degraded mode)
# ═════════════════════════════════════════════════════════════════════════════

class TestDefaultResponses(unittest.TestCase):
    """Tests for default/degraded responses."""
    
    def setUp(self):
        self.gui = WebGUI("127.0.0.1", 0)
    
    def test_defaults(self):
        methods = ["global_status", "list_servers", "list_plugins", "list_databases",
                   "show_config", "show_events", "list_instances", "check_database_health"]
        for m in methods:
            result = self.gui._default_response(m)
            self.assertIsNotNone(result, f"Method {m} returned None")
    
    def test_unknown_default(self):
        result = self.gui._default_response("unknown")
        self.assertEqual(result, {"message": "Not available"})


# ═════════════════════════════════════════════════════════════════════════════
# Degraded Mode Integration (no AdminCLI)
# ═════════════════════════════════════════════════════════════════════════════

class TestDegradedMode(ServerTestBase):
    """Tests that WebGUI works without AdminCLI (degraded mode)."""
    
    gui_class = WebGUI
    
    @classmethod
    def setUpClass(cls):
        cls.cli = None  # No AdminCLI = degraded mode
    
    def test_degraded_all_endpoints_return_ok(self):
        endpoints = [
            "/api/status", "/api/servers", "/api/plugins",
            "/api/databases", "/api/logs", "/api/config",
            "/api/instances", "/api/database-health",
        ]
        for ep in endpoints:
            status, body = self.fetch(ep)
            self.assertEqual(status, 200, f"Endpoint {ep} returned {status}")
            data = json.loads(body)
            self.assertTrue(data["ok"], f"Endpoint {ep} not ok: {data}")
            self.assertTrue(data.get("degraded", False),
                          f"Endpoint {ep} not marked degraded")


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
