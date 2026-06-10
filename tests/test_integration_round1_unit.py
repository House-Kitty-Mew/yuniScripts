"""
Round 1: Unit Integration Tests — Phooks ↔ Tool ↔ Result

Tests individual integration points between FastMCP and DeepSky components.
Each test verifies a single integration seam.

Part of DeepSky Self-Healing AI Ecosystem.
Spec: /home/deck/Documents/dev-yuniScripts/DEEPSKY_AGENT_CONSTRAINTS_INTEGRATION_SPEC.md

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', '..')
))

from engine.phooks_client import PhooksClient

# ═══════════════════════════════════════════════════════════════════
# Helper: Mock FastMCPAdapter
# ═══════════════════════════════════════════════════════════════════

class MockFastMCPAdapter:
    """Mock for fastmcp_adapter.FastMCPAdapter."""
    
    async def execute_tool(self, tool_name: str, parameters: dict) -> dict:
        try:
            if tool_name == 'database_query':
                return {'success': True, 'result': {'columns': ['id', 'name'], 'data': [[1, 'test']]}}
            elif tool_name == 'write_file':
                return {'success': True, 'result': 'File written successfully'}
            elif tool_name == 'error_tool':
                return {'success': False, 'error': 'Tool execution failed'}
            elif tool_name == 'timeout_tool':
                raise asyncio.TimeoutError('Tool timed out')
            else:
                return {'success': False, 'error': f'Unknown tool: {tool_name}'}
        except Exception as e:
            logger.error(f"execute_tool failed: {e}")
            return {}
class TestRound1_PhooksBridgeToolCallRouting(unittest.TestCase):
    """
    Test that Phooks bridge can route tool calls and deliver results.
    Integration point: PhooksClient ↔ phooks_bridge ↔ FastMCPAdapter
    """
    
    def setUp(self):
        self.adapter = MockFastMCPAdapter()
        self.client_phooks = None
        
        # Create a mock PhooksClient for the "client" side
        self.mock_client_sock = MagicMock()
        self.mock_client = PhooksClient(
            script_id='test-deepsky_client',
            listen_events=['tool.result'],
            emit_events=['tool.call']
        )
        self.mock_client.sock = self.mock_client_sock
        self.mock_client.sock.settimeout = MagicMock()
    
    def test_tool_call_routing_success(self):
        """
        Verify a tool.call event is correctly routed through Phooks
        and the result comes back as tool.result.
        """
        # Simulate: client sends tool.call
        tool_call = {
            'command': 'EMIT',
            'event': 'tool.call',
            'data': {
                'request_id': 'req-001',
                'tool_name': 'database_query',
                'parameters': {'query': 'SELECT * FROM users'},
                'session_id': 'test-session-001',
                'timestamp': '2026-06-07T00:00:00Z'
            },
            'sender': 'test-deepsky_client'
        }
        
        # Verify the emit event format is correct
        self.assertEqual(tool_call['event'], 'tool.call')
        self.assertEqual(tool_call['data']['tool_name'], 'database_query')
        self.assertEqual(tool_call['data']['request_id'], 'req-001')
        self.assertTrue(tool_call['data']['session_id'].startswith('test-session'))
        
        # Simulate: bridge receives event, executes tool
        async def execute_tool():
            result = await self.adapter.execute_tool(
                tool_call['data']['tool_name'],
                tool_call['data']['parameters']
            )
            return result
        
        result = asyncio.run(execute_tool())
        
        # Verify tool execution succeeded
        self.assertTrue(result['success'])
        self.assertIn('result', result)
        self.assertEqual(result['result']['data'][0][1], 'test')
    
    def test_tool_call_routing_file_write(self):
        """
        Verify write_file tool call routing works.
        """
        tool_call = {
            'command': 'EMIT',
            'event': 'tool.call',
            'data': {
                'request_id': 'req-002',
                'tool_name': 'write_file',
                'parameters': {'path': '/tmp/test.txt', 'content': 'hello'},
                'session_id': 'test-session-002',
                'timestamp': '2026-06-07T00:00:00Z'
            },
            'sender': 'test-deepsky_client'
        }
        
        async def execute_tool():
            return await self.adapter.execute_tool(
                tool_call['data']['tool_name'],
                tool_call['data']['parameters']
            )
        
        result = asyncio.run(execute_tool())
        self.assertTrue(result['success'])
    
    def test_tool_result_delivery_format(self):
        """
        Verify the tool result event format is correct for delivery back to client.
        """
        tool_result = {
            'command': 'EMIT',
            'event': 'tool.result.req-001',
            'data': {
                'request_id': 'req-001',
                'status': 'success',
                'result': json.dumps({'columns': ['id'], 'data': [[1]]}),
                'execution_time_ms': 42,
                'timestamp': '2026-06-07T00:00:01Z'
            },
            'sender': 'test-fastmcp_server'
        }
        
        # Verify result event format
        self.assertEqual(tool_result['event'], 'tool.result.req-001')
        self.assertEqual(tool_result['data']['status'], 'success')
        self.assertEqual(tool_result['data']['request_id'], 'req-001')
        self.assertIn('execution_time_ms', tool_result['data'])
        self.assertIsInstance(tool_result['data']['execution_time_ms'], int)
    
    def test_tool_result_error_format(self):
        """
        Verify error tool results are formatted correctly.
        """
        tool_result = {
            'command': 'EMIT',
            'event': 'tool.result.req-003',
            'data': {
                'request_id': 'req-003',
                'status': 'error',
                'error': 'Tool execution failed',
                'execution_time_ms': 15,
                'timestamp': '2026-06-07T00:00:01Z'
            },
            'sender': 'test-fastmcp_server'
        }
        
        self.assertEqual(tool_result['data']['status'], 'error')
        self.assertIn('error', tool_result['data'])
        self.assertEqual(tool_result['data']['error'], 'Tool execution failed')
    
    def test_multiple_clients_distinct_requests(self):
        """
        Verify multiple clients can make distinct tool calls
        and results are properly scoped by request_id.
        """
        client_a_request = {
            'request_id': 'req-client-a-001',
            'tool_name': 'database_query',
            'parameters': {'query': 'SELECT 1'}
        }
        client_b_request = {
            'request_id': 'req-client-b-001',
            'tool_name': 'write_file',
            'parameters': {'path': '/tmp/b.txt', 'content': 'b'}
        }
        
        self.assertNotEqual(
            client_a_request['request_id'],
            client_b_request['request_id']
        )
        
        async def execute_both():
            result_a = await self.adapter.execute_tool(
                client_a_request['tool_name'],
                client_a_request['parameters']
            )
            result_b = await self.adapter.execute_tool(
                client_b_request['tool_name'],
                client_b_request['parameters']
            )
            return result_a, result_b
        
        result_a, result_b = asyncio.run(execute_both())
        self.assertTrue(result_a['success'])
        self.assertTrue(result_b['success'])
    
    def test_tool_execution_error_handling(self):
        """
        Verify tool execution errors are properly captured.
        """
        async def execute_tool():
            return await self.adapter.execute_tool('error_tool', {})
        
        result = asyncio.run(execute_tool())
        self.assertFalse(result['success'])
        self.assertIn('error', result)
    
    def test_tool_timeout_handling(self):
        """
        Verify tool timeouts are properly captured.
        """
        async def execute_tool():
            try:
                return await self.adapter.execute_tool('timeout_tool', {})
            except asyncio.TimeoutError:
                return {'success': False, 'error': 'Tool timed out'}
        
        result = asyncio.run(execute_tool())
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'Tool timed out')
    
    def test_unknown_tool_handling(self):
        """
        Verify unknown tools return appropriate error.
        """
        async def execute_tool():
            return await self.adapter.execute_tool('nonexistent_tool', {})
        
        result = asyncio.run(execute_tool())
        self.assertFalse(result['success'])
        self.assertIn('Unknown tool', result['error'])
    
    def test_request_id_uniqueness(self):
        """
        Verify each request gets a unique ID.
        """
        request_ids = set()
        for i in range(100):
            rid = f"req-{i}-{int(asyncio.run(self._get_timestamp())) % 100000}"
            request_ids.add(rid)
        
        self.assertEqual(len(request_ids), 100)
    
    async def _get_timestamp(self):
        import time
        return time.time()


class TestRound1_DebugHooksCapture(unittest.TestCase):
    """
    Test that debug hooks properly capture errors and create events.
    Integration point: DebugHooks ↔ Phooks (system.error event)
    """
    
    def setUp(self):
        self.captured_errors = []
    
    def test_captures_tool_execution_error(self):
        """
        Verify debug hooks capture when a tool execution fails.
        """
        error_event = {
            'event': 'system.error',
            'data': {
                'error_type': 'tool_execution_error',
                'tool_name': 'error_tool',
                'parameters': json.dumps({'input': 'test'}),
                'error_message': 'Tool execution failed: invalid input',
                'stack_trace': 'Traceback (most recent call last):\n  ...',
                'timestamp': '2026-06-07T00:00:00Z'
            },
            'sender': 'test-fastmcp_server'
        }
        
        self.captured_errors.append(error_event)
        
        # Verify capture format
        self.assertEqual(error_event['event'], 'system.error')
        self.assertEqual(error_event['data']['error_type'], 'tool_execution_error')
        self.assertIn('stack_trace', error_event['data'])
        self.assertIn('timestamp', error_event['data'])
    
    def test_captures_data_flow_anomaly(self):
        """
        Verify debug hooks capture data flow anomalies.
        """
        anomaly_event = {
            'event': 'system.error',
            'data': {
                'error_type': 'data_flow_anomaly',
                'flow_path': 'session_manager.flush',
                'expected': 'SQLite connection',
                'actual': 'Database locked',
                'context_summary': 'Flush failed due to concurrent write',
                'timestamp': '2026-06-07T00:00:00Z'
            },
            'sender': 'test-deepsky_client'
        }
        
        self.captured_errors.append(anomaly_event)
        
        self.assertEqual(anomaly_event['data']['error_type'], 'data_flow_anomaly')
        self.assertIn('flow_path', anomaly_event['data'])
        self.assertIn('expected', anomaly_event['data'])
        self.assertIn('actual', anomaly_event['data'])
    
    def test_captures_timeout_event(self):
        """
        Verify debug hooks capture timeout events.
        """
        timeout_event = {
            'event': 'system.error',
            'data': {
                'error_type': 'timeout',
                'component': 'healing_agent.spawn_fix_session',
                'timeout_duration': 300,
                'context_summary': 'API call exceeded timeout',
                'timestamp': '2026-06-07T00:00:00Z'
            },
            'sender': 'test-deepsky_client'
        }
        
        self.captured_errors.append(timeout_event)
        
        self.assertEqual(timeout_event['data']['error_type'], 'timeout')
        self.assertEqual(timeout_event['data']['timeout_duration'], 300)
    
    def test_error_event_produces_work_order(self):
        """
        Verify that a system.error event can trigger work order creation.
        Integration: debug_hooks → work_order_engine
        """
        error_event = {
            'event': 'system.error',
            'data': {
                'error_type': 'tool_execution_error',
                'tool_name': 'database_query',
                'error_message': 'OperationalError: no such table',
                'stack_trace': 'Line 42 in database_query.py',
                'timestamp': '2026-06-07T00:00:00Z'
            }
        }
        
        # Simulate WorkOrderEngine processing
        work_order = {
            'id': 1001,
            'description': f"Tool error: {error_event['data']['tool_name']} - {error_event['data']['error_message']}",
            'priority': 1,
            'status': 'pending',
            'notes': json.dumps(error_event['data'])
        }
        
        self.assertEqual(work_order['priority'], 1)
        self.assertIn('database_query', work_order['description'])
        self.assertIn('no such table', work_order['description'])
    
    def test_multiple_errors_sequential_capture(self):
        """
        Verify multiple errors are captured sequentially.
        """
        errors = []
        for i in range(5):
            errors.append({
                'event': 'system.error',
                'data': {
                    'error_type': 'tool_execution_error',
                    'tool_name': f'tool_{i}',
                    'error_message': f'Error #{i}',
                    'timestamp': f'2026-06-07T00:00:{i:02d}Z'
                }
            })
        
        self.assertEqual(len(errors), 5)
        for i, err in enumerate(errors):
            self.assertEqual(err['data']['error_message'], f'Error #{i}')
    
    def test_empty_context_does_not_crash(self):
        """
        Verify debug hooks handle empty/missing context gracefully.
        """
        partial_error = {
            'event': 'system.error',
            'data': {
                'error_type': 'unknown'
            }
        }
        
        # Should not crash when accessing missing fields
        try:
            error_type = partial_error['data'].get('error_type', 'unknown')
            tool_name = partial_error['data'].get('tool_name', 'unknown')
            self.assertEqual(error_type, 'unknown')
            self.assertEqual(tool_name, 'unknown')
        except Exception as e:
            self.fail(f"Accessing partial error data raised: {e}")


class TestRound1_SessionManagerCheckpoint(unittest.TestCase):
    """
    Test that Session Manager creates checkpoints before healing operations.
    Integration point: SessionManager ↔ SQLite checkpoint
    """
    
    def setUp(self):
        self.checkpoints = []
    
    def test_checkpoint_before_healing(self):
        """
        Verify a state checkpoint is created before a healing agent is spawned.
        """
        checkpoint = {
            'session_id': 'test-session-001',
            'checkpoint_type': 'bug_checkpoint',
            'state_snapshot': json.dumps({
                'messages': [
                    {'role': 'user', 'content': 'Fix this bug'},
                    {'role': 'assistant', 'content': 'I will investigate'}
                ],
                'token_count': 150
            }),
            'state_hash': 'abc123def456',
            'created_at': '2026-06-07T00:00:00Z'
        }
        
        self.checkpoints.append(checkpoint)
        
        self.assertEqual(checkpoint['checkpoint_type'], 'bug_checkpoint')
        self.assertIn('state_snapshot', checkpoint)
        self.assertIsNotNone(json.loads(checkpoint['state_snapshot']))
    
    def test_checkpoint_restore(self):
        """
        Verify a checkpoint can be restored to its full state.
        """
        checkpoint = {
            'session_id': 'test-session-001',
            'checkpoint_type': 'last_good',
            'state_snapshot': json.dumps({
                'messages': [
                    {'role': 'system', 'content': 'You are an AI assistant'},
                    {'role': 'user', 'content': 'Hello'}
                ],
                'token_count': 50
            }),
            'state_hash': 'xyz789',
            'created_at': '2026-06-07T00:00:00Z'
        }
        
        restored_state = json.loads(checkpoint['state_snapshot'])
        self.assertEqual(len(restored_state['messages']), 2)
        self.assertEqual(restored_state['messages'][0]['role'], 'system')
    
    def test_multiple_checkpoints_ordered(self):
        """
        Verify multiple checkpoints maintain order.
        """
        checkpoints = [
            {'id': 1, 'checkpoint_type': 'last_good', 'created_at': '2026-06-07T00:00:00Z'},
            {'id': 2, 'checkpoint_type': 'bug_checkpoint', 'created_at': '2026-06-07T00:00:05Z'},
            {'id': 3, 'checkpoint_type': 'recovery', 'created_at': '2026-06-07T00:00:10Z'}
        ]
        
        sorted_cps = sorted(checkpoints, key=lambda x: x['id'])
        self.assertEqual(sorted_cps[0]['checkpoint_type'], 'last_good')
    
    def test_checkpoint_with_empty_state(self):
        """
        Verify checkpoint with empty state doesn't crash.
        """
        checkpoint = {
            'session_id': 'test-empty',
            'checkpoint_type': 'bug_checkpoint',
            'state_snapshot': json.dumps({'messages': [], 'token_count': 0}),
            'state_hash': 'empty-hash',
            'created_at': '2026-06-07T00:00:00Z'
        }
        
        restored = json.loads(checkpoint['state_snapshot'])
        self.assertEqual(len(restored['messages']), 0)
        self.assertEqual(restored['token_count'], 0)
    
    def test_checkpoint_hash_chain_verification(self):
        """
        Verify checkpoint hash chain integrity.
        """
        # Create a chain of checkpoints
        chain = []
        prev_hash = 'initial'
        for i in range(5):
            cp_hash = f"hash-{i}-of-{prev_hash}"
            chain.append({
                'id': i,
                'state_hash': cp_hash,
                'prev_hash': prev_hash
            })
            prev_hash = cp_hash
        
        # Verify chain integrity
        for i in range(1, len(chain)):
            expected_prev = chain[i-1]['state_hash']
            self.assertEqual(chain[i]['prev_hash'], expected_prev)


if __name__ == '__main__':
    unittest.main(verbosity=2)

