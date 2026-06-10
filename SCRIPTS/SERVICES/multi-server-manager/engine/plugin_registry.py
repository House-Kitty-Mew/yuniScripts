"""
╔══════════════════════════════════════════════════════════════════════╗
║  Multi-Server Manager — Subsystem Plugin Registry                  ║
║  Plugin discovery, registration, and management system             ║
╚══════════════════════════════════════════════════════════════════════╝

DESIGN:
  - ABC-based SubsystemPlugin base class with lifecycle hooks
  - PluginRegistry singleton that discovers, registers, and manages plugins
  - Config-driven per-server on/off toggle via JSON config
  - Dependency resolution for ordered startup/shutdown
  - Thread-safe operations

USAGE:
    from engine.plugin_registry import PluginRegistry, SubsystemPlugin

    class MyPlugin(SubsystemPlugin):
        name = "my_plugin"
        version = "1.0.0"
        dependencies = ["database"]
        description = "My custom subsystem"

        async def on_init(self, server_id, config):
            # Initialize subsystem for server_id
            pass

        async def on_shutdown(self, server_id):
            # Clean shutdown for server_id
            pass

    registry = PluginRegistry.get_instance()
    registry.register(MyPlugin)
    await registry.init_subsystem("my_plugin", "server1", {})
"""

import abc
import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Type, Any, Callable


# ──────────────────────────────────────────────────────────────────────────────
# Async-Compatible Lock
# ──────────────────────────────────────────────────────────────────────────────

class AsyncCompatibleLock:
    """
    A lock that supports BOTH sync and async context manager protocols.
    
    Uses threading.Lock internally. For sync usage, acquire/release directly.
    For async usage, uses run_in_executor to avoid blocking the event loop.
    
    Usage:
        lock = AsyncCompatibleLock()
        
        # Sync usage
        with lock:
            ...
        
        # Async usage
        async with lock:
            ...
    """
    
    def __init__(self):
        self._lock = threading.Lock()
    
    def __enter__(self):
        self._lock.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()
    
    async def __aenter__(self):
        await asyncio.get_running_loop().run_in_executor(None, self._lock.acquire)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()

logger = logging.getLogger("plugin_registry")


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class PluginError(Exception):
    """Base exception for plugin system errors."""
    pass

class PluginNotFoundError(PluginError):
    """Raised when a requested plugin is not registered."""
    pass

class PluginDependencyError(PluginError):
    """Raised when plugin dependencies cannot be satisfied."""
    pass

class PluginStateError(PluginError):
    """Raised when an operation is invalid for the current plugin state."""
    pass

class PluginConfigError(PluginError):
    """Raised when plugin configuration is invalid."""
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────

class PluginState(Enum):
    """Lifecycle state of a subsystem plugin instance."""
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    RUNNING = "running"
    DEGRADED = "degraded"
    SHUTTING_DOWN = "shutting_down"
    SHUTDOWN = "shutdown"
    ERROR = "error"


class PluginHealth(Enum):
    """Health status of a running subsystem plugin."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PluginManifest:
    """Manifest metadata for a registered plugin."""
    name: str
    version: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    optional_dependencies: List[str] = field(default_factory=list)
    author: str = "unknown"
    homepage: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class PluginInstanceInfo:
    """Runtime information about a plugin instance on a specific server."""
    plugin_name: str
    server_id: str
    state: PluginState = PluginState.UNINITIALIZED
    health: PluginHealth = PluginHealth.UNKNOWN
    config: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    last_health_check: Optional[datetime] = None
    error_count: int = 0
    last_error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# Base Plugin Class
# ──────────────────────────────────────────────────────────────────────────────

class SubsystemPlugin(abc.ABC):
    """
    Abstract base class for all subsystem plugins.
    
    Subclasses MUST define:
        name (str): Unique plugin identifier
        version (str): Semantic version string
        description (str): Human-readable description
    
    Subclasses CAN define:
        dependencies (List[str]): Required plugin names
        optional_dependencies (List[str]): Optional plugin names
        tags (List[str]): Categorization tags
    
    Lifecycle hooks (all async):
        on_init(server_id, config): Initialize plugin for server
        on_shutdown(server_id): Clean shutdown for server
        on_health_check(server_id): Return PluginHealth
        on_config_change(server_id, old_config, new_config): Handle config updates
    """
    
    # --- Required class attributes ---
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    
    # --- Optional class attributes ---
    dependencies: List[str] = []
    optional_dependencies: List[str] = []
    tags: List[str] = []
    author: str = "unknown"
    homepage: str = ""
    
    def __init_subclass__(cls, **kwargs):
        """Validate that subclasses define required attributes."""
        super().__init_subclass__(**kwargs)
        if not cls.name:
            raise PluginConfigError(
                f"Plugin class {cls.__name__} must define 'name'"
            )
        if not cls.version:
            raise PluginConfigError(
                f"Plugin class {cls.__name__} ({cls.name}) must define 'version'"
            )
        if not cls.description:
            raise PluginConfigError(
                f"Plugin class {cls.__name__} ({cls.name}) must define 'description'"
            )
    
    @property
    def manifest(self) -> PluginManifest:
        """Return the plugin manifest derived from class attributes."""
        return PluginManifest(
            name=self.name,
            version=self.version,
            description=self.description,
            dependencies=list(self.dependencies),
            optional_dependencies=list(self.optional_dependencies),
            author=self.author,
            homepage=self.homepage,
            tags=list(self.tags),
        )
    
    # ─── Lifecycle Hooks ──────────────────────────────────────────────
    
    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        """
        Initialize the plugin for a specific server.
        
        Args:
            server_id: Unique identifier for the server instance
            config: Plugin-specific configuration dictionary
        
        Raises:
            PluginError: If initialization fails
        """
        pass
    
    async def on_shutdown(self, server_id: str) -> None:
        """
        Gracefully shut down the plugin for a specific server.
        
        Args:
            server_id: Unique identifier for the server instance
        """
        pass
    
    async def on_health_check(self, server_id: str) -> PluginHealth:
        """
        Perform a health check for this plugin on a specific server.
        
        Args:
            server_id: Unique identifier for the server instance
        
        Returns:
            PluginHealth status
        """
        return PluginHealth.HEALTHY
    
    async def on_config_change(
        self,
        server_id: str,
        old_config: Dict[str, Any],
        new_config: Dict[str, Any]
    ) -> None:
        """
        Handle configuration changes for a server.
        
        Args:
            server_id: Unique identifier for the server instance
            old_config: Previous configuration dictionary
            new_config: New configuration dictionary
        """
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Plugin Registry
# ──────────────────────────────────────────────────────────────────────────────

class PluginRegistry:
    """
    Singleton registry that manages all subsystem plugins.
    
    Features:
    - Thread-safe plugin registration
    - Dependency resolution for startup/shutdown ordering
    - Per-plugin, per-server instance state tracking
    - Config-driven enabling/disabling per server
    - Plugin discovery via subclass scanning
    """
    
    _instance: Optional['PluginRegistry'] = None
    _instance_lock: threading.Lock = threading.Lock()
    
    def __init__(self):
        """Initialize the plugin registry."""
        self._lock = AsyncCompatibleLock()
        self._plugins: Dict[str, SubsystemPlugin] = {}
        self._instances: Dict[Tuple[str, str], PluginInstanceInfo] = {}
        self._config: Dict[str, Any] = {}
        self._initialized = False
    
    @classmethod
    def get_instance(cls) -> 'PluginRegistry':
        """Get or create the singleton PluginRegistry instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._instance_lock:
            cls._instance = None
    
    # ─── Plugin Registration ──────────────────────────────────────────
    
    def register(self, plugin_class: Type[SubsystemPlugin]) -> SubsystemPlugin:
        """
        Register a plugin class.
        
        Creates an instance of the plugin class and registers it by name.
        
        Args:
            plugin_class: A SubsystemPlugin subclass
        
        Returns:
            The plugin instance
        
        Raises:
            PluginError: If registration fails
            PluginConfigError: If plugin has invalid config
        """
        if not issubclass(plugin_class, SubsystemPlugin):
            raise PluginConfigError(
                f"{plugin_class.__name__} is not a subclass of SubsystemPlugin"
            )
        
        with self._lock:
            # Check for duplicate registration
            if plugin_class.name in self._plugins:
                existing = self._plugins[plugin_class.name]
                if type(existing) is not plugin_class:
                    raise PluginError(
                        f"Plugin '{plugin_class.name}' is already registered "
                        f"by a different class: {type(existing).__name__}"
                    )
                return existing
            
            # Create instance
            instance = plugin_class()
            self._plugins[plugin_class.name] = instance
            logger.info(
                "Registered plugin: %s v%s (%s)",
                instance.name, instance.version, instance.description
            )
            return instance
    
    def unregister(self, plugin_name: str) -> None:
        """
        Unregister a plugin by name.
        
        Args:
            plugin_name: Name of the plugin to unregister
        
        Raises:
            PluginNotFoundError: If plugin is not registered
            PluginStateError: If plugin has active instances
        """
        with self._lock:
            if plugin_name not in self._plugins:
                raise PluginNotFoundError(
                    f"Plugin '{plugin_name}' is not registered"
                )
            
            # Check for active instances
            active_instances = [
                key for key in self._instances
                if key[0] == plugin_name
                and self._instances[key].state not in (
                    PluginState.UNINITIALIZED, PluginState.SHUTDOWN
                )
            ]
            if active_instances:
                raise PluginStateError(
                    f"Plugin '{plugin_name}' has {len(active_instances)} "
                    f"active instance(s). Shut them down first."
                )
            
            del self._plugins[plugin_name]
            logger.info("Unregistered plugin: %s", plugin_name)
    
    def get_plugin(self, plugin_name: str) -> SubsystemPlugin:
        """
        Get a registered plugin by name.
        
        Args:
            plugin_name: Name of the plugin
        
        Returns:
            The plugin instance
        
        Raises:
            PluginNotFoundError: If plugin is not registered
        """
        with self._lock:
            if plugin_name not in self._plugins:
                raise PluginNotFoundError(
                    f"Plugin '{plugin_name}' is not registered. "
                    f"Registered plugins: {list(self._plugins.keys())}"
                )
            return self._plugins[plugin_name]
    
    def list_plugins(self) -> List[PluginManifest]:
        """
        List all registered plugins with their manifests.
        
        Returns:
            List of PluginManifest objects
        """
        with self._lock:
            return [
                plugin.manifest
                for plugin in self._plugins.values()
            ]
    
    def has_plugin(self, plugin_name: str) -> bool:
        """Check if a plugin is registered."""
        with self._lock:
            return plugin_name in self._plugins
    
    @property
    def plugin_count(self) -> int:
        """Number of registered plugins."""
        with self._lock:
            return len(self._plugins)
    
    # ─── Dependency Resolution ────────────────────────────────────────
    
    def get_startup_order(self, plugin_names: Optional[List[str]] = None) -> List[str]:
        """
        Resolve startup order respecting dependencies.
        
        Uses topological sort (Kahn's algorithm).
        
        Args:
            plugin_names: Subset of plugins to order (None = all)
        
        Returns:
            List of plugin names in startup order
        
        Raises:
            PluginDependencyError: If dependency cycle is detected
            PluginNotFoundError: If a dependency is not registered
        """
        with self._lock:
            names = plugin_names or list(self._plugins.keys())
            
            # Validate all names exist
            for name in names:
                if name not in self._plugins:
                    raise PluginNotFoundError(
                        f"Plugin '{name}' is not registered"
                    )
            
            # Build dependency graph
            graph: Dict[str, Set[str]] = {}
            for name in names:
                plugin = self._plugins[name]
                deps = set(
                    d for d in plugin.dependencies
                    if d in names  # only include dependencies in the subset
                )
                graph[name] = deps
            
            # Topological sort (Kahn's algorithm)
            # Build reverse adjacency: for each dep, who depends on it?
            dependents: Dict[str, Set[str]] = {n: set() for n in names}
            for name, deps in graph.items():
                for dep in deps:
                    if dep in dependents:
                        dependents[dep].add(name)
            
            # In-degree = number of prerequisites for each node
            in_degree: Dict[str, int] = {n: len(deps) for n, deps in graph.items()}
            
            queue = [n for n, d in in_degree.items() if d == 0]
            result = []
            
            while queue:
                node = queue.pop(0)
                result.append(node)
                for dependent in dependents.get(node, set()):
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)
            
            if len(result) != len(names):
                raise PluginDependencyError(
                    f"Dependency cycle detected. Cannot resolve startup order. "
                    f"Processed {len(result)}/{len(names)} plugins."
                )
            
            return result
    
    def get_shutdown_order(self, plugin_names: Optional[List[str]] = None) -> List[str]:
        """
        Resolve shutdown order (reverse of startup order).
        
        Args:
            plugin_names: Subset of plugins to order (None = all)
        
        Returns:
            List of plugin names in shutdown order (dependents first)
        """
        startup_order = self.get_startup_order(plugin_names)
        return list(reversed(startup_order))
    
    # ─── Instance Management ──────────────────────────────────────────
    
    async def init_subsystem(
        self,
        plugin_name: str,
        server_id: str,
        config: Optional[Dict[str, Any]] = None
    ) -> PluginInstanceInfo:
        """
        Initialize a subsystem plugin for a specific server.
        
        Args:
            plugin_name: Name of the registered plugin
            server_id: Unique identifier for the server
            config: Plugin-specific configuration
        
        Returns:
            PluginInstanceInfo with current state
        
        Raises:
            PluginNotFoundError: If plugin is not registered
            PluginStateError: If already initialized for this server
        """
        plugin = self.get_plugin(plugin_name)
        config = config or {}
        
        async with self._lock:
            key = (plugin_name, server_id)
            
            # Check if already initialized
            if key in self._instances:
                existing = self._instances[key]
                if existing.state in (PluginState.RUNNING, PluginState.DEGRADED):
                    raise PluginStateError(
                        f"Plugin '{plugin_name}' is already initialized "
                        f"for server '{server_id}' (state: {existing.state.value})"
                    )
            
            # Create or update instance info
            info = PluginInstanceInfo(
                plugin_name=plugin_name,
                server_id=server_id,
                state=PluginState.INITIALIZING,
                config=config,
            )
            self._instances[key] = info
        
        try:
            # Call lifecycle hook (outside lock)
            await plugin.on_init(server_id, config)
            
            # Update state on success
            async with self._lock:
                if key in self._instances:
                    self._instances[key].state = PluginState.RUNNING
                    self._instances[key].started_at = datetime.now(timezone.utc)
                    self._instances[key].last_health_check = datetime.now(timezone.utc)
                    self._instances[key].health = PluginHealth.HEALTHY
            
            logger.info(
                "Subsystem '%s' initialized for server '%s'",
                plugin_name, server_id
            )
            
        except Exception as e:
            # Update state on failure
            async with self._lock:
                if key in self._instances:
                    self._instances[key].state = PluginState.ERROR
                    self._instances[key].last_error = str(e)
                    self._instances[key].error_count += 1
            raise PluginError(
                f"Failed to initialize plugin '{plugin_name}' "
                f"for server '{server_id}': {e}"
            ) from e
        
        return self._instances.get(key, info)
    
    async def shutdown_subsystem(self, plugin_name: str, server_id: str) -> None:
        """
        Shut down a subsystem plugin for a specific server.
        
        Args:
            plugin_name: Name of the registered plugin
            server_id: Unique identifier for the server
        
        Raises:
            PluginNotFoundError: If plugin is not registered
            PluginStateError: If not initialized
        """
        plugin = self.get_plugin(plugin_name)
        key = (plugin_name, server_id)
        
        async with self._lock:
            if key not in self._instances:
                raise PluginStateError(
                    f"Plugin '{plugin_name}' is not initialized for server '{server_id}'"
                )
            self._instances[key].state = PluginState.SHUTTING_DOWN
        
        try:
            # Call lifecycle hook (outside lock)
            await plugin.on_shutdown(server_id)
        except Exception as e:
            logger.error(
                "Error shutting down plugin '%s' for server '%s': %s",
                plugin_name, server_id, e
            )
        
        # Update state
        async with self._lock:
            if key in self._instances:
                self._instances[key].state = PluginState.SHUTDOWN
        
        logger.info(
            "Subsystem '%s' shut down for server '%s'",
            plugin_name, server_id
        )
    
    async def check_health(self, plugin_name: str, server_id: str) -> PluginHealth:
        """
        Perform a health check on a subsystem instance.
        
        Args:
            plugin_name: Name of the registered plugin
            server_id: Unique identifier for the server
        
        Returns:
            PluginHealth status
        """
        plugin = self.get_plugin(plugin_name)
        key = (plugin_name, server_id)
        
        if key not in self._instances:
            return PluginHealth.UNKNOWN
        
        try:
            health = await plugin.on_health_check(server_id)
        except Exception as e:
            logger.warning(
                "Health check failed for '%s' on '%s': %s",
                plugin_name, server_id, e
            )
            health = PluginHealth.UNHEALTHY
        
        async with self._lock:
            if key in self._instances:
                self._instances[key].health = health
                self._instances[key].last_health_check = datetime.now(timezone.utc)
                if health == PluginHealth.UNHEALTHY:
                    self._instances[key].error_count += 1
                    if self._instances[key].state == PluginState.RUNNING:
                        self._instances[key].state = PluginState.DEGRADED
        
        return health
    
    def get_instance_info(
        self, plugin_name: str, server_id: str
    ) -> Optional[PluginInstanceInfo]:
        """Get instance info for a specific plugin on a server."""
        key = (plugin_name, server_id)
        with self._lock:
            return self._instances.get(key)
    
    def get_server_instances(self, server_id: str) -> List[PluginInstanceInfo]:
        """Get all plugin instances for a specific server."""
        with self._lock:
            return [
                info for key, info in self._instances.items()
                if key[1] == server_id
            ]
    
    def list_instances(self) -> List[PluginInstanceInfo]:
        """List all plugin instances across all servers."""
        with self._lock:
            return list(self._instances.values())
    
    # ─── Configuration ────────────────────────────────────────────────
    
    def set_config(self, config: Dict[str, Any]) -> None:
        """Set the global configuration."""
        with self._lock:
            self._config = config
    
    def get_config(self) -> Dict[str, Any]:
        """Get the global configuration."""
        with self._lock:
            return dict(self._config)
    
    def get_enabled_plugins_for_server(self, server_id: str) -> List[str]:
        """
        Get the list of enabled plugins for a specific server.
        
        Reads from config: config.servers[server_id].enabled_subsystems
        If not configured, returns all registered plugins.
        """
        with self._lock:
            servers = self._config.get("servers", {})
            server_config = servers.get(server_id, {})
            enabled = server_config.get("enabled_subsystems", None)
            if enabled is not None:
                return [e for e in enabled if e in self._plugins]
            return list(self._plugins.keys())
    
    # ─── Plugin Discovery ─────────────────────────────────────────────
    
    @staticmethod
    def discover_plugins(
        package_path: Optional[str] = None,
        base_class: Type = SubsystemPlugin
    ) -> List[Type[SubsystemPlugin]]:
        """
        Discover SubsystemPlugin subclasses by scanning modules.
        
        Args:
            package_path: Optional path to scan for plugin modules
            base_class: Base class to search for subclasses
        
        Returns:
            List of SubsystemPlugin subclasses found
        """
        plugins = []
        
        # Scan all subclasses of base_class loaded in memory
        for cls in base_class.__subclasses__():
            if not cls.name:
                continue
            plugins.append(cls)
        
        # If a package path is given, attempt to import modules
        if package_path:
            pkg_path = Path(package_path)
            if pkg_path.is_dir():
                for py_file in sorted(pkg_path.glob("*.py")):
                    if py_file.name.startswith("_"):
                        continue
                    module_name = py_file.stem
                    try:
                        import importlib.util
                        spec = importlib.util.spec_from_file_location(
                            module_name, str(py_file)
                        )
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
                    except Exception as e:
                        logger.warning(
                            "Failed to load plugin module '%s': %s",
                            module_name, e
                        )
        
        # Re-scan after loading modules
        discovered = []
        for cls in base_class.__subclasses__():
            if cls not in plugins and cls.name:
                discovered.append(cls)
        
        return plugins + discovered
    
    def load_config_from_file(self, config_path: str) -> Dict[str, Any]:
        """
        Load configuration from a JSON file.
        
        Args:
            config_path: Path to JSON config file
        
        Returns:
            Configuration dictionary
        """
        path = Path(config_path)
        if not path.exists():
            logger.warning("Config file not found: %s", config_path)
            return {}
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
            self.set_config(config)
            return config
        except (json.JSONDecodeError, OSError) as e:
            raise PluginConfigError(
                f"Failed to load config from '{config_path}': {e}"
            )
