"""
FastMCP Adapter — Wraps the existing FastMCP server for YuniScript integration.

Part of FastMCP Server (YuniScript Managed).
Provides a clean interface for Phooks bridge to execute tools via the existing
FastMCP server infrastructure.
"""

import asyncio
import importlib
import json
import logging
import os
import sys
from typing import Optional, Dict, Any, List, Callable

logger = logging.getLogger(__name__)

# ═══ HOST PROTECTION — Initialize at module load ═══
_HOST_PROTECTION_INIT = False
def _init_host_protection():
    global _HOST_PROTECTION_INIT
    if not _HOST_PROTECTION_INIT:
        # Use LOCAL host_protection — NO dependency on AIHandler FastMCP
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
_init_host_protection()


class FastMCPAdapter:
    """
    Adapter for the existing FastMCP server.
    
    Discovers available tools from the FastMCP tool registry and provides
    a unified interface for tool execution.
    """

    def __init__(self, tools_dir: Optional[str] = None, db_discovery: bool = True):
        from ecosystem_config import get_fastmcp_tools_path
        self.tools_dir = tools_dir or get_fastmcp_tools_path()
        self._tools = {}
        self._initialized = False
        from ecosystem_config import get_documentation_db_path
        self._db_path = get_documentation_db_path()
        self._db_discovery = db_discovery

    async def initialize(self):
        """Discover and load all available tools."""
        logger.info(f"Initializing FastMCP adapter from {self.tools_dir}")
        
        tools = {}
        
        # Load tools from registry DB (opt-in via db_discovery flag)
        if self._db_discovery:
            db_tools = self._discover_tools_from_db()
            tools.update(db_tools)
        
        # Also scan tools directory
        file_tools = self._discover_tools_from_filesystem()
        tools.update(file_tools)
        
        self._tools = tools
        self._initialized = True
        logger.info(f"FastMCP adapter initialized with {len(tools)} tools")
        return tools

    def _discover_tools_from_db(self) -> Dict[str, Callable]:
        """Discover tools from the FastMCP tool registry database."""
        tools = {}
        try:
            import sqlite3
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    SELECT tool_name, file_path, module_name, function_name 
                    FROM tool_registry WHERE enabled = 1
                ''')
                for row in cursor.fetchall():
                    tool_name, file_path, module_name, function_name = row
                    handler = self._create_tool_handler(tool_name, file_path, module_name, function_name)
                    if handler:
                        tools[tool_name] = handler
            except sqlite3.OperationalError:
                logger.warning("tool_registry table not found")
            
            conn.close()
        except Exception as e:
            logger.error(f"Error discovering tools from DB: {e}")
        
        return tools

    def _discover_tools_from_filesystem(self) -> Dict[str, Callable]:
        """Discover tools by scanning the tools directory."""
        tools = {}
        try:
            if not os.path.isdir(self.tools_dir):
                return tools
            
            for filename in os.listdir(self.tools_dir):
                if filename.endswith('.py') and not filename.startswith('_'):
                    module_name = filename[:-3]
                    handler = self._create_tool_handler_from_file(module_name)
                    if handler:
                        tools[module_name] = handler
            
        except Exception as e:
            logger.error(f"Error scanning tools directory: {e}")
        
        return tools

    def _create_tool_handler(self, tool_name: str, file_path: str, 
                            module_name: str, function_name: str) -> Optional[Callable]:
        """Create a handler for a tool from the registry."""
        try:
            # Try loading from tools directory
            tools_dir = self.tools_dir
            sys.path.insert(0, tools_dir)
            
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, function_name):
                    func = getattr(module, function_name)
                    
                    async def make_handler(f=func, name=tool_name):
                        async def handler(**kwargs):
                            try:
                                if asyncio.iscoroutinefunction(f):
                                    return await f(**kwargs)
                                return f(**kwargs)
                            except Exception as e:
                                logger.error(f"Tool {name} failed: {e}")
                                raise
                        return handler
                    
                    return make_handler
            except (ImportError, AttributeError) as e:
                logger.debug(f"Could not load tool {tool_name}: {e}")
            finally:
                if tools_dir in sys.path:
                    sys.path.remove(tools_dir)
        
        except Exception as e:
            logger.error(f"Error creating handler for {tool_name}: {e}")
        
        return None

    def _create_tool_handler_from_file(self, module_name: str) -> Optional[Callable]:
        """Create a handler by loading a tool module from file."""
        try:
            sys.path.insert(0, self.tools_dir)
            
            try:
                module = importlib.import_module(module_name)
                
                # Find the main function (same name, or 'main', or first public function)
                func_name = module_name
                if hasattr(module, func_name) and callable(getattr(module, func_name)):
                    func = getattr(module, func_name)
                elif hasattr(module, 'main'):
                    func = module.main
                else:
                    # Find first public function
                    for attr in dir(module):
                        if not attr.startswith('_') and callable(getattr(module, attr)):
                            func = getattr(module, attr)
                            break
                    else:
                        return None
                
                async def wrapper(**kwargs):
                    if asyncio.iscoroutinefunction(func):
                        return await func(**kwargs)
                    return func(**kwargs)
                
                return wrapper
                
            except ImportError as e:
                logger.debug(f"Could not import {module_name}: {e}")
                return None
            finally:
                if self.tools_dir in sys.path:
                    sys.path.remove(self.tools_dir)
        
        except Exception as e:
            logger.error(f"Error loading {module_name}: {e}")
            return None

    def get_tool(self, name: str) -> Optional[Callable]:
        """Get a tool handler by name."""
        return self._tools.get(name)

    def get_all_tools(self) -> Dict[str, Callable]:
        """Get all registered tool handlers."""
        return dict(self._tools)

    def get_tool_names(self) -> List[str]:
        """Get names of all registered tools."""
        return sorted(self._tools.keys())

    async def execute_tool(self, name: str, **kwargs) -> Any:
        """
        Execute a tool by name with parameters.
        
        Args:
            name: Tool name
            **kwargs: Tool parameters
            
        Returns:
            Tool result
            
        Raises:
            KeyError: If tool not found
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found. Available: {self.get_tool_names()}")
        
        handler = self._tools[name]
        
        # ═══ HOST PROTECTION (LOCAL — no AIHandler dependency) ═══
        try:
            from host_protection import HostSafeEnvironment
            
            with HostSafeEnvironment(tool_name=name, timeout=300):
                if asyncio.iscoroutinefunction(handler):
                    result = await asyncio.wait_for(
                        handler(**kwargs),
                        timeout=300
                    )
                else:
                    result = handler(**kwargs)
        except ImportError:
            # Fallback: run without protection
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**kwargs)
            else:
                result = handler(**kwargs)
        
        return result

    def get_tool_definitions_for_prompt(self) -> str:
        """Get tool definitions formatted for system prompt injection."""
        tools = []
        for name in self.get_tool_names():
            tools.append(f"- **{name}**: Registered FastMCP tool")
        
        if not tools:
            return "No tools available."
        
        return '\n'.join(tools)
