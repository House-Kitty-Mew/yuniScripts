#!/usr/bin/env python3
"""
FastMCP Server — YuniScript Managed Entry Point.

Part of DeepSky Self-Healing AI Ecosystem.
Spec: /home/deck/Documents/dev-yuniScripts/DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md

Wraps the existing FastMCP server with Phooks tool bridge and debug hooks.
Server type: critical (never killed by 5-second rule).
"""

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phooks_bridge import PhooksToolBridge
from debug_hooks import DebugHooks
from fastmcp_adapter import FastMCPAdapter

logger = logging.getLogger(__name__)


class FastMCPYuniScript:
    """
    YuniScript-managed FastMCP server.
    
    Wraps existing FastMCP with:
    - Phooks tool bridge for DeepSky client integration
    - Debug hooks for self-healing error capture
    - Tool adapter for existing FastMCP tools
    """

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'config.json'
        )
        self.config = self._load_config()
        
        # Components
        self.fastmcp = FastMCPAdapter()
        self.phooks_bridge = PhooksToolBridge()
        self.debug_hooks = DebugHooks()
        
        # Phooks client (will be connected)
        self.phooks_client = None
        
        # Runtime
        self._running = False
        self._healthy = True
        self._start_time = None
        self._restart_flag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'restart.flag')
        self._shutdown_requested = False
        
        self._setup_logging()
    
    def _load_config(self) -> dict:
        """Load configuration."""
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {
                'fastmcp': {'tools_dir': 'tools'},

                'phooks': {'hub_host': 'localhost', 'hub_port': 5555},
                'logging': {'level': 'INFO'}
            }
        except json.JSONDecodeError:
            return {}
    
    def _setup_logging(self):
        """Configure logging."""
        log_config = self.config.get('logging', {})
        level = getattr(logging, log_config.get('level', 'INFO').upper(), logging.INFO)
        
        file_handler = logging.handlers.RotatingFileHandler(
            'fastmcp_server.log', maxBytes=10485760, backupCount=3
        )
        file_handler.setLevel(level)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
    
    async def initialize(self):
        """Initialize all components."""
        logger.info("Initializing FastMCP YuniScript...")
        self._start_time = time.time()
        
        # 1. Initialize FastMCP adapter
        try:
            fm_config = self.config.get('fastmcp', {})
            tools_dir = fm_config.get('tools_dir', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
            self.fastmcp = FastMCPAdapter(tools_dir)
            tools = await self.fastmcp.initialize()
            logger.info(f"FastMCP adapter loaded {len(tools)} tools")
        except Exception as e:
            logger.error(f"Failed to initialize FastMCP adapter: {e}")
            self._healthy = False
        
        # 2. Register tools with Phooks bridge
        if self.fastmcp.get_tool_names():
            for tool_name in self.fastmcp.get_tool_names():
                handler = self.fastmcp.get_tool(tool_name)
                if handler:
                    self.phooks_bridge.register_tool(tool_name, handler)
            logger.info(f"Registered {len(self.fastmcp.get_tool_names())} tools with bridge")
        
        # 3. Try Phooks connection
        await self._connect_phooks()
        
        # 4. Start bridge
        await self.phooks_bridge.start()
        
        # 5. Start debug hooks
        await self.debug_hooks.start()
        
        logger.info("FastMCP YuniScript initialized")
    
    async def _connect_phooks(self):
        """Connect to Phooks hub."""
        phooks_config = self.config.get('phooks', {})
        host = phooks_config.get('hub_host', 'localhost')
        port = phooks_config.get('hub_port', 5555)
        
        try:
            sys.path.insert(0, '/home/deck/Documents/dev-yuniScripts')
            from SCRIPTS.SERVICES.phooks_hub.phooks_client import PhooksClient
            self.phooks_client = PhooksClient(host, port)
            
            self.phooks_bridge.phooks = self.phooks_client
            self.debug_hooks.phooks = self.phooks_client
            
            logger.info(f"Connected to Phooks at {host}:{port}")
        except ImportError:
            logger.info("Phooks client not available (running standalone)")
        except Exception as e:
            logger.warning(f"Phooks connection failed: {e}")
    
    async def run(self):
        """
        Main loop.
        Polls for restart flag every 2 seconds.
        When restart flag is detected:
          1. Log the restart request
          2. Remove the flag file
          3. Set _running to False
          4. Exit the loop (shutdown is called automatically)
        """
        self._running = True
        logger.info("FastMCP YuniScript entering main loop")
        
        try:
            while self._running:
                # Check for restart flag
                if os.path.exists(self._restart_flag_path):
                    logger.info("Restart flag detected - initiating graceful shutdown for restart")
                    self._shutdown_requested = True
                    try:
                        os.unlink(self._restart_flag_path)
                    except Exception:
                        pass
                    self._running = False
                    break
                
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
            self._shutdown_requested = True
        except Exception as e:
            logger.error(f"Main loop error: {e}")
        finally:
            await self.shutdown()
    
    async def shutdown(self):
        """Graceful shutdown."""
        reason = "restart" if self._shutdown_requested else "normal"
        logger.info(f"Shutting down FastMCP YuniScript (reason: {reason})...")
        self._running = False
        
        await self.debug_hooks.stop()
        await self.phooks_bridge.stop()
        
        if self.phooks_client:
            try:
                self.phooks_client.close()
            except Exception:
                pass
        
        uptime = time.time() - self._start_time if self._start_time else 0
        logger.info(f"Shutdown complete (reason: {reason}, uptime: {uptime:.1f}s)")



def _run_async(coro):
    """Run a coroutine synchronously. Avoids signal.signal() calls from asyncio.run()."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def main():
    """YuniScript entry point."""
    server = FastMCPYuniScript()
    
    loop = asyncio.get_event_loop()
    def shutdown_handler():
        asyncio.ensure_future(server.shutdown())
    
    try:
        loop.add_signal_handler(signal.SIGTERM, shutdown_handler)
        loop.add_signal_handler(signal.SIGINT, shutdown_handler)
    except NotImplementedError:
        pass
    
    try:
        await server.initialize()
        await server.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        await server.shutdown()
        raise


if __name__ == '__main__':
    _run_async(main())
