"""
╔══════════════════════════════════════════════════════════════════════╗
║  Multi-Server Manager — Admin Auto-Setup CLI                        ║
║  Command-line interface for administering servers, plugins, and VFS ║
╚══════════════════════════════════════════════════════════════════════╝

DESIGN:
  - Dual-mode: CLI (argparse) + programmatic API
  - Wraps PluginRegistry, VFSDatabaseManager, and LifecycleManager
  - Provides self-install/uninstall for subsystem registration
  - Output formatting for both human-readable (CLI) and JSON (API)

USAGE (CLI):
    from engine.admin_cli import AdminCLI
    
    cli = AdminCLI(registry, vfs_manager, lifecycle)
    await cli.run_cli(["server", "list"])
    
USAGE (programmatic):
    servers = await cli.list_servers()
    result = await cli.start_server("main")
"""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("admin_cli")


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class AdminCLIError(Exception):
    """Base exception for Admin CLI errors."""
    pass


class AdminCLIArgumentError(AdminCLIError):
    """Raised on invalid CLI arguments."""
    pass


class AdminCLIExecutionError(AdminCLIError):
    """Raised when a command execution fails."""
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Output Formatting
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CLIResult:
    """Structured result from a CLI command."""
    success: bool
    message: str
    data: Any = None
    command: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)
    
    def __str__(self) -> str:
        if self.data is not None and isinstance(self.data, (dict, list)):
            if isinstance(self.data, dict) and not self.data:
                return self.message
            return f"{self.message}\n{json.dumps(self.data, indent=2, default=str)}"
        return self.message


# ──────────────────────────────────────────────────────────────────────────────
# Admin CLI
# ──────────────────────────────────────────────────────────────────────────────

class AdminCLI:
    """
    Admin CLI for the Multi-Server Manager.
    
    Provides both argparse-based CLI and programmatic API for:
    - Server management (list, start, stop, restart, info)
    - Plugin management (list, register, unregister, info)
    - VFS database management (list, show, backup, health)
    - Global status and configuration
    - Event log monitoring
    
    Usage (CLI):
        cli = AdminCLI(registry, vfs_mgr, lifecycle)
        result = await cli.run_cli(["server", "list"])
        print(result)
    
    Usage (programmatic):
        cli = AdminCLI(registry, vfs_mgr, lifecycle)
        servers = await cli.list_servers()
        await cli.start_server("my_server")
    """
    
    def __init__(
        self,
        registry=None,
        vfs_manager=None,
        lifecycle=None,
        config_path: Optional[str] = None,
    ):
        """
        Initialize the Admin CLI.
        
        Args:
            registry: PluginRegistry instance (or None for partial functionality)
            vfs_manager: VFSDatabaseManager instance (or None)
            lifecycle: LifecycleManager instance (or None)
            config_path: Path to config JSON file (optional)
        """
        from engine.plugin_registry import PluginRegistry, PluginError
        from engine.vfs_db_isolation import VFSDatabaseManager, VFSDBError
        from engine.lifecycle_manager import LifecycleManager, LifecycleError
        
        self.registry = registry
        self.vfs_manager = vfs_manager
        self.lifecycle = lifecycle
        self.config_path = config_path
        
        # Store references to error types for consistent error handling
        self.PluginError = PluginError
        self.VFSDBError = VFSDBError
        self.LifecycleError = LifecycleError
        
        self._recent_events: List[Dict[str, Any]] = []
    
    # ══════════════════════════════════════════════════════════════════
    # Programmatic API: Server Management
    # ══════════════════════════════════════════════════════════════════
    
    async def list_servers(self) -> CLIResult:
        """
        List all registered servers and their status.
        
        Returns:
            CLIResult with server list data
        """
        if not self.lifecycle:
            return CLIResult(
                success=False,
                message="Lifecycle manager is not initialized",
                command="server list",
            )
        
        try:
            servers = self.lifecycle.list_servers()
            
            data = []
            for s in servers:
                data.append({
                    "server_id": s.server_id,
                    "display_name": s.display_name,
                    "state": s.state,
                    "plugins": s.plugin_count,
                    "healthy": s.healthy,
                    "errors": s.error_count,
                    "started": s.started_at.isoformat() if s.started_at else None,
                })
            
            return CLIResult(
                success=True,
                message=f"Servers ({len(servers)}):",
                data=data,
                command="server list",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to list servers: {e}",
                command="server list",
            )
    
    async def start_server(self, server_id: str) -> CLIResult:
        """
        Start a server and all its enabled subsystems.
        
        Args:
            server_id: Server identifier
        
        Returns:
            CLIResult indicating success/failure
        """
        if not self.lifecycle:
            return CLIResult(
                success=False,
                message="Lifecycle manager is not initialized",
                command=f"server start {server_id}",
            )
        
        try:
            result = await self.lifecycle.start_server(server_id)
            info = self.lifecycle.get_server_info(server_id)
            
            return CLIResult(
                success=result,
                message=(
                    f"Server '{server_id}' started successfully "
                    f"({info.plugin_count} plugins, state: {info.state})"
                    if result
                    else f"Server '{server_id}' started with partial failures "
                         f"(state: {info.state})"
                ),
                data={"server_id": server_id, "state": info.state, "plugins": info.plugin_count},
                command=f"server start {server_id}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to start server '{server_id}': {e}",
                command=f"server start {server_id}",
            )
    
    async def stop_server(self, server_id: str) -> CLIResult:
        """
        Stop a server and all its subsystems.
        
        Args:
            server_id: Server identifier
        
        Returns:
            CLIResult indicating success/failure
        """
        if not self.lifecycle:
            return CLIResult(
                success=False,
                message="Lifecycle manager is not initialized",
                command=f"server stop {server_id}",
            )
        
        try:
            result = await self.lifecycle.shutdown_server(server_id)
            
            return CLIResult(
                success=result,
                message=(
                    f"Server '{server_id}' stopped successfully"
                    if result
                    else f"Server '{server_id}' stopped with warnings"
                ),
                data={"server_id": server_id, "stopped": result},
                command=f"server stop {server_id}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to stop server '{server_id}': {e}",
                command=f"server stop {server_id}",
            )
    
    async def restart_server(self, server_id: str) -> CLIResult:
        """
        Restart a server.
        
        Args:
            server_id: Server identifier
        
        Returns:
            CLIResult indicating success/failure
        """
        if not self.lifecycle:
            return CLIResult(
                success=False,
                message="Lifecycle manager is not initialized",
                command=f"server restart {server_id}",
            )
        
        try:
            result = await self.lifecycle.restart_server(server_id)
            info = self.lifecycle.get_server_info(server_id)
            
            return CLIResult(
                success=result,
                message=(
                    f"Server '{server_id}' restarted successfully"
                    if result
                    else f"Server '{server_id}' restart completed with warnings"
                ),
                data={"server_id": server_id, "state": info.state},
                command=f"server restart {server_id}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to restart server '{server_id}': {e}",
                command=f"server restart {server_id}",
            )
    
    async def server_info(self, server_id: str) -> CLIResult:
        """
        Get detailed information about a server.
        
        Args:
            server_id: Server identifier
        
        Returns:
            CLIResult with server info data
        """
        if not self.lifecycle:
            return CLIResult(
                success=False,
                message="Lifecycle manager is not initialized",
                command=f"server info {server_id}",
            )
        
        try:
            info = self.lifecycle.get_server_info(server_id)
            
            # Get instances for this server
            instances = []
            if self.registry:
                inst_list = self.registry.get_server_instances(server_id)
                for inst in inst_list:
                    instances.append({
                        "plugin": inst.plugin_name,
                        "state": inst.state.value,
                        "health": inst.health.value,
                        "started": inst.started_at.isoformat() if inst.started_at else None,
                        "errors": inst.error_count,
                        "last_error": inst.last_error,
                    })
            
            data = {
                "server_id": info.server_id,
                "display_name": info.display_name,
                "state": info.state,
                "started": info.started_at.isoformat() if info.started_at else None,
                "healthy": info.healthy,
                "errors": info.error_count,
                "plugins": info.plugin_count,
                "instances": instances,
            }
            
            return CLIResult(
                success=True,
                message=f"Server '{server_id}' info:",
                data=data,
                command=f"server info {server_id}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to get server info '{server_id}': {e}",
                command=f"server info {server_id}",
            )
    
    async def register_server(
        self, server_id: str, display_name: str = "", config: Optional[Dict[str, Any]] = None
    ) -> CLIResult:
        """
        Register a new server.
        
        Args:
            server_id: Unique server identifier
            display_name: Human-readable name (defaults to server_id)
            config: Server-specific configuration
        
        Returns:
            CLIResult indicating success/failure
        """
        if not self.lifecycle:
            return CLIResult(
                success=False,
                message="Lifecycle manager is not initialized",
                command=f"server register {server_id}",
            )
        
        try:
            self.lifecycle.register_server(
                server_id=server_id,
                display_name=display_name or server_id,
                config=config or {},
            )
            return CLIResult(
                success=True,
                message=f"Server '{server_id}' registered",
                data={"server_id": server_id, "display_name": display_name or server_id},
                command=f"server register {server_id}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to register server '{server_id}': {e}",
                command=f"server register {server_id}",
            )
    
    async def unregister_server(self, server_id: str) -> CLIResult:
        """
        Unregister a server.
        
        Args:
            server_id: Server identifier (must be stopped first)
        
        Returns:
            CLIResult indicating success/failure
        """
        if not self.lifecycle:
            return CLIResult(
                success=False,
                message="Lifecycle manager is not initialized",
                command=f"server unregister {server_id}",
            )
        
        try:
            self.lifecycle.unregister_server(server_id)
            return CLIResult(
                success=True,
                message=f"Server '{server_id}' unregistered",
                command=f"server unregister {server_id}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to unregister server '{server_id}': {e}",
                command=f"server unregister {server_id}",
            )
    
    # ══════════════════════════════════════════════════════════════════
    # Programmatic API: Plugin Management
    # ══════════════════════════════════════════════════════════════════
    
    async def list_plugins(self) -> CLIResult:
        """
        List all registered plugins.
        
        Returns:
            CLIResult with plugin list data
        """
        if not self.registry:
            return CLIResult(
                success=False,
                message="Plugin registry is not initialized",
                command="plugin list",
            )
        
        try:
            manifests = self.registry.list_plugins()
            
            data = []
            for m in manifests:
                data.append({
                    "name": m.name,
                    "version": m.version,
                    "description": m.description,
                    "dependencies": m.dependencies,
                    "author": m.author,
                    "tags": m.tags,
                })
            
            return CLIResult(
                success=True,
                message=f"Plugins ({len(manifests)}):",
                data=data,
                command="plugin list",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to list plugins: {e}",
                command="plugin list",
            )
    
    async def register_plugin(self, plugin_class_path: str) -> CLIResult:
        """
        Register a plugin by importing and registering its class.
        
        Args:
            plugin_class_path: Dotted path to plugin class (e.g., "my_plugin.MyPlugin")
                              or file path to a Python file containing plugin classes
        
        Returns:
            CLIResult indicating success/failure
        """
        if not self.registry:
            return CLIResult(
                success=False,
                message="Plugin registry is not initialized",
                command=f"plugin register {plugin_class_path}",
            )
        
        try:
            # Try as a file path first
            path = Path(plugin_class_path)
            if path.exists() and path.suffix == ".py":
                import importlib.util
                module_name = path.stem
                spec = importlib.util.spec_from_file_location(module_name, str(path))
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    # Find SubsystemPlugin subclasses in the module
                    from engine.plugin_registry import SubsystemPlugin
                    registered = []
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (isinstance(attr, type) and
                            issubclass(attr, SubsystemPlugin) and
                            attr is not SubsystemPlugin and
                            attr.name):
                            plugin = self.registry.register(attr)
                            registered.append(attr.name)
                    
                    if not registered:
                        return CLIResult(
                            success=False,
                            message=f"No valid plugin classes found in '{plugin_class_path}'",
                            command=f"plugin register {plugin_class_path}",
                        )
                    
                    return CLIResult(
                        success=True,
                        message=f"Registered plugins from '{plugin_class_path}': {', '.join(registered)}",
                        data={"registered": registered, "source": plugin_class_path},
                        command=f"plugin register {plugin_class_path}",
                    )
            
            # Try as a dotted import path (e.g., "engine.my_plugin.MyPlugin")
            parts = plugin_class_path.rsplit(".", 1)
            if len(parts) == 2:
                module_path, class_name = parts
                import importlib
                module = importlib.import_module(module_path)
                plugin_class = getattr(module, class_name)
                
                from engine.plugin_registry import SubsystemPlugin
                if not (isinstance(plugin_class, type) and issubclass(plugin_class, SubsystemPlugin)):
                    return CLIResult(
                        success=False,
                        message=f"'{plugin_class_path}' is not a SubsystemPlugin subclass",
                        command=f"plugin register {plugin_class_path}",
                    )
                
                plugin = self.registry.register(plugin_class)
                return CLIResult(
                    success=True,
                    message=f"Plugin '{plugin.name}' v{plugin.version} registered",
                    data={"name": plugin.name, "version": plugin.version},
                    command=f"plugin register {plugin_class_path}",
                )
            
            return CLIResult(
                success=False,
                message=f"Cannot resolve '{plugin_class_path}' as a file or dotted path",
                command=f"plugin register {plugin_class_path}",
            )
            
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to register plugin: {e}",
                command=f"plugin register {plugin_class_path}",
            )
    
    async def unregister_plugin(self, plugin_name: str) -> CLIResult:
        """
        Unregister a plugin.
        
        Args:
            plugin_name: Name of the plugin to unregister
        
        Returns:
            CLIResult indicating success/failure
        """
        if not self.registry:
            return CLIResult(
                success=False,
                message="Plugin registry is not initialized",
                command=f"plugin unregister {plugin_name}",
            )
        
        try:
            self.registry.unregister(plugin_name)
            return CLIResult(
                success=True,
                message=f"Plugin '{plugin_name}' unregistered",
                command=f"plugin unregister {plugin_name}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to unregister plugin '{plugin_name}': {e}",
                command=f"plugin unregister {plugin_name}",
            )
    
    async def plugin_info(self, plugin_name: str) -> CLIResult:
        """
        Get detailed information about a plugin.
        
        Args:
            plugin_name: Name of the plugin
        
        Returns:
            CLIResult with plugin info data
        """
        if not self.registry:
            return CLIResult(
                success=False,
                message="Plugin registry is not initialized",
                command=f"plugin info {plugin_name}",
            )
        
        try:
            plugin = self.registry.get_plugin(plugin_name)
            manifest = plugin.manifest
            
            data = {
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "dependencies": manifest.dependencies,
                "optional_dependencies": manifest.optional_dependencies,
                "author": manifest.author,
                "homepage": manifest.homepage,
                "tags": manifest.tags,
            }
            
            return CLIResult(
                success=True,
                message=f"Plugin '{plugin_name}' info:",
                data=data,
                command=f"plugin info {plugin_name}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to get plugin info '{plugin_name}': {e}",
                command=f"plugin info {plugin_name}",
            )
    
    async def list_instances(self) -> CLIResult:
        """
        List all plugin instances across all servers.
        
        Returns:
            CLIResult with instance list data
        """
        if not self.registry:
            return CLIResult(
                success=False,
                message="Plugin registry is not initialized",
                command="plugin instances",
            )
        
        try:
            instances = self.registry.list_instances()
            
            data = []
            for inst in instances:
                data.append({
                    "plugin": inst.plugin_name,
                    "server_id": inst.server_id,
                    "state": inst.state.value,
                    "health": inst.health.value,
                    "started": inst.started_at.isoformat() if inst.started_at else None,
                    "errors": inst.error_count,
                })
            
            return CLIResult(
                success=True,
                message=f"Instances ({len(instances)}):",
                data=data,
                command="plugin instances",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to list instances: {e}",
                command="plugin instances",
            )
    
    # ══════════════════════════════════════════════════════════════════
    # Programmatic API: VFS Database Management
    # ══════════════════════════════════════════════════════════════════
    
    async def list_databases(self) -> CLIResult:
        """
        List all managed VFS databases.
        
        Returns:
            CLIResult with database list data
        """
        if not self.vfs_manager:
            return CLIResult(
                success=False,
                message="VFS database manager is not initialized",
                command="db list",
            )
        
        try:
            dbs = self.vfs_manager.list_databases()
            
            data = []
            for db in dbs:
                data.append({
                    "subsystem": db.subsystem,
                    "server_id": db.server_id,
                    "size_bytes": db.size_bytes,
                    "tables": db.table_count,
                    "healthy": db.is_healthy,
                })
            
            total_size = sum(db.size_bytes for db in dbs)
            
            return CLIResult(
                success=True,
                message=f"Databases ({len(dbs)}, total size: {self._format_size(total_size)}):",
                data=data,
                command="db list",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to list databases: {e}",
                command="db list",
            )
    
    async def show_database(self, subsystem: str, server_id: str) -> CLIResult:
        """
        Show detailed information about a specific database.
        
        Args:
            subsystem: Subsystem name
            server_id: Server identifier
        
        Returns:
            CLIResult with database details
        """
        if not self.vfs_manager:
            return CLIResult(
                success=False,
                message="VFS database manager is not initialized",
                command=f"db show {subsystem} {server_id}",
            )
        
        try:
            db = self.vfs_manager.get_database(subsystem, server_id)
            info = db.get_info()
            
            data = {
                "subsystem": info.subsystem,
                "server_id": info.server_id,
                "path": info.db_path,
                "size_bytes": info.size_bytes,
                "size_human": self._format_size(info.size_bytes),
                "tables": info.table_count,
                "created": info.created_at,
                "last_accessed": info.last_accessed,
                "schema_version": info.schema_version,
                "healthy": info.is_healthy,
                "table_names": db.get_table_names(),
            }
            
            return CLIResult(
                success=True,
                message=f"Database '{subsystem}/{server_id}':",
                data=data,
                command=f"db show {subsystem} {server_id}",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to show database: {e}",
                command=f"db show {subsystem} {server_id}",
            )
    
    async def backup_databases(
        self, subsystem: Optional[str] = None, server_id: Optional[str] = None
    ) -> CLIResult:
        """
        Backup VFS databases.
        
        Args:
            subsystem: If specified, only backup databases for this subsystem
            server_id: If specified, only backup databases for this server
        
        Returns:
            CLIResult with backup results
        """
        if not self.vfs_manager:
            return CLIResult(
                success=False,
                message="VFS database manager is not initialized",
                command="db backup",
            )
        
        try:
            if subsystem and server_id:
                # Backup a specific database
                db = self.vfs_manager.get_database(subsystem, server_id)
                backup_path = db.create_backup()
                return CLIResult(
                    success=True,
                    message=f"Backup created: {backup_path}",
                    data={"path": backup_path, "subsystem": subsystem, "server_id": server_id},
                    command=f"db backup {subsystem} {server_id}",
                )
            else:
                # Backup all databases
                results = self.vfs_manager.backup_all()
                success_count = sum(
                    1 for v in results.values() if not v.startswith("ERROR:")
                )
                return CLIResult(
                    success=True,
                    message=f"Backup complete: {success_count}/{len(results)} succeeded",
                    data=results,
                    command="db backup",
                )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Backup failed: {e}",
                command="db backup",
            )
    
    async def check_database_health(self) -> CLIResult:
        """
        Check health of all VFS databases.
        
        Returns:
            CLIResult with health check results
        """
        if not self.vfs_manager:
            return CLIResult(
                success=False,
                message="VFS database manager is not initialized",
                command="db health",
            )
        
        try:
            results = self.vfs_manager.health_check_all()
            healthy = sum(1 for v in results.values() if v)
            unhealthy = sum(1 for v in results.values() if not v)
            
            data = {
                "total": len(results),
                "healthy": healthy,
                "unhealthy": unhealthy,
                "details": results,
            }
            
            return CLIResult(
                success=True,
                message=f"Health: {healthy} healthy, {unhealthy} unhealthy (of {len(results)})",
                data=data,
                command="db health",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Health check failed: {e}",
                command="db health",
            )
    
    # ══════════════════════════════════════════════════════════════════
    # Programmatic API: Status & Configuration
    # ══════════════════════════════════════════════════════════════════
    
    async def global_status(self) -> CLIResult:
        """
        Get comprehensive global status.
        
        Returns:
            CLIResult with global status data
        """
        if not self.lifecycle:
            return CLIResult(
                success=False,
                message="Lifecycle manager is not initialized",
                command="status",
            )
        
        try:
            status = self.lifecycle.get_global_status()
            
            # Add plugin details
            if self.registry:
                manifests = self.registry.list_plugins()
                status["plugins"]["details"] = [
                    {"name": m.name, "version": m.version, "description": m.description}
                    for m in manifests
                ]
            
            # Add VFS details
            if self.vfs_manager:
                dbs = self.vfs_manager.list_databases()
                total_size = sum(db.size_bytes for db in dbs)
                status["vfs_databases"]["total_size_bytes"] = total_size
                status["vfs_databases"]["total_size_human"] = self._format_size(total_size)
            
            return CLIResult(
                success=True,
                message="Global status:",
                data=status,
                command="status",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to get status: {e}",
                command="status",
            )
    
    async def show_config(self) -> CLIResult:
        """
        Show the current configuration.
        
        Returns:
            CLIResult with config data
        """
        config = {}
        
        if self.registry:
            config = self.registry.get_config()
        
        if self.config_path and Path(self.config_path).exists():
            config["_config_file"] = self.config_path
        
        # Add lifecycle config
        if self.lifecycle:
            config["_lifecycle"] = {
                "health_check_interval": self.lifecycle.health_check_interval,
                "shutdown_timeout": self.lifecycle.shutdown_timeout,
                "auto_restart": self.lifecycle.auto_restart,
                "max_restart_attempts": self.lifecycle.max_restart_attempts,
            }
        
        return CLIResult(
            success=True,
            message="Configuration:",
            data=config,
            command="config",
        )
    
    async def reload_config(self) -> CLIResult:
        """
        Reload configuration from file.
        
        Returns:
            CLIResult indicating success/failure
        """
        if not self.config_path:
            return CLIResult(
                success=False,
                message="No config path set, cannot reload",
                command="config reload",
            )
        
        path = Path(self.config_path)
        if not path.exists():
            return CLIResult(
                success=False,
                message=f"Config file not found: {self.config_path}",
                command="config reload",
            )
        
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            if self.registry:
                self.registry.set_config(config)
            
            return CLIResult(
                success=True,
                message=f"Configuration reloaded from '{self.config_path}'",
                data=config,
                command="config reload",
            )
        except Exception as e:
            return CLIResult(
                success=False,
                message=f"Failed to reload config: {e}",
                command="config reload",
            )
    
    # ══════════════════════════════════════════════════════════════════
    # Programmatic API: Events
    # ══════════════════════════════════════════════════════════════════
    
    def record_event(self, event) -> None:
        """
        Record a lifecycle event for event log display.
        
        Args:
            event: LifecycleEvent instance
        """
        self._recent_events.append({
            "type": event.event_type,
            "server_id": event.server_id,
            "plugin": event.plugin_name,
            "timestamp": event.timestamp,
            "message": event.message,
            "success": event.success,
        })
        
        # Keep only the last 100 events
        if len(self._recent_events) > 100:
            self._recent_events = self._recent_events[-100:]
    
    async def show_events(self, limit: int = 20) -> CLIResult:
        """
        Show recent lifecycle events.
        
        Args:
            limit: Maximum number of events to show
        
        Returns:
            CLIResult with event data
        """
        events = self._recent_events[-limit:] if self._recent_events else []
        
        return CLIResult(
            success=True,
            message=f"Recent events ({len(events)}):" if events else "No events recorded",
            data=list(reversed(events)),  # Newest first
            command="events",
        )
    
    # ══════════════════════════════════════════════════════════════════
    # CLI Interface
    # ══════════════════════════════════════════════════════════════════
    
    async def run_cli(self, argv: List[str]) -> CLIResult:
        """
        Parse and execute a CLI command.
        
        Args:
            argv: Command-line argument list (e.g., ["server", "list"])
        
        Returns:
            CLIResult from the executed command
        
        Example:
            await cli.run_cli(["server", "list"])
            await cli.run_cli(["server", "start", "main"])
            await cli.run_cli(["plugin", "list"])
            await cli.run_cli(["db", "backup"])
            await cli.run_cli(["status"])
        """
        if not argv:
            return await self._show_help()
        
        command = argv[0]
        sub_argv = argv[1:]
        
        # Command dispatch
        dispatch = {
            "server": self._cmd_server,
            "plugin": self._cmd_plugin,
            "db": self._cmd_database,
            "status": lambda a: self.global_status(),
            "config": lambda a: self._cmd_config(a),
            "events": lambda a: self.show_events(),
            "help": lambda a: self._show_help(),
        }
        
        handler = dispatch.get(command)
        if not handler:
            return CLIResult(
                success=False,
                message=f"Unknown command: '{command}'. Type 'help' for available commands.",
                command=command,
            )
        
        return await handler(sub_argv)
    
    async def _cmd_server(self, argv: List[str]) -> CLIResult:
        """Handle 'server' subcommands."""
        if not argv:
            return await self.list_servers()
        
        sub = argv[0]
        args = argv[1:]
        
        if sub == "list":
            return await self.list_servers()
        elif sub == "start":
            if not args:
                return CLIResult(success=False, message="Usage: server start <server_id>")
            return await self.start_server(args[0])
        elif sub == "stop":
            if not args:
                return CLIResult(success=False, message="Usage: server stop <server_id>")
            return await self.stop_server(args[0])
        elif sub == "restart":
            if not args:
                return CLIResult(success=False, message="Usage: server restart <server_id>")
            return await self.restart_server(args[0])
        elif sub == "info":
            if not args:
                return CLIResult(success=False, message="Usage: server info <server_id>")
            return await self.server_info(args[0])
        elif sub == "register":
            if not args:
                return CLIResult(success=False, message="Usage: server register <server_id> [display_name]")
            display_name = args[1] if len(args) > 1 else ""
            return await self.register_server(args[0], display_name)
        elif sub == "unregister":
            if not args:
                return CLIResult(success=False, message="Usage: server unregister <server_id>")
            return await self.unregister_server(args[0])
        else:
            return CLIResult(
                success=False,
                message=f"Unknown server subcommand: '{sub}'. "
                        f"Available: list, start, stop, restart, info, register, unregister",
            )
    
    async def _cmd_plugin(self, argv: List[str]) -> CLIResult:
        """Handle 'plugin' subcommands."""
        if not argv:
            return await self.list_plugins()
        
        sub = argv[0]
        args = argv[1:]
        
        if sub == "list":
            return await self.list_plugins()
        elif sub == "register":
            if not args:
                return CLIResult(
                    success=False,
                    message="Usage: plugin register <module_path>  (file path or dotted import)",
                )
            return await self.register_plugin(args[0])
        elif sub == "unregister":
            if not args:
                return CLIResult(success=False, message="Usage: plugin unregister <plugin_name>")
            return await self.unregister_plugin(args[0])
        elif sub == "info":
            if not args:
                return CLIResult(success=False, message="Usage: plugin info <plugin_name>")
            return await self.plugin_info(args[0])
        elif sub == "instances":
            return await self.list_instances()
        else:
            return CLIResult(
                success=False,
                message=f"Unknown plugin subcommand: '{sub}'. "
                        f"Available: list, register, unregister, info, instances",
            )
    
    async def _cmd_database(self, argv: List[str]) -> CLIResult:
        """Handle 'db' subcommands."""
        if not argv:
            return await self.list_databases()
        
        sub = argv[0]
        args = argv[1:]
        
        if sub == "list":
            return await self.list_databases()
        elif sub == "show":
            if len(args) < 2:
                return CLIResult(
                    success=False,
                    message="Usage: db show <subsystem> <server_id>",
                )
            return await self.show_database(args[0], args[1])
        elif sub == "backup":
            if len(args) >= 2:
                return await self.backup_databases(args[0], args[1])
            return await self.backup_databases()
        elif sub == "health":
            return await self.check_database_health()
        else:
            return CLIResult(
                success=False,
                message=f"Unknown db subcommand: '{sub}'. "
                        f"Available: list, show, backup, health",
            )
    
    async def _cmd_config(self, argv: List[str]) -> CLIResult:
        """Handle 'config' subcommands."""
        if not argv:
            return await self.show_config()
        
        sub = argv[0]
        
        if sub == "reload":
            return await self.reload_config()
        else:
            return CLIResult(
                success=False,
                message=f"Unknown config subcommand: '{sub}'. Available: (show), reload",
            )
    
    async def _show_help(self) -> CLIResult:
        """Show the help text."""
        help_text = (
            "Multi-Server Manager Admin CLI\n"
            "═══════════════════════════════\n\n"
            "Commands:\n"
            "  server\n"
            "    list                        List all servers\n"
            "    start <server_id>           Start a server\n"
            "    stop <server_id>            Stop a server\n"
            "    restart <server_id>         Restart a server\n"
            "    info <server_id>            Show server details\n"
            "    register <id> [name]        Register a new server\n"
            "    unregister <server_id>      Unregister a server\n\n"
            "  plugin\n"
            "    list                        List all plugins\n"
            "    register <path>             Register a plugin (file or dotted path)\n"
            "    unregister <name>           Unregister a plugin\n"
            "    info <name>                 Show plugin details\n"
            "    instances                   List all plugin instances\n\n"
            "  db\n"
            "    list                        List all VFS databases\n"
            "    show <sub> <server>         Show database details\n"
            "    backup [sub] [server]       Backup databases\n"
            "    health                      Check database health\n\n"
            "  status                        Show global status\n"
            "  config [reload]               Show/reload configuration\n"
            "  events                        Show recent lifecycle events\n"
            "  help                          Show this help\n"
        )
        return CLIResult(
            success=True,
            message=help_text,
            command="help",
        )
    
    # ══════════════════════════════════════════════════════════════════
    # Utilities
    # ══════════════════════════════════════════════════════════════════
    
    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format bytes into human-readable string."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"
    
    @staticmethod
    def _format_table(headers: List[str], rows: List[List[str]]) -> str:
        """Format data as an ASCII table."""
        if not rows:
            return "(empty)"
        
        # Calculate column widths
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(str(cell)))
        
        # Build the table
        separator = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        
        lines = [separator]
        # Header
        header_row = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)) + " |"
        lines.append(header_row)
        lines.append(separator)
        
        # Data rows
        for row in rows:
            data_row = "| " + " | ".join(
                str(c).ljust(w) for c, w in zip(row, widths)
            ) + " |"
            lines.append(data_row)
        
        lines.append(separator)
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Standalone CLI Entry Point
# ══════════════════════════════════════════════════════════════════════

def create_arg_parser() -> argparse.ArgumentParser:
    """
    Create the argument parser for standalone CLI usage.
    
    Returns:
        Configured ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="multi-server-admin",
        description="Multi-Server Manager Admin CLI",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Server commands
    server_parser = subparsers.add_parser("server", help="Server management")
    server_sub = server_parser.add_subparsers(dest="subcommand", help="Server subcommands")
    
    server_sub.add_parser("list", help="List all servers")
    
    server_start = server_sub.add_parser("start", help="Start a server")
    server_start.add_argument("server_id", help="Server identifier")
    
    server_stop = server_sub.add_parser("stop", help="Stop a server")
    server_stop.add_argument("server_id", help="Server identifier")
    
    server_restart = server_sub.add_parser("restart", help="Restart a server")
    server_restart.add_argument("server_id", help="Server identifier")
    
    server_info = server_sub.add_parser("info", help="Show server details")
    server_info.add_argument("server_id", help="Server identifier")
    
    server_register = server_sub.add_parser("register", help="Register a new server")
    server_register.add_argument("server_id", help="Server identifier")
    server_register.add_argument("display_name", nargs="?", default="", help="Display name")
    
    server_unregister = server_sub.add_parser("unregister", help="Unregister a server")
    server_unregister.add_argument("server_id", help="Server identifier")
    
    # Plugin commands
    plugin_parser = subparsers.add_parser("plugin", help="Plugin management")
    plugin_sub = plugin_parser.add_subparsers(dest="subcommand", help="Plugin subcommands")
    
    plugin_sub.add_parser("list", help="List all plugins")
    
    plugin_register = plugin_sub.add_parser("register", help="Register a plugin")
    plugin_register.add_argument("path", help="Plugin file path or dotted import path")
    
    plugin_unregister = plugin_sub.add_parser("unregister", help="Unregister a plugin")
    plugin_unregister.add_argument("name", help="Plugin name")
    
    plugin_info = plugin_sub.add_parser("info", help="Show plugin details")
    plugin_info.add_argument("name", help="Plugin name")
    
    plugin_sub.add_parser("instances", help="List all plugin instances")
    
    # Database commands
    db_parser = subparsers.add_parser("db", help="VFS database management")
    db_sub = db_parser.add_subparsers(dest="subcommand", help="Database subcommands")
    
    db_sub.add_parser("list", help="List all databases")
    
    db_show = db_sub.add_parser("show", help="Show database details")
    db_show.add_argument("subsystem", help="Subsystem name")
    db_show.add_argument("server_id", help="Server identifier")
    
    db_backup = db_sub.add_parser("backup", help="Backup databases")
    db_backup.add_argument("subsystem", nargs="?", help="Subsystem name (optional)")
    db_backup.add_argument("server_id", nargs="?", help="Server identifier (optional)")
    
    db_sub.add_parser("health", help="Check database health")
    
    # Top-level commands
    subparsers.add_parser("status", help="Show global status")
    config_parser = subparsers.add_parser("config", help="Configuration management")
    config_parser.add_argument("reload", nargs="?", help="Reload configuration")
    
    subparsers.add_parser("events", help="Show recent events")
    subparsers.add_parser("help", help="Show this help")
    
    return parser


def main_entry() -> None:
    """
    Standalone CLI entry point (for command-line use).
    
    Usage:
        python -m engine.admin_cli server list
        python -m engine.admin_cli status
    """
    parser = create_arg_parser()
    args = parser.parse_args()
    
    if not args.command or args.command == "help":
        parser.print_help()
        return
    
    # Build argv list for run_cli
    argv = [args.command]
    if hasattr(args, "subcommand") and args.subcommand:
        argv.append(args.subcommand)
        # Add positional arguments
        for arg_name in ["server_id", "path", "name", "display_name", "subsystem", "reload"]:
            val = getattr(args, arg_name, None)
            if val is not None:
                argv.append(str(val))
    
    # This is a stub - actual execution requires initialized components
    print(f"Multi-Server Admin CLI (stub mode)")
    print(f"Run with: admin_cli.run_cli({argv})")
    print("Initialize components before using this CLI.")


if __name__ == "__main__":
    main_entry()
