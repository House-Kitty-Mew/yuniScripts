"""
╔══════════════════════════════════════════════════════════════════════╗
║  Multi-Server Manager — Subsystem Lifecycle Manager                 ║
║  Init, shutdown, health checks, and dependency resolution           ║
╚══════════════════════════════════════════════════════════════════════╝

DESIGN:
  - Coordinates plugin_registry + vfs_db_isolation for full lifecycle
  - Handles ordered startup (respecting plugin dependencies)
  - Graceful shutdown with configurable timeout
  - Periodic health check polling with auto-restart
  - Event emission for state changes

USAGE:
    from engine.lifecycle_manager import LifecycleManager
    from engine.plugin_registry import PluginRegistry

    registry = PluginRegistry.get_instance()
    mgr = LifecycleManager(registry)
    await mgr.start_server("server1")
    await mgr.shutdown_server("server1")
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any, Callable, Awaitable

from engine.plugin_registry import (
    PluginRegistry,
    SubsystemPlugin,
    PluginState,
    PluginHealth,
    PluginError,
    PluginNotFoundError,
    PluginStateError,
    PluginDependencyError,
    PluginInstanceInfo,
)
from engine.vfs_db_isolation import (
    VFSDatabaseManager,
    VFSDatabase,
    VFSDBError,
)

logger = logging.getLogger("lifecycle_manager")


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class LifecycleError(Exception):
    """Base exception for lifecycle manager errors."""
    pass

class ServerNotFoundError(LifecycleError):
    """Raised when a server is not found."""
    pass

class ServerStateError(LifecycleError):
    """Raised when an operation is invalid for the current server state."""
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ServerInfo:
    """Runtime information about a managed server."""
    server_id: str
    display_name: str
    state: str = "stopped"
    enabled_subsystems: List[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    healthy: bool = True
    health_message: str = ""
    plugin_count: int = 0
    error_count: int = 0


@dataclass
class LifecycleEvent:
    """Event emitted during lifecycle state changes."""
    event_type: str  # "start", "stop", "health", "error", "restart"
    server_id: str
    plugin_name: Optional[str] = None
    timestamp: str = ""
    message: str = ""
    success: bool = True


# ──────────────────────────────────────────────────────────────────────────────
# Lifecycle Manager
# ──────────────────────────────────────────────────────────────────────────────

class LifecycleManager:
    """
    Manages the lifecycle of subsystem plugins across multiple servers.
    
    Features:
    - Ordered startup/shutdown with dependency resolution
    - Periodic health check polling
    - Auto-restart of failed subsystems
    - Event emission for monitoring
    - Graceful shutdown with timeout
    """
    
    def __init__(
        self,
        plugin_registry: PluginRegistry,
        vfs_manager: Optional[VFSDatabaseManager] = None,
        vfs_data_root: str = "DATA/vfs",
        health_check_interval: float = 30.0,
        shutdown_timeout: float = 10.0,
        auto_restart: bool = True,
        max_restart_attempts: int = 3,
    ):
        self.registry = plugin_registry
        self.vfs = vfs_manager or VFSDatabaseManager(data_root=vfs_data_root)
        self.health_check_interval = health_check_interval
        self.shutdown_timeout = shutdown_timeout
        self.auto_restart = auto_restart
        self.max_restart_attempts = max_restart_attempts
        
        self._lock = threading.RLock()
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._health_tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._event_handlers: List[Callable[[LifecycleEvent], Awaitable[None]]] = []
        self._restart_counts: Dict[Tuple[str, str], int] = {}
    
    # ─── Event System ────────────────────────────────────────────────
    
    def add_event_handler(
        self, handler: Callable[[LifecycleEvent], Awaitable[None]]
    ) -> None:
        """Register an async event handler."""
        self._event_handlers.append(handler)
    
    def remove_event_handler(
        self, handler: Callable[[LifecycleEvent], Awaitable[None]]
    ) -> None:
        """Remove a registered event handler."""
        if handler in self._event_handlers:
            self._event_handlers.remove(handler)
    
    async def _emit_event(self, event: LifecycleEvent) -> None:
        """Emit an event to all registered handlers."""
        if not event.timestamp:
            event.timestamp = datetime.now(timezone.utc).isoformat()
        
        for handler in self._event_handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error("Event handler error: %s", e)
    
    # ─── Server Management ───────────────────────────────────────────
    
    def register_server(
        self,
        server_id: str,
        display_name: str = "",
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Register a server with the lifecycle manager.
        
        Args:
            server_id: Unique server identifier
            display_name: Human-readable server name
            config: Server-specific configuration
        
        Raises:
            LifecycleError: If server already registered
        """
        config = config or {}
        with self._lock:
            if server_id in self._servers:
                raise LifecycleError(
                    f"Server '{server_id}' is already registered"
                )
            
            self._servers[server_id] = {
                "id": server_id,
                "display_name": display_name or server_id,
                "config": config,
                "state": "stopped",
                "started_at": None,
                "error_count": 0,
            }
            logger.info("Registered server: %s (%s)", server_id, display_name)
    
    def unregister_server(self, server_id: str) -> None:
        """Unregister a server (must be stopped first)."""
        with self._lock:
            if server_id not in self._servers:
                raise ServerNotFoundError(
                    f"Server '{server_id}' is not registered"
                )
            if self._servers[server_id]["state"] != "stopped":
                raise ServerStateError(
                    f"Server '{server_id}' must be stopped before unregistering"
                )
            del self._servers[server_id]
    
    def list_servers(self) -> List[ServerInfo]:
        """List all registered servers with their status."""
        with self._lock:
            result = []
            for sid, info in self._servers.items():
                instances = self.registry.get_server_instances(sid)
                result.append(ServerInfo(
                    server_id=sid,
                    display_name=info.get("display_name", sid),
                    state=info.get("state", "stopped"),
                    enabled_subsystems=self.registry.get_enabled_plugins_for_server(sid),
                    started_at=info.get("started_at"),
                    healthy=info.get("healthy", True),
                    error_count=info.get("error_count", 0),
                    plugin_count=len(instances),
                ))
            return result
    
    def get_server_info(self, server_id: str) -> ServerInfo:
        """Get info for a specific server."""
        with self._lock:
            if server_id not in self._servers:
                raise ServerNotFoundError(
                    f"Server '{server_id}' is not registered"
                )
            info = self._servers[server_id]
            instances = self.registry.get_server_instances(server_id)
            return ServerInfo(
                server_id=server_id,
                display_name=info.get("display_name", server_id),
                state=info.get("state", "stopped"),
                enabled_subsystems=self.registry.get_enabled_plugins_for_server(server_id),
                started_at=info.get("started_at"),
                healthy=info.get("healthy", True),
                error_count=info.get("error_count", 0),
                plugin_count=len(instances),
            )
    
    # ─── Startup / Shutdown ──────────────────────────────────────────
    
    async def start_server(self, server_id: str) -> bool:
        """
        Start all enabled subsystems for a server.
        
        Uses dependency-ordered startup. Initializes VFS databases
        and plugin instances for each enabled subsystem.
        
        Args:
            server_id: Server to start
        
        Returns:
            True if all subsystems started successfully
        
        Raises:
            ServerNotFoundError: If server not registered
        """
        with self._lock:
            if server_id not in self._servers:
                raise ServerNotFoundError(
                    f"Server '{server_id}' is not registered. "
                    f"Call register_server() first."
                )
            
            self._servers[server_id]["state"] = "starting"
        
        enabled = self.registry.get_enabled_plugins_for_server(server_id)
        if not enabled:
            logger.warning(
                "No enabled subsystems for server '%s'", server_id
            )
            with self._lock:
                self._servers[server_id]["state"] = "running"
                self._servers[server_id]["started_at"] = datetime.now(timezone.utc)
            return True
        
        # Get startup order
        try:
            startup_order = self.registry.get_startup_order(enabled)
        except PluginDependencyError as e:
            with self._lock:
                self._servers[server_id]["state"] = "error"
            await self._emit_event(LifecycleEvent(
                event_type="error",
                server_id=server_id,
                message=f"Dependency resolution failed: {e}",
                success=False,
            ))
            raise LifecycleError(str(e))
        
        # Start each subsystem in order
        all_success = True
        for plugin_name in startup_order:
            try:
                await self._start_subsystem(server_id, plugin_name)
            except Exception as e:
                logger.error(
                    "Failed to start subsystem '%s' for server '%s': %s",
                    plugin_name, server_id, e
                )
                all_success = False
                with self._lock:
                    self._servers[server_id]["error_count"] = \
                        self._servers[server_id].get("error_count", 0) + 1
        
        # Update server state
        with self._lock:
            self._servers[server_id]["state"] = "running" if all_success else "degraded"
            self._servers[server_id]["started_at"] = datetime.now(timezone.utc)
        
        # Start health check loop
        if server_id not in self._health_tasks:
            self._health_tasks[server_id] = asyncio.create_task(
                self._health_check_loop(server_id)
            )
        
        await self._emit_event(LifecycleEvent(
            event_type="start",
            server_id=server_id,
            message=f"Server started with {len(startup_order)} subsystems "
                    f"({'all ok' if all_success else 'partial failures'})",
            success=all_success,
        ))
        
        return all_success
    
    async def _start_subsystem(
        self, server_id: str, plugin_name: str
    ) -> None:
        """
        Start a single subsystem for a server.
        
        1. Create/get VFS database
        2. Initialize plugin via registry
        
        Args:
            server_id: Server identifier
            plugin_name: Plugin name
        """
        plugin = self.registry.get_plugin(plugin_name)
        
        # Create VFS database for this subsystem+server
        db = self.vfs.get_database(plugin_name, server_id, auto_open=True)
        
        # Initialize the plugin
        server_config = self._get_server_config(server_id)
        plugin_config = server_config.get(plugin_name, {})
        
        await self.registry.init_subsystem(
            plugin_name, server_id, plugin_config
        )
        
        logger.info(
            "Started subsystem '%s' for server '%s'",
            plugin_name, server_id
        )
    
    async def shutdown_server(self, server_id: str) -> bool:
        """
        Shut down all subsystems for a server (reverse dependency order).
        
        Args:
            server_id: Server to shut down
        
        Returns:
            True if all subsystems shut down cleanly
        """
        with self._lock:
            if server_id not in self._servers:
                raise ServerNotFoundError(
                    f"Server '{server_id}' is not registered"
                )
            self._servers[server_id]["state"] = "stopping"
        
        # Stop health check loop
        if server_id in self._health_tasks:
            self._health_tasks[server_id].cancel()
            try:
                await self._health_tasks[server_id]
            except asyncio.CancelledError:
                pass
            del self._health_tasks[server_id]
        
        # Get instances to shut down
        instances = self.registry.get_server_instances(server_id)
        plugin_names = [i.plugin_name for i in instances]
        
        # No instances to shut down - server is already clean
        if not plugin_names:
            with self._lock:
                self._servers[server_id]["state"] = "stopped"
            await self._emit_event(LifecycleEvent(
                event_type="stop",
                server_id=server_id,
                message="Server shut down (no active subsystems)",
                success=True,
            ))
            return True
        
        # Get shutdown order (reverse of startup)
        try:
            shutdown_order = self.registry.get_shutdown_order(plugin_names)
        except PluginDependencyError:
            # If dependency resolution fails, just use reverse
            shutdown_order = list(reversed(plugin_names))
        
        # Shut down each subsystem in order
        all_success = True
        for plugin_name in shutdown_order:
            try:
                await asyncio.wait_for(
                    self.registry.shutdown_subsystem(plugin_name, server_id),
                    timeout=self.shutdown_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Shutdown timeout for '%s' on '%s'",
                    plugin_name, server_id
                )
                all_success = False
            except Exception as e:
                logger.error(
                    "Error shutting down '%s' on '%s': %s",
                    plugin_name, server_id, e
                )
                all_success = False
        
        # Close VFS databases
        for plugin_name in plugin_names:
            try:
                self.vfs.remove_database(plugin_name, server_id, delete_file=False)
            except Exception as e:
                logger.warning(
                    "Error closing VFS DB for '%s' on '%s': %s",
                    plugin_name, server_id, e
                )
        
        with self._lock:
            self._servers[server_id]["state"] = "stopped"
        
        await self._emit_event(LifecycleEvent(
            event_type="stop",
            server_id=server_id,
            message=f"Server shut down ({len(shutdown_order)} subsystems)",
            success=all_success,
        ))
        
        return all_success
    
    # ─── Restart ─────────────────────────────────────────────────────
    
    async def restart_server(self, server_id: str) -> bool:
        """
        Restart a server (shutdown then startup).
        
        Args:
            server_id: Server to restart
        
        Returns:
            True if restart succeeded
        """
        await self._emit_event(LifecycleEvent(
            event_type="restart",
            server_id=server_id,
            message="Restarting server...",
        ))
        
        await self.shutdown_server(server_id)
        result = await self.start_server(server_id)
        
        return result
    
    async def restart_subsystem(
        self, server_id: str, plugin_name: str
    ) -> bool:
        """
        Restart a single subsystem on a server.
        
        Args:
            server_id: Server identifier
            plugin_name: Plugin name to restart
        
        Returns:
            True if restart succeeded
        """
        try:
            await self.registry.shutdown_subsystem(plugin_name, server_id)
            await self._start_subsystem(server_id, plugin_name)
            return True
        except Exception as e:
            logger.error(
                "Failed to restart subsystem '%s' on '%s': %s",
                plugin_name, server_id, e
            )
            return False
    
    # ─── Health Checks ───────────────────────────────────────────────
    
    async def _health_check_loop(self, server_id: str) -> None:
        """Background task that periodically checks subsystem health."""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)
                await self._check_server_health(server_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Health check error for '%s': %s",
                    server_id, e
                )
    
    async def _check_server_health(self, server_id: str) -> None:
        """
        Check health of all subsystems on a server.
        
        Triggers auto-restart for unhealthy subsystems if configured.
        """
        instances = self.registry.get_server_instances(server_id)
        all_healthy = True
        
        for info in instances:
            if info.state not in (PluginState.RUNNING, PluginState.DEGRADED):
                continue
            
            try:
                health = await self.registry.check_health(
                    info.plugin_name, server_id
                )
                
                if health == PluginHealth.UNHEALTHY:
                    all_healthy = False
                    logger.warning(
                        "Unhealthy subsystem '%s' on '%s'",
                        info.plugin_name, server_id
                    )
                    
                    # Auto-restart if configured
                    if self.auto_restart:
                        await self._auto_restart(server_id, info.plugin_name)
                    
            except Exception as e:
                logger.warning(
                    "Health check error for '%s' on '%s': %s",
                    info.plugin_name, server_id, e
                )
                all_healthy = False
        
        # Update server health
        with self._lock:
            if server_id in self._servers:
                self._servers[server_id]["healthy"] = all_healthy
    
    async def _auto_restart(
        self, server_id: str, plugin_name: str
    ) -> None:
        """
        Auto-restart a failed subsystem with retry tracking.
        
        Args:
            server_id: Server identifier
            plugin_name: Plugin name to restart
        """
        key = (server_id, plugin_name)
        count = self._restart_counts.get(key, 0) + 1
        self._restart_counts[key] = count
        
        if count > self.max_restart_attempts:
            logger.error(
                "Max restart attempts (%d) exceeded for '%s' on '%s'",
                self.max_restart_attempts, plugin_name, server_id
            )
            await self._emit_event(LifecycleEvent(
                event_type="error",
                server_id=server_id,
                plugin_name=plugin_name,
                message=f"Max restart attempts exceeded ({count})",
                success=False,
            ))
            return
        
        logger.info(
            "Auto-restarting '%s' on '%s' (attempt %d/%d)",
            plugin_name, server_id, count, self.max_restart_attempts
        )
        
        success = await self.restart_subsystem(server_id, plugin_name)
        
        if success:
            # Reset restart count on success
            self._restart_counts[key] = 0
        
        await self._emit_event(LifecycleEvent(
            event_type="restart",
            server_id=server_id,
            plugin_name=plugin_name,
            message=f"Auto-restart {'succeeded' if success else 'failed'} "
                    f"(attempt {count})",
            success=success,
        ))
    
    # ─── Configuration ───────────────────────────────────────────────
    
    def _get_server_config(self, server_id: str) -> Dict[str, Any]:
        """Get the configuration for a server."""
        with self._lock:
            if server_id in self._servers:
                return self._servers[server_id].get("config", {})
            return {}
    
    def update_server_config(
        self, server_id: str, config: Dict[str, Any]
    ) -> None:
        """Update a server's configuration."""
        with self._lock:
            if server_id not in self._servers:
                raise ServerNotFoundError(
                    f"Server '{server_id}' is not registered"
                )
            self._servers[server_id]["config"] = config
    
    # ─── Status ──────────────────────────────────────────────────────
    
    def get_global_status(self) -> Dict[str, Any]:
        """
        Get a comprehensive status report.
        
        Returns:
            Dict with server status, plugin info, and VFS stats
        """
        servers = self.list_servers()
        plugins = self.registry.list_plugins()
        instances = self.registry.list_instances()
        vfs_dbs = self.vfs.list_databases()
        
        instance_states = {}
        for state in PluginState:
            count = sum(
                1 for i in instances if i.state == state
            )
            if count > 0:
                instance_states[state.value] = count
        
        return {
            "servers": {
                "total": len(servers),
                "running": sum(1 for s in servers if s.state == "running"),
                "stopped": sum(1 for s in servers if s.state == "stopped"),
                "degraded": sum(1 for s in servers if s.state == "degraded"),
                "error": sum(1 for s in servers if s.state == "error"),
            },
            "plugins": {
                "total": len(plugins),
                "registered": [p.name for p in plugins],
            },
            "instances": {
                "total": len(instances),
                "by_state": instance_states,
            },
            "vfs_databases": {
                "total": len(vfs_dbs),
            },
            "health_check_interval": self.health_check_interval,
            "auto_restart": self.auto_restart,
        }
