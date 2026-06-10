"""
Comprehensive unit tests for HealingAgent.

Part of DeepSky Self-Healing AI Client.
Tests: work order polling, system prompt generation, agent spawning,
       session recovery, escalation, stats, edge cases.

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from healing_agent import (
    HealingAgent, HealingAgentSession, AgentHealthStatus
)


class TestHealingAgentSession(unittest.TestCase):
    """Test HealingAgentSession data class."""

    def test_session_creation(self):
        """Test basic session creation."""
        session = HealingAgentSession(42, 'session-abc')
        self.assertEqual(session.work_order_id, 42)
        self.assertEqual(session.session_id, 'session-abc')
        self.assertEqual(session.status, AgentHealthStatus.RUNNING)
        self.assertIsNotNone(session.started_at)
        self.assertIsNone(session.result)

    def test_session_status_transitions(self):
        """Test status transitions."""
        session = HealingAgentSession(1, 's1')
        self.assertEqual(session.status, AgentHealthStatus.RUNNING)
        
        session.status = AgentHealthStatus.COMPLETED
        self.assertEqual(session.status, AgentHealthStatus.COMPLETED)
        
        session.status = AgentHealthStatus.FAILED
        self.assertEqual(session.status, AgentHealthStatus.FAILED)
        
        session.status = AgentHealthStatus.TIMEOUT
        self.assertEqual(session.status, AgentHealthStatus.TIMEOUT)

    def test_session_last_activity_updates(self):
        """Test last activity tracking."""
        session = HealingAgentSession(1, 's1')
        old_time = session.last_activity
        time.sleep(0.01)
        session.last_activity = time.time()
        self.assertGreater(session.last_activity, old_time)

    def test_session_initial_counts(self):
        """Test initial error and recovery counts."""
        session = HealingAgentSession(1, 's1')
        self.assertEqual(session.error_count, 0)
        self.assertEqual(session.recovery_attempts, 0)


class TestHealingAgentInit(unittest.TestCase):
    """Test HealingAgent initialization."""

    def test_init_default(self):
        """Test initialization with default config."""
        agent = HealingAgent({'enabled': True})
        self.assertIsNotNone(agent)
        self.assertTrue(agent._enabled)
        self.assertEqual(agent._poll_interval, 10)
        self.assertEqual(agent._agent_timeout, 300)
        self.assertEqual(agent._max_concurrent, 2)

    def test_init_with_components(self):
        """Test initialization with all components."""
        mock_api = MagicMock()
        mock_sm = MagicMock()
        mock_woe = MagicMock()
        
        agent = HealingAgent({'enabled': True}, 
                            api_client=mock_api,
                            session_manager=mock_sm,
                            work_order_engine=mock_woe)
        self.assertEqual(agent.api_client, mock_api)
        self.assertEqual(agent.session_manager, mock_sm)
        self.assertEqual(agent.work_order_engine, mock_woe)

    def test_init_custom_config(self):
        """Test initialization with custom config."""
        config = {
            'enabled': True,
            'poll_interval': 5,
            'agent_timeout': 100,
            'max_concurrent_agents': 1,
            'escalation_threshold': 5
        }
        agent = HealingAgent(config)
        self.assertEqual(agent._poll_interval, 5)
        self.assertEqual(agent._agent_timeout, 100)
        self.assertEqual(agent._max_concurrent, 1)
        self.assertEqual(agent._escalation_threshold, 5)

    def test_init_disabled(self):
        """Test initialization disabled."""
        agent = HealingAgent({'enabled': False})
        self.assertFalse(agent._enabled)

    def test_set_prompt_generator(self):
        """Test setting prompt generator."""
        agent = HealingAgent({'enabled': True})
        mock_gen = MagicMock()
        agent.set_prompt_generator(mock_gen)
        self.assertEqual(agent._prompt_generator, mock_gen)


class TestHealingAgentStartStop(unittest.TestCase):
    """Test start/stop lifecycle."""

    def test_start_creates_poll_task(self):
        """Test start creates polling task."""
        agent = HealingAgent({'enabled': True})
        
        async def run_test():
            await agent.start()
            self.assertTrue(agent._running)
            self.assertIsNotNone(agent._poll_task)
            await agent.stop()
        
        asyncio.run(run_test())

    def test_stop_cancels_poll_task(self):
        """Test stop cancels polling task."""
        agent = HealingAgent({'enabled': True})
        
        async def run_test():
            await agent.start()
            await agent.stop()
            self.assertFalse(agent._running)
        
        asyncio.run(run_test())

    def test_double_start_no_error(self):
        """Test starting twice doesn't error."""
        agent = HealingAgent({'enabled': True})
        
        async def run_test():
            await agent.start()
            # Second start should not crash
            await agent.start()
            await agent.stop()
        
        asyncio.run(run_test())


class TestHealingAgentPromptGeneration(unittest.TestCase):
    """Test system prompt generation for agents."""

    def setUp(self):
        self.agent = HealingAgent({'enabled': True})

    def test_generate_agent_prompt(self):
        """Test basic prompt generation."""
        order = {
            'id': 123,
            'priority': 2,
            'description': 'Fix the bug',
            'notes': '**Category:** code_bug\n**Component:** test\n**Summary:** Something broke',
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            prompt = await self.agent._generate_agent_prompt(order)
            self.assertIsNotNone(prompt)
            self.assertIn('Work Order #123', prompt)
            self.assertIn('Fix the bug', prompt)
            self.assertIn('code_bug', prompt)
        
        asyncio.run(run_test())

    def test_generate_agent_prompt_with_prompt_generator(self):
        """Test prompt generation uses prompt generator when set."""
        mock_gen = AsyncMock()
        mock_gen.generate_for_work_order.return_value = 'Custom prompt'
        self.agent.set_prompt_generator(mock_gen)
        
        order = {'id': 1, 'description': 'test', 'notes': ''}
        
        async def run_test():
            prompt = await self.agent._generate_agent_prompt(order)
            self.assertEqual(prompt, 'Custom prompt')
            mock_gen.generate_for_work_order.assert_called_once_with(order)
        
        asyncio.run(run_test())

    def test_generate_agent_prompt_with_stack_trace(self):
        """Test prompt includes stack trace from notes."""
        order = {
            'id': 456,
            'priority': 1,
            'description': 'Crash',
            'notes': '**Category:** code_bug\n**Component:** main\n**Stack Trace:**\n```\nFile "test.py", line 10, in func\n  raise ValueError("crash")\n```',
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            prompt = await self.agent._generate_agent_prompt(order)
            self.assertIn('crash', prompt)
        
        asyncio.run(run_test())

    def test_generate_agent_prompt_no_notes(self):
        """Test prompt generation with no notes."""
        order = {
            'id': 789,
            'priority': 3,
            'description': 'Fix issue',
            'notes': '',
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            prompt = await self.agent._generate_agent_prompt(order)
            self.assertIsNotNone(prompt)
            self.assertIn('Work Order #789', prompt)
        
        asyncio.run(run_test())


class TestHealingAgentExtractErrorInfo(unittest.TestCase):
    """Test error extraction from notes."""

    def setUp(self):
        self.agent = HealingAgent({'enabled': True})

    def test_extract_error_info(self):
        """Test extracting error info from notes."""
        notes = """
**Category:** data_flow
**Component:** database
**Summary:** Invalid query
**Stack Trace:**
```
File "db.py", line 42
```
**Data Flow Path:**
input -> validate -> query
"""
        info = self.agent._extract_error_info(notes)
        self.assertEqual(info.get('category'), 'data_flow')
        self.assertEqual(info.get('component'), 'database')

    def test_extract_error_info_empty(self):
        """Test extracting from empty notes."""
        info = self.agent._extract_error_info('')
        self.assertEqual(info, {})

    def test_extract_error_info_none(self):
        """Test extracting from None notes."""
        info = self.agent._extract_error_info(None)
        self.assertEqual(info, {})

    def test_extract_stack_trace(self):
        """Test extracting stack trace."""
        notes = '**Stack Trace:**\n```\nTraceback line 1\nLine 2\n```\n**End**'
        info = self.agent._extract_error_info(notes)
        self.assertIn('stack_trace', info)


class TestHealingAgentWorkOrderHandling(unittest.TestCase):
    """Test work order handling."""

    def setUp(self):
        self.agent = HealingAgent({
            'enabled': True,
            'poll_interval': 1,
            'agent_timeout': 5,
            'max_concurrent_agents': 2
        })

    def test_get_active_sessions(self):
        """Test getting active sessions."""
        sessions = self.agent.get_active_sessions()
        self.assertEqual(len(sessions), 0)  # Initially empty

    def test_get_active_sessions_after_start(self):
        """Test sessions after starting agent."""
        session = HealingAgentSession(1, 's1')
        self.agent._active_sessions['s1'] = session
        
        sessions = self.agent.get_active_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['work_order_id'], 1)

    def test_get_stats(self):
        """Test get stats."""
        stats = self.agent.get_stats()
        self.assertIn('enabled', stats)
        self.assertIn('active_sessions', stats)
        self.assertIn('max_concurrent', stats)
        self.assertTrue(stats['enabled'])

    @patch.object(HealingAgent, '_update_work_order_status')
    def test_work_order_handled(self, mock_update):
        """Test work order handling flow with error-related work order."""
        order = {
            'id': 1,
            'priority': 2,
            'description': 'error: test failure in component',
            'notes': 'Test notes - exception raised during processing',
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            result = await self.agent._handle_work_order(order)
            # Without API client, will fail gracefully after detecting error keyword
            self.assertFalse(result)
            mock_update.assert_called()
        
        asyncio.run(run_test())
    
    @patch.object(HealingAgent, '_update_work_order_status')
    def test_work_order_skipped_non_error(self, mock_update):
        """Test non-error work order is skipped gracefully."""
        order = {
            'id': 2,
            'priority': 2,
            'description': 'feature request: add new endpoint',
            'notes': 'Enhancement proposal',
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            result = await self.agent._handle_work_order(order)
            # Non-error work orders are skipped and return True (handled)
            self.assertTrue(result)
            mock_update.assert_called()
        
        asyncio.run(run_test())

    @patch.object(HealingAgent, '_update_work_order_status')
    def test_work_order_escalation(self, mock_update):
        """Test escalation after threshold exceeded."""
        self.agent._escalation_threshold = 0  # Always escalate
        
        order = {
            'id': 2,
            'priority': 2,
            'description': 'failing',
            'notes': '',
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            result = await self.agent._handle_work_order(order)
            self.assertFalse(result)
        
        asyncio.run(run_test())


class TestHealingAgentEscalation(unittest.TestCase):
    """Test work order escalation."""

    def setUp(self):
        self.agent = HealingAgent({'enabled': True})

    @patch('sqlite3.connect')
    def test_escalate_work_order(self, mock_connect):
        """Test escalation updates priority."""
        mock_cursor = MagicMock()
        mock_connect.return_value.cursor.return_value = mock_cursor
        
        self.agent._escalate_work_order(99)
        # Should not crash
        self.assertTrue(mock_cursor.execute.called)


class TestHealingAgentUpdateStatus(unittest.TestCase):
    """Test work order status updates."""

    def setUp(self):
        self.agent = HealingAgent({'enabled': True})

    @patch('sqlite3.connect')
    def test_update_status(self, mock_connect):
        """Test status update without notes append."""
        mock_cursor = MagicMock()
        mock_connect.return_value.cursor.return_value = mock_cursor
        
        self.agent._update_work_order_status(1, 'completed')
        self.assertTrue(mock_cursor.execute.called)

    @patch('sqlite3.connect')
    def test_update_status_with_notes(self, mock_connect):
        """Test status update with notes append."""
        mock_cursor = MagicMock()
        mock_connect.return_value.cursor.return_value = mock_cursor
        
        self.agent._update_work_order_status(1, 'failed', notes_append='Fix failed')
        self.assertTrue(mock_cursor.execute.called)


class TestHealingAgentDBError(unittest.TestCase):
    """Test database error handling."""

    def setUp(self):
        self.agent = HealingAgent({'enabled': True})

    def test_get_pending_orders_db_error(self):
        """Test handling of database error during poll."""
        with patch('sqlite3.connect') as mock_connect:
            mock_connect.side_effect = Exception('Cannot connect')
            orders = self.agent._get_pending_work_orders(0)
            self.assertEqual(orders, [])  # Graceful failure

    def test_get_last_checked_db_error(self):
        """Test handling of DB error getting last checked."""
        with patch('sqlite3.connect') as mock_connect:
            mock_connect.side_effect = Exception('DB error')
            last_id = self.agent._get_last_checked_work_order()
            self.assertEqual(last_id, 0)


if __name__ == '__main__':
    unittest.main()
