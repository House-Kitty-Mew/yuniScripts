"""
╔══════════════════════════════════════════════════════════════════════╗
║  Multi-Server Manager — Main Entry Point                            ║
║  YuniScript service for managing multiple game servers              ║
╚══════════════════════════════════════════════════════════════════════╝

PHASES:
  Phase 3: ✅ Subsystem plugin system with VFS-backed DB isolation
  Phase 4: 🚧 Admin auto-setup CLI/GUI
  Phase 5: 🚧 GUI extensions (multi-server view, command routing)
  Phase 6: 🚧 Port existing subsystems

This is a YuniScript SERVICE (server_type=critical).
"""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from engine.plugin_registry import PluginRegistry, PluginError, PluginHealth
from engine.vfs_db_isolation import VFSDatabaseManager
from engine.lifecycle_manager import LifecycleManager, LifecycleEvent
from engine.admin_cli import AdminCLI, CLIResult

# ── Dynamic Config Loader (Admin GUI API) ─────────────────────────
_DYNAMIC_CONFIG_AVAILABLE = False
try:
    _TOOLS_DIR = str(SCRIPT_DIR.parent.parent / 'SERVICES' / 'fastmcp_server' / 'tools')
    if _TOOLS_DIR not in sys.path:
        sys.path.insert(0, _TOOLS_DIR)
    from dynamic_config_loader import (
        register_configs, get_config, update_config,
        load_source, flush_source, on_config_change,
    )
    _DYNAMIC_CONFIG_AVAILABLE = True

    # Register multi-server-manager configurable settings for Admin GUI
    register_configs("multi-server-manager", [
        {"key": "port", "type": "int", "default": 8200,
         "description": "Primary service port",
         "valid_range": (1024, 65535), "category": "network"},
        {"key": "phooks_port", "type": "int", "default": 7891,
         "description": "Phooks hub communication port",
         "valid_range": (1024, 65535), "category": "network"},
        {"key": "web_gui_port", "type": "int", "default": 8200,
         "description": "Web GUI port",
         "valid_range": (1024, 65535), "category": "network"},
        {"key": "health_check_interval_seconds", "type": "float", "default": 30.0,
         "description": "Health check polling interval",
         "valid_range": (5.0, 300.0), "category": "performance"},
        {"key": "shutdown_timeout_seconds", "type": "float", "default": 10.0,
         "description": "Graceful shutdown timeout",
         "valid_range": (1.0, 120.0), "category": "performance"},
        {"key": "auto_restart_failed_subsystems", "type": "bool", "default": True,
         "description": "Auto-restart failed subsystem plugins",
         "category": "performance"},
        {"key": "max_restart_attempts", "type": "int", "default": 3,
         "description": "Maximum restart attempts per subsystem",
         "valid_range": (1, 20), "category": "performance"},
        {"key": "backup_limit", "type": "int", "default": 5,
         "description": "Maximum VFS backups to retain",
         "valid_range": (1, 100), "category": "performance"},
        {"key": "log_level", "type": "str", "default": "INFO",
         "description": "Logging level",
         "valid_options": ["DEBUG", "INFO", "WARNING", "ERROR"],
         "category": "debug"},
    ])

    # Register change listener for log_level to update live
    def _on_log_level_change(source, key, old_val, new_val):
        if key == "log_level":
            logging.getLogger("multi-server-manager").setLevel(
                getattr(logging, new_val.upper(), logging.INFO))
    on_config_change("multi-server-manager", "log_level", _on_log_level_change)

except ImportError:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = SCRIPT_DIR / "DATA"
VFS_ROOT = DATA_DIR / "vfs"
CONFIG_PATH = SCRIPT_DIR / "config.json"

# ──────────────────────────────────────────────────────────────────────────────
# Globals
# ──────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("multi-server-manager")
registry: Optional[PluginRegistry] = None
vfs_manager: Optional[VFSDatabaseManager] = None
lifecycle: Optional[LifecycleManager] = None
admin_cli: Optional[AdminCLI] = None
_config: Dict[str, Any] = {}
_running = False
_shutdown_event = asyncio.Event()


# ──────────────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the service."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json, merging with dynamic config values."""
    global _config
    
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _config = json.load(f)
            logger.info("Loaded config from %s", CONFIG_PATH)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load config: %s", e)
            _config = {}
    else:
        logger.warning("Config file not found: %s", CONFIG_PATH)
        _config = {}
    
    # Merge in dynamic config values (admin GUI overrides take precedence)
    if _DYNAMIC_CONFIG_AVAILABLE:
        # Map config keys (may differ from DCL keys)
        key_map = {
            "port": "port",
            "phooks_port": "phooks_port",
            "web_gui_port": "web_gui_port",
            "health_check_interval_seconds": "health_check_interval_seconds",
            "shutdown_timeout_seconds": "shutdown_timeout_seconds",
            "auto_restart_failed_subsystems": "auto_restart_failed_subsystems",
            "max_restart_attempts": "max_restart_attempts",
            "backup_limit": "backup_limit",
            "log_level": "log_level",
        }
        for config_key, dcl_key in key_map.items():
            dcl_val = get_config("multi-server-manager", dcl_key)
            if dcl_val is not None:
                _config[config_key] = dcl_val
        # Load persisted config from DATA/
        try:
            load_source("multi-server-manager")
        except Exception:
            pass
    
    return _config


def register_plugins(registry: Any) -> None:
    """
    Register all Phase 6 subsystem plugins with the plugin registry.
    
    Each plugin is a SubsystemPlugin subclass defined in the plugins/ package.
    Registration makes them available via the lifecycle manager and AdminCLI.
    """
    try:
        from plugins import ALL_PLUGINS
        for plugin_class in ALL_PLUGINS:
            try:
                registry.register(plugin_class)
                logger.info("Registered plugin: %s", plugin_class.name)
            except Exception as e:
                logger.error("Failed to register plugin %s: %s", plugin_class, e)
    except ImportError as e:
        logger.warning("Plugin package not available: %s — skipping plugin registration", e)


async def initialize() -> None:
    """Initialize the Multi-Server Manager service."""
    global registry, vfs_manager, lifecycle, admin_cli
    
    logger.info("Initializing Multi-Server Manager v1.0.0")
    
    # Initialize core systems
    registry = PluginRegistry.get_instance()
    
    vfs_root_path = _config.get("vfs_root", str(VFS_ROOT))
    vfs_manager = VFSDatabaseManager(
        data_root=vfs_root_path,
        auto_backup=True,
        backup_limit=_config.get("backup_limit", 5),
    )
    
    lifecycle = LifecycleManager(
        plugin_registry=registry,
        vfs_manager=vfs_manager,
        vfs_data_root=vfs_root_path,
        health_check_interval=_config.get("health_check_interval_seconds", 30.0),
        shutdown_timeout=_config.get("shutdown_timeout_seconds", 10.0),
        auto_restart=_config.get("auto_restart_failed_subsystems", True),
        max_restart_attempts=_config.get("max_restart_attempts", 3),
    )
    
    # Register event handler for logging
    lifecycle.add_event_handler(_log_event)
    
    # Create and wire AdminCLI
    admin_cli = AdminCLI(
        registry=registry,
        vfs_manager=vfs_manager,
        lifecycle=lifecycle,
        config_path=str(CONFIG_PATH),
    )
    lifecycle.add_event_handler(admin_cli.record_event)
    
    # Load config into registry
    registry.set_config(_config)
    
    # ── Phase 6: Register subsystem plugins ─────────────────────────
    register_plugins(registry)
    
    # ── Phase 5: Start Web GUI (optional, graceful degradation) ────
    web_gui_port = _config.get("web_gui_port", 8200)
    try:
        from engine.web_gui import WebGUI
        
        gui = WebGUI(host="localhost", port=web_gui_port)
        gui_started = await gui.start(admin_cli=admin_cli)
        if gui_started:
            logger.info("Web GUI started on http://localhost:%d", web_gui_port)
        else:
            logger.warning(
                "Web GUI port %d unavailable — skipping (graceful degradation)",
                web_gui_port
            )
    except ImportError:
        logger.debug("Web GUI module not available — skipping")
    except Exception as e:
        logger.warning("Web GUI failed to start: %s (graceful degradation)", e)
    
    # Register default servers from config
    servers = _config.get("servers", {})
    for server_id, server_config in servers.items():
        lifecycle.register_server(
            server_id=server_id,
            display_name=server_config.get("display_name", server_id),
            config=server_config.get("config", {}),
        )
    
    logger.info("Multi-Server Manager initialized")


# ──────────────────────────────────────────────────────────────────────────────
# Event Handler
# ──────────────────────────────────────────────────────────────────────────────

async def _log_event(event: LifecycleEvent) -> None:
    """Log lifecycle events."""
    logger.info(
        "[%s] %s | server=%s plugin=%s msg=%s",
        event.event_type.upper(),
        "OK" if event.success else "FAIL",
        event.server_id,
        event.plugin_name or "-",
        event.message,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────────────────────────────────────

async def shutdown() -> None:
    """Gracefully shut down the service."""
    global _running
    
    logger.info("Shutting down Multi-Server Manager...")
    
    # Flush dynamic configs to disk
    if _DYNAMIC_CONFIG_AVAILABLE:
        try:
            flush_source("multi-server-manager")
        except Exception as e:
            logger.warning("Failed to flush dynamic config: %s", e)
    _running = False
    
    if lifecycle:
        # Shut down all registered servers
        servers = lifecycle.list_servers()
        for server in servers:
            if server.state == "running" or server.state == "degraded":
                try:
                    await lifecycle.shutdown_server(server.server_id)
                except Exception as e:
                    logger.error(
                        "Error shutting down server '%s': %s",
                        server.server_id, e
                    )
        
        lifecycle = None
    
    if vfs_manager:
        vfs_manager.close_all()
        vfs_manager = None
    
    if registry:
        PluginRegistry.reset_instance()
        registry = None
    
    logger.info("Multi-Server Manager shut down complete")
    print("SHUTDOWN_COMPLETE")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

async def main_loop() -> None:
    """Main service loop."""
    global _running
    _running = True
    
    logger.info("Multi-Server Manager is running")
    
    # Auto-start all registered servers
    if lifecycle:
        servers = lifecycle.list_servers()
        for server in servers:
            try:
                await lifecycle.start_server(server.server_id)
            except Exception as e:
                logger.error(
                    "Failed to start server '%s': %s",
                    server.server_id, e
                )
    
    # Print status
    if lifecycle:
        status = lifecycle.get_global_status()
        logger.info(
            "Status: %d servers, %d plugins, %d VFS databases",
            status["servers"]["total"],
            status["plugins"]["total"],
            status["vfs_databases"]["total"],
        )
    
    # Wait for shutdown signal
    await _shutdown_event.wait()


def handle_signal(sig, frame) -> None:
    """Handle shutdown signals."""
    logger.info("Received signal %d, initiating shutdown...", sig)
    _shutdown_event.set()


def main() -> None:
    """Entry point."""
    
    # Check for admin mode
    if len(sys.argv) > 1 and sys.argv[1] == "--admin":
        asyncio.run(run_admin_command(sys.argv[2:]))
        return
    
    # Load config first
    load_config()
    
    # Sync dynamic config values from loaded config
    if _DYNAMIC_CONFIG_AVAILABLE:
        for key in _config:
            update_config("multi-server-manager", key, _config[key], changed_by="startup")
    
    # Setup logging
    setup_logging(_config.get("log_level", "INFO"))
    
    # Set up signal handlers
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    
    try:
        # Run async main
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error("Fatal error: %s", e)
        sys.exit(1)


async def async_main() -> None:
    """Async entry point."""
    try:
        await initialize()
        await main_loop()
    finally:
        await shutdown()


async def run_admin_command(argv: list) -> None:
    """Run admin CLI commands in standalone mode."""
    global registry, vfs_manager, lifecycle, admin_cli
    
    load_config()
    setup_logging(_config.get("log_level", "INFO"))
    
    try:
        await initialize()
        
        if admin_cli:
            result = await admin_cli.run_cli(argv)
            print(str(result))
        else:
            print("Error: Admin CLI not initialized")
    
    except Exception as e:
        logger.error("Admin command failed: %s", e)
        print(f"Error: {e}")
    
    finally:
        await shutdown()


if __name__ == "__main__":
    main()
