"""
Session Manager — Memory buffer + SQLite persistence for session continuity.

Part of DeepSky Self-Healing AI Client (YuniScript).
Spec: DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md (in YuniScripts base)

Provides:
- In-memory ring buffer (deque, maxlen=500) for fast access
- SQLite persistence for crash recovery
- Auto-flush timer (asyncio, configurable interval)
- SHA256 hash chain for state integrity verification
- Checkpoint system for recovery from known-good states
- Multiple flush triggers: timer, connection_drop, bug_detected, shutdown, manual
"""

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from collections import deque
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum, auto

logger = logging.getLogger(__name__)


class FlushReason(Enum):
    """Reasons for flushing buffer to persistent storage."""
    TIMER = 'timer'
    CONNECTION_DROP = 'connection_drop'
    BUG_DETECTED = 'bug_detected'
    SHUTDOWN = 'shutdown'
    MANUAL = 'manual'
    CHECKPOINT = "checkpoint"
    HEALING_AGENT_SPAWNED = "healing_agent_spawned"


class MessageEntry:
    """A single message/event in the session buffer."""
    
    __slots__ = ('message_id', 'session_id', 'role', 'content', 'tool_calls',
                 'tool_results', 'state_hash', 'timestamp', 'flush_reason')
    
    def __init__(self, session_id: str, role: str, content: Optional[str] = None,
                 tool_calls: Optional[List] = None, tool_results: Optional[List] = None,
                 prev_hash: str = '', message_id: Optional[str] = None):
        self.message_id = message_id or str(uuid.uuid4())
        self.session_id = session_id
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []
        self.tool_results = tool_results or []
        self.timestamp = time.time()
        self.flush_reason = None
        
        # Compute hash chain
        safe_tool_calls = tool_calls or []
        hash_input = prev_hash + role + (content or '') + json.dumps(safe_tool_calls, sort_keys=True) + str(self.timestamp)
        self.state_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
    
    def to_dict(self) -> Dict:
        return {
            'message_id': self.message_id,
            'session_id': self.session_id,
            'role': self.role,
            'content': self.content,
            'tool_calls': json.dumps(self.tool_calls),
            'tool_results': json.dumps(self.tool_results),
            'state_hash': self.state_hash,
            'timestamp': self.timestamp,
            'flush_reason': self.flush_reason
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'MessageEntry':
        entry = cls(
            session_id=d['session_id'],
            role=d['role'],
            content=d.get('content'),
            tool_calls=json.loads(d.get('tool_calls', '[]')),
            tool_results=json.loads(d.get('tool_results', '[]')),
            message_id=d.get('message_id')
        )
        entry.state_hash = d.get('state_hash', entry.state_hash)
        entry.timestamp = d.get('timestamp', time.time())
        entry.flush_reason = d.get('flush_reason')
        return entry


class SessionManager:
    """
    Manages session state with memory buffer and SQLite persistence.
    
    The memory buffer (deque) stores the latest N messages for fast access.
    An auto-flush timer periodically persists the buffer to SQLite.
    On flush triggers (connection drop, bug detection, shutdown), the full
    buffer state is written immediately.
    
    On recovery, the last known good checkpoint is loaded, hash chain is
    verified, and messages are replayed to maintain API continuity.
    """

    def __init__(self, config: Dict[str, Any]):
        self.buffer_maxlen = config.get('buffer_maxlen', 500)
        self.auto_flush_interval = config.get('auto_flush_interval', 30)
        self.db_path = config.get('db_path', 'session_state.db')
        self.hash_algorithm = config.get('hash_algorithm', 'sha256')
        self.max_recovery_attempts = config.get('max_recovery_attempts', 3)
        self.recovery_window_minutes = config.get('recovery_window_minutes', 60)
        
        # Session identity
        self.session_id = str(uuid.uuid4())
        self.model = None
        self.system_prompt = None
        
        # Memory buffer
        self._buffer = deque(maxlen=self.buffer_maxlen)
        self._buffer_lock = threading.RLock()
        self._last_hash = ''
        self._message_count = 0
        self._token_count = 0
        
        # Health tracking
        self._healthy = True
        self._last_error = None
        self._recovery_count = 0
        self._recovery_window_start = time.time()
        
        # Auto-flush task
        self._flush_task = None
        self._running = False
        
        # Initialize database
        self._init_db()
    
    def _init_db(self):
        """Create SQLite database and schema if not exists."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.executescript('''
                CREATE TABLE IF NOT EXISTS session_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    message_id TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
                    content TEXT,
                    tool_calls TEXT,
                    tool_results TEXT,
                    state_hash TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    flush_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_session_state_session 
                    ON session_state(session_id, timestamp);
                
                CREATE INDEX IF NOT EXISTS idx_session_state_message
                    ON session_state(message_id);
                
                CREATE TABLE IF NOT EXISTS session_metadata (
                    session_id TEXT PRIMARY KEY,
                    model TEXT,
                    system_prompt TEXT,
                    token_count INTEGER DEFAULT 0,
                    last_active REAL,
                    is_healthy INTEGER DEFAULT 1,
                    last_error TEXT,
                    recovery_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS state_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    checkpoint_type TEXT NOT NULL CHECK(
                        checkpoint_type IN ('last_good','bug_checkpoint','recovery','startup')
                    ),
                    state_snapshot TEXT NOT NULL,
                    state_hash TEXT,
                    message_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_checkpoints_session
                    ON state_checkpoints(session_id, created_at DESC);
            ''')
            conn.commit()
        finally:
            conn.close()
    
    # ─── Message Management ──────────────────────────────────────
    
    def append_message(self, role: str, content: Optional[str] = None,
                       tool_calls: Optional[List] = None,
                       tool_results: Optional[List] = None) -> str:
        """
        Append a message to the memory buffer.
        
        Args:
            role: 'system', 'user', 'assistant', or 'tool'
            content: Message text content
            tool_calls: List of tool call dicts (for assistant messages)
            tool_results: List of tool result dicts (for tool messages)
            
        Returns:
            message_id UUID string
        """
        if role not in ('system', 'user', 'assistant', 'tool'):
            raise ValueError(f"Invalid role: {role}. Must be system/user/assistant/tool")
        
        with self._buffer_lock:
            entry = MessageEntry(
                session_id=self.session_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_results=tool_results,
                prev_hash=self._last_hash
            )
            self._buffer.append(entry)
            self._last_hash = entry.state_hash
            self._message_count += 1
            
            # Track token usage (rough estimate: len/4 for tokens)
            if content:
                self._token_count += len(content) // 4
        
        return entry.message_id
    
    def get_messages(self, count: Optional[int] = None) -> List[Dict]:
        """
        Get recent messages from buffer for API calls.
        
        Args:
            count: Number of most recent messages (None = all)
            
        Returns:
            List of message dicts formatted for API
        """
        with self._buffer_lock:
            buf = list(self._buffer)
        
        if count is not None and count >= 0:
            if count == 0:
                return []
            if count < len(buf):
                buf = buf[-count:]
        
        return [{
            'role': e.role,
            'content': e.content,
            'tool_calls': e.tool_calls if e.tool_calls else None
        } for e in buf if e.role != 'system' or e.content]
    
    def get_system_prompt_context(self) -> Optional[str]:
        """Get the system prompt if it exists in buffer."""
        with self._buffer_lock:
            for e in self._buffer:
                if e.role == 'system' and e.content:
                    return e.content
        return self.system_prompt
    
    # ─── Flush System ────────────────────────────────────────────
    
    def _to_flush_reason(self, reason) -> FlushReason:
        """Convert string or FlushReason to FlushReason enum.
        
        Handles both FlushReason enum objects and string values.
        Strings that don't match any enum value default to MANUAL.
        """
        if isinstance(reason, FlushReason):
            return reason
        
        if isinstance(reason, str):
            for member in FlushReason:
                if member.value == reason or member.name == reason.upper():
                    return member
            return FlushReason.MANUAL
        return FlushReason.MANUAL

    def flush(self, reason: FlushReason = FlushReason.TIMER) -> bool:
        reason = self._to_flush_reason(reason)
        """
        Flush memory buffer to SQLite.
        
        Args:
            reason: Why the flush is being triggered
            
        Returns:
            True if flush succeeded
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            with self._buffer_lock:
                # Collect all unflushed entries (those without flush_reason)
                # We need to track which ones have been flushed
                entries_to_flush = [e for e in self._buffer if not e.flush_reason]
                
                if not entries_to_flush and reason != FlushReason.SHUTDOWN:
                    conn.close()
                    return True
                
                for entry in entries_to_flush:
                    entry.flush_reason = reason.value
                    d = entry.to_dict()
                    try:
                        cursor.execute('''
                            INSERT OR REPLACE INTO session_state
                            (session_id, message_id, role, content, tool_calls, 
                             tool_results, state_hash, timestamp, flush_reason)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            d['session_id'], d['message_id'], d['role'],
                            d['content'], d['tool_calls'], d['tool_results'],
                            d['state_hash'], d['timestamp'], d['flush_reason']
                        ))
                    except sqlite3.IntegrityError:
                        # Message already exists — skip
                        pass
                
                # Update session metadata
                cursor.execute('''
                    INSERT OR REPLACE INTO session_metadata
                    (session_id, model, system_prompt, token_count, last_active, is_healthy, recovery_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    self.session_id, self.model, self.system_prompt,
                    self._token_count, time.time(),
                    1 if self._healthy else 0, self._recovery_count
                ))
                
                # If bug detected, also create a checkpoint
                if reason == FlushReason.BUG_DETECTED:
                    self._create_checkpoint(cursor, 'bug_checkpoint')
                
                conn.commit()
            
            flushed_count = len(entries_to_flush)
            if flushed_count > 0:
                logger.debug(f"Flushed {flushed_count} entries to SQLite (reason: {reason.value})")
            
            conn.close()
            return True
            
        except sqlite3.Error as e:
            logger.error(f"SQLite flush failed: {e}")
            self._healthy = False
            self._last_error = str(e)
            return False
    
    def _create_checkpoint(self, cursor, checkpoint_type: str):
        """Create a state checkpoint for recovery."""
        with self._buffer_lock:
            snapshot = json.dumps([e.to_dict() for e in self._buffer])
            count = len(self._buffer)
        
        cursor.execute('''
            INSERT INTO state_checkpoints
            (session_id, checkpoint_type, state_snapshot, state_hash, message_count)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            self.session_id, checkpoint_type, snapshot,
            self._last_hash, count
        ))
    
    async def _auto_flush_loop(self):
        """Background task that periodically flushes buffer to SQLite."""
        while self._running:
            await asyncio.sleep(self.auto_flush_interval)
            if self._running:
                self.flush(FlushReason.TIMER)
    
    async def start_auto_flush(self):
        """Start the background auto-flush task.
        
        If already running, returns the existing task without creating a new one.
        """
        if self._running and self._flush_task is not None and not self._flush_task.done():
            return self._flush_task
        self._running = True
        self._flush_task = asyncio.create_task(self._auto_flush_loop())
        logger.info(f"Auto-flush started (interval: {self.auto_flush_interval}s)")
    
    def stop_auto_flush(self):
        """Stop the background auto-flush task."""
        self._running = False
    
    # ─── Recovery System ─────────────────────────────────────────
    
    def get_last_checkpoint(self) -> Optional[Dict]:
        """
        Get the most recent 'last_good' or 'bug_checkpoint' checkpoint.
        
        Returns:
            Checkpoint dict or None if no checkpoint exists
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, session_id, checkpoint_type, state_snapshot, state_hash, 
                       message_count, created_at
                FROM state_checkpoints
                WHERE checkpoint_type IN ('last_good', 'bug_checkpoint')
                ORDER BY created_at DESC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'session_id': row[1],
                    'checkpoint_type': row[2],
                    'state_snapshot': row[3],
                    'state_hash': row[4],
                    'message_count': row[5],
                    'created_at': row[6]
                }
            return None
        finally:
            conn.close()
    
    def get_last_session_metadata(self) -> Optional[Dict]:
        """Get the most recent session metadata for recovery."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT session_id, model, system_prompt, token_count, last_active,
                       is_healthy, recovery_count, created_at
                FROM session_metadata
                ORDER BY last_active DESC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            if row:
                return {
                    'session_id': row[0],
                    'model': row[1],
                    'system_prompt': row[2],
                    'token_count': row[3],
                    'last_active': row[4],
                    'is_healthy': bool(row[5]),
                    'recovery_count': row[6],
                    'created_at': row[7]
                }
            return None
        finally:
            conn.close()
    
    def restore_from_checkpoint(self, checkpoint_id: Optional[int] = None) -> List[Dict]:
        """
        Restore memory buffer from a checkpoint.
        
        Args:
            checkpoint_id: Specific checkpoint ID, or None for most recent
            
        Returns:
            List of message dicts formatted for API resumption
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            if checkpoint_id:
                cursor.execute('SELECT state_snapshot, state_hash, session_id FROM state_checkpoints WHERE id = ?',
                             (checkpoint_id,))
            else:
                cursor.execute('''
                    SELECT state_snapshot, state_hash, session_id FROM state_checkpoints
                    WHERE checkpoint_type IN ('last_good', 'bug_checkpoint')
                    ORDER BY created_at DESC LIMIT 1
                ''')
            
            row = cursor.fetchone()
            if not row:
                logger.warning("No checkpoint found for recovery")
                return []
            
            snapshot_json, checkpoint_hash, prev_session_id = row
            
            # Parse and verify hash chain
            try:
                entries_data = json.loads(snapshot_json)
            except (json.JSONDecodeError, ValueError, TypeError):
                logger.error("Corrupted checkpoint snapshot — cannot parse")
                return []
            
            # Reset buffer
            with self._buffer_lock:
                self._buffer.clear()
                self._last_hash = ''
                self._message_count = 0
                
                verified_entries = []
                for d in entries_data:
                    entry = MessageEntry.from_dict(d)
                    
                    # Verify hash chain
                    expected_hash = entry.state_hash
                    computed = self._compute_hash(
                        self._last_hash, entry.role, entry.content or '',
                        entry.tool_calls, entry.timestamp
                    )
                    
                    if computed != expected_hash:
                        logger.warning(f"Hash chain broken at message {entry.message_id}. "
                                      f"Using {len(verified_entries)} verified messages.")
                        break
                    
                    self._buffer.append(entry)
                    self._last_hash = computed
                    self._message_count += 1
                    verified_entries.append(entry)
                
                logger.info(f"Restored {len(verified_entries)} messages from checkpoint")
                
                # Update recovery tracking
                self._recovery_count += 1
                current_time = time.time()
                
                # Reset recovery window if enough time passed
                if (current_time - self._recovery_window_start) > (self.recovery_window_minutes * 60):
                    self._recovery_count = 1
                    self._recovery_window_start = current_time
                
                # Create recovery checkpoint
                c = conn.cursor()
                self._create_checkpoint(c, 'recovery')
                conn.commit()
            
            return self.get_messages()
            
        finally:
            conn.close()
    
    def _compute_hash(self, prev_hash: str, role: str, content: str,
                      tool_calls: List, timestamp: float) -> str:
        """Compute the SHA256 hash for a message entry."""
        hash_input = prev_hash + role + content + json.dumps(tool_calls, sort_keys=True) + str(timestamp)
        return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
    
    def verify_state_integrity(self) -> Tuple[bool, int]:
        """
        Verify the hash chain integrity of the entire buffer.
        
        Returns:
            Tuple of (is_valid: bool, verified_count: int)
        """
        with self._buffer_lock:
            if not self._buffer:
                return True, 0
            
            prev_hash = ''
            verified = 0
            
            for entry in self._buffer:
                computed = self._compute_hash(
                    prev_hash, entry.role, entry.content or '',
                    entry.tool_calls, entry.timestamp
                )
                if computed != entry.state_hash:
                    return False, verified
                prev_hash = computed
                verified += 1
            
            return True, verified
    
    # ─── Session Lifecycle ───────────────────────────────────────
    
    def set_session_info(self, model: str, system_prompt: Optional[str] = None):
        """Set session metadata."""
        self.model = model
        if system_prompt:
            self.system_prompt = system_prompt
    
    def mark_unhealthy(self, error: str):
        """Mark the session as unhealthy (bug detected)."""
        self._healthy = False
        self._last_error = error
    
    def get_state_summary(self) -> Dict:
        """Get a summary of the current session state."""
        with self._buffer_lock:
            buffer_size = len(self._buffer)
        
        return {
            'session_id': self.session_id,
            'model': self.model,
            'buffer_size': buffer_size,
            'buffer_maxlen': self.buffer_maxlen,
            'message_count': self._message_count,
            'token_count': self._token_count,
            'last_state_hash': self._last_hash,
            'healthy': self._healthy,
            'last_error': self._last_error,
            'recovery_count': self._recovery_count,
            'auto_flush_interval': self.auto_flush_interval,
            'flush_task_running': self._flush_task is not None and self._running
        }
    
    async def graceful_shutdown(self):
        """Perform graceful shutdown with final flush."""
        logger.info("Starting graceful shutdown...")
        self.stop_auto_flush()
        if self._flush_task:
            try:
                await asyncio.wait_for(self._flush_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        
        # Create checkpoint before final flush
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            self._create_checkpoint(cursor, 'last_good')
            conn.commit()
        finally:
            conn.close()
        
        # Final flush
        self.flush(FlushReason.SHUTDOWN)
        logger.info("Graceful shutdown complete")

