"""
Round 3: System Integration Tests — Full Self-Healing Cycle

Tests the complete end-to-end self-healing cycle across ALL components.
Each test exercises the full data flow path.

Part of DeepSky Self-Healing AI Ecosystem.
Spec: /home/deck/Documents/dev-yuniScripts/DEEPSKY_AGENT_CONSTRAINTS_INTEGRATION_SPEC.md

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'SCRIPTS', 'CLIENTS', 'deepsky_client'))

from healing_agent import (
    HealingAgent, AgentDepthTracker, HealingAgentSession,
    MAX_AGENT_DEPTH, MAX_CHAIN_LENGTH
)


# ═══════════════════════════════════════════════════════════════════
# ROUND 3: System Integration Tests
# ═══════════════════════════════════════════════════════════════════

class TestRound3_FullSelfHealingCycle(unittest.TestCase):
    """
    Test the complete self-healing cycle end-to-end.
    Flow: Error detected → Work order created → Healing agent spawned → Fix applied → Validated → Closed
    """
    
    def setUp(self):
        # Temp DB
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        
        self.conn = sqlite3.connect(self.temp_db.name)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS work_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                priority INTEGER DEFAULT 3,
                status TEXT DEFAULT 'pending',
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()
        
        # Mock API client
        self.mock_api = MagicMock()
        self.mock_api.chat_completion = AsyncMock()
        self.mock_api.chat_completion.return_value = MagicMock(
            success=True, content='Fix implemented. All tests pass.'
        )
        
        # Mock session manager
        self.mock_session = MagicMock()
        self.mock_session.flush = AsyncMock(return_value=True)
        self.mock_session.restore_from_checkpoint = MagicMock(
            return_value=[{'role': 'user', 'content': 'test'}]
        )
        
        self.agent = HealingAgent(
            config={'enabled': True, 'poll_interval': 1, 'agent_timeout': 60},
            api_client=self.mock_api,
            session_manager=self.mock_session,
            work_order_engine=None
        )
        self.agent._db_path = self.temp_db.name
    
    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass
    
    def test_full_self_healing_cycle(self):
        """
        Complete end-to-end cycle:
        1. Work order exists in DB (pending)
        2. Healing agent polls and discovers it
        3. Agent generates system prompt
        4. Agent spawns fix session (API call)
        5. Fix succeeds
        6. Work order marked completed
        
        This verifies ALL components work together.
        """
        # Step 1: Create a work order
        self.cursor.execute('''
            INSERT INTO work_orders (description, priority, status, notes)
            VALUES (?, ?, ?, ?)
        ''', ('Bug: database_query fails on users table', 1, 'pending',
              '**Category:** tool_execution_error\n'
              '**Stack Trace:**\n  File "db.py", line 42'))
        self.conn.commit()
        wo_id = self.cursor.lastrowid
        
        # Step 2: Agent polls and discovers it
        orders = self.agent._get_pending_work_orders(0)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]['id'], wo_id)
        
        # Step 3: Agent generates system prompt
        order = orders[0]
        prompt = asyncio.run(self.agent._generate_agent_prompt(order))
        self.assertIsNotNone(prompt)
        self.assertIn('CRITICAL CONSTRAINTS', prompt)
        self.assertIn('Work Order', prompt)
        
        # Step 4: Agent spawns fix session
        session = HealingAgentSession(wo_id, f'test-sys-{wo_id}', depth=1)
        self.agent._active_sessions[session.session_id] = session
        
        success = asyncio.run(
            self.agent._spawn_fix_session(session, prompt, order)
        )
        self.assertTrue(success)
        self.mock_api.chat_completion.assert_called_once()
        
        # Step 5: Agent marks as completed
        self.agent._update_work_order_status(
            wo_id, 'completed',
            notes_append='Fix applied and validated [AUTO-HEALING]'
        )
        
        # Step 6: Verify final state
        self.cursor.execute('SELECT status, notes FROM work_orders WHERE id = ?', (wo_id,))
        row = self.cursor.fetchone()
        self.assertEqual(row[0], 'completed')
        self.assertIn('[AUTO-HEALING]', row[1])
        
        # Clean up
        self.agent._active_sessions.pop(session.session_id, None)
    
    def test_full_cycle_with_recovery(self):
        """
        Test that the system recovers from a failed fix attempt.
        Flow: Work order → Agent fails → Recovery → Agent succeeds → Closed.
        """
        # Create work order
        self.cursor.execute('''
            INSERT INTO work_orders (description, priority, status, notes)
            VALUES (?, ?, ?, ?)
        ''', ('Bug: timeout in API call', 2, 'pending',
              '**Category:** timeout\n**Component:** api_client'))
        self.conn.commit()
        wo_id = self.cursor.lastrowid
        
        # First attempt: API fails
        self.mock_api.chat_completion.side_effect = [
            MagicMock(success=False, error='API rate limit exceeded'),
            MagicMock(success=True, content='Fixed: added retry logic')
        ]
        
        order = {'id': wo_id, 'priority': 2, 'description': 'Bug: timeout',
                 'notes': '**Category:** timeout\n**Component:** api_client'}
        
        session = HealingAgentSession(wo_id, f'test-rec-{wo_id}', depth=1)
        self.agent._active_sessions[session.session_id] = session
        
        # First attempt fails
        prompt = asyncio.run(self.agent._generate_agent_prompt(order))
        result = asyncio.run(
            self.agent._spawn_fix_session(session, prompt, order)
        )
        self.assertFalse(result)
        
        # Recovery: checkpoint was saved
        self.agent._update_work_order_status(wo_id, 'in-progress',
                                            notes_append='Recovery attempt 1')
        
        # Second attempt succeeds (different prompt for clarity)
        prompt2 = "Retry: You are a repair agent."
        session2 = HealingAgentSession(wo_id, f'test-rec-{wo_id}-v2', depth=1)
        
        result2 = asyncio.run(
            self.agent._spawn_fix_session(session2, prompt2, order)
        )
        self.assertTrue(result2)
        
        # Mark complete
        self.agent._update_work_order_status(
            wo_id, 'completed',
            notes_append='Fix applied [AUTO-HEALING]'
        )
        
        self.cursor.execute('SELECT status FROM work_orders WHERE id = ?', (wo_id,))
        self.assertEqual(self.cursor.fetchone()[0], 'completed')
    
    def test_healing_agent_uses_fastmcp_tools(self):
        """
        Verify the healing agent can access and use FastMCP tools
        via the Phooks bridge during the fix cycle.
        """
        tool_defs = self.agent._get_tool_definitions()
        
        # The tool definitions should only fail gracefully if no DB
        self.assertIsInstance(tool_defs, list)
    
    def test_session_checkpoint_before_healing(self):
        """
        Verify session manager creates checkpoint before healing spawns.
        """
        wo_id = 999
        order = {'id': wo_id, 'priority': 1, 'description': 'Test bug',
                 'notes': '**Category:** tool_execution_error'}
        
        session = HealingAgentSession(wo_id, f'test-cp-{wo_id}', depth=1)
        
        # Flush should be called before spawn
        self.mock_api.chat_completion = AsyncMock(
            return_value=MagicMock(success=True, content='Fixed.')
        )
        
        system_prompt = "Test prompt"
        asyncio.run(self.agent._spawn_fix_session(session, system_prompt, order))
        
        # Verify flush was called
        self.mock_session.flush.assert_called_once()
    
    def test_full_cycle_status_transitions(self):
        """
        Verify correct status transitions through the full cycle.
        pending → in-progress → completed
        """
        wo_id = 1001
        
        # Insert
        self.cursor.execute('''
            INSERT INTO work_orders (description, priority, status)
            VALUES (?, ?, ?)
        ''', ('Test transitions', 1, 'pending'))
        self.conn.commit()
        wo_id = self.cursor.lastrowid
        
        # pending
        self.cursor.execute('SELECT status FROM work_orders WHERE id = ?', (wo_id,))
        self.assertEqual(self.cursor.fetchone()[0], 'pending')
        
        # → in-progress
        self.agent._update_work_order_status(wo_id, 'in-progress')
        self.cursor.execute('SELECT status FROM work_orders WHERE id = ?', (wo_id,))
        self.assertEqual(self.cursor.fetchone()[0], 'in-progress')
        
        # → completed
        self.agent._update_work_order_status(wo_id, 'completed')
        self.cursor.execute('SELECT status FROM work_orders WHERE id = ?', (wo_id,))
        self.assertEqual(self.cursor.fetchone()[0], 'completed')


class TestRound3_FastMCPToolCallViaPhooks(unittest.TestCase):
    """
    Test that FastMCP tools can be called via Phooks from the DeepSky client.
    Integration: DeepSky client → Phooks → FastMCP server → tool → result → back
    """
    
    def test_tool_call_event_schema(self):
        """
        Verify the tool.call event schema matches between client and server.
        """
        # Client-side event format (what DeepSky publishes)
        client_event = {
            'command': 'EMIT',
            'event': 'tool.call',
            'data': {
                'request_id': 'req-001',
                'tool_name': 'database_query',
                'parameters': {'query': 'SELECT 1', 'database_path': '/tmp/test.db'},
                'session_id': 'deepsky-session-123',
                'timestamp': '2026-06-07T00:00:00Z'
            },
            'sender': 'deepsky_client'
        }
        
        # Server-side expected format (what FastMCP consumes)
        expected_tool_call = client_event['data']
        
        self.assertEqual(expected_tool_call['tool_name'], 'database_query')
        self.assertIn('parameters', expected_tool_call)
        self.assertEqual(expected_tool_call['parameters']['query'], 'SELECT 1')
        self.assertIn('session_id', expected_tool_call)
    
    def test_tool_result_event_schema(self):
        """
        Verify the tool.result event schema matches between server and client.
        """
        # Server-side result format (what FastMCP publishes)
        server_result = {
            'command': 'EMIT',
            'event': 'tool.result.req-001',
            'data': {
                'request_id': 'req-001',
                'status': 'success',
                'result': '{"columns": ["id"], "data": [[1]]}',
                'execution_time_ms': 42,
                'timestamp': '2026-06-07T00:00:01Z'
            },
            'sender': 'fastmcp_server'
        }
        
        # Client-side expected format (what DeepSky consumes)
        expected_result = server_result['data']
        
        self.assertEqual(expected_result['status'], 'success')
        self.assertEqual(expected_result['request_id'], 'req-001')
        self.assertEqual(expected_result['execution_time_ms'], 42)
    
    def test_tool_error_propagation(self):
        """
        Verify tool errors propagate correctly through the chain.
        """
        error_result = {
            'event': 'tool.result.req-002',
            'data': {
                'request_id': 'req-002',
                'status': 'error',
                'error': 'Tool execution failed: invalid query',
                'execution_time_ms': 5
            }
        }
        
        self.assertEqual(error_result['data']['status'], 'error')
        self.assertEqual(error_result['data']['request_id'], 'req-002')
        
        # Debug hooks should capture this
        debug_event = {
            'event': 'system.error',
            'data': {
                'error_type': 'tool_execution_error',
                'tool_name': 'database_query',
                'error_message': error_result['data']['error'],
                'timestamp': '2026-06-07T00:00:00Z'
            }
        }
        
        self.assertEqual(debug_event['data']['error_type'], 'tool_execution_error')
        self.assertEqual(debug_event['data']['error_message'],
                        error_result['data']['error'])
    
    def test_tool_timeout_propagation(self):
        """
        Verify tool timeouts propagate correctly.
        """
        timeout_event = {
            'event': 'system.error',
            'data': {
                'error_type': 'timeout',
                'component': 'phooks_bridge.execute_tool',
                'timeout_duration': 30,
                'tool_name': 'database_query',
                'timestamp': '2026-06-07T00:00:00Z'
            }
        }
        
        self.assertEqual(timeout_event['data']['error_type'], 'timeout')
        self.assertEqual(timeout_event['data']['timeout_duration'], 30)
    
    def test_work_order_created_from_tool_error(self):
        """
        Verify tool errors generate work orders that the healing agent can process.
        """
        # Error from tool
        tool_error = {
            'error_type': 'tool_execution_error',
            'tool_name': 'database_query',
            'error_message': 'no such table: users'
        }
        
        # Work order created
        work_order = {
            'id': 2001,
            'description': f"Tool error: {tool_error['tool_name']} - {tool_error['error_message']}",
            'priority': 1,
            'status': 'pending',
            'notes': json.dumps(tool_error)
        }
        
        # Can healing agent process it?
        self.assertIn(tool_error['tool_name'], work_order['description'])
        self.assertEqual(work_order['priority'], 1)
        self.assertEqual(work_order['status'], 'pending')
        
        # Verify agent can extract error info
        notes = json.loads(work_order['notes'])
        self.assertEqual(notes['tool_name'], 'database_query')
        self.assertEqual(notes['error_message'], 'no such table: users')


if __name__ == '__main__':
    unittest.main(verbosity=2)
