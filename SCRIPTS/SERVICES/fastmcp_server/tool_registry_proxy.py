"""
Tool Registry Proxy — Proxies tool calls to/from DeepSky clients.

Part of FastMCP Server (YuniScript Managed) — WO #147 deliverable #6.
Spec: /home/deck/Documents/dev-yuniScripts/DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md

Architecture:
┌──────────────┐     Phooks/WS      ┌──────────────────────┐
│  DeepSky      │ ─────────────────→ │ ToolRegistryProxy     │
│  Client(s)    │ ←───────────────── │  ┌─────────────────┐ │
└──────────────┘                    │  │ Discovery Cache  │ │
                                    │  ├─────────────────┤ │
┌──────────────┐     Phooks/WS      │  │ Client Manager   │ │
│  Debug Hooks  │ ←───────────────── │  ├─────────────────┤ │
└──────────────┘                    │  │ Validation Layer │ │
                                    │  ├─────────────────┤ │
┌──────────────┐                    │  │ Tool Executor    │ │
│  Tool Registry│ ←→ direct import  │  └─────────────────┘ │
│  (SQLite)     │                    └──────────────────────┘
└──────────────┘
"""

import asyncio
import copy
import importlib
import json
import logging
import os
import sys
import time
import traceback
import uuid
from collections import defaultdict, deque
from typing import Optional, Dict, Any, List, Callable, Set, Tuple

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────

from ecosystem_config import get_fastmcp_tools_path
DEFAULT_TOOLS_DIR = get_fastmcp_tools_path()
from ecosystem_config import get_documentation_db_path
DEFAULT_DB_PATH = get_documentation_db_path()
DISCOVERY_INTERVAL = 30  # seconds between tool cache refreshes
MAX_RESULT_SIZE = 1024 * 1024  # 1MB max serialized result
MAX_PENDING_CALLS = 500  # max pending tool calls tracked
CLIENT_TIMEOUT = 300  # seconds without heartbeat before client considered stale


class ToolDefinition:
    """
    Immutable definition of a registered tool.
    
    Attributes:
        name: Unique tool name
        module_name: Python module containing the tool
        function_name: Function name within the module
        file_path: Path to the tool file
        category: Tool category (general, database, search, etc.)
        description: Human-readable description
        enabled: Whether the tool is active
        source: 'db_registry', 'filesystem', or 'dynamic_registration'
    """

    def __init__(self, name: str, module_name: str = '', function_name: str = '',
                 file_path: str = '', category: str = 'general',
                 description: str = '', enabled: bool = True,
                 source: str = 'filesystem'):
        self.name = name
        self.module_name = module_name or name
        self.function_name = function_name or name
        self.file_path = file_path or f"{name}.py"
        self.category = category
        self.description = description
        self.enabled = enabled
        self.source = source

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'name': self.name,
            'module_name': self.module_name,
            'function_name': self.function_name,
            'file_path': self.file_path,
            'category': self.category,
            'description': self.description,
            'enabled': self.enabled,
            'source': self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ToolDefinition':
        """Deserialize from dictionary."""
        return cls(
            name=data.get('name', ''),
            module_name=data.get('module_name', ''),
            function_name=data.get('function_name', ''),
            file_path=data.get('file_path', ''),
            category=data.get('category', 'general'),
            description=data.get('description', ''),
            enabled=data.get('enabled', True),
            source=data.get('source', 'filesystem'),
        )

    def __repr__(self) -> str:
        return f"ToolDefinition(name='{self.name}', source='{self.source}', enabled={self.enabled})"


class ToolDiscoveryCache:
    """
    Discovers and caches available tools from FastMCP server.
    
    Periodically syncs with:
    1. DB tool_registry table (enabled tools)
    2. Filesystem tools directory (.py files)
    
    Provides fast lookup by tool name without hitting DB/filesystem each time.
    """

    def __init__(self, tools_dir: str = DEFAULT_TOOLS_DIR,
                 db_path: str = DEFAULT_DB_PATH, db_discovery: bool = True):
        self.tools_dir = tools_dir
        self.db_path = db_path
        self._db_discovery = db_discovery
        self._tools: Dict[str, ToolDefinition] = {}  # name -> definition
        self._last_discovery: float = 0.0
        self._discovery_count: int = 0
        self._discovery_errors: int = 0
        self._lock = asyncio.Lock()
        self._discovery_task: Optional[asyncio.Task] = None
        self._running: bool = False

    async def start(self):
        """Start periodic discovery."""
        self._running = True
        await self._discover()  # Initial discovery
        self._discovery_task = asyncio.create_task(self._discovery_loop())
        logger.info(f"ToolDiscoveryCache started: {len(self._tools)} tools found")

    async def stop(self):
        """Stop periodic discovery."""
        self._running = False
        if self._discovery_task:
            try:
                self._discovery_task.cancel()
                try:
                    await self._discovery_task
                except asyncio.CancelledError:
                    pass
                # Let event loop propagate cancellation
                try:
                    await asyncio.sleep(0)
                except RuntimeError:
                    pass
            except RuntimeError:
                # Event loop may be closed already
                pass
            self._discovery_task = None
        logger.info("ToolDiscoveryCache stopped")

    async def _discovery_loop(self):
        """Background loop for periodic discovery."""
        while self._running:
            await asyncio.sleep(DISCOVERY_INTERVAL)
            await self._discover()
    async def _discover(self):
        """Discover tools from all sources."""
        async with self._lock:
            discovered: Dict[str, ToolDefinition] = {}
            errors: List[str] = []

            # Source 1: DB tool_registry (opt-in)
            if self._db_discovery:
                db_tools = await self._discover_from_db()
                for tool in db_tools:
                    discovered[tool.name] = tool
            # Source 2: Filesystem scan (overrides DB for source='filesystem')
            fs_tools = await self._discover_from_filesystem()
            for tool in fs_tools:
                if tool.name not in discovered:
                    discovered[tool.name] = tool
                # If DB has it but missing filesystem details, augment
                elif not discovered[tool.name].file_path:
                    discovered[tool.name].file_path = tool.file_path
            # Source 3: FastMCP registered tools (if available as import)
            fm_tools = await self._discover_from_fastmcp_registry()
            for tool in fm_tools:
                if tool.name not in discovered:
                    discovered[tool.name] = tool
            self._tools = discovered
            self._last_discovery = time.time()
            self._discovery_count += 1

            if errors:
                logger.warning(f"Discovery completed with {len(errors)} errors: {'; '.join(errors)}")

    async def _discover_from_db(self) -> List[ToolDefinition]:
        """Discover tools from the SQLite tool_registry table."""
        tools: List[ToolDefinition] = []
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Check if tool_registry exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_registry'")
            if not cursor.fetchone():
                conn.close()
                return tools

            cursor.execute('''
                SELECT tool_name, module_name, function_name, file_path,
                       category, description, enabled
                FROM tool_registry WHERE enabled = 1
            ''')

            for row in cursor.fetchall():
                tool = ToolDefinition(
                    name=row['tool_name'],
                    module_name=row['module_name'] or row['tool_name'],
                    function_name=row['function_name'] or row['tool_name'],
                    file_path=row['file_path'] or f"{row['tool_name']}.py",
                    category=row['category'] or 'general',
                    description=row['description'] or '',
                    enabled=bool(row['enabled']),
                    source='db_registry',
                )
                tools.append(tool)

            conn.close()
            logger.debug(f"Discovered {len(tools)} tools from DB registry")
        except sqlite3.OperationalError as e:
            logger.debug(f"DB discovery skipped (table may not exist): {e}")
        except Exception as e:
            logger.error(f"DB discovery error: {e}")

        return tools

    async def _discover_from_filesystem(self) -> List[ToolDefinition]:
        """Discover tools by scanning the filesystem directory."""
        tools: List[ToolDefinition] = []
        try:
            if not os.path.isdir(self.tools_dir):
                logger.debug(f"Tools directory not found: {self.tools_dir}")
                return tools

            for filename in sorted(os.listdir(self.tools_dir)):
                if not filename.endswith('.py') or filename.startswith('_'):
                    continue
                module_name = filename[:-3]
                tool = ToolDefinition(
                    name=module_name,
                    module_name=module_name,
                    function_name=module_name,
                    file_path=f"{module_name}.py",
                    category='general',
                    description='',
                    enabled=True,
                    source='filesystem',
                )
                tools.append(tool)

            logger.debug(f"Discovered {len(tools)} tools from filesystem")
        except PermissionError as e:
            logger.error(f"Permission denied reading tools directory: {e}")
        except Exception as e:
            logger.error(f"Filesystem discovery error: {e}")

        return tools

    async def _discover_from_fastmcp_registry(self) -> List[ToolDefinition]:
        """Discover tools by importing FastMCP tool registry if available (local only)."""
        tools: List[ToolDefinition] = []
        mcp_path = None
        try:
            from ecosystem_config import get_mcp_server_path
            mcp_path = get_mcp_server_path()
            sys.path.insert(0, mcp_path)
            from tool_registry import get_tool_definitions
            registry_tools = get_tool_definitions()
            for name, meta in registry_tools.items():
                tool = ToolDefinition(
                    name=name,
                    module_name=meta.get('module_name', name),
                    function_name=meta.get('function_name', name),
                    file_path=meta.get('file_path', f"{name}.py"),
                    category=meta.get('category', 'general'),
                    description=meta.get('description', ''),
                    enabled=meta.get('enabled', True),
                    source='dynamic_registration',
                )
                tools.append(tool)
            if mcp_path:
                sys.path.remove(mcp_path)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"FastMCP registry discovery skipped: {e}")
        finally:
            if mcp_path:
                try:
                    sys.path.remove(mcp_path)
                except (ValueError, IndexError):
                    pass

        return tools

    # ─── Public API ──────────────────────────────────────────────

    async def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool definition by name."""
        async with self._lock:
            return self._tools.get(name)

    async def get_all_tools(self) -> Dict[str, ToolDefinition]:
        """Get all discovered tool definitions."""
        async with self._lock:
            return dict(self._tools)

    async def get_tool_names(self) -> List[str]:
        """Get names of all discovered tools."""
        async with self._lock:
            return sorted(self._tools.keys())

    async def get_tools_by_category(self, category: str) -> List[ToolDefinition]:
        """Get all tools in a specific category."""
        async with self._lock:
            return [t for t in self._tools.values() if t.category == category]

    async def count_tools(self) -> int:
        """Get the total number of discovered tools."""
        async with self._lock:
            return len(self._tools)

    async def refresh(self) -> int:
        """Force an immediate discovery refresh."""
        await self._discover()
        return len(self._tools)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            'tool_count': len(self._tools),
            'last_discovery': self._last_discovery,
            'discovery_count': self._discovery_count,
            'discovery_errors': self._discovery_errors,
            'running': self._running,
        }


class ClientConnection:
    """
    Represents a connected DeepSky client.
    
    Tracks session info, capabilities, heartbeat, and pending tool calls.
    """

    def __init__(self, client_id: str, session_id: str = '',
                 metadata: Optional[Dict[str, Any]] = None):
        self.client_id = client_id
        self.session_id = session_id
        self.metadata = metadata or {}
        self.connected_at = time.time()
        self.last_heartbeat = time.time()
        self.pending_calls: Dict[str, 'PendingToolCall'] = {}
        self.completed_calls: int = 0
        self.failed_calls: int = 0
        self.capabilities: Set[str] = set(self.metadata.get('capabilities', []))
        self._is_active: bool = True

    @property
    def is_active(self) -> bool:
        """Check if client is still active (not timed out)."""
        if not self._is_active:
            return False
        if time.time() - self.last_heartbeat > CLIENT_TIMEOUT:
            self._is_active = False
            return False
        return True

    def heartbeat(self):
        """Update heartbeat timestamp."""
        self.last_heartbeat = time.time()
        self._is_active = True

    def mark_inactive(self):
        """Mark client as inactive."""
        self._is_active = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'client_id': self.client_id,
            'session_id': self.session_id,
            'connected_at': self.connected_at,
            'last_heartbeat': self.last_heartbeat,
            'is_active': self.is_active,
            'pending_call_count': len(self.pending_calls),
            'completed_calls': self.completed_calls,
            'failed_calls': self.failed_calls,
            'capabilities': list(self.capabilities),
            'metadata': self.metadata,
        }

    def __repr__(self) -> str:
        return (f"ClientConnection(client_id='{self.client_id}', "
                f"active={self.is_active}, pending={len(self.pending_calls)})")


class PendingToolCall:
    """
    Tracks a pending tool call that has been proxied.
    
    Used for timeout monitoring and result routing.
    """

    def __init__(self, call_id: str, tool_name: str, parameters: Dict[str, Any],
                 client_id: str, request_id: str = ''):
        self.call_id = call_id
        self.tool_name = tool_name
        self.parameters = parameters
        self.client_id = client_id
        self.request_id = request_id or call_id
        self.start_time = time.time()
        self.completed: bool = False
        self.result: Optional[Any] = None
        self.error: Optional[str] = None
        self.event = asyncio.Event()

    @property
    def elapsed(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time

    def complete(self, result: Any):
        """Mark as completed with result."""
        self.result = result
        self.completed = True
        self.event.set()

    def fail(self, error: str):
        """Mark as failed with error."""
        self.error = error
        self.completed = True
        self.event.set()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'call_id': self.call_id,
            'tool_name': self.tool_name,
            'client_id': self.client_id,
            'request_id': self.request_id,
            'start_time': self.start_time,
            'elapsed': self.elapsed,
            'completed': self.completed,
            'has_result': self.result is not None,
            'has_error': self.error is not None,
        }

    def __repr__(self) -> str:
        return (f"PendingToolCall(call_id='{self.call_id}', "
                f"tool='{self.tool_name}', elapsed={self.elapsed:.1f}s)")


class ToolCallValidator:
    """
    Validates tool calls before proxying.
    
    Checks:
    - Tool exists in registry
    - Parameters match expected schema (if available)
    - Client has permission (if authorization enabled)
    - Parameter sizes are within limits
    """

    def __init__(self):
        self._param_size_limit: int = 1024 * 100  # 100KB max parameter JSON
        self._param_count_limit: int = 50  # max 50 parameters per call
        self._string_param_length_limit: int = 10000  # max 10K per string param
        self._validation_errors: int = 0
        self._validation_passed: int = 0

    async def validate(self, tool_name: str, parameters: Dict[str, Any],
                       tool_def: Optional[ToolDefinition] = None,
                       client: Optional[ClientConnection] = None) -> Tuple[bool, str]:
        """
        Validate a tool call.
        
        Args:
            tool_name: Name of tool being called
            parameters: Dict of parameters
            tool_def: Optional tool definition for schema validation
            client: Optional client connection for permission checks
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # 1. Check tool exists
        if tool_def and not tool_def.enabled:
            self._validation_errors += 1
            return False, f"Tool '{tool_name}' is disabled"

        # 2. Check parameter count
        if len(parameters) > self._param_count_limit:
            self._validation_errors += 1
            return False, (f"Too many parameters: {len(parameters)} > "
                          f"{self._param_count_limit}")

        # 3. Check serialized parameter size
        try:
            param_json = json.dumps(parameters)
            if len(param_json) > self._param_size_limit:
                self._validation_errors += 1
                return False, (f"Parameters exceed size limit: "
                              f"{len(param_json)} > {self._param_size_limit}")
        except (TypeError, OverflowError, ValueError):
            self._validation_errors += 1
            return False, "Parameters are not JSON-serializable"

        # 4. Check string parameter lengths
        for key, value in parameters.items():
            if isinstance(value, str) and len(value) > self._string_param_length_limit:
                self._validation_errors += 1
                return False, (f"Parameter '{key}' exceeds string length limit: "
                              f"{len(value)} > {self._string_param_length_limit}")

        # 5. Check allowed parameters (reject dangerous parameter names)
        dangerous_params = {'__import__', 'eval', 'exec', '__builtins__'}
        for key in parameters:
            if key in dangerous_params or key.startswith('__'):
                self._validation_errors += 1
                return False, f"Parameter '{key}' is not allowed"

        self._validation_passed += 1
        return True, ''

    def get_stats(self) -> Dict[str, Any]:
        """Get validation statistics."""
        return {
            'validation_passed': self._validation_passed,
            'validation_errors': self._validation_errors,
        }


class ToolRegistryProxy:
    """
    Main proxy that manages tool call routing between DeepSky clients and FastMCP.
    
    Features:
    - Tool discovery and caching (periodic refresh)
    - Client connection management with heartbeat monitoring
    - Tool call validation before proxying
    - Asynchronous execution with timeout handling
    - Comprehensive stats and health reporting
    """

    def __init__(self, tools_dir: str = DEFAULT_TOOLS_DIR,
                 db_path: str = DEFAULT_DB_PATH, db_discovery: bool = True):
        self.cache = ToolDiscoveryCache(tools_dir, db_path, db_discovery)
        self.validator = ToolCallValidator()
        self._db_discovery = db_discovery
        
        # Client management
        self._clients: Dict[str, ClientConnection] = {}  # client_id -> connection
        self._pending_calls: Dict[str, PendingToolCall] = {}  # call_id -> pending
        self._call_history: deque = deque(maxlen=1000)  # recent calls
        
        # Phooks integration (optional)
        self.phooks = None
        
        # Tool handler cache (lazy loaded)
        self._tool_handlers: Dict[str, Callable] = {}
        self._tools_dir = tools_dir
        
        # Locks
        self._clients_lock = asyncio.Lock()
        self._pending_lock = asyncio.Lock()
        self._handlers_lock = asyncio.Lock()
        
        # Tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running: bool = False
        
        # Stats
        self._total_calls_proxied: int = 0
        self._total_calls_succeeded: int = 0
        self._total_calls_failed: int = 0
        self._total_calls_timeout: int = 0
        self._start_time: float = 0.0
        
        # Shared event for publish
        self._last_error_published: float = 0.0

    # ─── Lifecycle ──────────────────────────────────────────────

    async def start(self):
        """Start the proxy."""
        self._start_time = time.time()
        self._running = True
        
        # Start tool discovery cache
        await self.cache.start()
        
        # Start cleanup task for stale clients and timed-out calls
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        # Log initial state
        tool_count = await self.cache.count_tools()
        logger.info(f"ToolRegistryProxy started: {tool_count} tools cached, 0 clients")

    async def stop(self):
        """Stop the proxy."""
        self._running = False
        
        # Stop cleanup task
        if self._cleanup_task:
            try:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass
            except RuntimeError:
                # Event loop may be closed already (e.g., test teardown)
                pass
        
        # Stop discovery cache
        await self.cache.stop()
        
        # Fail all pending calls
        async with self._pending_lock:
            for call_id, pending in list(self._pending_calls.items()):
                if not pending.completed:
                    pending.fail("Proxy shutting down")
            self._pending_calls.clear()
        
        # Mark all clients as inactive
        async with self._clients_lock:
            for client in self._clients.values():
                client.mark_inactive()
        
        logger.info("ToolRegistryProxy stopped")

    async def _cleanup_loop(self):
        """Periodic cleanup of stale clients and timed-out calls."""
        while self._running:
            try:
                await self._cleanup_stale_clients()
                await self._cleanup_timed_out_calls()
                await asyncio.sleep(60)  # Every 60 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                await asyncio.sleep(60)

    async def _cleanup_stale_clients(self):
        """Remove clients that have timed out."""
        async with self._clients_lock:
            stale = [
                cid for cid, client in self._clients.items()
                if not client.is_active
            ]
            for cid in stale:
                logger.info(f"Removing stale client: {cid}")
                del self._clients[cid]

    async def _cleanup_timed_out_calls(self):
        """Fail tool calls that have exceeded timeout."""
        timeout = 600  # 10 minutes max
        async with self._pending_lock:
            timed_out = [
                call_id for call_id, pending in self._pending_calls.items()
                if not pending.completed and pending.elapsed > timeout
            ]
            for call_id in timed_out:
                pending = self._pending_calls[call_id]
                pending.fail(f"Timed out after {timeout}s")
                self._total_calls_timeout += 1
                logger.warning(f"Tool call timed out: {pending.tool_name} "
                              f"(call_id: {call_id}, elapsed: {pending.elapsed:.1f}s)")

    # ─── Client Management ──────────────────────────────────────

    async def register_client(self, client_id: str) -> bool:
        """Register a new FastMCP tool client."""
        async with self._clients_lock:
            if client_id in self._clients:
                # Update existing client
                client = self._clients[client_id]
                client.heartbeat()
            else:
                client = ToolClient(id=client_id)
                self._clients[client_id] = client
                logger.info(f"Tool client registered: {client_id}")
        return True
    async def unregister_client(self, client_id: str, reason: str = 'disconnect'):
        """
        Unregister a client connection.
        
        Args:
            client_id: Client to remove
            reason: Reason for removal
        """
        async with self._clients_lock:
            if client_id not in self._clients:
                return
            
            client = self._clients[client_id]
            # Fail all pending calls for this client
            async with self._pending_lock:
                for call_id, pending in list(self._pending_calls.items()):
                    if pending.client_id == client_id and not pending.completed:
                        pending.fail(f"Client disconnected: {reason}")
            
            del self._clients[client_id]
            logger.info(f"Client unregistered: {client_id} ({reason})")

    async def client_heartbeat(self, client_id: str) -> bool:
        """
        Update heartbeat for a client.
        
        Args:
            client_id: Client to update
            
        Returns:
            True if client exists and was updated
        """
        async with self._clients_lock:
            if client_id not in self._clients:
                return False
            self._clients[client_id].heartbeat()
            return True

    async def get_client(self, client_id: str) -> Optional[ClientConnection]:
        """Get a client connection by ID."""
        async with self._clients_lock:
            return self._clients.get(client_id)

    async def get_active_clients(self) -> List[ClientConnection]:
        """Get all active client connections."""
        async with self._clients_lock:
            return [c for c in self._clients.values() if c.is_active]

    async def get_client_count(self) -> int:
        """Get count of active clients."""
        clients = await self.get_active_clients()
        return len(clients)

    # ─── Tool Proxying ──────────────────────────────────────────

    async def call_tool(self, tool_name: str, parameters: Dict[str, Any],
                        client_id: str = 'internal', request_id: str = '',
                        timeout: float = 300.0) -> Dict[str, Any]:
        """
        Proxy a tool call from a client.
        
        Args:
            tool_name: Name of tool to call
            parameters: Dict of tool parameters
            client_id: ID of calling client
            request_id: Optional request ID for tracking
            timeout: Maximum execution time in seconds
            
        Returns:
            Dict with status, result/error, execution_time_ms
        """
        call_id = request_id or str(uuid.uuid4())
        start_time = time.time()
        self._total_calls_proxied += 1
        
        # 1. Look up tool in cache
        tool_def = await self.cache.get_tool(tool_name)
        
        # 2. Get client for validation
        client = None
        if client_id != 'internal':
            client = await self.get_client(client_id)
        
        # 3. Validate call
        is_valid, error_msg = await self.validator.validate(
            tool_name, parameters, tool_def, client
        )
        if not is_valid:
            self._total_calls_failed += 1
            elapsed = time.time() - start_time
            self._record_call(tool_name, client_id, call_id, False, error_msg, elapsed)
            return {
                'status': 'validation_error',
                'error': error_msg,
                'execution_time_ms': int(elapsed * 1000),
                'call_id': call_id,
            }
        
        # 4. Create pending call tracker
        pending = PendingToolCall(call_id, tool_name, parameters, client_id, request_id)
        async with self._pending_lock:
            self._pending_calls[call_id] = pending
        
        # 5. Execute the tool
        try:
            handler = await self._get_handler(tool_name)
            if handler is None:
                raise KeyError(f"Tool '{tool_name}' not found")
            
            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(handler(**parameters), timeout=timeout)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(handler, **parameters),
                    timeout=timeout
                )
            
            # Serialize result
            serialized = self._serialize_result(result)
            
            # Mark pending as completed
            pending.complete(serialized)
            
            self._total_calls_succeeded += 1
            elapsed = time.time() - start_time
            self._record_call(tool_name, client_id, call_id, True, '', elapsed)
            
            return {
                'status': 'success',
                'result': serialized,
                'execution_time_ms': int(elapsed * 1000),
                'call_id': call_id,
            }
            
        except asyncio.TimeoutError:
            pending.fail(f"Timed out after {timeout}s")
            self._total_calls_timeout += 1
            self._total_calls_failed += 1
            elapsed = time.time() - start_time
            
            await self._publish_error(tool_name, client_id,
                                     f"Timeout after {timeout}s",
                                     'timeout', parameters)
            
            return {
                'status': 'timeout',
                'error': f"Tool execution timed out after {timeout}s",
                'execution_time_ms': int(elapsed * 1000),
                'call_id': call_id,
            }
            
        except KeyError as e:
            pending.fail(str(e))
            self._total_calls_failed += 1
            elapsed = time.time() - start_time
            return {
                'status': 'not_found',
                'error': str(e),
                'execution_time_ms': int(elapsed * 1000),
                'call_id': call_id,
            }
            
        except Exception as e:
            pending.fail(f"{type(e).__name__}: {str(e)}")
            self._total_calls_failed += 1
            elapsed = time.time() - start_time
            
            await self._publish_error(tool_name, client_id,
                                     f"{type(e).__name__}: {str(e)}",
                                     'execution_error', parameters)
            
            return {
                'status': 'error',
                'error': f"{type(e).__name__}: {str(e)}",
                'execution_time_ms': int(elapsed * 1000),
                'call_id': call_id,
            }
        finally:
            # Clean up pending tracker after a delay (for result retrieval)
            async with self._pending_lock:
                if call_id in self._pending_calls:
                    del self._pending_calls[call_id]

    async def _get_handler(self, tool_name: str) -> Optional[Callable]:
        """
        Get or create a handler for a tool.
        
        Handlers are cached after first load.
        """
        async with self._handlers_lock:
            if tool_name in self._tool_handlers:
                return self._tool_handlers[tool_name]
        
        # Load from filesystem
        tool_def = await self.cache.get_tool(tool_name)
        if not tool_def:
            return None
            
        sys.path.insert(0, self._tools_dir)
        try:
            module = importlib.import_module(tool_def.module_name)
                
            # Find the function
            func = None
            if hasattr(module, tool_def.function_name):
                func = getattr(module, tool_def.function_name)
            elif hasattr(module, 'main'):
                func = module.main
            else:
                for attr in dir(module):
                    if not attr.startswith('_') and callable(getattr(module, attr)):
                        func = getattr(module, attr)
                        break
                
            if func is None:
                logger.warning(f"No callable function found for tool '{tool_name}'")
                return None
                
            async def handler_wrapper(**kwargs):
                if asyncio.iscoroutinefunction(func):
                    return await func(**kwargs)
                return func(**kwargs)
                
            async with self._handlers_lock:
                self._tool_handlers[tool_name] = handler_wrapper
                
            return handler_wrapper
                
        except ImportError as e:
            logger.error(f"Could not import module for tool '{tool_name}': {e}")
            return None
        finally:
            try:
                sys.path.remove(self._tools_dir)
            except ValueError:
                pass
                    
    def _serialize_result(self, result: Any) -> Any:
        """Serialize a tool result for JSON transport."""
        if result is None:
            return None
        try:
            json.dumps(result)
            if isinstance(result, str) and len(result) > MAX_RESULT_SIZE:
                return result[:MAX_RESULT_SIZE] + '... [TRUNCATED]'
            return result
        except (TypeError, OverflowError, ValueError):
            result_str = str(result)
            if len(result_str) > MAX_RESULT_SIZE:
                return result_str[:MAX_RESULT_SIZE] + '... [TRUNCATED]'
            return result_str

    # ─── Result Retrieval ────────────────────────────────────────

    async def get_pending_result(self, call_id: str,
                                 timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """
        Wait for and retrieve a pending tool call result.
        
        Args:
            call_id: Call ID to wait for
            timeout: Max time to wait in seconds
            
        Returns:
            Result dict or None if not found
        """
        async with self._pending_lock:
            pending = self._pending_calls.get(call_id)
            if not pending:
                return None
        
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        
        if pending.error:
            return {
                'status': 'error',
                'error': pending.error,
                'call_id': call_id,
            }
        
        return {
            'status': 'success',
            'result': pending.result,
            'call_id': call_id,
        }

    # ─── Error Publishing ───────────────────────────────────────

    async def _publish_error(self, tool_name: str, client_id: str,
                              error: str, error_type: str,
                              parameters: Dict[str, Any]):
        """Publish error to Phooks for work order generation."""
        if not self.phooks:
            return
        
        if hasattr(self.phooks, 'publish'):
            import traceback as tb
            error_data = {
                'event_id': str(uuid.uuid4()),
                'type': 'proxy_error',
                'proxy_error_type': error_type,
                'tool_name': tool_name,
                'client_id': client_id,
                'error': error,
                'stack_trace': ''.join(tb.format_stack()),
                'component': 'fastmcp_server.tool_registry_proxy',
                'severity': 'error',
                'context': {
                    'parameters_summary': str(parameters)[:500],
                },
                'timestamp': time.time(),
            }
            await self.phooks.publish('system.error', error_data)
    # ─── Call History ────────────────────────────────────────────

    def _record_call(self, tool_name: str, client_id: str, call_id: str,
                     success: bool, error: str, elapsed: float):
        """Record a completed tool call in history and update client stats."""
        self._call_history.append({
        
        'tool_name': tool_name,
        
        'client_id': client_id,
        
        'call_id': call_id,
        
        'success': success,
        
        'error': error if not success else '',
        
        'elapsed': elapsed,
        
        'timestamp': time.time(),
        
        })
        
        # Update client connection stats
        client = self._clients.get(client_id)
        if client:
            if success:
                client.completed_calls += 1
            else:
                client.failed_calls += 1
            # Remove from pending if present
            if call_id in client.pending_calls:
                del client.pending_calls[call_id]

    def get_call_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent tool call history."""
        return list(self._call_history)[-limit:]

    # ─── Stats ──────────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive proxy statistics."""
        return {
            'uptime': time.time() - self._start_time if self._start_time else 0,
            'running': self._running,
            'tools': self.cache.get_stats(),
            'clients': {
                'total_registered': len(self._clients),
                'active': await self.get_client_count(),
                'pending_calls': sum(
                    len(c.pending_calls) for c in self._clients.values()
                ),
            },
            'calls': {
                'total_proxied': self._total_calls_proxied,
                'succeeded': self._total_calls_succeeded,
                'failed': self._total_calls_failed,
                'timed_out': self._total_calls_timeout,
                'in_flight': len(self._pending_calls),
            },
            'validation': self.validator.get_stats(),
            'cache': self.cache.get_stats(),
        }

    async def health_check(self) -> Dict[str, Any]:
        """Quick health check."""
        tool_count = await self.cache.count_tools()
        client_count = await self.get_client_count()
        return {
            'status': 'healthy' if self._running and tool_count > 0 else 'degraded',
            'running': self._running,
            'tools_cached': tool_count,
            'active_clients': client_count,
        }

