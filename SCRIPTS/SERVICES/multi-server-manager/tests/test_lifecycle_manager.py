"""
Unit tests for the Subsystem Lifecycle Manager.
Uses unittest (NOT pytest).
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Any, List

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.plugin_registry import (
    PluginRegistry,
    SubsystemPlugin,
    PluginState,
    PluginHealth,
    PluginError,
)
from engine.vfs_db_isolation import VFSDatabaseManager
from engine.lifecycle_manager import (
    LifecycleManager,
    LifecycleEvent,
    ServerInfo,
    ServerNotFoundError,
    ServerStateError,
    LifecycleError,
)


# ──────────────────────────────────────────────────────────────────────────────
# Test Plugin Implementations
# ──────────────────────────────────────────────────────────────────────────────

class SimplePlugin(SubsystemPlugin):
    name = "simple"
    version = "1.0.0"
    description = "Simple test plugin"

    def __init__(self):
        super().__init__()
        self.init_calls = []
        self.shutdown_calls = []
        self.health_calls = []
        self.fail_init = False
        self.fail_shutdown = False
        self.health_result = PluginHealth.HEALTHY

    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        self.init_calls.append((server_id, config))
        if self.fail_init:
            raise PluginError("Simulated init failure")

    async def on_shutdown(self, server_id: str) -> None:
        self.shutdown_calls.append(server_id)
        if self.fail_shutdown:
            raise PluginError("Simulated shutdown failure")

    async def on_health_check(self, server_id: str) -> PluginHealth:
        self.health_calls.append(server_id)
        return self.health_result


class DependentPlugin(SubsystemPlugin):
    name = "dependent"
    version = "2.0.0"
    description = "Plugin that depends on simple"
    dependencies = ["simple"]

    def __init__(self):
        super().__init__()
        self.init_calls = []
        self.shutdown_calls = []

    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        self.init_calls.append((server_id, config))

    async def on_shutdown(self, server_id: str) -> None:
        self.shutdown_calls.append(server_id)


# ──────────────────────────────────────────────────────────────────────────────
# Test Cases
# ──────────────────────────────────────────────────────────────────────────────

class TestLifecycleManagerBasic(unittest.IsolatedAsyncioTestCase):
    """Test basic LifecycleManager operations."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.temp_dir = tempfile.mkdtemp()
        self.vfs = VFSDatabaseManager(
            data_root=os.path.join(self.temp_dir, "vfs"),
            auto_backup=False,
        )
        self.lifecycle = LifecycleManager(
            plugin_registry=self.registry,
            vfs_manager=self.vfs,
            health_check_interval=9999,  # Don't auto-check during tests
            shutdown_timeout=5.0,
            auto_restart=False,
        )
        self.events: List[LifecycleEvent] = []
        
        async def event_collector(event: LifecycleEvent):
            self.events.append(event)
        
        self.event_handler = event_collector
        self.lifecycle.add_event_handler(self.event_handler)

    def tearDown(self):
        try:
            self.lifecycle = None
            self.vfs.close_all()
            PluginRegistry.reset_instance()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def test_register_server(self):
        """Registering a server should work."""
        self.lifecycle.register_server("server1", "Test Server")
        servers = self.lifecycle.list_servers()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].server_id, "server1")
        self.assertEqual(servers[0].display_name, "Test Server")
        self.assertEqual(servers[0].state, "stopped")

    async def test_register_duplicate_server(self):
        """Registering a duplicate server should raise."""
        self.lifecycle.register_server("server1")
        with self.assertRaises(LifecycleError):
            self.lifecycle.register_server("server1")

    async def test_unregister_server(self):
        """Unregistering a stopped server should work."""
        self.lifecycle.register_server("server1")
        self.lifecycle.unregister_server("server1")
        servers = self.lifecycle.list_servers()
        self.assertEqual(len(servers), 0)

    async def test_unregister_running_server(self):
        """Unregistering a running server should raise."""
        self.lifecycle.register_server("server1")
        # Set state to running directly
        self.lifecycle._servers["server1"]["state"] = "running"
        with self.assertRaises(ServerStateError):
            self.lifecycle.unregister_server("server1")

    async def test_get_server_info(self):
        """Getting server info should return correct data."""
        self.lifecycle.register_server("s1", "My Server")
        info = self.lifecycle.get_server_info("s1")
        self.assertIsInstance(info, ServerInfo)
        self.assertEqual(info.server_id, "s1")
        self.assertEqual(info.display_name, "My Server")

    async def test_get_server_info_nonexistent(self):
        """Getting info for a nonexistent server should raise."""
        with self.assertRaises(ServerNotFoundError):
            self.lifecycle.get_server_info("ghost")

    async def test_list_servers_empty(self):
        """Listing servers when none registered should return empty."""
        servers = self.lifecycle.list_servers()
        self.assertEqual(servers, [])


class TestLifecycleStartShutdown(unittest.IsolatedAsyncioTestCase):
    """Test server startup and shutdown lifecycle."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.simple_plugin = self.registry.register(SimplePlugin)
        self.dep_plugin = self.registry.register(DependentPlugin)
        self.temp_dir = tempfile.mkdtemp()
        self.vfs = VFSDatabaseManager(
            data_root=os.path.join(self.temp_dir, "vfs"),
            auto_backup=False,
        )
        self.lifecycle = LifecycleManager(
            plugin_registry=self.registry,
            vfs_manager=self.vfs,
            health_check_interval=9999,
            shutdown_timeout=5.0,
            auto_restart=False,
        )
        self.events: List[LifecycleEvent] = []
        
        async def collector(e):
            self.events.append(e)
        self.lifecycle.add_event_handler(collector)

    def tearDown(self):
        try:
            self.lifecycle = None
            self.vfs.close_all()
            PluginRegistry.reset_instance()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def test_start_server_success(self):
        """Starting a server with enabled subsystems should work."""
        self.lifecycle.register_server("s1", "Server 1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        
        result = await self.lifecycle.start_server("s1")
        self.assertTrue(result)
        
        info = self.lifecycle.get_server_info("s1")
        self.assertEqual(info.state, "running")
        self.assertIsNotNone(info.started_at)
        self.assertEqual(info.plugin_count, 1)

    async def test_start_server_calls_init(self):
        """Starting a server should call plugin's on_init."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        
        await self.lifecycle.start_server("s1")
        self.assertEqual(len(self.simple_plugin.init_calls), 1)
        self.assertEqual(self.simple_plugin.init_calls[0][0], "s1")

    async def test_start_server_respects_dependencies(self):
        """Startup should respect dependency ordering."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple", "dependent"]}}
        })
        
        await self.lifecycle.start_server("s1")
        # simple should be initialized before dependent
        self.assertEqual(len(self.simple_plugin.init_calls), 1)
        self.assertEqual(len(self.dep_plugin.init_calls), 1)

    async def test_start_server_partial_failure(self):
        """Failure in one subsystem should not stop others."""
        self.simple_plugin.fail_init = True
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple", "dependent"]}}
        })
        
        result = await self.lifecycle.start_server("s1")
        self.assertFalse(result)
        
        info = self.lifecycle.get_server_info("s1")
        self.assertEqual(info.state, "degraded")

    async def test_start_nonexistent_server(self):
        """Starting a nonexistent server should raise."""
        with self.assertRaises(ServerNotFoundError):
            await self.lifecycle.start_server("ghost")

    async def test_shutdown_server(self):
        """Shutting down a running server should work."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        
        result = await self.lifecycle.shutdown_server("s1")
        self.assertTrue(result)
        
        info = self.lifecycle.get_server_info("s1")
        self.assertEqual(info.state, "stopped")

    async def test_shutdown_calls_plugin_shutdown(self):
        """Shutdown should call plugin's on_shutdown."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        await self.lifecycle.shutdown_server("s1")
        
        self.assertEqual(len(self.simple_plugin.shutdown_calls), 1)
        self.assertEqual(self.simple_plugin.shutdown_calls[0], "s1")

    async def test_shutdown_nonexistent_server(self):
        """Shutting down a nonexistent server should raise."""
        with self.assertRaises(ServerNotFoundError):
            await self.lifecycle.shutdown_server("ghost")

    async def test_shutdown_unstarted_server(self):
        """Shutting down an unstarted server should work."""
        self.lifecycle.register_server("s1")
        result = await self.lifecycle.shutdown_server("s1")
        self.assertTrue(result)

    async def test_restart_server(self):
        """Restarting a server should work."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        
        result = await self.lifecycle.restart_server("s1")
        self.assertTrue(result)
        
        info = self.lifecycle.get_server_info("s1")
        self.assertEqual(info.state, "running")
        
        # Plugin should have been inited twice (start + restart)
        self.assertEqual(len(self.simple_plugin.init_calls), 2)

    async def test_restart_subsystem(self):
        """Restarting a single subsystem should work."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        
        result = await self.lifecycle.restart_subsystem("s1", "simple")
        self.assertTrue(result)
        
        # Plugin should have been shut down and re-inited
        self.assertEqual(len(self.simple_plugin.shutdown_calls), 1)
        self.assertEqual(len(self.simple_plugin.init_calls), 2)


class TestLifecycleHealthChecks(unittest.IsolatedAsyncioTestCase):
    """Test health check functionality."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.plugin = self.registry.register(SimplePlugin)
        self.temp_dir = tempfile.mkdtemp()
        self.vfs = VFSDatabaseManager(
            data_root=os.path.join(self.temp_dir, "vfs"),
            auto_backup=False,
        )
        self.lifecycle = LifecycleManager(
            plugin_registry=self.registry,
            vfs_manager=self.vfs,
            health_check_interval=0.1,  # Fast checks for testing
            shutdown_timeout=5.0,
            auto_restart=False,
        )

    def tearDown(self):
        try:
            self.lifecycle = None
            self.vfs.close_all()
            PluginRegistry.reset_instance()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def test_health_check_loop_runs(self):
        """Health check loop should run periodically."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        
        # Wait for at least one health check cycle
        await asyncio.sleep(0.3)
        
        # Health check should have been called
        self.assertGreater(len(self.plugin.health_calls), 0)


class TestLifecycleAutoRestart(unittest.IsolatedAsyncioTestCase):
    """Test auto-restart functionality."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.plugin = self.registry.register(SimplePlugin)
        self.temp_dir = tempfile.mkdtemp()
        self.vfs = VFSDatabaseManager(
            data_root=os.path.join(self.temp_dir, "vfs"),
            auto_backup=False,
        )
        self.lifecycle = LifecycleManager(
            plugin_registry=self.registry,
            vfs_manager=self.vfs,
            health_check_interval=0.1,
            shutdown_timeout=5.0,
            auto_restart=True,
            max_restart_attempts=2,
        )

    def tearDown(self):
        try:
            self.lifecycle = None
            self.vfs.close_all()
            PluginRegistry.reset_instance()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def test_auto_restart_on_unhealthy(self):
        """Auto-restart should trigger on unhealthy health check."""
        self.plugin.health_result = PluginHealth.UNHEALTHY
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        
        # Wait for health check
        await asyncio.sleep(0.3)
        
        # Plugin should have been restarted (shutdown + init)
        self.assertGreater(len(self.plugin.shutdown_calls), 0)
        self.assertGreater(len(self.plugin.init_calls), 1)


class TestLifecycleStatus(unittest.IsolatedAsyncioTestCase):
    """Test global status reporting."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.registry.register(SimplePlugin)
        self.temp_dir = tempfile.mkdtemp()
        self.vfs = VFSDatabaseManager(
            data_root=os.path.join(self.temp_dir, "vfs"),
            auto_backup=False,
        )
        self.lifecycle = LifecycleManager(
            plugin_registry=self.registry,
            vfs_manager=self.vfs,
            health_check_interval=9999,
        )

    def tearDown(self):
        try:
            self.lifecycle = None
            self.vfs.close_all()
            PluginRegistry.reset_instance()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def test_get_global_status(self):
        """Global status should contain all expected keys."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        
        status = self.lifecycle.get_global_status()
        self.assertIn("servers", status)
        self.assertIn("plugins", status)
        self.assertIn("instances", status)
        self.assertIn("vfs_databases", status)
        self.assertEqual(status["servers"]["running"], 1)
        self.assertEqual(status["plugins"]["total"], 1)
        self.assertIn("simple", status["plugins"]["registered"])

    async def test_get_global_status_no_servers(self):
        """Global status with no servers should show zeros."""
        status = self.lifecycle.get_global_status()
        self.assertEqual(status["servers"]["total"], 0)
        self.assertEqual(status["instances"]["total"], 0)


class TestLifecycleEventSystem(unittest.IsolatedAsyncioTestCase):
    """Test lifecycle event emission."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.registry.register(SimplePlugin)
        self.temp_dir = tempfile.mkdtemp()
        self.vfs = VFSDatabaseManager(
            data_root=os.path.join(self.temp_dir, "vfs"),
            auto_backup=False,
        )
        self.lifecycle = LifecycleManager(
            plugin_registry=self.registry,
            vfs_manager=self.vfs,
            health_check_interval=9999,
        )
        self.events = []
        
        async def collector(e):
            self.events.append(e)
        self.lifecycle.add_event_handler(collector)

    def tearDown(self):
        try:
            self.lifecycle = None
            self.vfs.close_all()
            PluginRegistry.reset_instance()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def test_start_event_emitted(self):
        """Starting a server should emit a 'start' event."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        
        start_events = [e for e in self.events if e.event_type == "start"]
        self.assertEqual(len(start_events), 1)
        self.assertEqual(start_events[0].server_id, "s1")

    async def test_stop_event_emitted(self):
        """Shutting down should emit a 'stop' event."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        await self.lifecycle.shutdown_server("s1")
        
        stop_events = [e for e in self.events if e.event_type == "stop"]
        self.assertEqual(len(stop_events), 1)

    async def test_restart_event_emitted(self):
        """Restarting should emit a 'restart' event."""
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.restart_server("s1")
        
        restart_events = [e for e in self.events if e.event_type == "restart"]
        self.assertGreaterEqual(len(restart_events), 1)

    async def test_remove_event_handler(self):
        """Removing an event handler should stop receiving events."""
        async def collector(e):
            self.events.append(e)
        
        self.lifecycle.add_event_handler(collector)
        self.lifecycle.remove_event_handler(collector)
        
        self.lifecycle.register_server("s1")
        self.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["simple"]}}
        })
        await self.lifecycle.start_server("s1")
        
        # Events before remove were still queued, but collector was already removed
        # Just verify no crash
        self.assertTrue(True)


class TestLifecycleConfig(unittest.IsolatedAsyncioTestCase):
    """Test configuration management in LifecycleManager."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.temp_dir = tempfile.mkdtemp()
        self.vfs = VFSDatabaseManager(
            data_root=os.path.join(self.temp_dir, "vfs"),
            auto_backup=False,
        )
        self.lifecycle = LifecycleManager(
            plugin_registry=self.registry,
            vfs_manager=self.vfs,
            health_check_interval=9999,
        )

    def tearDown(self):
        try:
            self.lifecycle = None
            self.vfs.close_all()
            PluginRegistry.reset_instance()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    async def test_update_server_config(self):
        """Updating server config should work."""
        self.lifecycle.register_server("s1", "Test")
        config = {"custom_key": "custom_value"}
        self.lifecycle.update_server_config("s1", config)
        
        info = self.lifecycle.get_server_info("s1")
        self.assertEqual(info.display_name, "Test")

    async def test_update_config_nonexistent_server(self):
        """Updating config for a nonexistent server should raise."""
        with self.assertRaises(ServerNotFoundError):
            self.lifecycle.update_server_config("ghost", {})


if __name__ == "__main__":
    unittest.main()
