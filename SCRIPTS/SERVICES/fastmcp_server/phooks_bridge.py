"""
Phooks Tool Bridge — Routes tool calls between DeepSky clients and FastMCP via Phooks events.

Part of FastMCP Server (YuniScript Managed).
Spec: /home/deck/Documents/dev-yuniScripts/DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md

Flow:
1. DeepSky client publishes tool call as Phooks event 'tool.call'
2. phooks_bridge.py subscribes to 'tool.call'
3. Executes the tool via FastMCP adaptor
4. Publishes result to 'tool.result.{request_id}'
5. DeepSky client receives result via Phooks subscription
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
import uuid
from typing import Optional, Dict, Any, Callable, Awaitable

# ═══ DYNAMIC CONFIG LOADER ═══
try:
    from dynamic_config_loader import (
        register_configs, get_config, get_all_configs, get_setting,
        get_config_sources, update_config, get_config_summary,
        get_change_history, flush_source, flush_all, load_source, load_all,
        reset_config, start_admin_gui,
    )
    _DYNAMIC_CONFIG_AVAILABLE = True
except ImportError:
    _DYNAMIC_CONFIG_AVAILABLE = False

logger = logging.getLogger(__name__)

# ═══ HOST PROTECTION + GOD WATCHER — Initialize at module load ═══
# Uses LOCAL standalone modules — NO dependency on AIHandler FastMCP.
_HOST_PROTECTION_INIT = False
_GOD_WATCHER_INIT = False
def _init_host_protection():
    global _HOST_PROTECTION_INIT, _GOD_WATCHER_INIT
    if not _HOST_PROTECTION_INIT:
        # 1. Initialize Host Protection (local, lightweight)
        try:
            # Add tools dir to sys.path (append, not prepend, to avoid shadowing local modules)
            _tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
            if _tools_dir not in sys.path:
                sys.path.append(_tools_dir)
            from host_protection import initialize as _init_hp
            _init_hp()
            _HOST_PROTECTION_INIT = True
        except ImportError:
            logger.warning("Local host_protection not available — running without OS patches")
        
        # 2. Initialize GOD WATCHER (local, standalone)
        if not _GOD_WATCHER_INIT:
            try:
                from god_watcher import initialize_god_watcher
                initialize_god_watcher()
                _GOD_WATCHER_INIT = True
            except ImportError:
                logger.warning("Local god_watcher not available — running without GOD WATCHER")
_init_host_protection()


class PhooksToolBridge:
    """
    Bridges between Phooks event system and FastMCP tool execution.
    
    Subscribes to 'tool.call' events, executes tools, and returns results
    via 'tool.result.{request_id}' events.
    """

    def __init__(self, phooks_client=None, auto_register_configs: bool = True):
        self.phooks = phooks_client
        self._tool_registry = {}  # name -> callable
        self._running = False
        self._listen_task = None
        self._result_handlers = {}  # request_id -> asyncio.Event
        
        # Stats
        self._calls_received = 0
        self._calls_completed = 0
        self._calls_failed = 0
        
        # Register config management tools
        if auto_register_configs:
            self.register_config_tools()

    def register_tool(self, name: str, handler: Callable):
        """
        Register a tool handler.
        
        Args:
            name: Tool name (e.g., 'database_query')
            handler: Async callable that takes **kwargs and returns result
        """
        self._tool_registry[name] = handler
        logger.debug(f"Registered tool: {name}")

    def register_tools(self, tools: Dict[str, Callable]):
        """Register multiple tool handlers at once."""
        for name, handler in tools.items():
            self.register_tool(name, handler)

    async def start(self):
        """Start listening for tool call events."""
        if not self.phooks:
            logger.warning("Phooks client not available, bridge cannot start")
            return
        
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("Phooks Tool Bridge started")

    async def stop(self):
        """Stop listening for tool call events."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        logger.info("Phooks Tool Bridge stopped")

    async def _listen_loop(self):
        """Background task that listens for tool call events."""
        while self._running:
            try:
                # In production, this would use PhooksClient.subscribe()
                # For now, simulate event polling
                events = await self._poll_events()
                for event in events:
                    asyncio.create_task(self._handle_event(event))
                
                await asyncio.sleep(0.1)  # Poll interval
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Bridge listen error: {e}")
                await asyncio.sleep(1.0)

    async def _poll_events(self) -> list:
        """
        Poll for tool call events from Phooks.
        
        In a real implementation, this would use PhooksClient.get_events('tool.call').
        This method provides the interface for integration.
        """
        if self.phooks and hasattr(self.phooks, 'get_events'):
            try:
                return await self.phooks.get_events('tool.call')
            except Exception:
                pass
        return []

    async def _handle_event(self, event: Dict):
        """
        Handle a single tool call event.
        
        Expected event format:
        {
            'topic': 'tool.call',
            'data': {
                'request_id': 'uuid',
                'tool_name': 'database_query',
                'parameters': {...},
                'session_id': '...',
                'timestamp': '...'
            }
        }
        """
        self._calls_received += 1
        
        try:
            data = event.get('data', event) if isinstance(event, dict) else event
            request_id = data.get('request_id', str(uuid.uuid4()))
            tool_name = data.get('tool_name', '')
            parameters = data.get('parameters', {})
            
            logger.info(f"Tool call received: {tool_name} (request: {request_id})")
            
            # Execute tool
            start_time = time.time()
            result = await self._execute_tool(tool_name, parameters)
            elapsed = time.time() - start_time
            
            # Publish result
            await self._publish_result(request_id, tool_name, result, elapsed)
            
        except Exception as e:
            logger.error(f"Error handling tool call event: {e}")
            self._calls_failed += 1
            await self._publish_error(request_id, str(e))

    async def _execute_tool(self, tool_name: str, parameters: Dict) -> Any:
        """
        Execute a tool by name with given parameters.
        
        Args:
            tool_name: Name of registered tool
            parameters: Dict of parameters for the tool
            
        Returns:
            Tool execution result
            
        Raises:
            KeyError: If tool not found
        """
        if tool_name not in self._tool_registry:
            available = list(self._tool_registry.keys())
            raise KeyError(f"Tool '{tool_name}' not found. Available: {available}")
        
        handler = self._tool_registry[tool_name]
        
        # ═══ HOST PROTECTION ═══
        # Execute with LOCAL HostSafeEnvironment — no AIHandler dependency
        try:
            from host_protection import HostSafeEnvironment
            
            tool_name_for_protection = tool_name
            
            with HostSafeEnvironment(tool_name=tool_name_for_protection, timeout=300):
                if asyncio.iscoroutinefunction(handler):
                    result = await asyncio.wait_for(
                        handler(**parameters),
                        timeout=300
                    )
                else:
                    result = handler(**parameters)
        except ImportError:
            # Fallback: run without protection
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**parameters)
            else:
                result = handler(**parameters)
        
        self._calls_completed += 1
        return result

    async def _publish_result(self, request_id: str, tool_name: str, 
                             result: Any, elapsed: float):
        """Publish tool execution result to Phooks."""
        topic = f"tool.result.{request_id}"
        payload = {
            'topic': topic,
            'data': {
                'request_id': request_id,
                'tool_name': tool_name,
                'status': 'success',
                'result': self._serialize_result(result),
                'execution_time_ms': int(elapsed * 1000),
                'timestamp': time.time()
            }
        }
        
        if self.phooks and hasattr(self.phooks, 'publish'):
            try:
                await self.phooks.publish(topic, payload['data'])
                logger.debug(f"Result published to {topic}")
            except Exception as e:
                logger.error(f"Failed to publish result: {e}")
        
        # Notify any waiting handlers
        if request_id in self._result_handlers:
            self._result_handlers[request_id].set()

    async def _publish_error(self, request_id: str, error: str):
        """Publish tool execution error to Phooks."""
        topic = f"tool.result.{request_id}"
        payload = {
            'topic': topic,
            'data': {
                'request_id': request_id,
                'status': 'error',
                'error': error,
                'timestamp': time.time()
            }
        }
        
        if self.phooks and hasattr(self.phooks, 'publish'):
            try:
                await self.phooks.publish(topic, payload['data'])
            except Exception:
                pass

    def _serialize_result(self, result: Any) -> Any:
        """Serialize tool result for Phooks transport."""
        try:
            json.dumps(result)
            return result
        except (TypeError, OverflowError):
            # Non-serializable — convert to string
            return str(result)

    # ─── Direct Call Interface ──────────────────────────────────

    async def call_tool_direct(self, tool_name: str, parameters: Dict,
                              timeout: float = 30.0) -> Dict:
        """
        Call a tool directly (bypasses Phooks) and wait for result.
        
        Used for testing and direct integration.
        
        Args:
            tool_name: Name of tool to call
            parameters: Dict of parameters
            timeout: Maximum wait time in seconds
            
        Returns:
            Dict with status, result/error, execution_time_ms
        """
        start_time = time.time()
        
        try:
            result = await self._execute_tool(tool_name, parameters)
            elapsed = time.time() - start_time
            
            return {
                'status': 'success',
                'result': self._serialize_result(result),
                'execution_time_ms': int(elapsed * 1000)
            }
        except KeyError as e:
            return {
                'status': 'error',
                'error': str(e),
                'execution_time_ms': int((time.time() - start_time) * 1000)
            }
        except Exception as e:
            return {
                'status': 'error',
                'error': f"{type(e).__name__}: {str(e)}",
                'execution_time_ms': int((time.time() - start_time) * 1000)
            }

    # ─── Stats ──────────────────────────────────────────────────

    # ═══════════════════════════════════════════════════════════════
    # DYNAMIC CONFIG MANAGEMENT TOOLS
    # ═══════════════════════════════════════════════════════════════

    def register_config_tools(self):
        """Register all config management tools."""
        if not _DYNAMIC_CONFIG_AVAILABLE:
            logger.warning("DynamicConfigLoader not available — skipping config tools")
            return
        
        self.register_tool("config_list_all", self._config_list_all)
        self.register_tool("config_get", self._config_get)
        self.register_tool("config_set", self._config_set)
        self.register_tool("config_flush", self._config_flush)
        self.register_tool("config_flush_source", self._config_flush_source)
        self.register_tool("config_reload", self._config_reload)
        self.register_tool("config_reload_source", self._config_reload_source)
        self.register_tool("config_history", self._config_history)
        self.register_tool("config_summary", self._config_summary)
        self.register_tool("config_start_gui", self._config_start_gui)
        logger.info("Registered 10 dynamic config management tools")

    async def _config_list_all(self, **kwargs) -> Dict:
        """List all registered configs with current values."""
        return {"configs": get_all_configs()}

    async def _config_get(self, source: str = "", key: str = "", **kwargs) -> Dict:
        """Get a single config value or all configs for a source.
        
        Args:
            source: Source name
            key: Setting key (optional — omit to get all from source)
        """
        if key:
            setting = get_setting(source, key)
            if setting:
                return {"setting": setting.to_dict()}
            return {"error": f"Setting '{source}.{key}' not found"}
        else:
            all_c = get_all_configs()
            src = all_c.get(source, [])
            return {"source": source, "settings": src}

    async def _config_set(self, source: str = "", key: str = "",
                           value: Any = None, **kwargs) -> Dict:
        """Update a config value in memory.
        
        Args:
            source: Source name
            key: Setting key
            value: New value
        """
        if not source or not key:
            return {"error": "source and key are required"}
        success, msg = update_config(source, key, value, "phooks_bridge")
        return {"success": success, "message": msg}

    async def _config_flush(self, source: str = "", **kwargs) -> Dict:
        """Flush configs to disk files.
        
        Args:
            source: Optional source name — flush all if omitted
        """
        if source:
            success, msg = flush_source(source)
            return {"results": [{"source": source, "success": success, "message": msg}]}
        results = flush_all()
        return {"results": results}

    async def _config_flush_source(self, source: str = "", **kwargs) -> Dict:
        """Flush a single source's configs to disk."""
        if not source:
            return {"error": "source is required"}
        success, msg = flush_source(source)
        return {"success": success, "message": msg}

    async def _config_reload(self, source: str = "", **kwargs) -> Dict:
        """Reload configs from disk files.
        
        Args:
            source: Optional source name — reload all if omitted
        """
        if source:
            success, msg = load_source(source)
            return {"results": [{"source": source, "success": success, "message": msg}]}
        results = load_all()
        return {"results": results}

    async def _config_reload_source(self, source: str = "", **kwargs) -> Dict:
        """Reload a single source's configs from disk."""
        if not source:
            return {"error": "source is required"}
        success, msg = load_source(source)
        return {"success": success, "message": msg}

    async def _config_history(self, limit: int = 50, **kwargs) -> Dict:
        """Get recent config change history."""
        return {"history": get_change_history(limit)}

    async def _config_summary(self, **kwargs) -> Dict:
        """Get config registry summary stats."""
        return {"summary": get_config_summary()}

    async def _config_start_gui(self, port: int = 8180, **kwargs) -> Dict:
        """Start the admin config GUI web server.
        
        Args:
            port: Port to listen on (default 8180)
        """
        try:
            server = start_admin_gui(port=port)
            return {"success": True, "message": f"Admin GUI started on port {port}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_stats(self) -> Dict:
        """Get bridge statistics."""
        return {
            'running': self._running,
            'tools_registered': len(self._tool_registry),
            'tools': list(self._tool_registry.keys()),
            'calls_received': self._calls_received,
            'calls_completed': self._calls_completed,
            'calls_failed': self._calls_failed
        }
