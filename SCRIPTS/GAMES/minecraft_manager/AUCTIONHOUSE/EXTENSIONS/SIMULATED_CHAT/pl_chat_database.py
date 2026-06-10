"""
pl_chat_database.py — Database schema for player-to-persona chat.

Tables:
  ext_chat_player_connections — Links player UUIDs to persona UUIDs
  ext_chat_conversations      — Conversation session tracking
  ext_chat_messages           — Individual messages between player and persona
  ext_chat_queued_messages    — Messages from persona to player awaiting pickup
  ext_chat_player_interest    — Per-player interest level per persona (0-100)
"""

from typing import Optional, Any
from datetime import datetime, timezone
import json

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

_SCHEMA_SQL = """

CREATE TABLE IF NOT EXISTS ext_chat_player_connections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_uuid     TEXT NOT NULL,
    persona_uuid    TEXT NOT NULL,
    first_message   TEXT NOT NULL,
    last_message_at TEXT,
    message_count   INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE(player_uuid, persona_uuid)
);
CREATE INDEX IF NOT EXISTS idx_chat_conn_player ON ext_chat_player_connections(player_uuid);
CREATE INDEX IF NOT EXISTS idx_chat_conn_persona ON ext_chat_player_connections(persona_uuid);

CREATE TABLE IF NOT EXISTS ext_chat_conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_uuid     TEXT NOT NULL,
    persona_uuid    TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    last_message_at TEXT,
    message_count   INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_chat_conv_player ON ext_chat_conversations(player_uuid);

CREATE TABLE IF NOT EXISTS ext_chat_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    sender_type     TEXT NOT NULL CHECK(sender_type IN ('player', 'persona')),
    sender_uuid     TEXT NOT NULL,
    content         TEXT NOT NULL,
    context_type    TEXT,
    stat_changes    TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_msg_conv ON ext_chat_messages(conversation_id);

CREATE TABLE IF NOT EXISTS ext_chat_queued_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_uuid     TEXT NOT NULL,
    persona_uuid    TEXT NOT NULL,
    content         TEXT NOT NULL,
    context_type    TEXT DEFAULT 'message',
    is_read         INTEGER NOT NULL DEFAULT 0,
    replied_at      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_queue_player ON ext_chat_queued_messages(player_uuid);
CREATE INDEX IF NOT EXISTS idx_chat_queue_unread ON ext_chat_queued_messages(player_uuid, is_read);

CREATE TABLE IF NOT EXISTS ext_chat_player_interest (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid    TEXT NOT NULL,
    player_uuid     TEXT NOT NULL,
    interest_level  REAL NOT NULL DEFAULT 25.0,
    last_interaction_at TEXT,
    total_gifts      INTEGER NOT NULL DEFAULT 0,
    total_insults    INTEGER NOT NULL DEFAULT 0,
    total_trades     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(persona_uuid, player_uuid)
);
CREATE INDEX IF NOT EXISTS idx_chat_int_persona ON ext_chat_player_interest(persona_uuid);
"""


def ensure_schema():
    db = get_db()
    for stmt in _SCHEMA_SQL.split(";"):
        s = stmt.strip()
        if s:
            try:
                db.execute(s)
            except Exception as e:
                log.warn("pl_chat", f"Schema stmt: {e}")
    log.info("pl_chat", "SIMULATED_CHAT schema ensured")


# ── Player Connection CRUD ──────────────────────────────────────

def get_or_create_connection(player_uuid: str, persona_uuid: str) -> dict:
    if not player_uuid or not persona_uuid:
        return {}
    db = get_db()
    conn = db.fetch_one(
        "SELECT * FROM ext_chat_player_connections WHERE player_uuid = ? AND persona_uuid = ?",
        (player_uuid, persona_uuid))
    if conn:
        return dict(conn)
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_chat_player_connections
        (player_uuid, persona_uuid, first_message, last_message_at, message_count)
        VALUES (?, ?, ?, ?, 1)
    """, (player_uuid, persona_uuid, now, now))
    return {"player_uuid": player_uuid, "persona_uuid": persona_uuid,
            "message_count": 1, "is_active": 1}


def update_connection(player_uuid: str, persona_uuid: str):
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        UPDATE ext_chat_player_connections
        SET last_message_at = ?, message_count = message_count + 1
        WHERE player_uuid = ? AND persona_uuid = ?
    """, (now, player_uuid, persona_uuid))


# ── Conversation CRUD ───────────────────────────────────────────

def create_conversation(player_uuid: str, persona_uuid: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    cursor = db.execute("""
        INSERT INTO ext_chat_conversations
        (player_uuid, persona_uuid, started_at, last_message_at)
        VALUES (?, ?, ?, ?)
    """, (player_uuid, persona_uuid, now, now))
    return cursor.lastrowid if hasattr(cursor, 'lastrowid') else 0


def get_active_conversation(player_uuid: str, persona_uuid: str) -> Optional[dict]:
    db = get_db()
    return db.fetch_one("""
        SELECT * FROM ext_chat_conversations
        WHERE player_uuid = ? AND persona_uuid = ? AND is_active = 1
        ORDER BY last_message_at DESC LIMIT 1
    """, (player_uuid, persona_uuid))


def log_message(conv_id: int, sender_type: str, sender_uuid: str,
                content: str, stat_changes: Optional[dict] = None) -> dict:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = db.execute("""
        INSERT INTO ext_chat_messages
        (conversation_id, sender_type, sender_uuid, content, stat_changes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (conv_id, sender_type, sender_uuid, content[:1024],
          json.dumps(stat_changes) if stat_changes else None, now))
    return {"message_id": cursor.lastrowid if hasattr(cursor, 'lastrowid') else 0}


# ── Persona Listing ─────────────────────────────────────────────

def get_known_personas() -> list[dict]:
    db = get_db()
    return db.fetch_all(
        "SELECT persona_uuid, name, archetype, job FROM ext_sp_profiles WHERE active = 1 ORDER BY name"
    )


# ── Queued Messages ─────────────────────────────────────────────

def queue_message(player_uuid: str, persona_uuid: str, content: str,
                  context_type: str = "message") -> dict:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = db.execute("""
        INSERT INTO ext_chat_queued_messages
        (player_uuid, persona_uuid, content, context_type, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (player_uuid, persona_uuid, content[:1024], context_type, now))
    return {"queued_id": cursor.lastrowid if hasattr(cursor, 'lastrowid') else 0}


def get_queued_messages(player_uuid: str, unread_only: bool = True) -> list[dict]:
    db = get_db()
    if unread_only:
        return db.fetch_all("""
            SELECT q.*, p.name as persona_name
            FROM ext_chat_queued_messages q
            LEFT JOIN ext_sp_profiles p ON q.persona_uuid = p.persona_uuid
            WHERE q.player_uuid = ? AND q.is_read = 0
            ORDER BY q.created_at ASC
        """, (player_uuid,))
    return db.fetch_all("""
        SELECT q.*, p.name as persona_name
        FROM ext_chat_queued_messages q
        LEFT JOIN ext_sp_profiles p ON q.persona_uuid = p.persona_uuid
        WHERE q.player_uuid = ?
        ORDER BY q.created_at DESC LIMIT 50
    """, (player_uuid,))


def mark_message_read(msg_id: int) -> bool:
    db = get_db()
    cursor = db.execute("UPDATE ext_chat_queued_messages SET is_read = 1 WHERE id = ?",
               (msg_id,))
    return cursor.rowcount > 0 if hasattr(cursor, 'rowcount') else True


def reply_to_queued(msg_id: int, reply_content: str) -> bool:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = db.execute("""
        UPDATE ext_chat_queued_messages
        SET is_read = 1, replied_at = ?
        WHERE id = ?
    """, (now, msg_id))
    return cursor.rowcount > 0 if hasattr(cursor, 'rowcount') else True


# ── Interest System ─────────────────────────────────────────────

def get_or_create_interest(persona_uuid: str, player_uuid: str) -> dict:
    db = get_db()
    intr = db.fetch_one(
        "SELECT * FROM ext_chat_player_interest WHERE persona_uuid = ? AND player_uuid = ?",
        (persona_uuid, player_uuid))
    if intr:
        return dict(intr)
    db.execute("""
        INSERT INTO ext_chat_player_interest (persona_uuid, player_uuid)
        VALUES (?, ?)
    """, (persona_uuid, player_uuid))
    return {"interest_level": 25.0}


def update_interest(persona_uuid: str, player_uuid: str, delta: float) -> float:
    if not persona_uuid or not player_uuid:
        return 0.0
    get_or_create_interest(persona_uuid, player_uuid)
    db = get_db()
    current = db.fetch_one(
        "SELECT interest_level FROM ext_chat_player_interest WHERE persona_uuid = ? AND player_uuid = ?",
        (persona_uuid, player_uuid))
    new_val = max(0.0, min(100.0, current["interest_level"] + delta))
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        UPDATE ext_chat_player_interest
        SET interest_level = ?, last_interaction_at = ?
        WHERE persona_uuid = ? AND player_uuid = ?
    """, (new_val, now, persona_uuid, player_uuid))
    return new_val
