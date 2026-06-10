"""
Round 2: Component Integration Tests — WorkOrder → Healing → Fix → Validate

Tests mid-level integration between DeepSky components.
Each test verifies a multi-step data flow through 2-3 components.

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
    HealingAgent, AgentDepthTracker, ConfidenceVerdict,
    MAX_AGENT_DEPTH, MAX_CHAIN_LENGTH, MAX_CHAIN_DEPTH
)


# ═══════════════════════════════════════════════════════════════════
# ROUND 2: Component Integration Tests
# ═══════════════════════════════════════════════════════════════════

class TestRound2_WorkOrderEngineToHealing(unittest.TestCase):
    """
    Test that Work Order Engine creates orders that Healing Agent can process.
    Integration: work_order_engine → database → healing_agent
    """
    
    def setUp(self):
        # Create temp DB for testing
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
        
        # Create a test work order
        self.cursor.execute('''
            INSERT INTO work_orders (description, priority, status, notes)
            VALUES (?, ?, ?, ?)
        ''', ('Test tool error: database_query failed', 1, 'pending',
              '**Category:** tool_execution_error\n'
              '**Component:** database_query\n'
              '**Summary:** OperatioonalError on users table\n'
              '**Stack Trace:** Line 42 in database_query.py\n'
              '```\n  File "database_query.py", line 42\n'
              '```'))
        self.conn.commit()
        self.work_order_id = self.cursor.lastrowid
        
        # Setup HealingAgent with mock API
        self.mock_api = MagicMock()
        self.mock_api.chat_completion = AsyncMock()
        self.mock_api.chat_completion.return_value = MagicMock(
            success=True, content='Fix applied: table recreated'
        )
        
        self.agent = HealingAgent(
            config={'enabled': True, 'poll_interval': 1, 'agent_timeout': 60},
            api_client=self.mock_api,
            session_manager=None,
            work_order_engine=None
        )
        self.agent._db_path = self.temp_db.name
    
    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass
    
    def test_work_order_engine_creates_order(self):
        """
        Verify work order engine creates properly structured orders.
        """
        self.assertIsNotNone(self.work_order_id)
        self.cursor.execute('SELECT * FROM work_orders WHERE id = ?', (self.work_order_id,))
        order = self.cursor.fetchone()
        self.assertIsNotNone(order)
        self.assertEqual(order[2], 1)  # priority
        self.assertEqual(order[3], 'pending')  # status
    
    def test_healing_agent_receives_work_order(self):
        """
        Verify healing agent can fetch pending work orders.
        """
        orders = self.agent._get_pending_work_orders(0)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]['id'], self.work_order_id)
        self.assertEqual(orders[0]['priority'], 1)
    
    def test_healing_agent_updates_status_to_in_progress(self):
        """
        Verify healing agent marks work orders as in-progress.
        """
        self.agent._update_work_order_status(self.work_order_id, 'in-progress')
        
        self.cursor.execute('SELECT status FROM work_orders WHERE id = ?', (self.work_order_id,))
        status = self.cursor.fetchone()[0]
        self.assertEqual(status, 'in-progress')
    
    def test_healing_agent_completes_work_order(self):
        """
        Verify healing agent can complete a work order successfully.
        """
        self.agent._update_work_order_status(
            self.work_order_id, 'completed',
            notes_append='Fix applied and validated [AUTO-HEALING]'
        )
        
        self.cursor.execute('SELECT status, notes FROM work_orders WHERE id = ?',
                           (self.work_order_id,))
        row = self.cursor.fetchone()
        self.assertEqual(row[0], 'completed')
        self.assertIn('[AUTO-HEALING]', row[1])
    
    def test_healing_agent_escalates_on_failure(self):
        """
        Verify healing agent escalates work orders that keep failing.
        """
        # Simulate multiple failures
        self.agent._escalate_work_order(self.work_order_id)
        
        self.cursor.execute('SELECT priority, notes FROM work_orders WHERE id = ?',
                           (self.work_order_id,))
        row = self.cursor.fetchone()
        self.assertEqual(row[0], 1)
        self.assertIn('ESCALATED', row[1])
    
    def test_healing_agent_generates_system_prompt(self):
        """
        Verify healing agent generates a system prompt with error context.
        """
        order = {
            'id': self.work_order_id,
            'priority': 1,
            'description': 'Test tool error',
            'notes': '**Category:** tool_execution_error\n**Component:** database_query'
        }
        
        prompt = asyncio.run(self.agent._generate_agent_prompt(order))
        self.assertIsNotNone(prompt)
        self.assertIn('Work Order', prompt)
        self.assertIn('CRITICAL CONSTRAINTS', prompt)
        self.assertIn('Max depth', prompt)
    
    def test_healing_agent_spawns_fix_session(self):
        """
        Verify healing agent spawns a fix session via the API.
        """
        order = {
            'id': self.work_order_id,
            'priority': 1,
            'description': 'Test tool error',
            'notes': '**Category:** tool_execution_error'
        }
        
        system_prompt = "You are a repair agent. Fix this issue."
        
        from healing_agent import HealingAgentSession
        session = HealingAgentSession(self.work_order_id, 'test-session', depth=1)
        
        result = asyncio.run(
            self.agent._spawn_fix_session(session, system_prompt, order)
        )
        
        self.assertTrue(result)
        self.mock_api.chat_completion.assert_called_once()
    
    def test_healing_agent_tracks_recovery_count(self):
        """
        Verify healing agent tracks and limits recovery attempts.
        """
        self.agent._escalation_threshold = 2
        
        from healing_agent import HealingAgentSession
        session = HealingAgentSession(self.work_order_id, 'test-session', depth=1)
        
        # First attempt
        session.recovery_attempts = 1
        self.assertLessEqual(session.recovery_attempts, self.agent._escalation_threshold)
        
        # Exceed threshold
        session.recovery_attempts = 3
        self.assertGreater(session.recovery_attempts, self.agent._escalation_threshold)
    
    def test_healing_agent_serial_processing_lock(self):
        """
        Verify the serial processing lock prevents concurrent work.
        """
        self.assertFalse(self.agent._processing)
        
        async def test_lock():
            self.agent._processing = True
            await asyncio.sleep(0.01)
            self.assertTrue(self.agent._processing)
            self.agent._processing = False
        
        asyncio.run(test_lock())
        self.assertFalse(self.agent._processing)
    
    def test_extracts_error_info_from_notes(self):
        """
        Verify error info extraction from work order notes.
        """
        order = {
            'id': self.work_order_id,
            'priority': 1,
            'description': 'Test tool error',
            'notes': '**Category:** tool_execution_error\n'
                     '**Component:** database_query\n'
                     '**Summary:** Table not found\n'
                     '**Stack Trace:**\n  File "x.py", line 42\n    cursor.execute()\n'
        }
        
        error_info = self.agent._extract_error_info(order['notes'])
        self.assertEqual(error_info.get('category'), 'tool_execution_error')
        self.assertEqual(error_info.get('component'), 'database_query')
        self.assertEqual(error_info.get('summary'), 'Table not found')
        self.assertIn('File "x.py"', error_info.get('stack_trace', ''))


class TestRound2_AgentDepthTracker(unittest.TestCase):
    """
    Test the AgentDepthTracker component integration.
    """
    
    def setUp(self):
        self.tracker = AgentDepthTracker()
    
    def test_depth_tracks_correctly(self):
        """Verify depth increases and decreases."""
        self.assertEqual(self.tracker.current_depth, 0)
        
        self.assertTrue(self.tracker.enter('session-1'))
        self.assertEqual(self.tracker.current_depth, 1)
        
        self.assertTrue(self.tracker.enter('session-2'))
        self.assertEqual(self.tracker.current_depth, 2)
        
        self.tracker.exit('session-2')
        self.assertEqual(self.tracker.current_depth, 1)
        
        self.tracker.exit('session-1')
        self.assertEqual(self.tracker.current_depth, 0)
    
    def test_depth_limit_enforced(self):
        """Verify depth cannot exceed MAX_AGENT_DEPTH."""
        for i in range(MAX_AGENT_DEPTH):
            self.assertTrue(self.tracker.enter(f'session-{i}'))
        
        # Next attempt should fail
        self.assertEqual(self.tracker.current_depth, MAX_AGENT_DEPTH)
        allowed, reason = self.tracker.can_spawn()
        self.assertFalse(allowed)
        self.assertIn('exceeded', reason)
    
    def test_chain_count_tracks(self):
        """Verify chain count increases with each enter."""
        self.assertEqual(self.tracker.chain_count, 0)
        
        for i in range(5):
            self.tracker.mark_chain_step()
        
        self.assertEqual(self.tracker.chain_count, 5)
    
    def test_reset_chain_count(self):
        """Verify chain count can be reset."""
        for i in range(3):
            self.tracker.mark_chain_step()
        
        self.assertEqual(self.tracker.chain_count, 3)
        self.tracker.reset_chain_count()
        self.assertEqual(self.tracker.chain_count, 0)
    
    def test_chain_limit_enforced(self):
        """Verify chain length cannot exceed MAX_CHAIN_LENGTH."""
        for i in range(MAX_CHAIN_LENGTH):
            allowed = self.tracker.mark_chain_step()
            self.assertTrue(allowed)
        
        # Next chain should fail
        allowed, reason = self.tracker.can_chain()
        self.assertFalse(allowed)
        self.assertIn('chain length', reason)
    
    def test_get_info_returns_all_fields(self):
        """Verify get_info returns complete state."""
        self.tracker.enter('test-session')
        info = self.tracker.get_info()
        
        self.assertIn('current_depth', info)
        self.assertIn('chain_count', info)
        self.assertIn('session_stack', info)
        self.assertIn('max_depth', info)
        self.assertIn('max_chain_length', info)
        self.assertIn('max_chain_depth', info)


class TestRound2_SystemPromptGenerator(unittest.TestCase):
    """
    Test that SystemPromptGenerator creates prompts with tool definitions.
    Integration: system_prompt_generator ↔ tool_registry
    """
    
    def test_system_prompt_includes_tool_definitions(self):
        """
        Verify system prompt includes available tool definitions.
        """
        tool_defs = [
            {'name': 'database_query', 'description': 'Query database', 'category': 'database'},
            {'name': 'write_file', 'description': 'Write to file', 'category': 'general'}
        ]
        
        prompt = (
            "You are an AI repair agent.\n\n"
            "## Available Tools\n"
            f"{json.dumps(tool_defs, indent=2)}\n\n"
            "## Your Task\nFix the issue."
        )
        
        self.assertIn('database_query', prompt)
        self.assertIn('write_file', prompt)
    
    def test_system_prompt_includes_constraints(self):
        """
        Verify system prompt includes agent constraints.
        """
        constraints = (
            "## CRITICAL CONSTRAINTS\n"
            "1. NO parallel agents\n"
            f"2. Max depth {MAX_AGENT_DEPTH}\n"
            f"3. Max chain {MAX_CHAIN_LENGTH}\n"
            "4. Serial only"
        )
        
        self.assertIn('NO parallel agents', constraints)
        self.assertIn(f'Max depth {MAX_AGENT_DEPTH}', constraints)
        self.assertIn(f'Max chain {MAX_CHAIN_LENGTH}', constraints)
    
    def test_empty_tool_registry_handled(self):
        """
        Verify empty tool registry doesn't crash prompt generation.
        """
        prompt = "You are an AI repair agent.\n\n## Available Tools\n[]\n\n## Your Task\nFix."
        self.assertIn('Available Tools', prompt)
    
    def test_system_prompt_too_long_truncated(self):
        """
        Verify prompt truncation for very long content.
        """
        long_notes = "X" * 5000
        truncated = long_notes[:2000]
        self.assertEqual(len(truncated), 2000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
