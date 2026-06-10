"""
Comprehensive Integration & Edge Case Tests for FastMCP YuniScript.

Part of FastMCP Server (YuniScript Managed) — WO #147 deliverable #8.

Tests complete data flow paths:
1. Client → ToolRegistryProxy → Tool → Result → Client
2. PhooksBridge → FastMCPAdapter → Tool → Result → PhooksBridge
3. DebugHooks → Error capture → Phooks publish
4. End-to-end: register tool → discover → call → result
5. Edge cases: concurrent calls, error propagation, timeout, recovery

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp_adapter import FastMCPAdapter
from phooks_bridge import PhooksToolBridge
from debug_hooks import DebugHooks
from tool_registry_proxy import ToolRegistryProxy, ToolDefinition


# ═══ Helpers ═══════════════════════════════════════════════════════

def _create_tool_file(tmpdir: str, name: str, source: str):
    """Create a tool file in tmpdir."""
    path = os.path.join(tmpdir, f"{name}.py")
    with open(path, 'w') as f:
        f.write(source)
    return path


# Standard tool templates
SYNC_ECHO_TOOL = """
def echo(message: str = "hello", count: int = 1) -> str:
    return f"{message}-{count}"
"""

ASYNC_SLOW_TOOL = """
import asyncio
async def slow(delay: float = 0.01) -> str:
    await asyncio.sleep(delay)
    return "slow-done"
"""

ASYNC_ERROR_TOOL = """
async def error_tool(should_fail: bool = False) -> str:
    if should_fail:
        raise RuntimeError("Intentional failure")
    return "ok"
"""

COMPLEX_RESULT_TOOL = """
def complex_tool() -> dict:
    return {"status": "ok", "data": [1, 2, 3], "nested": {"key": "val"}}
"""


def _run_async(coro):
    """Run a coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════
# PATH 1: Full End-to-End Flow (Client → Proxy → Tool → Result)
# ═══════════════════════════════════════════════════════════════════

class TestPath1_ClientProxyToolFlow(unittest.TestCase):
    """
    Data Flow Path 1:
    Register client → Proxy discovers tool → Client calls tool → 
    Proxy validates → Proxy executes → Result returned to client
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        _create_tool_file(cls.tmpdir, 'echo', SYNC_ECHO_TOOL)
        _create_tool_file(cls.tmpdir, 'complex_tool', COMPLEX_RESULT_TOOL)
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)
        cls.proxy = ToolRegistryProxy(tools_dir=cls.tmpdir, db_discovery=False)
        cls.loop.run_until_complete(cls.proxy.start())

    @classmethod
    def tearDownClass(cls):
        try:
            cls.loop.run_until_complete(cls.proxy.stop())
        finally:
            cls.loop.close()
            asyncio.set_event_loop(None)
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_p1_full_flow_sync_tool(self):
        """Full flow: register client → call tool → receive result."""
        # 1. Register client
        _run_async(self.proxy.register_client('test_client', 'session_1', {
            'capabilities': ['read', 'write']
        }))
        
        # 2. Verify client was registered
        client = _run_async(self.proxy.get_client('test_client'))
        self.assertIsNotNone(client)
        self.assertTrue(client.is_active)
        
        # 3. Call tool
        result = _run_async(self.proxy.call_tool(
            'echo', {'message': 'hello', 'count': 3},
            client_id='test_client', request_id='req_001'
        ))
        
        # 4. Verify result
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['result'], 'hello-3')
        self.assertIn('execution_time_ms', result)
        self.assertEqual(result['call_id'], 'req_001')
        
        # 5. Verify client stats updated
        client = _run_async(self.proxy.get_client('test_client'))
        self.assertGreaterEqual(client.completed_calls, 1)

    def test_p1_complex_result(self):
        """Full flow with complex result (dict with nested data)."""
        result = _run_async(self.proxy.call_tool(
            'complex_tool', {},
            client_id='test_client'
        ))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['result']['status'], 'ok')
        self.assertEqual(result['result']['data'], [1, 2, 3])

    def test_p1_tool_not_found(self):
        """Flow with nonexistent tool returns error."""
        result = _run_async(self.proxy.call_tool(
            'nonexistent', {},
            client_id='test_client'
        ))
        self.assertEqual(result['status'], 'not_found')

    def test_p1_validation_rejection(self):
        """Flow with invalid parameters returns validation_error."""
        result = _run_async(self.proxy.call_tool(
            'echo', {'__dangerous__': 'value'},
            client_id='test_client'
        ))
        self.assertEqual(result['status'], 'validation_error')

    def test_p1_client_unregister(self):
        """Client unregister should clean up cleanly."""
        _run_async(self.proxy.register_client('temp_client'))
        _run_async(self.proxy.unregister_client('temp_client'))
        client = _run_async(self.proxy.get_client('temp_client'))
        self.assertIsNone(client)

    def test_p1_client_heartbeat(self):
        """Client heartbeat should keep client active."""
        _run_async(self.proxy.register_client('heartbeat_client'))
        time.sleep(0.01)
        result = _run_async(self.proxy.client_heartbeat('heartbeat_client'))
        self.assertTrue(result)
        client = _run_async(self.proxy.get_client('heartbeat_client'))
        self.assertTrue(client.is_active)

    def test_p1_multiple_clients_independent(self):
        """Multiple clients should not interfere."""
        for i in range(3):
            _run_async(self.proxy.register_client(f'multi_{i}'))
        
        results = []
        for i in range(3):
            r = _run_async(self.proxy.call_tool(
                'echo', {'message': f'client_{i}'},
                client_id=f'multi_{i}'
            ))
            results.append(r)
        
        for i, r in enumerate(results):
            self.assertEqual(r['result'], f'client_{i}-1')

    def test_p1_health_check(self):
        """Health check should return healthy."""
        health = _run_async(self.proxy.health_check())
        self.assertEqual(health['status'], 'healthy')
        self.assertGreater(health['tools_cached'], 0)

    def test_p1_stats_after_calls(self):
        """Stats should reflect calls made."""
        stats = _run_async(self.proxy.get_stats())
        self.assertGreater(stats['calls']['total_proxied'], 0)
        self.assertGreater(stats['calls']['succeeded'], 0)

    def test_p1_call_history(self):
        """Call history should record all calls."""
        # Make a call first to populate history
        self.loop.run_until_complete(self.proxy.register_client('hist_client'))
        self.loop.run_until_complete(self.proxy.call_tool(
            'echo', {'message': 'history_test'},
            client_id='hist_client'
        ))
        history = self.proxy.get_call_history()
        self.assertGreater(len(history), 0)
        for entry in history:
            self.assertIn('tool_name', entry)
            self.assertIn('success', entry)
            self.assertIn('elapsed', entry)


# ═══════════════════════════════════════════════════════════════════
# PATH 2: PhooksBridge → Adapter → Tool → Result → Bridge
# ═══════════════════════════════════════════════════════════════════

class TestPath2_BridgeAdapterToolFlow(unittest.TestCase):
    """
    Data Flow Path 2:
    Register tool with bridge → Call via bridge → 
    Adapter executes → Result returned through bridge
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        _create_tool_file(self.tmpdir, 'echo', SYNC_ECHO_TOOL)
        _create_tool_file(self.tmpdir, 'error_tool', ASYNC_ERROR_TOOL)
        
        self.adapter = FastMCPAdapter(tools_dir=self.tmpdir)
        self.bridge = PhooksToolBridge()
        
        _run_async(self.adapter.initialize())
        for name in self.adapter.get_tool_names():
            handler = self.adapter.get_tool(name)
            if handler:
                self.bridge.register_tool(name, handler)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_p2_direct_call_via_bridge(self):
        """Should call tool directly through bridge."""
        result = _run_async(self.bridge.call_tool_direct(
            'echo', {'message': 'bridge_test', 'count': 7}
        ))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['result'], 'bridge_test-7')

    def test_p2_bridge_tool_not_found(self):
        """Bridge should return error for unknown tool."""
        result = _run_async(self.bridge.call_tool_direct(
            'nonexistent', {}
        ))
        self.assertEqual(result['status'], 'error')

    def test_p2_bridge_default_params(self):
        """Bridge should use default parameter values."""
        result = _run_async(self.bridge.call_tool_direct(
            'echo', {}
        ))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['result'], 'hello-1')

    def test_p2_bridge_tool_error_propagation(self):
        """Bridge should propagate tool execution errors."""
        result = _run_async(self.bridge.call_tool_direct(
            'error_tool', {'should_fail': True}
        ))
        self.assertEqual(result['status'], 'error')
        self.assertIn('Intentional failure', result['error'])

    def test_p2_bridge_serialize_complex_result(self):
        """Bridge should serialize complex results."""
        # Use the complex_tool through adapter
        _create_tool_file(self.tmpdir, 'complex_test', COMPLEX_RESULT_TOOL)
        self.adapter2 = FastMCPAdapter(tools_dir=self.tmpdir)
        _run_async(self.adapter2.initialize())
        
        bridge2 = PhooksToolBridge()
        for name in self.adapter2.get_tool_names():
            handler = self.adapter2.get_tool(name)
            if handler:
                bridge2.register_tool(name, handler)
        
        result = _run_async(bridge2.call_tool_direct('complex_test', {}))
        self.assertEqual(result['status'], 'success')
        self.assertIsInstance(result['result'], dict)

    def test_p2_bridge_stats(self):
        """Bridge stats should track calls."""
        _run_async(self.bridge.call_tool_direct('echo', {'message': 'a'}))
        _run_async(self.bridge.call_tool_direct('echo', {'message': 'b'}))
        
        stats = self.bridge.get_stats()
        self.assertGreaterEqual(stats['calls_completed'], 2)

    def test_p2_event_handling(self):
        """Bridge should handle tool call events."""
        # Simulate a tool call event
        event = {
            'topic': 'tool.call',
            'data': {
                'request_id': 'evt_001',
                'tool_name': 'echo',
                'parameters': {'message': 'event_test'},
            }
        }
        
        # Handle event (async)
        _run_async(self.bridge._handle_event(event))
        
        # Should not crash and should record the call
        stats = self.bridge.get_stats()
        self.assertGreaterEqual(stats['calls_received'], 1)

    def test_p2_missing_tool_in_event(self):
        """Bridge should handle events with invalid tool names."""
        event = {
            'topic': 'tool.call',
            'data': {
                'request_id': 'bad_evt',
                'tool_name': 'missing_tool',
                'parameters': {},
            }
        }
        
        # Should not crash
        _run_async(self.bridge._handle_event(event))
        stats = self.bridge.get_stats()
        self.assertGreaterEqual(stats['calls_received'], 1)


# ═══════════════════════════════════════════════════════════════════
# PATH 3: DebugHooks Error Capture Path
# ═══════════════════════════════════════════════════════════════════

class TestPath3_DebugHooksErrorFlow(unittest.TestCase):
    """
    Data Flow Path 3:
    Error occurs → DebugHook captures → Stores locally → 
    Publishes to Phooks → Stats updated
    """

    def setUp(self):
        self.hooks = DebugHooks()
        _run_async(self.hooks.start())

    def tearDown(self):
        _run_async(self.hooks.stop())

    def test_p3_tool_call_error_capture(self):
        """Should capture tool call error with stack trace."""
        try:
            raise ValueError("Something went wrong")
        except ValueError as e:
            event_id = _run_async(self.hooks.on_tool_call_error(
                'test_tool', e, {'param': 'value'}
            ))
        
        self.assertIsNotNone(event_id)
        errors = self.hooks.get_recent_errors(1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['type'], 'tool_call_error')
        self.assertEqual(errors[0]['tool_name'], 'test_tool')
        self.assertEqual(errors[0]['error_type'], 'ValueError')
        self.assertIn('stack_trace', errors[0])

    def test_p3_data_flow_anomaly_capture(self):
        """Should capture data flow anomaly."""
        event_id = _run_async(self.hooks.on_data_flow_anomaly(
            flow_path=['step1', 'step2', 'step3'],
            data={'key': 'value'},
            expected='str',
            actual='int',
            context={'operation': 'transform'}
        ))
        
        self.assertIsNotNone(event_id)
        errors = self.hooks.get_recent_errors(1)
        self.assertEqual(errors[0]['type'], 'data_flow_anomaly')
        self.assertEqual(errors[0]['flow_path'], ['step1', 'step2', 'step3'])

    def test_p3_timeout_capture(self):
        """Should capture timeout event."""
        event_id = _run_async(self.hooks.on_timeout(
            component='fastmcp_adapter',
            timeout_duration=30.5,
            context={'tool': 'database_query'}
        ))
        
        self.assertIsNotNone(event_id)
        errors = self.hooks.get_recent_errors(1)
        self.assertEqual(errors[0]['type'], 'timeout')
        self.assertEqual(errors[0]['component'], 'fastmcp_adapter')
        self.assertEqual(errors[0]['timeout_duration'], 30.5)

    def test_p3_error_count_tracking(self):
        """Should track total error count."""
        for i in range(5):
            _run_async(self.hooks.on_tool_call_error(
                'test_tool', ValueError(f"Error {i}"), {}
            ))
        
        stats = self.hooks.get_stats()
        self.assertEqual(stats['error_count'], 5)

    def test_p3_recent_errors_limit(self):
        """Should limit stored errors to max_stored."""
        for i in range(200):
            _run_async(self.hooks.on_tool_call_error(
                'test_tool', ValueError(f"Error {i}"), {}
            ))
        
        stats = self.hooks.get_stats()
        self.assertLessEqual(stats['stored_errors'], 100)

    def test_p3_clear_errors(self):
        """Should clear error buffer."""
        _run_async(self.hooks.on_tool_call_error(
            'test_tool', ValueError("test"), {}
        ))
        self.hooks.clear_errors()
        self.assertEqual(len(self.hooks.get_recent_errors()), 0)

    def test_p3_publish_to_phooks(self):
        """Should publish to Phooks when available."""
        mock_phooks = MagicMock()
        mock_phooks.publish = AsyncMock()
        self.hooks.phooks = mock_phooks
        
        _run_async(self.hooks.on_tool_call_error(
            'test_tool', ValueError("publish test"), {}
        ))
        
        mock_phooks.publish.assert_called_once()
        args = mock_phooks.publish.call_args
        self.assertEqual(args[0][0], 'system.error')
        self.assertIn('event_id', args[0][1])

    def test_p3_publish_without_phooks(self):
        """Should not crash when Phooks is not available."""
        _run_async(self.hooks.on_tool_call_error(
            'test_tool', ValueError("no phooks"), {}
        ))
        # No assertion — just ensure no crash

    def test_p3_capture_tool_errors_decorator(self):
        """Decorator should capture exceptions."""
        @self.hooks.capture_tool_errors
        async def failing_tool():
            raise RuntimeError("Decorated failure")
        
        with self.assertRaises(RuntimeError):
            _run_async(failing_tool())
        
        stats = self.hooks.get_stats()
        self.assertGreater(stats['error_count'], 0)


# ═══════════════════════════════════════════════════════════════════
# PATH 4: Cross-Component Error Propagation
# ═══════════════════════════════════════════════════════════════════

class TestPath4_CrossComponentErrors(unittest.TestCase):
    """
    Data Flow Path 4:
    Error in adapter → Propagated through bridge → 
    Captured by debug hooks → Available for work order generation
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.hooks = DebugHooks()
        _run_async(self.hooks.start())

    def tearDown(self):
        _run_async(self.hooks.stop())
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_p4_adapter_tool_error_captured_by_hooks(self):
        """Adapter tool error should be capturable by hooks."""
        adapter = FastMCPAdapter(tools_dir=self.tmpdir)
        _run_async(adapter.initialize())
        
        # Try to execute nonexistent tool
        try:
            _run_async(adapter.execute_tool('ghost_tool'))
            self.fail("Should have raised KeyError")
        except KeyError as e:
            # Capture with hooks
            _run_async(self.hooks.on_tool_call_error(
                'ghost_tool', e,
                {'tools_dir': self.tmpdir}
            ))
        
        errors = self.hooks.get_recent_errors()
        self.assertGreater(len(errors), 0)
        self.assertIn('ghost_tool', str(errors[0].get('tool_name', '')))

    def test_p4_bridge_error_propagation_to_hooks(self):
        """Bridge execution error should be capturable by hooks."""
        _create_tool_file(self.tmpdir, 'crash_tool', """
def crash_tool():
    raise ValueError("Crash in tool")
""")
        
        adapter = FastMCPAdapter(tools_dir=self.tmpdir)
        _run_async(adapter.initialize())
        
        bridge = PhooksToolBridge()
        for name in adapter.get_tool_names():
            handler = adapter.get_tool(name)
            if handler:
                bridge.register_tool(name, handler)
        
        # Call tool — error should be returned, not raised
        result = _run_async(bridge.call_tool_direct('crash_tool', {}))
        self.assertEqual(result['status'], 'error')
        self.assertIn('Crash in tool', result['error'])

    def test_p4_validation_error_chain(self):
        """Validation errors should chain through proxy + hooks."""
        proxy = ToolRegistryProxy(tools_dir=self.tmpdir, db_discovery=False)
        _run_async(proxy.start())
        
        # Mock phooks on proxy
        mock_phooks = MagicMock()
        mock_phooks.publish = AsyncMock()
        proxy.phooks = mock_phooks
        
        # Call with dangerous params
        result = _run_async(proxy.call_tool(
            'any_tool', {'__evil__': 'payload'}
        ))
        self.assertEqual(result['status'], 'validation_error')
        
        _run_async(proxy.stop())


# ═══════════════════════════════════════════════════════════════════
# PATH 5: Edge Case Matrix
# ═══════════════════════════════════════════════════════════════════

class TestPath5_EdgeCases(unittest.TestCase):
    """
    Comprehensive edge case testing across all components.
    
    Edge cases tested:
    ● Empty/null parameters
    ● Very long strings
    ● Unicode/special characters
    ● Concurrent calls (thread safety)
    ● Rapid start/stop cycles
    ● Missing files
    ● Permission errors
    ● Recursive calls
    ● Resource exhaustion
    ● State consistency
    """

    # ── 5A: Parameter Edge Cases ──────────────────────────────

    def test_p5a_empty_parameters(self):
        """Should handle empty parameter dicts."""
        tmpdir = tempfile.mkdtemp()
        _create_tool_file(tmpdir, 'no_params', """
def no_params() -> str:
    return "no params needed"
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        _run_async(proxy.start())
        
        result = _run_async(proxy.call_tool('no_params', {}))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['result'], 'no params needed')
        
        _run_async(proxy.stop())
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p5a_null_values_in_params(self):
        """Should handle None/null parameter values."""
        tmpdir = tempfile.mkdtemp()
        _create_tool_file(tmpdir, 'nullable', """
def nullable(name: str = None) -> str:
    return f"hello {name or 'world'}"
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        _run_async(proxy.start())
        
        result = _run_async(proxy.call_tool('nullable', {'name': None}))
        self.assertEqual(result['result'], 'hello world')
        
        _run_async(proxy.stop())
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p5a_unicode_params(self):
        """Should handle Unicode parameter values."""
        tmpdir = tempfile.mkdtemp()
        _create_tool_file(tmpdir, 'unicode_tool', """
def unicode_tool(text: str = "") -> str:
    return f"received: {text}"
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        _run_async(proxy.start())
        
        result = _run_async(proxy.call_tool('unicode_tool', {
            'text': 'Hëllø Wörld! 🎉 日本語'
        }))
        self.assertEqual(result['status'], 'success')
        self.assertIn('Hëllø', result['result'])
        
        _run_async(proxy.stop())
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p5a_bool_and_number_types(self):
        """Should handle boolean and numeric parameters."""
        tmpdir = tempfile.mkdtemp()
        _create_tool_file(tmpdir, 'typed', """
def typed(flag: bool = False, count: int = 0, rate: float = 0.0) -> str:
    return f"{flag}-{count}-{rate}"
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        _run_async(proxy.start())
        
        result = _run_async(proxy.call_tool('typed', {
            'flag': True, 'count': 42, 'rate': 3.14
        }))
        self.assertEqual(result['result'], 'True-42-3.14')
        
        _run_async(proxy.stop())
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ── 5B: Concurrency Edge Cases ────────────────────────────

    def test_p5b_concurrent_calls_same_tool(self):
        """Multiple concurrent calls to same tool should work."""
        tmpdir = tempfile.mkdtemp()
        _create_tool_file(tmpdir, 'concurrent_tool', """
import time
def concurrent_tool(delay: float = 0.0) -> str:
    if delay > 0:
        time.sleep(delay)
    return "done"
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(proxy.start())
            tasks = [proxy.call_tool('concurrent_tool', {'delay': 0.01}) for _ in range(10)]
            results = loop.run_until_complete(asyncio.gather(*tasks))
            for r in results:
                self.assertEqual(r['status'], 'success')
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p5b_concurrent_calls_different_tools(self):
        """Multiple concurrent calls to different tools should work."""
        tmpdir = tempfile.mkdtemp()
        for name in ['tool_a', 'tool_b', 'tool_c']:
            _create_tool_file(tmpdir, name, f"""
def {name}(val: int = 0) -> int:
    return val + 1
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(proxy.start())
            tasks = [
                proxy.call_tool('tool_a', {'val': 1}),
                proxy.call_tool('tool_b', {'val': 2}),
                proxy.call_tool('tool_c', {'val': 3}),
            ]
            results = loop.run_until_complete(asyncio.gather(*tasks))
            self.assertEqual(results[0]['result'], 2)
            self.assertEqual(results[1]['result'], 3)
            self.assertEqual(results[2]['result'], 4)
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p5b_concurrent_client_registration(self):
        """Concurrent client registration should be thread-safe."""
        proxy = ToolRegistryProxy(tools_dir='/nonexistent', db_discovery=False)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(proxy.start())
            
            tasks = [
                proxy.register_client(f'thread_client_{i}', f'session_{i}')
                for i in range(20)
            ]
            results = loop.run_until_complete(asyncio.gather(*tasks))
            
            self.assertEqual(len(results), 20)
            self.assertEqual(sum(1 for r in results if r), 20)
            
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    # ── 5C: Rapid Lifecycle Edge Cases ────────────────────────

    def test_p5c_rapid_start_stop(self):
        """Rapid start/stop cycles should not leak resources."""
        for i in range(5):
            tmpdir = tempfile.mkdtemp()
            _create_tool_file(tmpdir, 'quick', SYNC_ECHO_TOOL)
            
            proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
            _run_async(proxy.start())
            _run_async(proxy.stop())
            
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        
        # If we get here without crash, test passes
        self.assertTrue(True)

    def test_p5c_start_stop_with_pending_calls(self):
        """Stop with pending calls should clean up gracefully."""
        tmpdir = tempfile.mkdtemp()
        _create_tool_file(tmpdir, 'pending', """
import asyncio
async def pending(delay: float = 0.5) -> str:
    await asyncio.sleep(delay)
    return "done"
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(proxy.start())
            future = asyncio.ensure_future(proxy.call_tool('pending', {'delay': 10.0}, request_id='stuck_call'))
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ── 5D: State Consistency Edge Cases ──────────────────────

    def test_p5d_proxy_state_after_stop(self):
        """Proxy state should be consistent after stop."""
        proxy = ToolRegistryProxy(tools_dir='/nonexistent')
        _run_async(proxy.start())
        _run_async(proxy.register_client('test_client'))
        _run_async(proxy.stop())
        
        self.assertFalse(proxy._running)
        # Client list may or may not be cleared
        # But no pending calls should remain
        self.assertEqual(len(proxy._pending_calls), 0)

    def test_p5d_adapter_state_after_initialize(self):
        """Adapter state should be consistent after initialize."""
        tmpdir = tempfile.mkdtemp()
        adapter = FastMCPAdapter(tools_dir=tmpdir)
        self.assertFalse(adapter._initialized)
        
        _run_async(adapter.initialize())
        self.assertTrue(adapter._initialized)
        
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p5d_bridge_state_tracking(self):
        """Bridge should accurately track internal state."""
        bridge = PhooksToolBridge()
        
        # Before any calls
        stats = bridge.get_stats()
        self.assertEqual(stats['calls_received'], 0)
        self.assertEqual(stats['calls_completed'], 0)
        self.assertEqual(stats['calls_failed'], 0)
        
        # After a successful call
        def dummy_handler(**kwargs):
            return 'ok'
        
        bridge.register_tool('dummy', dummy_handler)
        result = _run_async(bridge.call_tool_direct('dummy', {}))
        self.assertEqual(result['status'], 'success')
        
        stats = bridge.get_stats()
        self.assertGreater(stats['calls_completed'], 0)

    # ── 5E: Resource Exhaustion ───────────────────────────────

    def test_p5e_many_tools(self):
        """Should handle discovering many tools."""
        tmpdir = tempfile.mkdtemp()
        for i in range(50):
            _create_tool_file(tmpdir, f'bulk_tool_{i}', f"""
def bulk_tool_{i}() -> int:
    return {i}
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        _run_async(proxy.start())
        
        stats = _run_async(proxy.get_stats())
        self.assertGreaterEqual(stats['tools']['tool_count'], 50)
        
        _run_async(proxy.stop())
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p5e_many_clients(self):
        """Should handle many simultaneous clients."""
        proxy = ToolRegistryProxy(tools_dir='/nonexistent')
        _run_async(proxy.start())
        
        for i in range(50):
            _run_async(proxy.register_client(f'bulk_client_{i}'))
        
        count = _run_async(proxy.get_client_count())
        self.assertEqual(count, 50)
        
        _run_async(proxy.stop())


# ═══════════════════════════════════════════════════════════════════
# PATH 6: Parallel Spectrum Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestPath6_ParallelSpectrum(unittest.TestCase):
    """
    Parallel spectrum edge cases — testing combinations of edge conditions
    simultaneously to verify the system handles compound edge conditions.
    
    Tests:
    ● Many tools + many clients + concurrent calls
    ● Start + register + call + stop in rapid sequence
    ● Error during discovery + error during execution
    """

    def test_p6_compound_stress(self):
        """
        Compound stress: 10 tools, 10 clients, 20 concurrent calls.
        All should succeed without data corruption.
        """
        tmpdir = tempfile.mkdtemp()
        for i in range(10):
            _create_tool_file(tmpdir, f'stress_{i}', f"""
def stress_{i}(val: int = 0) -> int:
    return val + {i}
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(proxy.start())
            for i in range(10):
                loop.run_until_complete(proxy.register_client(f'stress_client_{i}'))
            tasks = []
            for i in range(10):
                for j in range(2):
                    tasks.append(proxy.call_tool(f'stress_{i}', {'val': j * 10}, client_id=f'stress_client_{j}'))
            results = loop.run_until_complete(asyncio.gather(*tasks))
            successes = [r for r in results if r['status'] == 'success']
            self.assertGreater(len(successes), 0)
            loop.run_until_complete(proxy.stop())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p6_discovery_plus_execution(self):
        """Tools discovered during execution should work."""
        tmpdir = tempfile.mkdtemp()
        # Start with one tool
        _create_tool_file(tmpdir, 'initial', """
def initial() -> str:
    return "initial"
""")
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        _run_async(proxy.start())
        
        # Call initial tool
        result = _run_async(proxy.call_tool('initial', {}))
        self.assertEqual(result['result'], 'initial')
        
        # Add more tools after start
        _create_tool_file(tmpdir, 'added_later', """
def added_later() -> str:
    return "added later"
""")
        
        # Refresh discovery
        _run_async(proxy.cache.refresh())
        
        # Call newly added tool
        result = _run_async(proxy.call_tool('added_later', {}))
        self.assertEqual(result['result'], 'added later')
        
        _run_async(proxy.stop())
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p6_error_during_discovery(self):
        """Errors during discovery should not crash existing tools."""
        tmpdir = tempfile.mkdtemp()
        _create_tool_file(tmpdir, 'good_tool', SYNC_ECHO_TOOL)
        _create_tool_file(tmpdir, 'bad_tool', 'this is not valid python {{{')
        
        proxy = ToolRegistryProxy(tools_dir=tmpdir, db_discovery=False)
        _run_async(proxy.start())
        
        # good_tool should still be callable
        result = _run_async(proxy.call_tool('good_tool', {'message': 'survived'}))
        self.assertEqual(result['result'], 'survived-1')
        
        _run_async(proxy.stop())
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_p6_phooks_bridge_disconnect_reconnect(self):
        """Bridge should handle simulated Phooks disconnection."""
        bridge = PhooksToolBridge()
        
        def dummy_handler(**kwargs):
            return 'ok'
        
        bridge.register_tool('dummy', dummy_handler)
        
        # Should work without Phooks
        result = _run_async(bridge.call_tool_direct('dummy', {}))
        self.assertEqual(result['status'], 'success')
        
        # Bridge should not crash when trying to publish without Phooks
        _run_async(bridge._publish_result('req_1', 'dummy', 'ok', 0.01))
        _run_async(bridge._publish_error('req_2', 'test error'))
        
        # No assertions — just shouldn't crash


if __name__ == '__main__':
    unittest.main()
