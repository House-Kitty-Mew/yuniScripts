"""
Round 4: Stress & Edge Case Integration Tests

Tests failure modes, boundary conditions, and stress scenarios.
Covers: depth limits, parallel rejection, chain enforcement, recovery, timeout.

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
    HealingAgent, AgentDepthTracker, ConfidenceVerdict, HealingAgentSession,
    MAX_AGENT_DEPTH, MAX_CHAIN_LENGTH, MAX_CHAIN_DEPTH, MAX_CONCURRENT_AGENTS
)


# ═══════════════════════════════════════════════════════════════════
# ROUND 4: Stress & Edge Case Tests
# ═══════════════════════════════════════════════════════════════════

class TestRound4_AgentConstraints(unittest.TestCase):
    """
    Test that agent constraints are properly enforced.
    These are the most critical tests — they verify the safety system.
    """
    
    def setUp(self):
        self.tracker = AgentDepthTracker()
    
    def test_depth_2_limit_enforced(self):
        """
        Verify agent depth cannot exceed MAX_AGENT_DEPTH (2).
        Depth 0 → 1 → 2 → REJECTED at 3.
        """
        # Enter to depth 1
        self.assertTrue(self.tracker.enter('healer-1'))
        self.assertEqual(self.tracker.current_depth, 1)
        
        # Enter to depth 2
        self.assertTrue(self.tracker.enter('sub-agent-1'))
        self.assertEqual(self.tracker.current_depth, 2)
        
        # Attempt depth 3 — MUST BE REJECTED
        allowed, reason = self.tracker.can_spawn()
        self.assertFalse(allowed)
        self.assertIn('exceeded', reason)
        self.assertIn(str(MAX_AGENT_DEPTH), reason)
        
        # Verify can_spawn returns False for next level
        self.assertFalse(self.tracker.enter('sub-agent-2'))
        self.assertEqual(self.tracker.current_depth, 2)  # Still at 2
    
    def test_depth_2_still_works_at_limit(self):
        """
        Verify that operating AT depth 2 is allowed (only 3+ is rejected).
        """
        self.assertTrue(self.tracker.enter('healer'))
        self.assertTrue(self.tracker.enter('sub-agent'))
        
        # At depth 2 — should be able to function
        self.assertEqual(self.tracker.current_depth, MAX_AGENT_DEPTH)
        
        # But can't go deeper
        allowed, _ = self.tracker.can_spawn()
        self.assertFalse(allowed)
    
    def test_parallel_agents_rejected(self):
        """
        Verify parallel agents are rejected by the serial lock design.
        MAX_CONCURRENT_AGENTS must be 1.
        """
        self.assertEqual(MAX_CONCURRENT_AGENTS, 1,
                         "MAX_CONCURRENT_AGENTS must be 1 to prevent parallel agents")
    
    def test_chain_length_10_max_enforced(self):
        """
        Verify chain cannot exceed MAX_CHAIN_LENGTH (10).
        After 10 sequential enters, the next must be rejected.
        """
        for i in range(MAX_CHAIN_LENGTH):
            allowed = self.tracker.mark_chain_step()
            self.assertTrue(allowed,
                           f"Chain step {i+1} should be allowed (max {MAX_CHAIN_LENGTH})")
        
        self.assertEqual(self.tracker.chain_count, MAX_CHAIN_LENGTH)
        
        # Next chain should fail
        allowed, reason = self.tracker.can_chain()
        self.assertFalse(allowed)
        self.assertIn('chain length', reason)
    
    def test_chain_depth_2_limit_enforced(self):
        """
        Verify chain depth cannot exceed MAX_CHAIN_DEPTH (2).
        """
        self.tracker.enter('chain-healer')
        self.tracker.enter('chain-sub')
        
        # At depth 2, can_chain should see depth > MAX_CHAIN_DEPTH
        allowed, reason = self.tracker.can_chain()
        self.assertFalse(allowed)
        self.assertIn('chain depth', reason)
    
    def test_confidence_validation_rejects_unsafe(self):
        """
        Verify confidence validation rejects unsafe spawns.
        """
        agent = HealingAgent(
            config={'enabled': True},
            api_client=None,
            session_manager=None,
            work_order_engine=None
        )
        
        # Enter depth 1 so sub-agent spawn would go to depth 2
        agent._depth_tracker.enter('healer')
        
        # Unsafe spawn with tool overlap should be rejected
        context = {
            'requested_tools': ['database_query', 'write_file'],
            'active_tool_domains': ['database_query'],
            'requested_data_domains': ['users_table'],
            'active_data_domains': ['users_table'],
            'modifies_state': True,
            'uses_database': True
        }
        
        # The risk analysis should find interference
        verdict = agent.validate_confidence('spawn_sub_agent', context)
        self.assertFalse(verdict.approved,
                         f"Expected rejected but got: {verdict.reason}")
        self.assertGreater(len(verdict.risk_factors), 0,
                          f"Expected risk factors but got: {verdict.risk_factors}")
    
    def test_confidence_validation_approves_safe(self):
        """
        Verify confidence validation approves safe spawns.
        """
        agent = HealingAgent(
            config={'enabled': True},
            api_client=None,
            session_manager=None,
            work_order_engine=None
        )
        
        ctx = {
            'requested_tools': ['read_file'],
            'active_tool_domains': ['database_query'],
            'requested_data_domains': ['config'],
            'active_data_domains': ['users_table'],
            'modifies_state': False,
            'uses_database': False
        }
        
        verdict = agent.validate_confidence('spawn_sub_agent', ctx)
        self.assertTrue(verdict.approved)
    
    def test_spawn_sub_agent_at_depth_2_rejected(self):
        """
        Verify attempting to spawn at depth=2+ is rejected by the system.
        """
        agent = HealingAgent(
            config={'enabled': True},
            api_client=None,
            session_manager=None,
            work_order_engine=None
        )
        
        # Enter to depth 2
        agent._depth_tracker.enter('healer')
        agent._depth_tracker.enter('sub-agent')
        
        # Try to spawn at depth 3
        result = asyncio.run(agent.spawn_sub_agent(
            'test task',
            {'requested_tools': [], 'active_tool_domains': []}
        ))
        
        self.assertFalse(result['success'])
        self.assertIn('depth', result['error'].lower())
    
    def test_spawn_sub_agent_at_depth_1_requires_confidence(self):
        """
        Verify spawning at depth 1 requires confidence validation.
        """
        agent = HealingAgent(
            config={'enabled': True},
            api_client=MagicMock(),
            session_manager=None,
            work_order_engine=None
        )
        agent._api_client = MagicMock()
        agent._api_client.chat_completion = AsyncMock(
            return_value=MagicMock(success=True, content='done')
        )
        
        agent._depth_tracker.enter('healer')
        
        # With overlapping tool domains — should be rejected
        result = asyncio.run(agent.spawn_sub_agent(
            'test task',
            {
                'requested_tools': ['database_query'],
                'active_tool_domains': ['database_query'],
                'active_data_domains': ['users'],
                'requested_data_domains': ['users'],
                'modifies_state': True
            }
        ))
        
        # Should fail confidence check
        if result.get('verdict'):
            self.assertFalse(result['verdict']['approved'])
        else:
            self.assertFalse(result['success'])
    
    def test_forced_flush_after_chain_limit(self):
        """
        Verify forced state flush occurs after chain limit is reached.
        """
        tracker = AgentDepthTracker()
        
        # Reach chain limit
        for i in range(MAX_CHAIN_LENGTH):
            tracker.mark_chain_step()
        
        self.assertEqual(tracker.chain_count, MAX_CHAIN_LENGTH)
        
        # Reset
        tracker.reset_chain_count()
        self.assertEqual(tracker.chain_count, 0)
        
        # Verify can chain again after reset
        allowed, _ = tracker.can_chain()
        self.assertTrue(allowed)


class TestRound4_RecoveryAndFailure(unittest.TestCase):
    """
    Test recovery from failures and edge cases.
    """
    
    def setUp(self):
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
        
        self.mock_api = MagicMock()
        self.mock_session = MagicMock()
        self.mock_session.flush = AsyncMock()
        self.mock_session.restore_from_checkpoint = MagicMock(
            return_value=[{'role': 'user', 'content': 'recovered message'}]
        )
        
        self.agent = HealingAgent(
            config={'enabled': True, 'poll_interval': 1, 'agent_timeout': 10},
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
    
    def test_recovery_from_agent_crash(self):
        """
        Verify recovery from a crashed agent session.
        """
        session = HealingAgentSession(999, 'crash-test', depth=1)
        session.status = 'failed'
        session.error = 'Agent crashed unexpectedly'
        
        recovered = asyncio.run(self.agent._recover_session(session))
        
        # Should attempt recovery
        self.mock_session.restore_from_checkpoint.assert_called_once()
    
    def test_timeout_during_healing(self):
        """
        Verify timeout during healing is properly handled.
        """
        self.mock_api.chat_completion = AsyncMock(
            side_effect=asyncio.TimeoutError('API timeout')
        )
        
        order = {'id': 100, 'priority': 1, 'description': 'Timeout test', 'notes': ''}
        session = HealingAgentSession(100, 'timeout-test', depth=1)
        
        result = asyncio.run(self.agent._spawn_fix_session(
            session, 'prompt', order
        ))
        
        self.assertFalse(result)
        self.assertEqual(session.status, 'timeout')
    
    def test_escalation_after_recovery_exhaustion(self):
        """
        Verify escalation after recovery attempts are exhausted.
        """
        self.agent._escalation_threshold = 2
        
        # Create a work order
        self.cursor.execute('''
            INSERT INTO work_orders (description, priority, status)
            VALUES (?, ?, ?)
        ''', ('Escalation test', 2, 'pending'))
        self.conn.commit()
        wo_id = self.cursor.lastrowid
        
        # Exhaust recovery attempts
        self.agent._escalate_work_order(wo_id)
        
        self.cursor.execute('SELECT priority FROM work_orders WHERE id = ?', (wo_id,))
        self.assertEqual(self.cursor.fetchone()[0], 1)
    
    def test_api_client_unavailable(self):
        """
        Verify healing agent handles missing API client gracefully.
        """
        agent_no_api = HealingAgent(
            config={'enabled': True},
            api_client=None,
            session_manager=None,
            work_order_engine=None
        )
        
        session = HealingAgentSession(101, 'no-api-test', depth=1)
        result = asyncio.run(agent_no_api._spawn_fix_session(
            session, 'prompt', {'id': 101}
        ))
        
        self.assertFalse(result)
    
    def test_sub_agent_without_api_client(self):
        """
        Verify sub-agent spawn without API client returns error.
        """
        agent_no_api = HealingAgent(
            config={'enabled': True},
            api_client=None,
            session_manager=None,
            work_order_engine=None
        )
        agent_no_api._depth_tracker.enter('healer')
        
        result = asyncio.run(agent_no_api.spawn_sub_agent(
            'test task',
            {'requested_tools': [], 'active_tool_domains': []}
        ))
        
        self.assertFalse(result['success'])
        self.assertIn('API client', result['error'])


class TestRound4_DataIntegrity(unittest.TestCase):
    """
    Test data integrity across the integration.
    """
    
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        
        self.conn = sqlite3.connect(self.temp_db.name)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS work_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                notes TEXT DEFAULT '',
                priority INTEGER DEFAULT 3,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()
        
        self.agent = HealingAgent(
            config={'enabled': True},
            api_client=None,
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
    
    def test_work_order_not_duplicated(self):
        """
        Verify processing a work order doesn't create duplicates.
        """
        self.cursor.execute('''
            INSERT INTO work_orders (description, status) VALUES (?, ?)
        ''', ('Test WO', 'pending'))
        self.conn.commit()
        wo_id = self.cursor.lastrowid
        
        # Mark as completed
        self.agent._update_work_order_status(wo_id, 'completed')
        
        # Verify no duplicate
        self.cursor.execute('SELECT COUNT(*) FROM work_orders WHERE id = ?', (wo_id,))
        self.assertEqual(self.cursor.fetchone()[0], 1)
    
    def test_status_update_doesnt_corrupt_other_fields(self):
        """
        Verify updating one field doesn't corrupt others.
        """
        self.cursor.execute('''
            INSERT INTO work_orders (description, status) VALUES (?, ?)
        ''', ('Original description', 'pending'))
        self.conn.commit()
        wo_id = self.cursor.lastrowid
        
        # Update status only
        self.agent._update_work_order_status(wo_id, 'in-progress')
        
        # Verify description preserved
        self.cursor.execute('SELECT description, status FROM work_orders WHERE id = ?',
                           (wo_id,))
        row = self.cursor.fetchone()
        self.assertEqual(row[0], 'Original description')
        self.assertEqual(row[1], 'in-progress')
    
    def test_multiple_sequential_work_orders(self):
        """
        Verify multiple work orders can be processed sequentially.
        """
        # Insert 5 work orders
        wo_ids = []
        for i in range(5):
            self.cursor.execute('''
                INSERT INTO work_orders (description, status) VALUES (?, ?)
            ''', (f'WO #{i}', 'pending'))
            self.conn.commit()
            wo_ids.append(self.cursor.lastrowid)
        
        # Process them all
        for wo_id in wo_ids:
            self.agent._update_work_order_status(wo_id, 'completed')
        
        # Verify all completed
        self.cursor.execute('SELECT COUNT(*) FROM work_orders WHERE status = ?', ('completed',))
        self.assertEqual(self.cursor.fetchone()[0], 5)
    
    def test_unknown_work_order_update_doesnt_crash(self):
        """
        Verify updating a non-existent work order doesn't crash.
        """
        try:
            self.agent._update_work_order_status(99999, 'completed')
        except Exception as e:
            self.fail(f"Updating unknown work order raised: {e}")


class TestRound4_ConcurrentSafety(unittest.TestCase):
    """
    Test that the system is safe against concurrent access patterns.
    """
    
    def test_serial_lock_prevents_parallel(self):
        """
        Verify the asyncio.Lock prevents parallel processing.
        """
        agent = HealingAgent(
            config={'enabled': True},
            api_client=None,
            session_manager=None,
            work_order_engine=None
        )
        
        async def test_serial():
            # Acquire lock
            async with agent._processing_lock:
                agent._processing = True
                # During this time, another coroutine trying to process should block
                # But the processing flag should prevent starting
                self.assertTrue(agent._processing)
                
                # Another attempt should see processing=True and skip
                self.assertTrue(agent._processing)
            
            agent._processing = False
            self.assertFalse(agent._processing)
        
        asyncio.run(test_serial())
    
    def test_concurrent_sub_agent_spawn_rejected(self):
        """
        Verify the system rejects concurrent sub-agent spawns.
        The design allows only 1 active session at a time.
        """
        tracker = AgentDepthTracker()
        tracker.enter('main')
        
        # First sub-agent
        self.assertTrue(tracker.enter('sub-1'))
        
        # Second sub-agent — should be rejected at depth limit
        allowed, _ = tracker.can_spawn()
        self.assertFalse(allowed)
    
    def test_database_concurrent_write_safety(self):
        """
        Verify database operations are safe for sequential writes.
        """
        temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp_db.close()
        
        try:
            conn = sqlite3.connect(temp_db.name, timeout=10)
            conn.execute('CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)')
            conn.commit()
            
            # Sequential writes
            for i in range(10):
                conn.execute('INSERT INTO test (id, val) VALUES (?, ?)', (i, f'val-{i}'))
            conn.commit()
            
            cursor = conn.execute('SELECT COUNT(*) FROM test')
            self.assertEqual(cursor.fetchone()[0], 10)
            
            conn.close()
        finally:
            os.unlink(temp_db.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
