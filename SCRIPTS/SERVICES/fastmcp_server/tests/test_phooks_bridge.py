"""
Unit tests for PhooksToolBridge.

Part of FastMCP Server (YuniScript Managed).
Tests: tool registration, event handling, direct calls, error handling, stats.

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phooks_bridge import PhooksToolBridge


def _run_async(coro):
    """Run a coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestPhooksToolBridgeInit(unittest.TestCase):
    """Test initialization."""

    def test_init_no_phooks(self):
        """Test initialization without Phooks client."""
        bridge = PhooksToolBridge()
        self.assertIsNone(bridge.phooks)
        self.assertEqual(bridge._tool_registry, {})
        self.assertFalse(bridge._running)

    def test_init_with_phooks(self):
        """Test initialization with Phooks client."""
        mock_phooks = MagicMock()
        bridge = PhooksToolBridge(mock_phooks)
        self.assertEqual(bridge.phooks, mock_phooks)

    def test_init_stats_zeros(self):
        """Test initial stats are zero."""
        bridge = PhooksToolBridge()
        self.assertEqual(bridge._calls_received, 0)
        self.assertEqual(bridge._calls_completed, 0)
        self.assertEqual(bridge._calls_failed, 0)


class TestPhooksToolBridgeRegistration(unittest.TestCase):
    """Test tool registration."""

    def setUp(self):
        self.bridge = PhooksToolBridge()

    def test_register_single_tool(self):
        """Test registering a single tool."""
        handler = MagicMock()
        self.bridge.register_tool('test_tool', handler)
        self.assertIn('test_tool', self.bridge._tool_registry)

    def test_register_multiple_tools(self):
        """Test registering multiple tools at once."""
        handlers = {
            'tool1': MagicMock(),
            'tool2': MagicMock(),
            'tool3': MagicMock()
        }
        self.bridge.register_tools(handlers)
        self.assertEqual(len(self.bridge._tool_registry), 3)

    def test_register_overwrite(self):
        """Test registering overwrites existing tool."""
        self.bridge.register_tool('dup', MagicMock())
        new_handler = MagicMock()
        self.bridge.register_tool('dup', new_handler)
        self.assertEqual(self.bridge._tool_registry['dup'], new_handler)


class TestPhooksToolBridgeExecution(unittest.TestCase):
    """Test tool execution."""

    def setUp(self):
        self.bridge = PhooksToolBridge()

    def test_execute_sync_tool(self):
        """Test executing a synchronous tool."""
        def sync_handler(**kwargs):
            return {'result': kwargs.get('input', 'ok')}
        
        self.bridge.register_tool('sync_tool', sync_handler)

        async def run_test():
            result = await self.bridge._execute_tool('sync_tool', {'input': 'test'})
            self.assertEqual(result, {'result': 'test'})

        _run_async(run_test())

    def test_execute_async_tool(self):
        """Test executing an async tool."""
        async def async_handler(**kwargs):
            await asyncio.sleep(0.01)
            return {'async': True, **kwargs}

        self.bridge.register_tool('async_tool', async_handler)

        async def run_test():
            result = await self.bridge._execute_tool('async_tool', {'key': 'val'})
            self.assertTrue(result['async'])
            self.assertEqual(result['key'], 'val')

        _run_async(run_test())

    def test_execute_tool_not_found(self):
        """Test executing a non-existent tool raises KeyError."""

        async def run_test():
            with self.assertRaises(KeyError):
                await self.bridge._execute_tool('nonexistent', {})

        _run_async(run_test())

    def test_execute_tool_not_found_message(self):
        """Test error message includes available tools."""
        self.bridge.register_tool('available1', MagicMock())

        async def run_test():
            try:
                await self.bridge._execute_tool('missing', {})
                self.fail("Should have raised")
            except KeyError as e:
                self.assertIn('available1', str(e))

        _run_async(run_test())

    def test_execute_tool_raises_exception(self):
        """Test that tool exceptions propagate."""
        def broken_tool(**kwargs):
            raise ValueError("Internal error")

        self.bridge.register_tool('broken', broken_tool)

        async def run_test():
            with self.assertRaises(ValueError):
                await self.bridge._execute_tool('broken', {})

        _run_async(run_test())


class TestPhooksToolBridgeDirectCall(unittest.TestCase):
    """Test direct tool call interface."""

    def setUp(self):
        self.bridge = PhooksToolBridge()

    def test_direct_call_success(self):
        """Test direct call returns success result."""
        def tool(**kwargs):
            return {'data': kwargs}

        self.bridge.register_tool('direct_tool', tool)

        async def run_test():
            result = await self.bridge.call_tool_direct('direct_tool', {'x': 1})
            self.assertEqual(result['status'], 'success')
            self.assertIn('execution_time_ms', result)

        _run_async(run_test())

    def test_direct_call_tool_not_found(self):
        """Test direct call with unknown tool returns error."""

        async def run_test():
            result = await self.bridge.call_tool_direct('unknown', {})
            self.assertEqual(result['status'], 'error')
            self.assertIn('not found', result['error'])

        _run_async(run_test())

    def test_direct_call_tool_error(self):
        """Test direct call with tool that raises returns error."""
        def failing_tool(**kwargs):
            raise RuntimeError("Failed!")

        self.bridge.register_tool('failing_tool', failing_tool)

        async def run_test():
            result = await self.bridge.call_tool_direct('failing_tool', {})
            self.assertEqual(result['status'], 'error')
            self.assertIn('Failed', result['error'])

        _run_async(run_test())

    def test_direct_call_tracks_time(self):
        """Test direct call records execution time."""
        def slow_tool(**kwargs):
            import time
            time.sleep(0.01)
            return {'done': True}

        self.bridge.register_tool('slow_tool', slow_tool)

        async def run_test():
            result = await self.bridge.call_tool_direct('slow_tool', {})
            self.assertEqual(result['status'], 'success')
            self.assertGreater(result['execution_time_ms'], 0)

        _run_async(run_test())


class TestPhooksToolBridgeResultSerialization(unittest.TestCase):
    """Test result serialization."""

    def setUp(self):
        self.bridge = PhooksToolBridge()

    def test_serialize_dict(self):
        """Test serializing a dict."""
        result = self.bridge._serialize_result({'key': 'value'})
        self.assertEqual(result['key'], 'value')

    def test_serialize_list(self):
        """Test serializing a list."""
        result = self.bridge._serialize_result([1, 2, 3])
        self.assertEqual(result, [1, 2, 3])

    def test_serialize_string(self):
        """Test serializing a string."""
        result = self.bridge._serialize_result("hello")
        self.assertEqual(result, "hello")

    def test_serialize_non_serializable(self):
        """Test serializing non-JSON-serializable objects."""
        class CustomObj:
            def __str__(self):
                return "CustomObj:test"
        
        result = self.bridge._serialize_result(CustomObj())
        self.assertEqual(result, "CustomObj:test")


class TestPhooksToolBridgeStats(unittest.TestCase):
    """Test statistics."""

    def setUp(self):
        self.bridge = PhooksToolBridge()

    def test_get_stats(self):
        """Test get stats returns all fields."""
        stats = self.bridge.get_stats()
        self.assertIn('running', stats)
        self.assertIn('tools_registered', stats)
        self.assertIn('tools', stats)
        self.assertIn('calls_received', stats)
        self.assertIn('calls_completed', stats)
        self.assertIn('calls_failed', stats)
        self.assertFalse(stats['running'])

    def test_get_stats_after_calls(self):
        """Test stats after simulated calls."""
        self.bridge._calls_received = 10
        self.bridge._calls_completed = 8
        self.bridge._calls_failed = 2
        
        stats = self.bridge.get_stats()
        self.assertEqual(stats['calls_received'], 10)
        self.assertEqual(stats['calls_completed'], 8)
        self.assertEqual(stats['calls_failed'], 2)

    def test_get_stats_tools_list(self):
        """Test stats includes tool names."""
        self.bridge.register_tool('a', MagicMock())
        self.bridge.register_tool('b', MagicMock())
        
        stats = self.bridge.get_stats()
        self.assertEqual(stats['tools_registered'], 2)
        self.assertIn('a', stats['tools'])
        self.assertIn('b', stats['tools'])


class TestPhooksToolBridgeStartStop(unittest.TestCase):
    """Test start/stop lifecycle."""

    def test_start_no_phooks(self):
        """Test start without Phooks (should not crash)."""
        bridge = PhooksToolBridge()

        async def run_test():
            await bridge.start()
            self.assertFalse(bridge._running)  # No Phooks client

        _run_async(run_test())

    def test_start_with_phooks(self):
        """Test start with Phooks creates listen task."""
        bridge = PhooksToolBridge(MagicMock())

        async def run_test():
            await bridge.start()
            self.assertTrue(bridge._running)
            self.assertIsNotNone(bridge._listen_task)
            await bridge.stop()

        _run_async(run_test())

    def test_stop_no_start(self):
        """Test stop without start (should not crash)."""
        bridge = PhooksToolBridge()

        async def run_test():
            await bridge.stop()
            self.assertFalse(bridge._running)

        _run_async(run_test())


if __name__ == '__main__':
    unittest.main()
