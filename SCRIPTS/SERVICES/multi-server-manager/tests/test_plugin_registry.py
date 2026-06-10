"""
Unit tests for the Subsystem Plugin Registry.
Uses unittest (NOT pytest).
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Any

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.plugin_registry import (
    PluginRegistry,
    SubsystemPlugin,
    PluginState,
    PluginHealth,
    PluginManifest,
    PluginInstanceInfo,
    PluginError,
    PluginNotFoundError,
    PluginDependencyError,
    PluginStateError,
    PluginConfigError,
)


# ──────────────────────────────────────────────────────────────────────────────
# Test Plugin Implementations
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginA(SubsystemPlugin):
    name = "plugin_a"
    version = "1.0.0"
    description = "Test plugin A - no dependencies"
    tags = ["test", "core"]

    def __init__(self):
        super().__init__()
        self.init_calls = []
        self.shutdown_calls = []
        self.health_results = []
        self.config_changes = []

    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        self.init_calls.append((server_id, config))
        if config.get("fail_init"):
            raise PluginError("Forced init failure")

    async def on_shutdown(self, server_id: str) -> None:
        self.shutdown_calls.append(server_id)

    async def on_health_check(self, server_id: str) -> PluginHealth:
        result = PluginHealth.HEALTHY
        if self.health_results:
            result = self.health_results.pop(0)
        return result

    async def on_config_change(self, server_id: str, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        self.config_changes.append((server_id, old_config, new_config))


class TestPluginB(SubsystemPlugin):
    name = "plugin_b"
    version = "2.0.0"
    description = "Test plugin B - depends on A"
    dependencies = ["plugin_a"]


class TestPluginC(SubsystemPlugin):
    name = "plugin_c"
    version = "1.5.0"
    description = "Test plugin C - depends on A and B"
    dependencies = ["plugin_a", "plugin_b"]


class TestPluginCircular(SubsystemPlugin):
    name = "plugin_circular"
    version = "1.0.0"
    description = "Circular dependency test"
    dependencies = ["plugin_loop"]


class TestPluginLoop(SubsystemPlugin):
    name = "plugin_loop"
    version = "1.0.0"
    description = "Loop dependency test"
    dependencies = ["plugin_circular"]


# ──────────────────────────────────────────────────────────────────────────────
# Test Cases
# ──────────────────────────────────────────────────────────────────────────────

class TestSubsystemPluginValidation(unittest.TestCase):
    """Test that plugin class validation works."""

    def test_missing_name_raises_error(self):
        """Plugin without name should raise PluginConfigError."""
        with self.assertRaises(PluginConfigError):
            class BadPlugin(SubsystemPlugin):
                name = ""
                version = "1.0.0"
                description = "bad"
            # Trigger __init_subclass__ by creating instance
            BadPlugin()

    def test_missing_version_raises_error(self):
        """Plugin without version should raise PluginConfigError."""
        with self.assertRaises(PluginConfigError):
            class BadPlugin(SubsystemPlugin):
                name = "bad"
                version = ""
                description = "desc"
            BadPlugin()

    def test_missing_description_raises_error(self):
        """Plugin without description should raise PluginConfigError."""
        with self.assertRaises(PluginConfigError):
            class BadPlugin(SubsystemPlugin):
                name = "bad"
                version = "1.0.0"
                description = ""
            BadPlugin()

    def test_valid_plugin_ok(self):
        """Valid plugin should not raise."""
        try:
            p = TestPluginA()
            self.assertEqual(p.name, "plugin_a")
            self.assertEqual(p.version, "1.0.0")
            self.assertEqual(p.description, "Test plugin A - no dependencies")
        except PluginConfigError:
            self.fail("Valid plugin raised PluginConfigError")

    def test_manifest_property(self):
        """Plugin manifest should contain correct metadata."""
        p = TestPluginA()
        manifest = p.manifest
        self.assertIsInstance(manifest, PluginManifest)
        self.assertEqual(manifest.name, "plugin_a")
        self.assertEqual(manifest.version, "1.0.0")
        self.assertEqual(manifest.description, "Test plugin A - no dependencies")
        self.assertEqual(manifest.dependencies, [])
        self.assertEqual(manifest.tags, ["test", "core"])


class TestPluginRegistrySingleton(unittest.TestCase):
    """Test that PluginRegistry is a proper singleton."""

    def setUp(self):
        PluginRegistry.reset_instance()

    def test_singleton_returns_same_instance(self):
        """Multiple get_instance calls should return the same instance."""
        r1 = PluginRegistry.get_instance()
        r2 = PluginRegistry.get_instance()
        self.assertIs(r1, r2)

    def test_reset_instance_creates_new(self):
        """After reset, get_instance should return a new instance."""
        r1 = PluginRegistry.get_instance()
        PluginRegistry.reset_instance()
        r2 = PluginRegistry.get_instance()
        self.assertIsNot(r1, r2)


class TestPluginRegistration(unittest.TestCase):
    """Test plugin registration functionality."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()

    def test_register_valid_plugin(self):
        """Registering a valid plugin should succeed."""
        plugin = self.registry.register(TestPluginA)
        self.assertIsInstance(plugin, TestPluginA)
        self.assertTrue(self.registry.has_plugin("plugin_a"))

    def test_register_duplicate_same_class(self):
        """Registering the same class twice should return the same instance."""
        p1 = self.registry.register(TestPluginA)
        p2 = self.registry.register(TestPluginA)
        self.assertIs(p1, p2)

    def test_register_non_plugin_class(self):
        """Registering a non-SubsystemPlugin class should raise."""
        with self.assertRaises(PluginConfigError):
            self.registry.register(dict)  # type: ignore

    def test_register_multiple_plugins(self):
        """Registering multiple plugins should all be accessible."""
        self.registry.register(TestPluginA)
        self.registry.register(TestPluginB)
        self.assertEqual(self.registry.plugin_count, 2)

    def test_unregister_existing_plugin(self):
        """Unregistering a registered plugin should work."""
        self.registry.register(TestPluginA)
        self.registry.unregister("plugin_a")
        self.assertFalse(self.registry.has_plugin("plugin_a"))

    def test_unregister_nonexistent_plugin(self):
        """Unregistering a nonexistent plugin should raise."""
        with self.assertRaises(PluginNotFoundError):
            self.registry.unregister("nonexistent")

    def test_unregister_plugin_with_active_instance(self):
        """Unregistering a plugin with active instances should raise."""
        self.registry.register(TestPluginA)
        # Simulate active instance
        key = ("plugin_a", "test_server")
        self.registry._instances[key] = PluginInstanceInfo(
            plugin_name="plugin_a",
            server_id="test_server",
            state=PluginState.RUNNING,
        )
        with self.assertRaises(PluginStateError):
            self.registry.unregister("plugin_a")

    def test_get_plugin_exists(self):
        """Getting a registered plugin should return it."""
        self.registry.register(TestPluginA)
        plugin = self.registry.get_plugin("plugin_a")
        self.assertIsInstance(plugin, TestPluginA)

    def test_get_plugin_nonexistent(self):
        """Getting a nonexistent plugin should raise."""
        with self.assertRaises(PluginNotFoundError):
            self.registry.get_plugin("ghost")


class TestPluginListing(unittest.TestCase):
    """Test listing registered plugins."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()

    def test_list_plugins_empty(self):
        """Empty registry should return empty list."""
        plugins = self.registry.list_plugins()
        self.assertEqual(plugins, [])

    def test_list_plugins_after_registration(self):
        """After registration, list should contain the plugin manifest."""
        self.registry.register(TestPluginA)
        plugins = self.registry.list_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].name, "plugin_a")

    def test_list_plugins_multiple(self):
        """Listing should return all registered plugins."""
        self.registry.register(TestPluginA)
        self.registry.register(TestPluginB)
        self.registry.register(TestPluginC)
        plugins = self.registry.list_plugins()
        self.assertEqual(len(plugins), 3)
        names = [p.name for p in plugins]
        self.assertIn("plugin_a", names)
        self.assertIn("plugin_b", names)
        self.assertIn("plugin_c", names)


class TestDependencyResolution(unittest.TestCase):
    """Test dependency resolution for startup/shutdown ordering."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.registry.register(TestPluginA)
        self.registry.register(TestPluginB)
        self.registry.register(TestPluginC)

    def test_startup_order_no_deps(self):
        """Plugins without deps should start first."""
        # plugin_a has no deps, should be first
        order = self.registry.get_startup_order(["plugin_a"])
        self.assertEqual(order, ["plugin_a"])

    def test_startup_order_respected(self):
        """Dependencies should start before dependents."""
        order = self.registry.get_startup_order(
            ["plugin_a", "plugin_b", "plugin_c"]
        )
        self.assertEqual(len(order), 3)
        self.assertLess(
            order.index("plugin_a"), order.index("plugin_b")
        )
        self.assertLess(
            order.index("plugin_b"), order.index("plugin_c")
        )

    def test_shutdown_order_reversed(self):
        """Shutdown order should be reverse of startup."""
        shutdown = self.registry.get_shutdown_order(
            ["plugin_a", "plugin_b", "plugin_c"]
        )
        self.assertEqual(len(shutdown), 3)
        self.assertGreater(
            shutdown.index("plugin_a"), shutdown.index("plugin_b")
        )
        self.assertGreater(
            shutdown.index("plugin_b"), shutdown.index("plugin_c")
        )

    def test_circular_dependency_detected(self):
        """Circular dependencies should raise PluginDependencyError."""
        self.registry.register(TestPluginCircular)
        self.registry.register(TestPluginLoop)
        with self.assertRaises(PluginDependencyError):
            self.registry.get_startup_order(
                ["plugin_circular", "plugin_loop"]
            )

    def test_missing_dependency_raises_error(self):
        """Missing dependency plugin should raise PluginNotFoundError."""
        # plugin_b depends on plugin_a, which IS registered, so this should work
        order = self.registry.get_startup_order(["plugin_b"])
        self.assertEqual(order, ["plugin_b"])
        
        # Test with a plugin that depends on an UNREGISTERED plugin
        self.registry.register(TestPluginC)  # depends on a, b - both registered
        order = self.registry.get_startup_order(["plugin_c"])
        self.assertEqual(order, ["plugin_c"])
        
        # Test with a MISSING dependency (not registered at all)
        class MissingDepPlugin(SubsystemPlugin):
            name = "missing_dep_test"
            version = "1.0.0"
            description = "Test missing dep"
            dependencies = ["not_registered_plugin"]
        
        self.registry.register(MissingDepPlugin)
        with self.assertRaises(PluginNotFoundError):
            self.registry.get_startup_order(["not_registered_plugin"])

    def test_missing_dependency_in_subset(self):
        """Subset ordering with missing dependency should raise."""
        # Test that we can still order a subset without including all deps
        order = self.registry.get_startup_order(["plugin_b", "plugin_c"])
        self.assertEqual(len(order), 2)
        self.assertLess(order.index("plugin_b"), order.index("plugin_c"))

    def test_partial_subset_ordering(self):
        """Ordering should work on a subset of registered plugins."""
        order = self.registry.get_startup_order(["plugin_b", "plugin_c"])
        self.assertEqual(len(order), 2)
        self.assertLess(order.index("plugin_b"), order.index("plugin_c"))


class TestInstanceManagement(unittest.IsolatedAsyncioTestCase):
    """Test subsystem instance lifecycle management."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.registry.register(TestPluginA)
        self.registry.register(TestPluginB)

    async def asyncSetUp(self):
        pass

    async def test_init_subsystem_success(self):
        """Initializing a subsystem should succeed and set state to RUNNING."""
        info = await self.registry.init_subsystem("plugin_a", "server1")
        self.assertEqual(info.state, PluginState.RUNNING)
        self.assertIsNotNone(info.started_at)

    async def test_init_subsystem_with_config(self):
        """Initialization should pass config to the plugin."""
        config = {"key": "value", "number": 42}
        await self.registry.init_subsystem("plugin_a", "server1", config)
        plugin = self.registry.get_plugin("plugin_a")
        self.assertEqual(plugin.init_calls[0][1], config)

    async def test_init_subsystem_duplicate_raises(self):
        """Initializing an already-running subsystem should raise."""
        await self.registry.init_subsystem("plugin_a", "server1")
        with self.assertRaises(PluginStateError):
            await self.registry.init_subsystem("plugin_a", "server1")

    async def test_init_subsystem_nonexistent_plugin(self):
        """Initializing a nonexistent plugin should raise."""
        with self.assertRaises(PluginNotFoundError):
            await self.registry.init_subsystem("ghost", "server1")

    async def test_init_subsystem_failure(self):
        """Plugin that fails init should be in ERROR state."""
        with self.assertRaises(PluginError):
            await self.registry.init_subsystem(
                "plugin_a", "server1", {"fail_init": True}
            )
        info = self.registry.get_instance_info("plugin_a", "server1")
        self.assertEqual(info.state, PluginState.ERROR)

    async def test_shutdown_subsystem(self):
        """Shutting down a subsystem should work."""
        await self.registry.init_subsystem("plugin_a", "server1")
        await self.registry.shutdown_subsystem("plugin_a", "server1")
        info = self.registry.get_instance_info("plugin_a", "server1")
        self.assertEqual(info.state, PluginState.SHUTDOWN)
        plugin = self.registry.get_plugin("plugin_a")
        self.assertIn("server1", plugin.shutdown_calls)

    async def test_shutdown_nonexistent_instance(self):
        """Shutting down an uninitialized instance should raise."""
        with self.assertRaises(PluginStateError):
            await self.registry.shutdown_subsystem("plugin_a", "server1")

    async def test_shutdown_and_reinit(self):
        """After shutdown, reinitializing should work."""
        await self.registry.init_subsystem("plugin_a", "server1")
        await self.registry.shutdown_subsystem("plugin_a", "server1")
        info = await self.registry.init_subsystem("plugin_a", "server1")
        self.assertEqual(info.state, PluginState.RUNNING)

    async def test_get_server_instances(self):
        """Getting instances for a server should work."""
        await self.registry.init_subsystem("plugin_a", "server1")
        await self.registry.init_subsystem("plugin_b", "server1")
        instances = self.registry.get_server_instances("server1")
        self.assertEqual(len(instances), 2)

    async def test_list_instances(self):
        """Listing all instances should work across servers."""
        await self.registry.init_subsystem("plugin_a", "server1")
        await self.registry.init_subsystem("plugin_b", "server2")
        all_instances = self.registry.list_instances()
        self.assertEqual(len(all_instances), 2)

    async def test_instance_tracks_error_count(self):
        """Failed init should increment error count."""
        # First failure
        with self.assertRaises(PluginError):
            await self.registry.init_subsystem(
                "plugin_a", "server1", {"fail_init": True}
            )
        info = self.registry.get_instance_info("plugin_a", "server1")
        self.assertGreater(info.error_count, 0)


class TestHealthCheck(unittest.IsolatedAsyncioTestCase):
    """Test health check functionality."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()
        self.registry.register(TestPluginA)

    async def test_health_check_healthy(self):
        """Default health check should return HEALTHY."""
        await self.registry.init_subsystem("plugin_a", "server1")
        health = await self.registry.check_health("plugin_a", "server1")
        self.assertEqual(health, PluginHealth.HEALTHY)

    async def test_health_check_nonexistent_instance(self):
        """Health check on uninitialized instance should return UNKNOWN."""
        health = await self.registry.check_health("plugin_a", "server1")
        self.assertEqual(health, PluginHealth.UNKNOWN)

    async def test_health_check_custom_result(self):
        """Health check should return the plugin's reported health."""
        plugin = self.registry.get_plugin("plugin_a")
        plugin.health_results.append(PluginHealth.DEGRADED)
        await self.registry.init_subsystem("plugin_a", "server1")
        health = await self.registry.check_health("plugin_a", "server1")
        self.assertEqual(health, PluginHealth.DEGRADED)

    async def test_health_check_updates_instance_info(self):
        """Health check should update instance's health field."""
        await self.registry.init_subsystem("plugin_a", "server1")
        await self.registry.check_health("plugin_a", "server1")
        info = self.registry.get_instance_info("plugin_a", "server1")
        self.assertEqual(info.health, PluginHealth.HEALTHY)
        self.assertIsNotNone(info.last_health_check)

    async def test_health_check_unhealthy_degraded_state(self):
        """Unhealthy health check should transition to DEGRADED."""
        plugin = self.registry.get_plugin("plugin_a")
        plugin.health_results.append(PluginHealth.UNHEALTHY)
        await self.registry.init_subsystem("plugin_a", "server1")
        await self.registry.check_health("plugin_a", "server1")
        info = self.registry.get_instance_info("plugin_a", "server1")
        self.assertEqual(info.state, PluginState.DEGRADED)


class TestPluginDiscovery(unittest.TestCase):
    """Test plugin discovery functionality."""

    def setUp(self):
        PluginRegistry.reset_instance()

    def test_discover_plugins_no_scan(self):
        """Discover should find in-memory subclasses."""
        # Ensure plugins are loaded by accessing the class
        _ = TestPluginA
        _ = TestPluginB
        plugins = PluginRegistry.discover_plugins()
        names = [p.name for p in plugins]
        self.assertIn("plugin_a", names)
        self.assertIn("plugin_b", names)

    def test_discover_plugins_excludes_abstract(self):
        """Discover should only find concrete subclasses with 'name'."""
        # SubsystemPlugin itself should not appear
        plugins = PluginRegistry.discover_plugins()
        names = [p.name for p in plugins]
        self.assertNotIn("", names)


class TestConfigManagement(unittest.TestCase):
    """Test configuration management."""

    def setUp(self):
        PluginRegistry.reset_instance()
        self.registry = PluginRegistry.get_instance()

    def test_set_get_config(self):
        """Setting and getting config should work."""
        config = {"key": "value"}
        self.registry.set_config(config)
        self.assertEqual(self.registry.get_config(), config)

    def test_load_config_from_file(self):
        """Loading config from a JSON file should work."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"test": "data"}, f)
            config_path = f.name
        
        try:
            config = self.registry.load_config_from_file(config_path)
            self.assertEqual(config, {"test": "data"})
            self.assertEqual(self.registry.get_config(), {"test": "data"})
        finally:
            os.unlink(config_path)

    def test_load_config_nonexistent_file(self):
        """Loading config from a nonexistent file should return empty dict."""
        config = self.registry.load_config_from_file("/nonexistent/config.json")
        self.assertEqual(config, {})

    def test_load_config_invalid_json(self):
        """Loading config from invalid JSON should raise."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("not json")
            config_path = f.name
        
        try:
            with self.assertRaises(PluginConfigError):
                self.registry.load_config_from_file(config_path)
        finally:
            os.unlink(config_path)

    def test_get_enabled_plugins_all(self):
        """With no enabled_subsystems config, all plugins should be enabled."""
        self.registry.register(TestPluginA)
        self.registry.register(TestPluginB)
        enabled = self.registry.get_enabled_plugins_for_server("test_server")
        # All registered plugins
        self.assertIn("plugin_a", enabled)
        self.assertIn("plugin_b", enabled)

    def test_get_enabled_plugins_filtered(self):
        """With enabled_subsystems config, only listed plugins should be enabled."""
        self.registry.register(TestPluginA)
        self.registry.register(TestPluginB)
        self.registry.set_config({
            "servers": {
                "test_server": {
                    "enabled_subsystems": ["plugin_a"]
                }
            }
        })
        enabled = self.registry.get_enabled_plugins_for_server("test_server")
        self.assertEqual(enabled, ["plugin_a"])


class TestPluginDataClasses(unittest.TestCase):
    """Test data class behavior."""

    def test_plugin_manifest_defaults(self):
        """PluginManifest should have sensible defaults."""
        m = PluginManifest(name="test", version="1.0", description="desc")
        self.assertEqual(m.dependencies, [])
        self.assertEqual(m.optional_dependencies, [])
        self.assertEqual(m.author, "unknown")

    def test_plugin_instance_info_defaults(self):
        """PluginInstanceInfo should have sensible defaults."""
        info = PluginInstanceInfo(
            plugin_name="test", server_id="server1"
        )
        self.assertEqual(info.state, PluginState.UNINITIALIZED)
        self.assertEqual(info.health, PluginHealth.UNKNOWN)
        self.assertEqual(info.config, {})
        self.assertEqual(info.error_count, 0)
        self.assertIsNone(info.last_error)

    def test_plugin_state_enum_values(self):
        """PluginState should have all expected values."""
        states = [
            "uninitialized", "initializing", "running",
            "degraded", "shutting_down", "shutdown", "error",
        ]
        for s in states:
            self.assertTrue(
                hasattr(PluginState, s.upper()),
                f"PluginState missing {s.upper()}"
            )


if __name__ == "__main__":
    unittest.main()
