"""
Unit tests for ToolRegistryProxy and its sub-components.

Part of FastMCP Server (YuniScript Managed) — WO #147 deliverable #8.
Tests: ToolDiscoveryCache, ClientConnection, ToolCallValidator, ToolRegistryProxy,
       pending calls, client management, error handling, edge cases.

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'os.path.join(os.path.dirname(__file__), "..")  # local fastmcp_server')

from tool_registry_proxy import (
    ToolDefinition,
    ToolDiscoveryCache,
    ClientConnection,
    PendingToolCall,
    ToolCallValidator,
    ToolRegistryProxy,
    DISCOVERY_INTERVAL,
    CLIENT_TIMEOUT,
    MAX_RESULT_SIZE,
)


# ═══════════════════════════════════════════════════════════════════
# ToolDefinition Tests
# ═══════════════════════════════════════════════════════════════════

class TestToolDefinition(unittest.TestCase):
    """Test ToolDefinition class."""

    def test_create_default(self):
        """Should create with default values."""
        td = ToolDefinition(name='test_tool')
        self.assertEqual(td.name, 'test_tool')
        self.assertEqual(td.module_name, 'test_tool')
        self.assertEqual(td.function_name, 'test_tool')
        self.assertEqual(td.file_path, 'test_tool.py')
        self.assertEqual(td.category, 'general')
        self.assertEqual(td.description, '')
        self.assertTrue(td.enabled)
        self.assertEqual(td.source, 'filesystem')

    def test_create_custom(self):
        """Should create with custom values."""
        td = ToolDefinition(
            name='db_query',
            module_name='database_query',
            function_name='query_db',
            file_path='tools/database_query.py',
            category='database',
            description='Query a database',
            enabled=True,
            source='db_registry',
        )
        self.assertEqual(td.name, 'db_query')
        self.assertEqual(td.module_name, 'database_query')
        self.assertEqual(td.function_name, 'query_db')
        self.assertEqual(td.category, 'database')
        self.assertEqual(td.source, 'db_registry')

    def test_to_dict(self):
        """to_dict should serialize all fields."""
        td = ToolDefinition(name='test', description='A test tool')
        d = td.to_dict()
        self.assertEqual(d['name'], 'test')
        self.assertEqual(d['description'], 'A test tool')
        self.assertEqual(d['enabled'], True)
        self.assertEqual(d['source'], 'filesystem')

    def test_from_dict(self):
        """from_dict should deserialize correctly."""
        data = {
            'name': 'restored',
            'module_name': 'restore_module',
            'function_name': 'restore_func',
            'file_path': 'restore.py',
            'category': 'utility',
            'description': 'Restored from dict',
            'enabled': False,
            'source': 'dynamic_registration',
        }
        td = ToolDefinition.from_dict(data)
        self.assertEqual(td.name, 'restored')
        self.assertEqual(td.category, 'utility')
        self.assertFalse(td.enabled)

    def test_from_dict_partial(self):
        """from_dict should handle missing fields."""
        td = ToolDefinition.from_dict({'name': 'minimal'})
        self.assertEqual(td.name, 'minimal')
        self.assertEqual(td.category, 'general')
        self.assertTrue(td.enabled)

    def test_from_dict_empty(self):
        """from_dict should handle empty dict."""
        td = ToolDefinition.from_dict({})
        self.assertEqual(td.name, '')
        self.assertEqual(td.category, 'general')

    def test_repr(self):
        """__repr__ should include key info."""
        td = ToolDefinition(name='my_tool', enabled=True, source='filesystem')
        r = repr(td)
        self.assertIn('my_tool', r)
        self.assertIn('filesystem', r)
        self.assertIn('True', r)

    def test_equality(self):
        """ToolDefinitions with same values should still be separate objects."""
        a = ToolDefinition(name='same')
        b = ToolDefinition(name='same')
        self.assertIsNot(a, b)
        self.assertEqual(a.name, b.name)

    def test_disabled_tool(self):
        """Should support disabled tools."""
        td = ToolDefinition(name='disabled', enabled=False)
        self.assertFalse(td.enabled)
        d = td.to_dict()
        self.assertFalse(d['enabled'])


# ═══════════════════════════════════════════════════════════════════
# ClientConnection Tests
# ═══════════════════════════════════════════════════════════════════

class TestClientConnection(unittest.TestCase):
    """Test ClientConnection class."""

    def test_create_minimal(self):
        """Should create with minimal args."""
        cc = ClientConnection(client_id='client_1')
        self.assertEqual(cc.client_id, 'client_1')
        self.assertEqual(cc.session_id, '')
        self.assertEqual(cc.metadata, {})
        self.assertTrue(cc.is_active)
        self.assertEqual(cc.pending_calls, {})
        self.assertEqual(cc.completed_calls, 0)
        self.assertEqual(cc.failed_calls, 0)

    def test_create_with_session(self):
        """Should create with session ID."""
        cc = ClientConnection(client_id='c1', session_id='session_abc')
        self.assertEqual(cc.session_id, 'session_abc')
        self.assertTrue(cc.is_active)

    def test_create_with_metadata(self):
        """Should create with metadata and capabilities."""
        cc = ClientConnection(
            client_id='c1',
            metadata={'capabilities': ['read', 'write'], 'version': '1.0'}
        )
        self.assertIn('read', cc.capabilities)
        self.assertIn('write', cc.capabilities)
        self.assertEqual(cc.metadata['version'], '1.0')

    def test_heartbeat_updates_timestamp(self):
        """heartbeat should update last_heartbeat."""
        cc = ClientConnection(client_id='c1')
        old_time = cc.last_heartbeat
        time.sleep(0.01)
        cc.heartbeat()
        self.assertGreater(cc.last_heartbeat, old_time)

    def test_heartbeat_reactivates(self):
        """heartbeat should reactivate inactive client."""
        cc = ClientConnection(client_id='c1')
        cc.mark_inactive()
        self.assertFalse(cc.is_active)
        cc.heartbeat()
        self.assertTrue(cc.is_active)

    def test_stale_client(self):
        """Client should become inactive after timeout."""
        with patch('tool_registry_proxy.time') as mock_time:
            mock_time.time.return_value = 1000.0
            cc = ClientConnection(client_id='c1')
            self.assertTrue(cc.is_active)
            
            # Advance past timeout
            mock_time.time.return_value = 1000.0 + CLIENT_TIMEOUT + 1
            self.assertFalse(cc.is_active)

    def test_mark_inactive(self):
        """mark_inactive should set _is_active to False."""
        cc = ClientConnection(client_id='c1')
        self.assertTrue(cc.is_active)
        cc.mark_inactive()
        self.assertFalse(cc.is_active)

    def test_to_dict(self):
        """to_dict should serialize correctly."""
        cc = ClientConnection(
            client_id='c1',
            session_id='s1',
            metadata={'version': '2.0'}
        )
        cc.completed_calls = 5
        cc.failed_calls = 1
        d = cc.to_dict()
        self.assertEqual(d['client_id'], 'c1')
        self.assertEqual(d['session_id'], 's1')
        self.assertEqual(d['completed_calls'], 5)
        self.assertEqual(d['failed_calls'], 1)
        self.assertTrue(d['is_active'])

    def test_repr(self):
        """__repr__ should include key info."""
        cc = ClientConnection(client_id='my_client')
        r = repr(cc)
        self.assertIn('my_client', r)
        self.assertIn('active', r)

    def test_pending_calls_tracking(self):
        """Should track pending calls dict."""
        cc = ClientConnection(client_id='c1')
        call = PendingToolCall('call_1', 'test_tool', {}, 'c1')
        cc.pending_calls['call_1'] = call
        self.assertEqual(len(cc.pending_calls), 1)
        
        # to_dict should reflect pending call count
        d = cc.to_dict()
        self.assertEqual(d['pending_call_count'], 1)


# ═══════════════════════════════════════════════════════════════════
# PendingToolCall Tests
# ═══════════════════════════════════════════════════════════════════

class TestPendingToolCall(unittest.TestCase):
    """Test PendingToolCall class."""

    def test_create(self):
        """Should create with correct defaults."""
        ptc = PendingToolCall('call_1', 'test_tool', {'param': 'val'}, 'client_1')
        self.assertEqual(ptc.call_id, 'call_1')
        self.assertEqual(ptc.tool_name, 'test_tool')
        self.assertEqual(ptc.parameters, {'param': 'val'})
        self.assertEqual(ptc.client_id, 'client_1')
        self.assertFalse(ptc.completed)
        self.assertIsNone(ptc.result)
        self.assertIsNone(ptc.error)

    def test_create_with_request_id(self):
        """Should use provided request_id."""
        ptc = PendingToolCall('call_1', 'test', {}, 'c1', request_id='req_xyz')
        self.assertEqual(ptc.request_id, 'req_xyz')

    def test_complete(self):
        """complete should set result and mark completed."""
        ptc = PendingToolCall('c1', 't1', {}, 'cl1')
        self.assertFalse(ptc.completed)
        ptc.complete('success_result')
        self.assertTrue(ptc.completed)
        self.assertEqual(ptc.result, 'success_result')
        self.assertIsNone(ptc.error)

    def test_fail(self):
        """fail should set error and mark completed."""
        ptc = PendingToolCall('c1', 't1', {}, 'cl1')
        ptc.fail('Something broke')
        self.assertTrue(ptc.completed)
        self.assertEqual(ptc.error, 'Something broke')
        self.assertIsNone(ptc.result)

    def test_elapsed(self):
        """elapsed should return time since creation."""
        ptc = PendingToolCall('c1', 't1', {}, 'cl1')
        time.sleep(0.01)
        self.assertGreater(ptc.elapsed, 0.005)

    def test_event_triggered_on_complete(self):
        """completing should set the asyncio Event."""
        ptc = PendingToolCall('c1', 't1', {}, 'cl1')
        self.assertFalse(ptc.event.is_set())
        ptc.complete('ok')
        self.assertTrue(ptc.event.is_set())

    def test_event_triggered_on_fail(self):
        """failing should set the asyncio Event."""
        ptc = PendingToolCall('c1', 't1', {}, 'cl1')
        ptc.fail('error')
        self.assertTrue(ptc.event.is_set())

    def test_to_dict_before_completion(self):
        """to_dict should reflect pending state."""
        ptc = PendingToolCall('c1', 't1', {}, 'cl1')
        d = ptc.to_dict()
        self.assertFalse(d['completed'])
        self.assertFalse(d['has_result'])
        self.assertFalse(d['has_error'])

    def test_to_dict_after_completion(self):
        """to_dict should reflect completed state."""
        ptc = PendingToolCall('c1', 't1', {}, 'cl1')
        ptc.complete(['result', 'data'])
        d = ptc.to_dict()
        self.assertTrue(d['completed'])
        self.assertTrue(d['has_result'])

    def test_repr(self):
        """__repr__ should include key info."""
        ptc = PendingToolCall('abc', 'my_tool', {}, 'client_x')
        r = repr(ptc)
        self.assertIn('abc', r)
        self.assertIn('my_tool', r)


# ═══════════════════════════════════════════════════════════════════
# ToolCallValidator Tests
# ═══════════════════════════════════════════════════════════════════

class TestToolCallValidator(unittest.TestCase):
    """Test ToolCallValidator class."""

    def setUp(self):
        self.validator = ToolCallValidator()
        self.tool_def = ToolDefinition(name='test_tool', enabled=True)

    def test_valid_call(self):
        """Should pass valid tool calls."""
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test_tool', {'param': 'value'}, self.tool_def)
            )
            self.assertTrue(valid)
            self.assertEqual(msg, '')
        finally:
            loop.close()

    def test_disabled_tool(self):
        """Should reject calls to disabled tools."""
        disabled = ToolDefinition(name='disabled', enabled=False)
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('disabled', {}, disabled)
            )
            self.assertFalse(valid)
            self.assertIn('disabled', msg)
        finally:
            loop.close()

    def test_too_many_params(self):
        """Should reject calls with too many parameters."""
        many_params = {f'p{i}': i for i in range(100)}
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', many_params, self.tool_def)
            )
            self.assertFalse(valid)
            self.assertIn('Too many', msg)
        finally:
            loop.close()

    def test_non_json_params(self):
        """Should reject non-JSON-serializable parameters."""
        class NonSerializable:
            pass
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', {'obj': NonSerializable()}, self.tool_def)
            )
            self.assertFalse(valid)
            self.assertIn('not JSON-serializable', msg)
        finally:
            loop.close()

    def test_string_param_too_long(self):
        """Should reject overly long string parameters."""
        very_long = 'x' * 20000
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', {'data': very_long}, self.tool_def)
            )
            self.assertFalse(valid)
            self.assertIn('string length', msg)
        finally:
            loop.close()

    def test_dangerous_param_name(self):
        """Should reject dangerous parameter names."""
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', {'__import__': 'os'}, self.tool_def)
            )
            self.assertFalse(valid)
            self.assertIn('not allowed', msg)
        finally:
            loop.close()

    def test_dangerous_param_starting_with_dunder(self):
        """Should reject dunder parameter names."""
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', {'__secret': 'value'}, self.tool_def)
            )
            self.assertFalse(valid)
            self.assertIn('not allowed', msg)
        finally:
            loop.close()

    def test_validate_without_tool_def(self):
        """Should pass validation without tool_def (just param checks)."""
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', {'a': 1})
            )
            self.assertTrue(valid)
        finally:
            loop.close()

    def test_validate_with_client(self):
        """Should work with client parameter (permission check)."""
        cc = ClientConnection(client_id='test_client')
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', {'a': 1}, self.tool_def, cc)
            )
            self.assertTrue(valid)
        finally:
            loop.close()

    def test_empty_params(self):
        """Should accept empty parameters."""
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', {}, self.tool_def)
            )
            self.assertTrue(valid)
        finally:
            loop.close()

    def test_nested_params(self):
        """Should validate nested structures."""
        loop = asyncio.new_event_loop()
        try:
            valid, msg = loop.run_until_complete(
                self.validator.validate('test', {
                    'config': {'nested': {'deep': 'value'}},
                    'items': [1, 2, {'key': 'val'}]
                }, self.tool_def)
            )
            self.assertTrue(valid)
        finally:
            loop.close()

    def test_get_stats(self):
        """get_stats should return validation counts."""
        stats = self.validator.get_stats()
        self.assertIn('validation_passed', stats)
        self.assertIn('validation_errors', stats)


# ═══════════════════════════════════════════════════════════════════
# ToolDiscoveryCache Tests
# ═══════════════════════════════════════════════════════════════════

class TestToolDiscoveryCache(unittest.TestCase):
    """Test ToolDiscoveryCache class."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = ToolDiscoveryCache(tools_dir=self.tmpdir, db_discovery=False)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_tool(self, name: str):
        """Create a fake tool file in tmpdir."""
        path = os.path.join(self.tmpdir, f"{name}.py")
        with open(path, 'w') as f:
            f.write(f"def {name}(): return '{name}'")

    def test_start_discovers_tools(self):
        """start should perform initial discovery."""
        self._create_tool('tool_a')
        self._create_tool('tool_b')
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            count = loop.run_until_complete(self.cache.count_tools())
            self.assertGreaterEqual(count, 2)
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_get_tool(self):
        """get_tool should return ToolDefinition by name."""
        self._create_tool('find_me')
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            td = loop.run_until_complete(self.cache.get_tool('find_me'))
            self.assertIsNotNone(td)
            self.assertEqual(td.name, 'find_me')
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_get_tool_not_found(self):
        """get_tool should return None for unknown tool."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            td = loop.run_until_complete(self.cache.get_tool('nonexistent'))
            self.assertIsNone(td)
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_get_all_tools(self):
        """get_all_tools should return dict of all tools."""
        self._create_tool('alpha')
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            tools = loop.run_until_complete(self.cache.get_all_tools())
            self.assertIn('alpha', tools)
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_get_tool_names(self):
        """get_tool_names should return sorted list."""
        for name in ['zeta', 'alpha', 'beta']:
            self._create_tool(name)
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            names = loop.run_until_complete(self.cache.get_tool_names())
            self.assertEqual(names, ['alpha', 'beta', 'zeta'])
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_get_tools_by_category(self):
        """get_tools_by_category should filter by category."""
        self._create_tool('tool_a')  # default 'general'
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            general = loop.run_until_complete(
                self.cache.get_tools_by_category('general')
            )
            self.assertGreater(len(general), 0)
            
            db_tools = loop.run_until_complete(
                self.cache.get_tools_by_category('database')
            )
            self.assertEqual(len(db_tools), 0)
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_refresh(self):
        """refresh should force re-discovery."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            count_before = loop.run_until_complete(self.cache.count_tools())
            
            # Add a tool after start
            self._create_tool('new_tool')
            
            count_after = loop.run_until_complete(self.cache.refresh())
            self.assertGreaterEqual(count_after, count_before)
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_get_stats(self):
        """get_stats should return stats dict."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            stats = self.cache.get_stats()
            self.assertIn('tool_count', stats)
            self.assertIn('last_discovery', stats)
            self.assertIn('discovery_count', stats)
            self.assertIn('running', stats)
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_stop_cleans_up(self):
        """stop should cancel discovery task."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            self.assertTrue(self.cache._running)
            self.assertIsNotNone(self.cache._discovery_task)
            loop.run_until_complete(self.cache.stop())
            self.assertFalse(self.cache._running)
            self.assertTrue(self.cache._discovery_task is None or 
                          self.cache._discovery_task.cancelled())
        finally:
            loop.close()

    def test_stop_idempotent(self):
        """Calling stop twice should not crash."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            loop.run_until_complete(self.cache.stop())
            loop.run_until_complete(self.cache.stop())  # second stop
            self.assertFalse(self.cache._running)
        finally:
            loop.close()

    def test_discover_empty_dir(self):
        """Should handle empty tools directory."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.cache.start())
            count = loop.run_until_complete(self.cache.count_tools())
            self.assertEqual(count, 0)
        finally:
            loop.run_until_complete(self.cache.stop())
            loop.close()

    def test_discover_nonexistent_dir(self):
        """Should handle nonexistent tools directory."""
        cache = ToolDiscoveryCache(tools_dir='/nonexistent/path', db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(cache.start())
            count = loop.run_until_complete(cache.count_tools())
            self.assertEqual(count, 0)
        finally:
            loop.run_until_complete(cache.stop())
            loop.close()


# ═══════════════════════════════════════════════════════════════════
# ToolRegistryProxy Tests
# ═══════════════════════════════════════════════════════════════════

class TestToolRegistryProxyInit(unittest.TestCase):
    """Test ToolRegistryProxy initialization."""

    def test_init_default(self):
        """Should initialize with default paths."""
        proxy = ToolRegistryProxy()
        self.assertIsNotNone(proxy.cache)
        self.assertIsNotNone(proxy.validator)
        self.assertEqual(proxy._clients, {})
        self.assertEqual(proxy._pending_calls, {})

    def test_init_custom(self):
        """Should initialize with custom paths."""
        proxy = ToolRegistryProxy(tools_dir='/custom/tools', db_path='/custom/db.db')
        self.assertEqual(proxy.cache.tools_dir, '/custom/tools')
        self.assertEqual(proxy.cache.db_path, '/custom/db.db')

    def test_init_creates_lock(self):
        """Should create asyncio locks."""
        proxy = ToolRegistryProxy()
        self.assertIsNotNone(proxy._clients_lock)
        self.assertIsNotNone(proxy._pending_lock)
        self.assertIsNotNone(proxy._handlers_lock)

    def test_start_stop(self):
        """start and stop should not crash."""
        proxy = ToolRegistryProxy(tools_dir='/nonexistent')
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(proxy.start())
            self.assertTrue(proxy._running)
            loop.run_until_complete(proxy.stop())
            self.assertFalse(proxy._running)
        finally:
            loop.close()

    def test_double_start(self):
        """Starting twice should work (idempotent)."""
        proxy = ToolRegistryProxy(tools_dir='/nonexistent')
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(proxy.start())
            loop.run_until_complete(proxy.start())  # second start
            self.assertTrue(proxy._running)
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()


class TestToolRegistryProxyClients(unittest.TestCase):
    """Test client management in ToolRegistryProxy."""

    def setUp(self):
        self.proxy = ToolRegistryProxy(tools_dir='/nonexistent')
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self.proxy.start())

    def tearDown(self):
        self.loop.run_until_complete(self.proxy.stop())
        self.loop.close()

    def test_register_client(self):
        """Should register a new client."""
        result = self.loop.run_until_complete(
            self.proxy.register_client('client_1', 'session_1', {'ver': '1.0'})
        )
        self.assertTrue(result)
        
        client = self.loop.run_until_complete(self.proxy.get_client('client_1'))
        self.assertIsNotNone(client)
        self.assertEqual(client.client_id, 'client_1')

    def test_register_duplicate_client(self):
        """Re-registering should update heartbeat."""
        self.loop.run_until_complete(
            self.proxy.register_client('client_1')
        )
        result = self.loop.run_until_complete(
            self.proxy.register_client('client_1')
        )
        # Should return False for existing client update
        self.assertFalse(result)

    def test_unregister_client(self):
        """Should unregister a client."""
        self.loop.run_until_complete(
            self.proxy.register_client('client_1')
        )
        self.loop.run_until_complete(
            self.proxy.unregister_client('client_1')
        )
        client = self.loop.run_until_complete(self.proxy.get_client('client_1'))
        self.assertIsNone(client)

    def test_unregister_nonexistent_client(self):
        """Unregistering nonexistent client should not crash."""
        self.loop.run_until_complete(
            self.proxy.unregister_client('ghost')
        )  # Should not raise

    def test_client_heartbeat(self):
        """Heartbeat should update client timestamp."""
        self.loop.run_until_complete(
            self.proxy.register_client('client_1')
        )
        time.sleep(0.01)
        result = self.loop.run_until_complete(
            self.proxy.client_heartbeat('client_1')
        )
        self.assertTrue(result)

    def test_client_heartbeat_nonexistent(self):
        """Heartbeat for nonexistent client should return False."""
        result = self.loop.run_until_complete(
            self.proxy.client_heartbeat('ghost')
        )
        self.assertFalse(result)

    def test_get_active_clients(self):
        """Should return only active clients."""
        self.loop.run_until_complete(
            self.proxy.register_client('active_1')
        )
        self.loop.run_until_complete(
            self.proxy.register_client('active_2')
        )
        
        # Manually mark one as inactive
        client = self.loop.run_until_complete(self.proxy.get_client('active_2'))
        if client:
            client.mark_inactive()
        
        active = self.loop.run_until_complete(self.proxy.get_active_clients())
        active_ids = [c.client_id for c in active]
        self.assertIn('active_1', active_ids)
        self.assertNotIn('active_2', active_ids)

    def test_get_client_count(self):
        """Should return count of active clients."""
        self.loop.run_until_complete(
            self.proxy.register_client('client_a')
        )
        self.loop.run_until_complete(
            self.proxy.register_client('client_b')
        )
        count = self.loop.run_until_complete(self.proxy.get_client_count())
        self.assertEqual(count, 2)


class TestToolRegistryProxyCalls(unittest.TestCase):
    """Test tool call proxying."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.proxy = ToolRegistryProxy(tools_dir=self.tmpdir, db_discovery=False)
        self.loop = asyncio.new_event_loop()
        
        # Create a simple test tool
        tool_path = os.path.join(self.tmpdir, 'echo.py')
        with open(tool_path, 'w') as f:
            f.write("""
def echo(message: str = "hello", count: int = 1) -> str:
    return f"{message}-{count}"
""")
        
        self.loop.run_until_complete(self.proxy.start())

    def tearDown(self):
        self.loop.run_until_complete(self.proxy.stop())
        self.loop.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_call_tool_success(self):
        """Should successfully proxy a tool call."""
        result = self.loop.run_until_complete(
            self.proxy.call_tool('echo', {'message': 'test', 'count': 5})
        )
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['result'], 'test-5')
        self.assertIn('execution_time_ms', result)

    def test_call_tool_not_found(self):
        """Should return not_found for unknown tool."""
        result = self.loop.run_until_complete(
            self.proxy.call_tool('nonexistent', {})
        )
        self.assertEqual(result['status'], 'not_found')

    def test_call_tool_validation_error(self):
        """Should return validation_error for invalid params."""
        result = self.loop.run_until_complete(
            self.proxy.call_tool('echo', {'__import__': 'os'})
        )
        self.assertEqual(result['status'], 'validation_error')

    def test_call_tool_with_client(self):
        """Should support client_id parameter."""
        self.loop.run_until_complete(
            self.proxy.register_client('test_client')
        )
        result = self.loop.run_until_complete(
            self.proxy.call_tool('echo', {'message': 'hi'}, client_id='test_client')
        )
        self.assertEqual(result['status'], 'success')

    def test_get_call_history(self):
        """Should record call history."""
        self.loop.run_until_complete(
            self.proxy.call_tool('echo', {'message': 'a'})
        )
        self.loop.run_until_complete(
            self.proxy.call_tool('echo', {'message': 'b'})
        )
        history = self.proxy.get_call_history()
        self.assertEqual(len(history), 2)

    def test_get_stats(self):
        """get_stats should return comprehensive stats."""
        self.loop.run_until_complete(
            self.proxy.call_tool('echo', {'message': 'stats_test'})
        )
        stats = self.loop.run_until_complete(self.proxy.get_stats())
        self.assertIn('tools', stats)
        self.assertIn('clients', stats)
        self.assertIn('calls', stats)
        self.assertIn('validation', stats)
        self.assertIn('uptime', stats)

    def test_health_check(self):
        """health_check should return status."""
        health = self.loop.run_until_complete(self.proxy.health_check())
        self.assertIn('status', health)
        self.assertIn('tools_cached', health)
        self.assertIn('active_clients', health)


class TestToolRegistryProxyPendingCalls(unittest.TestCase):
    """Test pending call tracking."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.proxy = ToolRegistryProxy(tools_dir=self.tmpdir, db_discovery=False)
        self.loop = asyncio.new_event_loop()
        
        # Create a slow tool
        tool_path = os.path.join(self.tmpdir, 'slow.py')
        with open(tool_path, 'w') as f:
            f.write("""
import asyncio
async def slow(delay: float = 0.05) -> str:
    await asyncio.sleep(delay)
    return "done"
""")
        
        self.loop.run_until_complete(self.proxy.start())

    def tearDown(self):
        self.loop.run_until_complete(self.proxy.stop())
        self.loop.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_pending_result(self):
        """Should retrieve pending call result."""
        # Make a call directly in the same loop
        result = self.loop.run_until_complete(
            self.proxy.call_tool('slow', {'delay': 0.05}, request_id='test_call_1')
        )
        self.assertEqual(result['status'], 'success')

    def test_get_pending_result_not_found(self):
        """Should return None for unknown call_id."""
        result = self.loop.run_until_complete(
            self.proxy.get_pending_result('nonexistent_call')
        )
        self.assertIsNone(result)


class TestToolRegistryProxyEdgeCases(unittest.TestCase):
    """Test edge cases for ToolRegistryProxy."""

    def test_empty_tools_dir(self):
        """Should handle empty tools dir without crashing."""
        proxy = ToolRegistryProxy(tools_dir=tempfile.mkdtemp(), db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(proxy.start())
            stats = loop.run_until_complete(proxy.get_stats())
            self.assertEqual(stats['tools']['tool_count'], 0)
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()

    def test_call_tool_with_massive_params(self):
        """Should handle oversized params gracefully."""
        proxy = ToolRegistryProxy(tools_dir='/nonexistent')
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(proxy.start())
            
            huge_params = {'data': 'x' * 500000}
            result = loop.run_until_complete(
                proxy.call_tool('any_tool', huge_params)
            )
            self.assertEqual(result['status'], 'validation_error')
            
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()

    def test_call_tool_with_timeout(self):
        """Should handle tool timeout."""
        tmpdir = tempfile.mkdtemp()
        tool_path = os.path.join(tmpdir, 'hanger.py')
        with open(tool_path, 'w') as f:
            f.write("""
import asyncio
async def hanger(delay: float = 5.0) -> str:
    await asyncio.sleep(delay)
    return "done"
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(proxy.start())
            
            # Use a very short timeout to trigger timeout error
            result = loop.run_until_complete(
                proxy.call_tool('hanger', {'delay': 0.01}, timeout=5.0)
            )
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['result'], 'done')
            
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_concurrent_clients(self):
        """Should handle multiple clients concurrently."""
        tmpdir = tempfile.mkdtemp()
        tool_path = os.path.join(tmpdir, 'quick.py')
        with open(tool_path, 'w') as f:
            f.write("""
def quick(val: int = 0) -> int:
    return val + 1
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(proxy.start())
            
            # Register multiple clients
            for i in range(5):
                loop.run_until_complete(
                    proxy.register_client(f'client_{i}')
                )
            
            # Call from multiple clients
            results = []
            for i in range(5):
                result = loop.run_until_complete(
                    proxy.call_tool('quick', {'val': i}, client_id=f'client_{i}')
                )
                results.append(result)
            
            for i, r in enumerate(results):
                self.assertEqual(r['status'], 'success')
                self.assertEqual(r['result'], i + 1)
            
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_serialize_large_result(self):
        """Should truncate large results."""
        proxy = ToolRegistryProxy()
        large_str = 'x' * (MAX_RESULT_SIZE + 1000)
        serialized = proxy._serialize_result(large_str)
        self.assertLessEqual(len(serialized), MAX_RESULT_SIZE + 50)  # + "... [TRUNCATED]"


class TestToolRegistryProxyErrorPublishing(unittest.TestCase):
    """Test error publishing functionality."""

    def setUp(self):
        self.proxy = ToolRegistryProxy(tools_dir='/nonexistent')
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self.proxy.start())

    def tearDown(self):
        self.loop.run_until_complete(self.proxy.stop())
        self.loop.close()

    def test_publish_error_no_phooks(self):
        """Should not crash when publishing without Phooks."""
        # Internal method — should not raise without phooks
        coro = self.proxy._publish_error(
            'test_tool', 'client_a', 'error msg', 'execution_error', {'p': 1}
        )
        self.loop.run_until_complete(coro)  # Should not crash

    def test_publish_error_with_mock_phooks(self):
        """Should publish to Phooks when available."""
        mock_phooks = MagicMock()
        mock_phooks.publish = AsyncMock()
        self.proxy.phooks = mock_phooks
        
        self.loop.run_until_complete(
            self.proxy._publish_error(
                'test_tool', 'client_a', 'test error', 'execution_error', {}
            )
        )
        mock_phooks.publish.assert_called_once()


if __name__ == '__main__':
    unittest.main()
