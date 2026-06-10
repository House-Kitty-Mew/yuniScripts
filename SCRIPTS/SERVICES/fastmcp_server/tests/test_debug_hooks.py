"""
Unit tests for DebugHooks.

Part of FastMCP Server (YuniScript Managed).
Tests: error capture, data flow anomalies, timeouts, decorator, stats.

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from debug_hooks import DebugHooks


class TestDebugHooksInit(unittest.TestCase):
    """Test initialization."""

    def test_init_no_phooks(self):
        """Test initialization without Phooks."""
        hooks = DebugHooks()
        self.assertIsNone(hooks.phooks)
        self.assertEqual(hooks._error_count, 0)
        self.assertEqual(len(hooks._captured_errors), 0)

    def test_init_with_phooks(self):
        """Test initialization with Phooks."""
        mock_phooks = MagicMock()
        hooks = DebugHooks(mock_phooks)
        self.assertEqual(hooks.phooks, mock_phooks)

    def test_max_stored_errors(self):
        """Test default max stored errors."""
        hooks = DebugHooks()
        self.assertEqual(hooks._max_stored_errors, 100)


class TestDebugHooksErrorCapture(unittest.TestCase):
    """Test error capture methods."""

    def setUp(self):
        self.hooks = DebugHooks()

    def test_on_tool_call_error(self):
        """Test capturing tool call error."""
        async def run_test():
            event_id = await self.hooks.on_tool_call_error(
                'test_tool', ValueError("Invalid input"),
                {'param': 'x'}
            )
            self.assertIsNotNone(event_id)
            self.assertEqual(self.hooks._error_count, 1)
            self.assertEqual(len(self.hooks._captured_errors), 1)

        asyncio.run(run_test())

    def test_on_tool_call_error_stores_correctly(self):
        """Test error data is stored correctly."""
        async def run_test():
            error = ValueError("Test error")
            await self.hooks.on_tool_call_error('my_tool', error, {'key': 'val'})
            
            stored = self.hooks._captured_errors[0]
            self.assertEqual(stored['type'], 'tool_call_error')
            self.assertEqual(stored['tool_name'], 'my_tool')
            self.assertEqual(stored['error'], 'Test error')
            self.assertEqual(stored['context']['key'], 'val')

        asyncio.run(run_test())

    def test_on_data_flow_anomaly(self):
        """Test capturing data flow anomaly."""
        async def run_test():
            event_id = await self.hooks.on_data_flow_anomaly(
                ['step1', 'step2'], 
                {'data': 'test'},
                'int', 'str',
                {'source': 'validation'}
            )
            self.assertIsNotNone(event_id)
            self.assertEqual(self.hooks._error_count, 1)

        asyncio.run(run_test())

    def test_on_data_flow_anomaly_stores_path(self):
        """Test data flow path is stored."""
        async def run_test():
            flow_path = ['input', 'transform', 'validate', 'output']
            await self.hooks.on_data_flow_anomaly(
                flow_path, {}, 'expected', 'actual'
            )
            
            stored = self.hooks._captured_errors[0]
            self.assertEqual(stored['flow_path'], flow_path)
            self.assertEqual(stored['type'], 'data_flow_anomaly')

        asyncio.run(run_test())

    def test_on_timeout(self):
        """Test capturing timeout."""
        async def run_test():
            event_id = await self.hooks.on_timeout(
                'database_query', 30.5,
                {'query': 'SELECT *'}
            )
            self.assertIsNotNone(event_id)
            self.assertEqual(self.hooks._error_count, 1)

        asyncio.run(run_test())

    def test_on_timeout_severity_warning(self):
        """Test timeout under 30s is warning severity."""
        async def run_test():
            await self.hooks.on_timeout('fast_comp', 5.0)
            stored = self.hooks._captured_errors[0]
            self.assertEqual(stored['severity'], 'warning')

        asyncio.run(run_test())

    def test_on_timeout_severity_error(self):
        """Test timeout over 30s is error severity."""
        async def run_test():
            await self.hooks.on_timeout('slow_comp', 60.0)
            stored = self.hooks._captured_errors[0]
            self.assertEqual(stored['severity'], 'error')

        asyncio.run(run_test())

    def test_max_stored_overflow(self):
        """Test that error buffer doesn't exceed max."""
        async def run_test():
            hooks = DebugHooks()
            hooks._max_stored_errors = 5
            
            for i in range(10):
                await hooks.on_tool_call_error(f'tool_{i}', ValueError(f'Error {i}'))
            
            self.assertEqual(len(hooks._captured_errors), 5)  # Limited

        asyncio.run(run_test())


class TestDebugHooksDecorator(unittest.TestCase):
    """Test the error capture decorator."""

    def setUp(self):
        self.hooks = DebugHooks()

    def test_decorator_sync_function(self):
        """Test decorator on sync function."""
        @self.hooks.capture_tool_errors
        def good_tool(**kwargs):
            return {'ok': True}

        async def run_test():
            result = await good_tool(test=1)
            self.assertEqual(result['ok'], True)

        asyncio.run(run_test())

    def test_decorator_async_function(self):
        """Test decorator on async function."""
        @self.hooks.capture_tool_errors
        async def good_async(**kwargs):
            await asyncio.sleep(0.01)
            return {'async': True}

        async def run_test():
            result = await good_async()
            self.assertTrue(result['async'])

        asyncio.run(run_test())

    def test_decorator_captures_exception(self):
        """Test decorator captures and re-raises exception."""
        @self.hooks.capture_tool_errors
        def broken_tool(**kwargs):
            raise RuntimeError("Boom!")

        async def run_test():
            with self.assertRaises(RuntimeError):
                await broken_tool()
            self.assertEqual(self.hooks._error_count, 1)

        asyncio.run(run_test())


class TestDebugHooksQuery(unittest.TestCase):
    """Test query methods."""

    def setUp(self):
        self.hooks = DebugHooks()

    def test_get_recent_errors_empty(self):
        """Test getting recent errors when none exist."""
        errors = self.hooks.get_recent_errors()
        self.assertEqual(errors, [])

    def test_get_recent_errors_limit(self):
        """Test getting recent errors with limit."""
        async def run_test():
            for i in range(10):
                await self.hooks.on_tool_call_error(f't{i}', ValueError(f'err{i}'))
            
            errors = self.hooks.get_recent_errors(count=3)
            self.assertEqual(len(errors), 3)

        asyncio.run(run_test())

    def test_clear_errors(self):
        """Test clearing error buffer."""
        async def run_test():
            await self.hooks.on_tool_call_error('t', ValueError('e'))
            self.hooks.clear_errors()
            self.assertEqual(len(self.hooks._captured_errors), 0)

        asyncio.run(run_test())

    def test_get_stats(self):
        """Test get stats."""
        async def run_test():
            await self.hooks.on_tool_call_error('t', ValueError('e'))
            stats = self.hooks.get_stats()
            
            self.assertEqual(stats['error_count'], 1)
            self.assertEqual(stats['stored_errors'], 1)
            self.assertEqual(stats['max_stored'], 100)

        asyncio.run(run_test())

    def test_get_stats_not_running(self):
        """Test stats reflect running state."""
        stats = self.hooks.get_stats()
        self.assertFalse(stats['running'])


class TestDebugHooksStartStop(unittest.TestCase):
    """Test start/stop lifecycle."""

    def test_start(self):
        """Test start sets running."""
        hooks = DebugHooks()

        async def run_test():
            await hooks.start()
            self.assertTrue(hooks._running)
            await hooks.stop()

        asyncio.run(run_test())

    def test_stop(self):
        """Test stop clears running."""
        hooks = DebugHooks()

        async def run_test():
            await hooks.start()
            await hooks.stop()
            self.assertFalse(hooks._running)

        asyncio.run(run_test())


if __name__ == '__main__':
    unittest.main()
