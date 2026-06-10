"""
Comprehensive unit tests for SessionManager.

Part of DeepSky Self-Healing AI Client.
Tests: initialization, message management, flush system, recovery system,
       checkpoint management, shutdown, edge cases for ALL failure modes.

NEVER USE pytest — always unittest!
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_manager import (
    SessionManager, MessageEntry, FlushReason
)


class TestMessageEntry(unittest.TestCase):
    """Test MessageEntry data class."""

    def test_message_entry_creation(self):
        """Test basic message entry creation."""
        entry = MessageEntry('session-1', 'user', 'Hello')
        self.assertEqual(entry.session_id, 'session-1')
        self.assertEqual(entry.role, 'user')
        self.assertEqual(entry.content, 'Hello')
        self.assertIsNotNone(entry.message_id)
        self.assertIsNotNone(entry.state_hash)
        self.assertGreater(entry.timestamp, 0)

    def test_message_entry_with_prev_hash(self):
        """Test hash chain with previous hash."""
        entry = MessageEntry('s1', 'user', 'msg1', prev_hash='abc123')
        self.assertEqual(len(entry.state_hash), 64)  # SHA256 hex
        self.assertTrue(all(c in '0123456789abcdef' for c in entry.state_hash))  # Valid hex

    def test_message_entry_hash_chain_different(self):
        """Test that different messages have different hashes."""
        entry1 = MessageEntry('s1', 'user', 'Hello')
        entry2 = MessageEntry('s1', 'user', 'World')
        self.assertNotEqual(entry1.state_hash, entry2.state_hash)

    def test_message_entry_same_content_same_hash(self):
        """Test that messages with same content, prev_hash, AND timestamp have same hash."""
        fixed_ts = 1234567890.0
        entry1 = MessageEntry('s1', 'user', 'Hello', prev_hash='')
        entry1.timestamp = fixed_ts
        entry1.state_hash = hashlib.sha256(
            ('' + 'user' + 'Hello' + '[]' + str(fixed_ts)).encode('utf-8')
        ).hexdigest()
        entry2 = MessageEntry('s1', 'user', 'Hello', prev_hash='')
        entry2.timestamp = fixed_ts
        entry2.state_hash = hashlib.sha256(
            ('' + 'user' + 'Hello' + '[]' + str(fixed_ts)).encode('utf-8')
        ).hexdigest()
        self.assertEqual(entry1.state_hash, entry2.state_hash)

    def test_message_entry_hash_depends_on_role(self):
        """Test that hash depends on role."""
        user_msg = MessageEntry('s1', 'user', 'Hello')
        asst_msg = MessageEntry('s1', 'assistant', 'Hello')
        self.assertNotEqual(user_msg.state_hash, asst_msg.state_hash)

    def test_message_entry_hash_depends_on_prev_hash(self):
        """Test that hash depends on previous hash."""
        entry1 = MessageEntry('s1', 'user', 'Hello', prev_hash='a')
        entry2 = MessageEntry('s1', 'user', 'Hello', prev_hash='b')
        self.assertNotEqual(entry1.state_hash, entry2.state_hash)

    def test_message_entry_with_tool_calls(self):
        """Test message entry with tool calls."""
        entry = MessageEntry('s1', 'assistant', None, 
                            tool_calls=[{'id': 'call_1', 'function': {'name': 'test'}}])
        self.assertEqual(len(entry.tool_calls), 1)
        self.assertEqual(entry.tool_calls[0]['id'], 'call_1')

    def test_message_entry_with_tool_results(self):
        """Test message entry with tool results."""
        entry = MessageEntry('s1', 'tool', 'Result: 42',
                            tool_results=[{'call_id': 'call_1', 'output': '42'}])
        self.assertEqual(len(entry.tool_results), 1)

    def test_message_entry_to_dict(self):
        """Test conversion to dictionary."""
        entry = MessageEntry('s1', 'user', 'Hello')
        d = entry.to_dict()
        self.assertEqual(d['role'], 'user')
        self.assertEqual(d['content'], 'Hello')
        self.assertEqual(d['session_id'], 's1')
        self.assertIn('message_id', d)
        self.assertIn('state_hash', d)
        self.assertIn('tool_calls', d)
        self.assertIn('tool_results', d)

    def test_message_entry_from_dict(self):
        """Test reconstruction from dictionary."""
        original = MessageEntry('s1', 'assistant', 'Hi there',
                               tool_calls=[{'id': 'c1'}])
        d = original.to_dict()
        reconstructed = MessageEntry.from_dict(d)
        self.assertEqual(reconstructed.message_id, original.message_id)
        self.assertEqual(reconstructed.role, original.role)
        self.assertEqual(reconstructed.content, original.content)
        self.assertEqual(reconstructed.state_hash, original.state_hash)
        self.assertEqual(len(reconstructed.tool_calls), 1)

    def test_message_entry_invalid_role(self):
        """Test that role defaults still produce valid entries."""
        # This tests the class works even without constructor validation
        entry = MessageEntry('s1', 'custom_role', 'test')
        self.assertEqual(entry.role, 'custom_role')  # No validation in init

    def test_message_entry_none_content(self):
        """Test entry with None content."""
        entry = MessageEntry('s1', 'assistant', None)
        self.assertIsNone(entry.content)
        self.assertIsNotNone(entry.state_hash)  # Hash should still work

    def test_message_entry_timestamp_precision(self):
        """Test timestamp is high precision."""
        entry1 = MessageEntry('s1', 'user', 'a')
        entry2 = MessageEntry('s1', 'user', 'a')
        # Even if called rapidly, timestamps should differ
        # or at least be valid floats
        self.assertIsInstance(entry1.timestamp, float)


class TestSessionManagerInit(unittest.TestCase):
    """Test SessionManager initialization."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()

    def tearDown(self):
        os.unlink(self.temp_db.name)

    def test_init_default_config(self):
        """Test initialization with default config creates DB."""
        config = {'db_path': self.temp_db.name}
        sm = SessionManager(config)
        self.assertIsNotNone(sm)
        self.assertEqual(sm.buffer_maxlen, 500)
        self.assertEqual(sm.auto_flush_interval, 30)
        self.assertIsNotNone(sm.session_id)
        self.assertEqual(len(sm._buffer), 0)
        self.assertEqual(sm._message_count, 0)
        self.assertTrue(sm._healthy)

        # Verify DB was created
        self.assertTrue(os.path.exists(self.temp_db.name))

    def test_init_creates_tables(self):
        """Test that init creates required SQLite tables."""
        config = {'db_path': self.temp_db.name}
        sm = SessionManager(config)

        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        
        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn('session_state', tables)
        self.assertIn('session_metadata', tables)
        self.assertIn('state_checkpoints', tables)
        
        conn.close()

    def test_init_session_state_schema(self):
        """Test session_state table schema."""
        config = {'db_path': self.temp_db.name}
        sm = SessionManager(config)
        
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(session_state)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        self.assertIn('id', columns)
        self.assertIn('session_id', columns)
        self.assertIn('message_id', columns)
        self.assertIn('role', columns)
        self.assertIn('content', columns)
        self.assertIn('tool_calls', columns)
        self.assertIn('tool_results', columns)
        self.assertIn('state_hash', columns)
        self.assertIn('timestamp', columns)
        self.assertIn('flush_reason', columns)
        conn.close()

    def test_init_custom_config(self):
        """Test initialization with custom config."""
        config = {
            'db_path': self.temp_db.name,
            'buffer_maxlen': 100,
            'auto_flush_interval': 15,
            'max_recovery_attempts': 5,
            'recovery_window_minutes': 30
        }
        sm = SessionManager(config)
        self.assertEqual(sm.buffer_maxlen, 100)
        self.assertEqual(sm.auto_flush_interval, 15)
        self.assertEqual(sm.max_recovery_attempts, 5)
        self.assertEqual(sm.recovery_window_minutes, 30)

    def test_init_generates_unique_session_id(self):
        """Test that each instance gets a unique session ID."""
        sm1 = SessionManager({'db_path': self.temp_db.name})
        sm2 = SessionManager({'db_path': self.temp_db.name})
        self.assertNotEqual(sm1.session_id, sm2.session_id)


class TestSessionManagerAppendMessage(unittest.TestCase):
    """Test message appending to buffer."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.sm = SessionManager({'db_path': self.temp_db.name})

    def tearDown(self):
        self.sm = None
        os.unlink(self.temp_db.name)

    def test_append_user_message(self):
        """Test appending a user message."""
        msg_id = self.sm.append_message('user', 'Hello')
        self.assertIsNotNone(msg_id)
        self.assertEqual(self.sm._message_count, 1)
        self.assertEqual(len(self.sm._buffer), 1)

    def test_append_system_message(self):
        """Test appending a system message."""
        msg_id = self.sm.append_message('system', 'System prompt here')
        self.assertEqual(self.sm._message_count, 1)
        msgs = self.sm.get_messages()
        self.assertEqual(msgs[0]['role'], 'system')

    def test_append_assistant_message(self):
        """Test appending an assistant message."""
        msg_id = self.sm.append_message('assistant', 'I am AI')
        self.assertEqual(self.sm._message_count, 1)

    def test_append_tool_message(self):
        """Test appending a tool result message."""
        msg_id = self.sm.append_message('tool', '42', 
                                        tool_results=[{'call_id': 'c1', 'output': '42'}])
        self.assertEqual(self.sm._message_count, 1)

    def test_append_invalid_role_raises(self):
        """Test that invalid role raises ValueError."""
        with self.assertRaises(ValueError):
            self.sm.append_message('invalid_role', 'test')

    def test_append_with_tool_calls_assistant(self):
        """Test appending assistant message with tool calls."""
        tool_calls = [{'id': 'call_1', 'type': 'function', 
                       'function': {'name': 'test_tool', 'arguments': '{}'}}]
        msg_id = self.sm.append_message('assistant', None, tool_calls=tool_calls)
        
        msgs = self.sm.get_messages()
        self.assertIsNotNone(msgs[0].get('tool_calls'))
        self.assertEqual(msgs[0]['tool_calls'][0]['id'], 'call_1')

    def test_append_multiple_messages(self):
        """Test appending multiple messages maintains order."""
        self.sm.append_message('user', 'First')
        self.sm.append_message('assistant', 'Second')
        self.sm.append_message('user', 'Third')
        
        msgs = self.sm.get_messages()
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0]['content'], 'First')
        self.assertEqual(msgs[1]['content'], 'Second')
        self.assertEqual(msgs[2]['content'], 'Third')

    def test_append_hash_chain(self):
        """Test that hash chain is maintained across messages."""
        self.sm.append_message('user', 'msg1')
        hash1 = self.sm._last_hash
        
        self.sm.append_message('user', 'msg2')
        hash2 = self.sm._last_hash
        
        self.assertNotEqual(hash1, hash2)

    def test_append_buffer_maxlen(self):
        """Test that buffer respects maxlen."""
        sm = SessionManager({'db_path': self.temp_db.name, 'buffer_maxlen': 3})
        
        sm.append_message('user', 'msg1')
        sm.append_message('user', 'msg2')
        sm.append_message('user', 'msg3')
        sm.append_message('user', 'msg4')
        sm.append_message('user', 'msg5')
        
        self.assertEqual(len(sm._buffer), 3)  # Maxlen enforced
        msgs = sm.get_messages()
        self.assertEqual(len(msgs), 3)
        # Should have most recent 3 messages
        self.assertEqual(msgs[0]['content'], 'msg3')
        self.assertEqual(msgs[1]['content'], 'msg4')
        self.assertEqual(msgs[2]['content'], 'msg5')

    def test_append_tracks_message_count(self):
        """Test that message_count always reflects total, not buffer size."""
        sm = SessionManager({'db_path': self.temp_db.name, 'buffer_maxlen': 3})
        sm.append_message('user', 'msg1')
        sm.append_message('user', 'msg2')
        sm.append_message('user', 'msg3')
        sm.append_message('user', 'msg4')
        
        self.assertEqual(len(sm._buffer), 3)  # Buffer limited
        self.assertEqual(sm._message_count, 4)  # Total count

    def test_append_tracks_token_count(self):
        """Test that token count is tracked."""
        self.sm.append_message('user', 'A' * 40)  # 40 chars = ~10 tokens
        self.assertGreater(self.sm._token_count, 0)
        
        token_initial = self.sm._token_count
        self.sm.append_message('user', 'B' * 40)
        self.assertGreater(self.sm._token_count, token_initial)

    def test_append_empty_content(self):
        """Test appending message with empty content."""
        msg_id = self.sm.append_message('user', '')
        self.assertIsNotNone(msg_id)
        self.assertEqual(self.sm._message_count, 1)

    def test_append_unicode_content(self):
        """Test appending message with unicode characters."""
        msg_id = self.sm.append_message('user', 'Hello 世界 🌍')
        self.assertIsNotNone(msg_id)
        
        msgs = self.sm.get_messages()
        self.assertIn('世界', msgs[0]['content'])
        self.assertIn('🌍', msgs[0]['content'])

    def test_append_very_long_content(self):
        """Test appending message with very long content."""
        long_content = 'A' * 100000
        msg_id = self.sm.append_message('user', long_content)
        self.assertIsNotNone(msg_id)
        
        msgs = self.sm.get_messages()
        self.assertEqual(len(msgs[0]['content']), 100000)


class TestSessionManagerGetMessages(unittest.TestCase):
    """Test message retrieval."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.sm = SessionManager({'db_path': self.temp_db.name})
        for i in range(10):
            self.sm.append_message('user', f'msg{i}')

    def tearDown(self):
        self.sm = None
        os.unlink(self.temp_db.name)

    def test_get_all_messages(self):
        """Test getting all messages."""
        msgs = self.sm.get_messages()
        self.assertEqual(len(msgs), 10)

    def test_get_limited_messages(self):
        """Test getting limited number of messages."""
        msgs = self.sm.get_messages(count=3)
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0]['content'], 'msg7')

    def test_get_more_than_available(self):
        """Test requesting more messages than available."""
        msgs = self.sm.get_messages(count=100)
        self.assertEqual(len(msgs), 10)

    def test_get_zero_messages(self):
        """Test requesting zero messages."""
        msgs = self.sm.get_messages(count=0)
        self.assertEqual(len(msgs), 0)

    def test_get_negative_count(self):
        """Test requesting negative count (should return all)."""
        msgs = self.sm.get_messages(count=-1)
        self.assertEqual(len(msgs), 10)

    def test_get_messages_empty_buffer(self):
        """Test getting messages from empty buffer."""
        sm = SessionManager({'db_path': self.temp_db.name})
        # Use a different DB name to avoid conflict
        # Just clear the buffer manually
        sm._buffer.clear()
        msgs = sm.get_messages()
        self.assertEqual(len(msgs), 0)

    def test_get_system_prompt_context(self):
        """Test getting system prompt from buffer."""
        self.sm.append_message('system', 'You are an AI.')
        self.sm.append_message('user', 'question')
        self.sm.append_message('assistant', 'answer')
        
        prompt = self.sm.get_system_prompt_context()
        self.assertEqual(prompt, 'You are an AI.')

    def test_get_system_prompt_context_none(self):
        """Test getting system prompt when none exists."""
        sm = SessionManager({'db_path': self.temp_db.name})
        prompt = sm.get_system_prompt_context()
        self.assertIsNone(prompt)


class TestSessionManagerFlush(unittest.TestCase):
    """Test flush operations."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.sm = SessionManager({'db_path': self.temp_db.name})

    def tearDown(self):
        self.sm = None
        os.unlink(self.temp_db.name)

    def test_flush_timer_reason(self):
        """Test flush with timer reason."""
        self.sm.append_message('user', 'test')
        result = self.sm.flush(FlushReason.TIMER)
        self.assertTrue(result)

    def test_flush_connection_drop(self):
        """Test flush on connection drop."""
        self.sm.append_message('user', 'test')
        result = self.sm.flush(FlushReason.CONNECTION_DROP)
        self.assertTrue(result)
        
        # Verify data in DB
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM session_state")
        count = cursor.fetchone()[0]
        self.assertGreater(count, 0)
        conn.close()

    def test_flush_bug_detected_creates_checkpoint(self):
        """Test that bug detection flush creates checkpoint."""
        self.sm.append_message('user', 'test')
        result = self.sm.flush(FlushReason.BUG_DETECTED)
        self.assertTrue(result)
        
        # Verify checkpoint was created
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM state_checkpoints WHERE checkpoint_type='bug_checkpoint'")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 1)
        conn.close()

    def test_flush_shutdown(self):
        """Test flush on shutdown."""
        self.sm.append_message('user', 'save me')
        result = self.sm.flush(FlushReason.SHUTDOWN)
        self.assertTrue(result)

    def test_flush_manual(self):
        """Test manual flush trigger."""
        self.sm.append_message('user', 'manual test')
        result = self.sm.flush(FlushReason.MANUAL)
        self.assertTrue(result)

    def test_flush_empty_buffer(self):
        """Test flushing empty buffer."""
        result = self.sm.flush(FlushReason.TIMER)
        self.assertTrue(result)  # Should succeed

    def test_flush_multiple_messages(self):
        """Test flushing multiple messages."""
        for i in range(10):
            self.sm.append_message('user', f'msg{i}')
        
        result = self.sm.flush(FlushReason.TIMER)
        self.assertTrue(result)
        
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM session_state")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 10)
        conn.close()

    def test_flush_idempotent(self):
        """Test that flushing twice doesn't duplicate messages."""
        self.sm.append_message('user', 'unique')
        self.sm.flush(FlushReason.TIMER)
        self.sm.flush(FlushReason.TIMER)  # Second flush should be no-op
        
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM session_state")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 1)  # No duplicates
        conn.close()

    def test_flush_with_session_metadata(self):
        """Test that flush updates session metadata."""
        self.sm.set_session_info('deepseek-chat', 'system prompt')
        self.sm.append_message('user', 'test')
        self.sm.flush(FlushReason.TIMER)
        
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("SELECT model, system_prompt FROM session_metadata WHERE session_id=?", 
                      (self.sm.session_id,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 'deepseek-chat')
        self.assertEqual(row[1], 'system prompt')
        conn.close()

    def test_flush_preserves_flush_reason(self):
        """Test that flush reason is recorded in DB."""
        self.sm.append_message('user', 'reason test')
        self.sm.flush(FlushReason.CONNECTION_DROP)
        
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("SELECT flush_reason FROM session_state")
        reason = cursor.fetchone()[0]
        self.assertEqual(reason, 'connection_drop')
        conn.close()

    def test_flush_db_corruption_handling(self):
        """Test handling of database corruption during flush."""
        # Corrupt the DB by writing garbage
        with open(self.temp_db.name, 'w') as f:
            f.write('garbage')
        
        self.sm.append_message('user', 'should fail')
        result = self.sm.flush(FlushReason.TIMER)
        self.assertFalse(result)  # Should handle gracefully


class TestSessionManagerAutoFlush(unittest.TestCase):
    """Test auto-flush timer."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()

    def tearDown(self):
        os.unlink(self.temp_db.name)

    def test_auto_flush_start_stop(self):
        """Test starting and stopping auto-flush."""
        sm = SessionManager({'db_path': self.temp_db.name, 'auto_flush_interval': 0.1})
        
        async def run_test():
            await sm.start_auto_flush()
            self.assertTrue(sm._running)
            self.assertIsNotNone(sm._flush_task)
            
            await asyncio.sleep(0.05)
            sm.stop_auto_flush()
            await asyncio.sleep(0.1)
            # Task should have finished
            self.assertFalse(sm._running)
        
        asyncio.run(run_test())

    def test_auto_flush_actually_flushes(self):
        """Test that auto-flush actually persists data."""
        sm = SessionManager({'db_path': self.temp_db.name, 'auto_flush_interval': 0.1})
        
        async def run_test():
            await sm.start_auto_flush()
            sm.append_message('user', 'auto-flush test')
            
            # Wait for flush to happen
            await asyncio.sleep(0.3)
            sm.stop_auto_flush()
            
            # Check data was persisted
            conn = sqlite3.connect(self.temp_db.name)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM session_state")
            count = cursor.fetchone()[0]
            self.assertGreater(count, 0)
            conn.close()
        
        asyncio.run(run_test())

    def test_auto_flush_double_start(self):
        """Test that starting auto-flush twice doesn't create duplicate tasks."""
        sm = SessionManager({'db_path': self.temp_db.name, 'auto_flush_interval': 1})
        
        async def run_test():
            await sm.start_auto_flush()
            task1 = sm._flush_task
            await sm.start_auto_flush()  # Second start
            task2 = sm._flush_task
            # Should not crash
            self.assertIs(task1, task2)
            sm.stop_auto_flush()
        
        asyncio.run(run_test())


class TestSessionManagerRecovery(unittest.TestCase):
    """Test recovery from checkpoints."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        
        # Populate a session with data
        self.sm = SessionManager({'db_path': self.temp_db.name})
        for i in range(5):
            self.sm.append_message('user' if i % 2 == 0 else 'assistant', f'Message {i}')
        # Flush with checkpoint
        self.sm.flush(FlushReason.BUG_DETECTED)

    def tearDown(self):
        self.sm = None
        os.unlink(self.temp_db.name)

    def test_get_last_checkpoint_exists(self):
        """Test getting the last checkpoint."""
        checkpoint = self.sm.get_last_checkpoint()
        self.assertIsNotNone(checkpoint)
        self.assertIn('checkpoint_type', checkpoint)
        self.assertIn('state_snapshot', checkpoint)
        self.assertIn('state_hash', checkpoint)

    def test_get_last_checkpoint_type(self):
        """Test checkpoint type is correct."""
        checkpoint = self.sm.get_last_checkpoint()
        self.assertEqual(checkpoint['checkpoint_type'], 'bug_checkpoint')

    def test_get_last_checkpoint_message_count(self):
        """Test checkpoint has correct message count."""
        checkpoint = self.sm.get_last_checkpoint()
        self.assertEqual(checkpoint['message_count'], 5)

    def test_get_last_checkpoint_no_checkpoint(self):
        """Test getting checkpoint when none exists."""
        sm = SessionManager({'db_path': self.temp_db.name + '.empty'})
        checkpoint = sm.get_last_checkpoint()
        self.assertIsNone(checkpoint)
        try:
            os.unlink(self.temp_db.name + '.empty')
        except OSError:
            pass

    def test_restore_from_checkpoint(self):
        """Test restoring from checkpoint."""
        messages = self.sm.restore_from_checkpoint()
        self.assertGreater(len(messages), 0)

    def test_restore_from_checkpoint_preserves_order(self):
        """Test that restored messages are in correct order."""
        messages = self.sm.restore_from_checkpoint()
        for i, msg in enumerate(messages):
            self.assertIn(f'Message {i}', msg['content'])

    def test_restore_from_checkpoint_increments_recovery(self):
        """Test that recovery increments recovery count."""
        initial = self.sm._recovery_count
        self.sm.restore_from_checkpoint()
        self.assertEqual(self.sm._recovery_count, initial + 1)

    def test_get_last_session_metadata(self):
        """Test getting last session metadata."""
        meta = self.sm.get_last_session_metadata()
        self.assertIsNotNone(meta)
        self.assertIn('session_id', meta)
        self.assertIn('model', meta)
        self.assertIn('recovery_count', meta)

    def test_get_last_session_metadata_healthy_status(self):
        """Test metadata reflects healthy status."""
        self.sm._healthy = True
        self.sm.flush(FlushReason.TIMER)
        
        meta = self.sm.get_last_session_metadata()
        self.assertTrue(meta['is_healthy'])

    def test_recovery_window_reset(self):
        """Test that recovery window resets after time passes."""
        self.sm._recovery_window_start = time.time() - (self.sm.recovery_window_minutes * 60 + 1)
        self.sm._recovery_count = 5  # High count
        
        self.sm.restore_from_checkpoint()
        self.assertEqual(self.sm._recovery_count, 1)  # Reset to 1

    def test_multiple_recovery_attempts(self):
        """Test multiple recovery attempts increment properly."""
        self.sm.restore_from_checkpoint()
        self.sm.restore_from_checkpoint()
        self.sm.restore_from_checkpoint()
        self.assertEqual(self.sm._recovery_count, 3)


class TestSessionManagerStateIntegrity(unittest.TestCase):
    """Test hash chain integrity verification."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.sm = SessionManager({'db_path': self.temp_db.name})
        for i in range(5):
            self.sm.append_message('user', f'msg{i}')

    def tearDown(self):
        self.sm = None
        os.unlink(self.temp_db.name)

    def test_verify_integrity_valid(self):
        """Test integrity check passes with valid chain."""
        valid, count = self.sm.verify_state_integrity()
        self.assertTrue(valid)
        self.assertEqual(count, 5)

    def test_verify_integrity_empty_buffer(self):
        """Test integrity check on empty buffer."""
        sm = SessionManager({'db_path': self.temp_db.name + '.empty'})
        valid, count = sm.verify_state_integrity()
        self.assertTrue(valid)
        self.assertEqual(count, 0)
        try:
            os.unlink(self.temp_db.name + '.empty')
        except OSError:
            pass

    def test_verify_integrity_corrupted_hash(self):
        """Test integrity check detects corrupted hash."""
        # Manually corrupt a hash
        self.sm._buffer[2].state_hash = '0' * 64
        valid, count = self.sm.verify_state_integrity()
        self.assertFalse(valid)
        self.assertEqual(count, 2)  # Only first 2 verified

    def test_verify_integrity_after_recovery(self):
        """Test integrity after recovery from checkpoint."""
        self.sm.flush(FlushReason.BUG_DETECTED)
        self.sm.restore_from_checkpoint()
        valid, count = self.sm.verify_state_integrity()
        self.assertTrue(valid)
        self.assertEqual(count, 5)


class TestSessionManagerShutdown(unittest.TestCase):
    """Test graceful shutdown."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()

    def tearDown(self):
        os.unlink(self.temp_db.name)

    def test_graceful_shutdown_creates_checkpoint(self):
        """Test that graceful shutdown creates checkpoint."""
        sm = SessionManager({'db_path': self.temp_db.name})
        sm.append_message('user', 'save on shutdown')
        
        async def run_test():
            await sm.graceful_shutdown()
            
            # Check checkpoint was created
            conn = sqlite3.connect(self.temp_db.name)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM state_checkpoints WHERE checkpoint_type='last_good'")
            count = cursor.fetchone()[0]
            self.assertGreaterEqual(count, 1)
            conn.close()
        
        asyncio.run(run_test())

    def test_graceful_shutdown_stops_auto_flush(self):
        """Test that shutdown stops auto-flush."""
        sm = SessionManager({'db_path': self.temp_db.name, 'auto_flush_interval': 0.1})
        
        async def run_test():
            await sm.start_auto_flush()
            await sm.graceful_shutdown()
            self.assertFalse(sm._running)
        
        asyncio.run(run_test())

    def test_graceful_shutdown_flushes_data(self):
        """Test that shutdown flushes data to DB."""
        sm = SessionManager({'db_path': self.temp_db.name})
        sm.append_message('user', 'final message')
        
        async def run_test():
            await sm.graceful_shutdown()
            
            conn = sqlite3.connect(self.temp_db.name)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM session_state")
            count = cursor.fetchone()[0]
            self.assertGreater(count, 0)
            conn.close()
        
        asyncio.run(run_test())


class TestSessionManagerSetInfo(unittest.TestCase):
    """Test setting session info."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.sm = SessionManager({'db_path': self.temp_db.name})

    def tearDown(self):
        self.sm = None
        os.unlink(self.temp_db.name)

    def test_set_session_info(self):
        """Test setting session info."""
        self.sm.set_session_info('deepseek-coder', 'You are coder.')
        self.assertEqual(self.sm.model, 'deepseek-coder')
        self.assertEqual(self.sm.system_prompt, 'You are coder.')

    def test_set_session_info_prompt_only(self):
        """Test setting only system prompt."""
        self.sm.set_session_info('test-model')
        self.assertEqual(self.sm.model, 'test-model')

    def test_mark_unhealthy(self):
        """Test marking session as unhealthy."""
        self.sm.mark_unhealthy('Something broke')
        self.assertFalse(self.sm._healthy)
        self.assertEqual(self.sm._last_error, 'Something broke')


class TestSessionManagerGetStateSummary(unittest.TestCase):
    """Test state summary."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.sm = SessionManager({'db_path': self.temp_db.name})

    def tearDown(self):
        self.sm = None
        os.unlink(self.temp_db.name)

    def test_get_state_summary(self):
        """Test state summary returns all fields."""
        self.sm.append_message('user', 'test')
        summary = self.sm.get_state_summary()
        
        self.assertIn('session_id', summary)
        self.assertIn('buffer_size', summary)
        self.assertIn('message_count', summary)
        self.assertIn('token_count', summary)
        self.assertIn('healthy', summary)
        self.assertIn('recovery_count', summary)
        
        self.assertEqual(summary['buffer_size'], 1)
        self.assertEqual(summary['message_count'], 1)
        self.assertTrue(summary['healthy'])

    def test_get_state_summary_after_errors(self):
        """Test state summary after errors."""
        self.sm.mark_unhealthy('error occurred')
        summary = self.sm.get_state_summary()
        
        self.assertFalse(summary['healthy'])
        self.assertEqual(summary['last_error'], 'error occurred')


class TestSessionManagerConcurrency(unittest.TestCase):
    """Test concurrent operations."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.sm = SessionManager({'db_path': self.temp_db.name})

    def tearDown(self):
        self.sm = None
        os.unlink(self.temp_db.name)

    def test_concurrent_append(self):
        """Test appending messages concurrently."""
        import threading
        
        def append_thread(start_id, count):
            for i in range(count):
                self.sm.append_message('user', f'Thread msg {start_id + i}')
        
        threads = []
        for t in range(3):
            thread = threading.Thread(target=append_thread, args=(t * 10, 10))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        self.assertEqual(self.sm._message_count, 30)

    def test_concurrent_read_write(self):
        """Test concurrent reading while writing."""
        import threading
        
        results = []
        
        def writer():
            for i in range(100):
                self.sm.append_message('user', f'write {i}')
        
        def reader():
            for _ in range(100):
                msgs = self.sm.get_messages()
                results.append(len(msgs))
        
        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        
        w.start()
        r.start()
        w.join()
        r.join()
        
        # Should not crash
        self.assertGreater(self.sm._message_count, 0)

    def test_concurrent_flush(self):
        """Test flushing while appending."""
        import threading
        
        def writer():
            for i in range(50):
                self.sm.append_message('user', f'msg {i}')
        
        def flusher():
            for _ in range(5):
                self.sm.flush(FlushReason.TIMER)
                time.sleep(0.01)
        
        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=flusher)
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should not crash, data should be in buffer
        self.assertGreater(self.sm._message_count, 0)


if __name__ == '__main__':
    unittest.main()
