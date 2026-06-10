"""
Unit tests for the Admin CLI.
Uses unittest (NOT pytest).
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Any, List, Optional

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.admin_cli import (
    AdminCLI,
    CLIResult,
    AdminCLIError,
    AdminCLIArgumentError,
    AdminCLIExecutionError,
    create_arg_parser,
)
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
)


# ──────────────────────────────────────────────────────────────────────────────
# Test Plugin Implementations
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginSimple(SubsystemPlugin):
    name = "test_simple"
    version = "1.0.0"
    description = "Simple test plugin for admin CLI"

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

class TestCLIResult(unittest.TestCase):
    """Test CLIResult data class."""

    def test_default_success(self):
        """Default result should be successful."""
        r = CLIResult(success=True, message="ok")
        self.assertTrue(r.success)
        self.assertEqual(r.message, "ok")
        self.assertIsNone(r.data)

    def test_failure_result(self):
        """Failure result should work."""
        r = CLIResult(success=False, message="error occurred")
        self.assertFalse(r.success)

    def test_to_dict(self):
        """to_dict should return serializable dict."""
        r = CLIResult(success=True, message="test", data={"key": "val"}, command="test")
        d = r.to_dict()
        self.assertEqual(d["success"], True)
        self.assertEqual(d["message"], "test")
        self.assertEqual(d["data"], {"key": "val"})

    def test_to_json(self):
        """to_json should return valid JSON."""
        r = CLIResult(success=True, message="test", data=[1, 2, 3], command="test")
        j = r.to_json()
        parsed = json.loads(j)
        self.assertEqual(parsed["message"], "test")
        self.assertEqual(parsed["data"], [1, 2, 3])

    def test_str_with_data(self):
        """String representation should include data."""
        r = CLIResult(success=True, message="Results:", data={"count": 5}, command="test")
        s = str(r)
        self.assertIn("Results:", s)
        self.assertIn("count", s)

    def test_str_without_data(self):
        """String representation without data should just show message."""
        r = CLIResult(success=True, message="Done", command="test")
        self.assertEqual(str(r), "Done")


class TestAdminCLIInitialization(unittest.TestCase):
    """Test AdminCLI initialization."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.temp_dir = tempfile.mkdtemp()
        self.vfs = VFSDatabaseManager(
            data_root=os.path.join(self.temp_dir, "vfs"),
            auto_backup=False,
        )

    def tearDown(self):
        try:
            self.vfs.close_all()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_init_with_all_components(self):
        """Initializing with all components should work."""
        lifecycle = LifecycleManager(
            plugin_registry=self.registry,
            vfs_manager=self.vfs,
            health_check_interval=9999,
        )
        cli = AdminCLI(
            registry=self.registry,
            vfs_manager=self.vfs,
            lifecycle=lifecycle,
        )
        self.assertIsNotNone(cli.registry)
        self.assertIsNotNone(cli.vfs_manager)
        self.assertIsNotNone(cli.lifecycle)

    def test_init_with_partial_components(self):
        """Initializing with some None components should work."""
        cli = AdminCLI(registry=self.registry)
        self.assertIsNotNone(cli.registry)
        self.assertIsNone(cli.vfs_manager)
        self.assertIsNone(cli.lifecycle)

    def test_init_with_config_path(self):
        """Initializing with a config path should work."""
        cli = AdminCLI(config_path="/tmp/test_config.json")
        self.assertEqual(cli.config_path, "/tmp/test_config.json")


# ──────────────────────────────────────────────────────────────────────────────
# Fixture for full CLI tests
# ──────────────────────────────────────────────────────────────────────────────

class AdminCLITestFixture:
    """Fixture that sets up a complete environment for CLI testing."""

    def __init__(self):
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
            shutdown_timeout=5.0,
            auto_restart=False,
        )
        self.cli = AdminCLI(
            registry=self.registry,
            vfs_manager=self.vfs,
            lifecycle=self.lifecycle,
        )
        self.registry.register(TestPluginSimple)

    def cleanup(self):
        try:
            self.lifecycle = None
            self.vfs.close_all()
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Test Server Commands
# ──────────────────────────────────────────────────────────────────────────────

class TestServerCommands(unittest.IsolatedAsyncioTestCase):
    """Test server management commands."""

    def setUp(self):
        self.fix = AdminCLITestFixture()

    def tearDown(self):
        self.fix.cleanup()

    async def test_list_servers_empty(self):
        """Listing servers when none registered should show empty."""
        result = await self.fix.cli.list_servers()
        self.assertTrue(result.success)
        self.assertEqual(result.data, [])

    async def test_list_servers_with_one(self):
        """Listing servers after registering one should show it."""
        self.fix.lifecycle.register_server("s1", "Server 1")
        result = await self.fix.cli.list_servers()
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.data[0]["server_id"], "s1")

    async def test_register_server(self):
        """Registering a server should succeed."""
        result = await self.fix.cli.register_server("s1", "My Server")
        self.assertTrue(result.success)
        self.assertEqual(result.data["server_id"], "s1")
        
        # Verify it's registered
        servers = self.fix.lifecycle.list_servers()
        self.assertEqual(len(servers), 1)

    async def test_register_server_minimal(self):
        """Registering a server without display name should work."""
        result = await self.fix.cli.register_server("s1")
        self.assertTrue(result.success)

    async def test_register_duplicate(self):
        """Registering a duplicate should fail."""
        await self.fix.cli.register_server("s1")
        result = await self.fix.cli.register_server("s1")
        self.assertFalse(result.success)
        self.assertIn("already registered", result.message.lower())

    async def test_unregister_server(self):
        """Unregistering a server should succeed."""
        await self.fix.cli.register_server("s1")
        result = await self.fix.cli.unregister_server("s1")
        self.assertTrue(result.success)
        
        servers = self.fix.lifecycle.list_servers()
        self.assertEqual(len(servers), 0)

    async def test_unregister_nonexistent(self):
        """Unregistering a nonexistent server should fail."""
        result = await self.fix.cli.unregister_server("ghost")
        self.assertFalse(result.success)

    async def test_start_server(self):
        """Starting a server should succeed."""
        self.fix.lifecycle.register_server("s1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        
        result = await self.fix.cli.start_server("s1")
        self.assertTrue(result.success)
        self.assertEqual(result.data["state"], "running")

    async def test_start_nonexistent_server(self):
        """Starting a nonexistent server should fail."""
        result = await self.fix.cli.start_server("ghost")
        self.assertFalse(result.success)

    async def test_stop_server(self):
        """Stopping a running server should succeed."""
        self.fix.lifecycle.register_server("s1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        await self.fix.cli.start_server("s1")
        
        result = await self.fix.cli.stop_server("s1")
        self.assertTrue(result.success)

    async def test_stop_nonexistent_server(self):
        """Stopping a nonexistent server should fail."""
        result = await self.fix.cli.stop_server("ghost")
        self.assertFalse(result.success)

    async def test_restart_server(self):
        """Restarting a server should succeed."""
        self.fix.lifecycle.register_server("s1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        await self.fix.cli.start_server("s1")
        
        result = await self.fix.cli.restart_server("s1")
        self.assertTrue(result.success)
        self.assertEqual(result.data["state"], "running")

    async def test_server_info(self):
        """Getting server info should return details."""
        self.fix.lifecycle.register_server("s1", "Test")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        await self.fix.cli.start_server("s1")
        
        result = await self.fix.cli.server_info("s1")
        self.assertTrue(result.success)
        self.assertEqual(result.data["server_id"], "s1")
        self.assertEqual(result.data["state"], "running")
        self.assertIn("instances", result.data)
        self.assertEqual(len(result.data["instances"]), 1)

    async def test_server_info_nonexistent(self):
        """Getting info for a nonexistent server should fail."""
        result = await self.fix.cli.server_info("ghost")
        self.assertFalse(result.success)


# ──────────────────────────────────────────────────────────────────────────────
# Test Plugin Commands
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginCommands(unittest.IsolatedAsyncioTestCase):
    """Test plugin management commands."""

    def setUp(self):
        self.fix = AdminCLITestFixture()

    def tearDown(self):
        self.fix.cleanup()

    async def test_list_plugins(self):
        """Listing plugins should show registered ones."""
        result = await self.fix.cli.list_plugins()
        self.assertTrue(result.success)
        names = [p["name"] for p in result.data]
        self.assertIn("test_simple", names)

    async def test_plugin_info(self):
        """Getting plugin info should return manifest details."""
        result = await self.fix.cli.plugin_info("test_simple")
        self.assertTrue(result.success)
        self.assertEqual(result.data["name"], "test_simple")
        self.assertEqual(result.data["version"], "1.0.0")

    async def test_plugin_info_nonexistent(self):
        """Getting info for a nonexistent plugin should fail."""
        result = await self.fix.cli.plugin_info("ghost")
        self.assertFalse(result.success)

    async def test_unregister_plugin(self):
        """Unregistering a plugin should succeed."""
        result = await self.fix.cli.unregister_plugin("test_simple")
        self.assertTrue(result.success)
        self.assertFalse(self.fix.registry.has_plugin("test_simple"))

    async def test_unregister_nonexistent(self):
        """Unregistering a nonexistent plugin should fail."""
        result = await self.fix.cli.unregister_plugin("ghost")
        self.assertFalse(result.success)

    async def test_list_instances_empty(self):
        """Listing instances when none exist should show empty."""
        result = await self.fix.cli.list_instances()
        self.assertTrue(result.success)
        self.assertEqual(result.data, [])

    async def test_list_instances_with_data(self):
        """Listing instances after starting a server should show them."""
        self.fix.lifecycle.register_server("s1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        await self.fix.lifecycle.start_server("s1")
        
        result = await self.fix.cli.list_instances()
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.data[0]["plugin"], "test_simple")
        self.assertEqual(result.data[0]["server_id"], "s1")

    async def test_register_plugin_from_file(self):
        """Registering a plugin from a file should work."""
        # Create a temporary plugin file
        plugin_code = '''
from engine.plugin_registry import SubsystemPlugin
from typing import Dict, Any

class FilePlugin(SubsystemPlugin):
    name = "file_plugin"
    version = "1.0.0"
    description = "Plugin loaded from file"

    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        pass
'''
        plugin_file = os.path.join(self.fix.temp_dir, "my_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(plugin_code)
        
        result = await self.fix.cli.register_plugin(plugin_file)
        self.assertTrue(result.success)
        self.assertIn("file_plugin", result.message)
        self.assertTrue(self.fix.registry.has_plugin("file_plugin"))

    async def test_register_plugin_from_invalid_file(self):
        """Registering from a nonexistent file path should fail."""
        result = await self.fix.cli.register_plugin("/nonexistent/plugin.py")
        self.assertFalse(result.success)


# ──────────────────────────────────────────────────────────────────────────────
# Test Database Commands
# ──────────────────────────────────────────────────────────────────────────────

class TestDatabaseCommands(unittest.IsolatedAsyncioTestCase):
    """Test VFS database management commands."""

    def setUp(self):
        self.fix = AdminCLITestFixture()

    def tearDown(self):
        self.fix.cleanup()

    async def test_list_databases_empty(self):
        """Listing databases when none exist should show empty."""
        result = await self.fix.cli.list_databases()
        self.assertTrue(result.success)
        self.assertEqual(result.data, [])

    async def test_list_databases_with_data(self):
        """Listing databases after creating one should show it."""
        self.fix.vfs.get_database("sub", "srv")
        
        result = await self.fix.cli.list_databases()
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.data[0]["subsystem"], "sub")
        self.assertEqual(result.data[0]["server_id"], "srv")

    async def test_show_database(self):
        """Showing database details should return info."""
        db = self.fix.vfs.get_database("sub", "srv")
        db.execute("CREATE TABLE test (id INT)")
        
        result = await self.fix.cli.show_database("sub", "srv")
        self.assertTrue(result.success)
        self.assertEqual(result.data["subsystem"], "sub")
        self.assertEqual(result.data["server_id"], "srv")
        self.assertIn("test", result.data["table_names"])

    async def test_show_database_nonexistent(self):
        """Showing a nonexistent db combo should work (creates it)."""
        result = await self.fix.cli.show_database("new_sub", "new_srv")
        self.assertTrue(result.success)

    async def test_backup_databases_all(self):
        """Backing up all databases should work."""
        self.fix.vfs.get_database("sub", "srv")
        
        result = await self.fix.cli.backup_databases()
        self.assertTrue(result.success)

    async def test_backup_specific_database(self):
        """Backing up a specific database should work."""
        self.fix.vfs.get_database("sub", "srv")
        
        result = await self.fix.cli.backup_databases("sub", "srv")
        self.assertTrue(result.success)

    async def test_database_health(self):
        """Health check on databases should work."""
        self.fix.vfs.get_database("sub", "srv")
        
        result = await self.fix.cli.check_database_health()
        self.assertTrue(result.success)
        self.assertEqual(result.data["healthy"], 1)
        self.assertEqual(result.data["unhealthy"], 0)

    async def test_database_health_empty(self):
        """Health check with no databases should work."""
        result = await self.fix.cli.check_database_health()
        self.assertTrue(result.success)
        self.assertEqual(result.data["total"], 0)


# ──────────────────────────────────────────────────────────────────────────────
# Test Status and Config Commands
# ──────────────────────────────────────────────────────────────────────────────

class TestStatusConfigCommands(unittest.IsolatedAsyncioTestCase):
    """Test status and configuration commands."""

    def setUp(self):
        self.fix = AdminCLITestFixture()

    def tearDown(self):
        self.fix.cleanup()

    async def test_global_status(self):
        """Global status should show all system info."""
        self.fix.lifecycle.register_server("s1", "Server 1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        await self.fix.lifecycle.start_server("s1")
        
        result = await self.fix.cli.global_status()
        self.assertTrue(result.success)
        self.assertIn("servers", result.data)
        self.assertIn("plugins", result.data)
        self.assertIn("instances", result.data)
        self.assertIn("vfs_databases", result.data)
        self.assertEqual(result.data["servers"]["running"], 1)
        self.assertIn("test_simple", result.data["plugins"]["registered"])

    async def test_global_status_no_servers(self):
        """Global status with no servers should show zeros."""
        result = await self.fix.cli.global_status()
        self.assertTrue(result.success)
        self.assertEqual(result.data["servers"]["total"], 0)

    async def test_show_config(self):
        """Showing config should return current config."""
        self.fix.registry.set_config({"test_key": "test_value"})
        
        result = await self.fix.cli.show_config()
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("test_key"), "test_value")

    async def test_show_config_with_lifecycle(self):
        """Config should include lifecycle settings."""
        result = await self.fix.cli.show_config()
        self.assertTrue(result.success)
        self.assertIn("_lifecycle", result.data)

    async def test_reload_config_no_path(self):
        """Reloading config without a path should fail."""
        cli_without_path = AdminCLI(registry=self.fix.registry)
        result = await cli_without_path.reload_config()
        self.assertFalse(result.success)
        self.assertIn("no config path", result.message.lower())

    async def test_reload_config_from_file(self):
        """Reloading config from an existing file should work."""
        config_file = os.path.join(self.fix.temp_dir, "test_config.json")
        with open(config_file, "w") as f:
            json.dump({"reloaded": True, "servers": {}}, f)
        
        cli = AdminCLI(
            registry=self.fix.registry,
            config_path=config_file,
        )
        result = await cli.reload_config()
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("reloaded"), True)


# ──────────────────────────────────────────────────────────────────────────────
# Test Event Commands
# ──────────────────────────────────────────────────────────────────────────────

class TestEventCommands(unittest.IsolatedAsyncioTestCase):
    """Test event monitoring commands."""

    def setUp(self):
        self.fix = AdminCLITestFixture()

    def tearDown(self):
        self.fix.cleanup()

    async def test_show_events_empty(self):
        """Showing events when none recorded should show message."""
        result = await self.fix.cli.show_events()
        self.assertTrue(result.success)
        self.assertEqual(result.data, [])

    async def test_show_events_with_data(self):
        """Showing events after recording should show them."""
        # Record some events via the lifecycle
        self.fix.lifecycle.add_event_handler(self.fix.cli.record_event)
        self.fix.lifecycle.register_server("s1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        await self.fix.lifecycle.start_server("s1")
        
        result = await self.fix.cli.show_events()
        self.assertTrue(result.success)
        self.assertGreater(len(result.data), 0)
        
        # Check event structure
        event = result.data[0]
        self.assertIn("type", event)
        self.assertIn("server_id", event)
        self.assertIn("message", event)

    async def test_record_event(self):
        """Manually recording an event should work."""
        self.fix.cli.record_event(LifecycleEvent(
            event_type="test",
            server_id="s1",
            message="Test event",
            success=True,
        ))
        
        result = await self.fix.cli.show_events()
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.data[0]["type"], "test")
        self.assertEqual(result.data[0]["message"], "Test event")

    async def test_event_limit(self):
        """Events should be limited to 100."""
        for i in range(150):
            self.fix.cli.record_event(LifecycleEvent(
                event_type="test",
                server_id=f"s{i}",
                message=f"Event {i}",
                success=True,
            ))
        
        self.assertLessEqual(len(self.fix.cli._recent_events), 100)


# ──────────────────────────────────────────────────────────────────────────────
# Test CLI Interface
# ──────────────────────────────────────────────────────────────────────────────

class TestCLIDispatch(unittest.IsolatedAsyncioTestCase):
    """Test the CLI command dispatch."""

    def setUp(self):
        self.fix = AdminCLITestFixture()

    def tearDown(self):
        self.fix.cleanup()

    async def test_empty_argv_shows_help(self):
        """Empty argv should show help."""
        result = await self.fix.cli.run_cli([])
        self.assertTrue(result.success)
        self.assertIn("Commands:", result.message)

    async def test_help_command(self):
        """Help command should show help text."""
        result = await self.fix.cli.run_cli(["help"])
        self.assertTrue(result.success)
        self.assertIn("server", result.message)
        self.assertIn("plugin", result.message)
        self.assertIn("db", result.message)
        self.assertIn("status", result.message)
        self.assertIn("config", result.message)

    async def test_unknown_command(self):
        """Unknown command should show error."""
        result = await self.fix.cli.run_cli(["unknown_cmd"])
        self.assertFalse(result.success)
        self.assertIn("unknown", result.message.lower())

    async def test_server_list_command(self):
        """CLI 'server list' should work."""
        self.fix.lifecycle.register_server("s1")
        result = await self.fix.cli.run_cli(["server", "list"])
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)

    async def test_server_list_no_subcommand(self):
        """CLI 'server' without subcommand should list servers."""
        self.fix.lifecycle.register_server("s1")
        result = await self.fix.cli.run_cli(["server"])
        self.assertTrue(result.success)

    async def test_server_start_command(self):
        """CLI 'server start' should work."""
        self.fix.lifecycle.register_server("s1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        result = await self.fix.cli.run_cli(["server", "start", "s1"])
        self.assertTrue(result.success)
        self.assertEqual(result.data["state"], "running")

    async def test_server_start_missing_arg(self):
        """CLI 'server start' without args should show usage."""
        result = await self.fix.cli.run_cli(["server", "start"])
        self.assertFalse(result.success)
        self.assertIn("usage", result.message.lower())

    async def test_server_stop_command(self):
        """CLI 'server stop' should work."""
        self.fix.lifecycle.register_server("s1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        await self.fix.lifecycle.start_server("s1")
        
        result = await self.fix.cli.run_cli(["server", "stop", "s1"])
        self.assertTrue(result.success)

    async def test_server_restart_command(self):
        """CLI 'server restart' should work."""
        self.fix.lifecycle.register_server("s1")
        self.fix.registry.set_config({
            "servers": {"s1": {"enabled_subsystems": ["test_simple"]}}
        })
        
        result = await self.fix.cli.run_cli(["server", "restart", "s1"])
        self.assertTrue(result.success)

    async def test_server_info_command(self):
        """CLI 'server info' should work."""
        self.fix.lifecycle.register_server("s1")
        result = await self.fix.cli.run_cli(["server", "info", "s1"])
        self.assertTrue(result.success)

    async def test_server_register_command(self):
        """CLI 'server register' should work."""
        result = await self.fix.cli.run_cli(["server", "register", "s1", "My Server"])
        self.assertTrue(result.success)
        info = self.fix.lifecycle.get_server_info("s1")
        self.assertEqual(info.display_name, "My Server")

    async def test_server_unregister_command(self):
        """CLI 'server unregister' should work."""
        self.fix.lifecycle.register_server("s1")
        result = await self.fix.cli.run_cli(["server", "unregister", "s1"])
        self.assertTrue(result.success)
        self.assertEqual(len(self.fix.lifecycle.list_servers()), 0)

    async def test_server_unknown_subcommand(self):
        """Unknown server subcommand should show available commands."""
        result = await self.fix.cli.run_cli(["server", "fly"])
        self.assertFalse(result.success)
        self.assertIn("available", result.message.lower())

    async def test_plugin_list_command(self):
        """CLI 'plugin list' should work."""
        result = await self.fix.cli.run_cli(["plugin", "list"])
        self.assertTrue(result.success)
        names = [p["name"] for p in result.data]
        self.assertIn("test_simple", names)

    async def test_plugin_list_no_subcommand(self):
        """CLI 'plugin' without subcommand should list plugins."""
        result = await self.fix.cli.run_cli(["plugin"])
        self.assertTrue(result.success)

    async def test_plugin_info_command(self):
        """CLI 'plugin info' should work."""
        result = await self.fix.cli.run_cli(["plugin", "info", "test_simple"])
        self.assertTrue(result.success)
        self.assertEqual(result.data["name"], "test_simple")

    async def test_plugin_unregister_command(self):
        """CLI 'plugin unregister' should work."""
        result = await self.fix.cli.run_cli(["plugin", "unregister", "test_simple"])
        self.assertTrue(result.success)
        self.assertFalse(self.fix.registry.has_plugin("test_simple"))

    async def test_plugin_instances_command(self):
        """CLI 'plugin instances' should work."""
        result = await self.fix.cli.run_cli(["plugin", "instances"])
        self.assertTrue(result.success)

    async def test_plugin_unknown_subcommand(self):
        """Unknown plugin subcommand should show available commands."""
        result = await self.fix.cli.run_cli(["plugin", "dance"])
        self.assertFalse(result.success)
        self.assertIn("available", result.message.lower())

    async def test_db_list_command(self):
        """CLI 'db list' should work."""
        result = await self.fix.cli.run_cli(["db", "list"])
        self.assertTrue(result.success)

    async def test_db_list_no_subcommand(self):
        """CLI 'db' without subcommand should list databases."""
        self.fix.vfs.get_database("sub", "srv")
        result = await self.fix.cli.run_cli(["db"])
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)

    async def test_db_show_command(self):
        """CLI 'db show' should work."""
        db = self.fix.vfs.get_database("sub", "srv")
        db.execute("CREATE TABLE t (id INT)")
        result = await self.fix.cli.run_cli(["db", "show", "sub", "srv"])
        self.assertTrue(result.success)
        self.assertIn("t", result.data["table_names"])

    async def test_db_show_missing_args(self):
        """CLI 'db show' without args should show usage."""
        result = await self.fix.cli.run_cli(["db", "show"])
        self.assertFalse(result.success)
        self.assertIn("usage", result.message.lower())

    async def test_db_backup_command(self):
        """CLI 'db backup' should work."""
        self.fix.vfs.get_database("sub", "srv")
        result = await self.fix.cli.run_cli(["db", "backup"])
        self.assertTrue(result.success)

    async def test_db_backup_specific(self):
        """CLI 'db backup sub server' should work."""
        self.fix.vfs.get_database("sub", "srv")
        result = await self.fix.cli.run_cli(["db", "backup", "sub", "srv"])
        self.assertTrue(result.success)

    async def test_db_health_command(self):
        """CLI 'db health' should work."""
        result = await self.fix.cli.run_cli(["db", "health"])
        self.assertTrue(result.success)

    async def test_db_unknown_subcommand(self):
        """Unknown db subcommand should show available commands."""
        result = await self.fix.cli.run_cli(["db", "restore"])
        self.assertFalse(result.success)
        self.assertIn("available", result.message.lower())

    async def test_status_command(self):
        """CLI 'status' should work."""
        result = await self.fix.cli.run_cli(["status"])
        self.assertTrue(result.success)
        self.assertIn("servers", result.data)

    async def test_config_command(self):
        """CLI 'config' should work."""
        result = await self.fix.cli.run_cli(["config"])
        self.assertTrue(result.success)

    async def test_config_reload_command(self):
        """CLI 'config reload' should work."""
        # Without a config path, it should fail
        result = await self.fix.cli.run_cli(["config", "reload"])
        self.assertFalse(result.success)

    async def test_events_command(self):
        """CLI 'events' should work."""
        result = await self.fix.cli.run_cli(["events"])
        self.assertTrue(result.success)


# ──────────────────────────────────────────────────────────────────────────────
# Test Edge Cases and Error Handling
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeCases(unittest.IsolatedAsyncioTestCase):
    """Test edge cases and error handling."""

    def setUp(self):
        self.fix = AdminCLITestFixture()

    def tearDown(self):
        self.fix.cleanup()

    async def test_list_servers_without_lifecycle(self):
        """Listing servers without lifecycle should fail gracefully."""
        cli = AdminCLI(registry=self.fix.registry)
        result = await cli.list_servers()
        self.assertFalse(result.success)
        self.assertIn("not initialized", result.message.lower())

    async def test_list_plugins_without_registry(self):
        """Listing plugins without registry should fail gracefully."""
        cli = AdminCLI()
        result = await cli.list_plugins()
        self.assertFalse(result.success)
        self.assertIn("not initialized", result.message.lower())

    async def test_list_databases_without_vfs(self):
        """Listing databases without VFS should fail gracefully."""
        cli = AdminCLI(registry=self.fix.registry)
        result = await cli.list_databases()
        self.assertFalse(result.success)
        self.assertIn("not initialized", result.message.lower())

    async def test_start_server_without_lifecycle(self):
        """Starting a server without lifecycle should fail gracefully."""
        cli = AdminCLI()
        result = await cli.start_server("s1")
        self.assertFalse(result.success)

    async def test_show_events_ordering(self):
        """Events should be returned newest first."""
        from datetime import datetime, timezone
        
        for i in range(3):
            self.fix.cli.record_event(LifecycleEvent(
                event_type="test",
                server_id="s1",
                message=f"Event {i}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                success=True,
            ))
        
        result = await self.fix.cli.show_events(limit=10)
        self.assertEqual(len(result.data), 3)
        # Should be newest first (reversed from recorded order)
        self.assertEqual(result.data[0]["message"], "Event 2")

    async def test_show_events_limit(self):
        """Events should respect the limit parameter."""
        for i in range(10):
            self.fix.cli.record_event(LifecycleEvent(
                event_type="test",
                server_id="s1",
                message=f"Event {i}",
                success=True,
            ))
        
        result = await self.fix.cli.show_events(limit=3)
        self.assertEqual(len(result.data), 3)


# ──────────────────────────────────────────────────────────────────────────────
# Test Utility Functions
# ──────────────────────────────────────────────────────────────────────────────

class TestUtilities(unittest.TestCase):
    """Test utility functions."""

    def test_format_size_bytes(self):
        """_format_size should format bytes correctly."""
        self.assertIn("B", AdminCLI._format_size(0))
        self.assertIn("B", AdminCLI._format_size(500))
        self.assertIn("KB", AdminCLI._format_size(2048))

    def test_format_size_large(self):
        """_format_size should handle large values."""
        result = AdminCLI._format_size(1024 * 1024 * 1024)
        self.assertIn("GB", result)

    def test_format_table_empty(self):
        """_format_table with no rows should return (empty)."""
        result = AdminCLI._format_table(["A", "B"], [])
        self.assertEqual(result, "(empty)")

    def test_format_table_with_data(self):
        """_format_table should format data correctly."""
        result = AdminCLI._format_table(
            ["Name", "Value"],
            [["Alice", "100"], ["Bob", "200"]],
        )
        self.assertIn("Alice", result)
        self.assertIn("Bob", result)
        self.assertIn("Name", result)
        self.assertIn("Value", result)


# ──────────────────────────────────────────────────────────────────────────────
# Test Argument Parser
# ──────────────────────────────────────────────────────────────────────────────

class TestArgumentParser(unittest.TestCase):
    """Test the argparse argument parser."""

    def test_create_parser(self):
        """Creating the parser should not raise."""
        parser = create_arg_parser()
        self.assertIsNotNone(parser)

    def test_parse_server_list(self):
        """Parsing 'server list' should work."""
        parser = create_arg_parser()
        args = parser.parse_args(["server", "list"])
        self.assertEqual(args.command, "server")
        self.assertEqual(args.subcommand, "list")

    def test_parse_server_start(self):
        """Parsing 'server start s1' should work."""
        parser = create_arg_parser()
        args = parser.parse_args(["server", "start", "s1"])
        self.assertEqual(args.command, "server")
        self.assertEqual(args.subcommand, "start")
        self.assertEqual(args.server_id, "s1")

    def test_parse_plugin_list(self):
        """Parsing 'plugin list' should work."""
        parser = create_arg_parser()
        args = parser.parse_args(["plugin", "list"])
        self.assertEqual(args.command, "plugin")
        self.assertEqual(args.subcommand, "list")

    def test_parse_status(self):
        """Parsing 'status' should work."""
        parser = create_arg_parser()
        args = parser.parse_args(["status"])
        self.assertEqual(args.command, "status")

    def test_parse_db_show(self):
        """Parsing 'db show sub server' should work."""
        parser = create_arg_parser()
        args = parser.parse_args(["db", "show", "auction", "main"])
        self.assertEqual(args.command, "db")
        self.assertEqual(args.subcommand, "show")
        self.assertEqual(args.subsystem, "auction")
        self.assertEqual(args.server_id, "main")


if __name__ == "__main__":
    unittest.main()
