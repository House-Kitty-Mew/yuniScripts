"""
pl_announce_database.py — Schema & queries for the SIMULATED_ANNOUNCE extension.

Tables:
  ext_announce_subscriptions:  player → persona subscription
  ext_announce_queue:          queued announcements awaiting delivery
  ext_announce_log:            delivered announcement history
"""

import json
import sqlite3, time
from typing import Optional
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()
EXT_NAME = "pl_announce"

# ── Schema ───────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ext_announce_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT    NOT NULL,
    persona_id  TEXT    NOT NULL,
    created_at  REAL    NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    UNIQUE(player_name, persona_id)
);

CREATE INDEX IF NOT EXISTS idx_announce_subs_player
    ON ext_announce_subscriptions(player_name);
CREATE INDEX IF NOT EXISTS idx_announce_subs_persona
    ON ext_announce_subscriptions(persona_id);

CREATE TABLE IF NOT EXISTS ext_announce_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT    NOT NULL,
    persona_id  TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    description TEXT    NOT NULL DEFAULT '',
    details     TEXT    NOT NULL DEFAULT '{}',  -- JSON blob for extra context
    interestingness INTEGER NOT NULL DEFAULT 5,
    created_at  REAL    NOT NULL DEFAULT (strftime('%s','now')),
    delivered   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_announce_queue_undelivered
    ON ext_announce_queue(player_name, delivered);

CREATE TABLE IF NOT EXISTS ext_announce_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT    NOT NULL,
    persona_id  TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    interestingness INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL DEFAULT (strftime('%s','now')),
    delivered_at REAL   NOT NULL DEFAULT (strftime('%s','now'))
);
"""

# ── Schema management ────────────────────────────────────────────────

_ensured = False


def ensure_schema():
    """Create tables if they don't exist.  Called once at extension load."""
    global _ensured
    if _ensured:
        return
    db = get_db()
    db.executescript(_SCHEMA_SQL)
    db.commit()
    _ensured = True
    log.info(EXT_NAME, "Schema ensured (3 tables)")


def drop_tables():
    """Drop all announce tables (for testing / reset)."""
    global _ensured
    db = get_db()
    for t in ("ext_announce_subscriptions", "ext_announce_queue", "ext_announce_log"):
        db.execute(f"DROP TABLE IF EXISTS {t}")
    db.commit()
    _ensured = False


# ── Subscription queries ─────────────────────────────────────────────

def subscribe(player_name: str, persona_id: str) -> bool:
    """Subscribe a player to a persona.  Returns True if new subscription."""
    db = get_db()
    try:
        cursor = db.execute(
            "INSERT OR IGNORE INTO ext_announce_subscriptions (player_name, persona_id, created_at) VALUES (?, ?, ?)",
            (player_name, persona_id, time.time())
        )
        db.commit()
        # Check if the INSERT actually added a row (vs. IGNORE'd)
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        log.error(EXT_NAME, f"subscribe error: {e}")
        return False


def unsubscribe(player_name: str, persona_id: str) -> bool:
    """Unsubscribe a player from a persona.  Returns True if existed."""
    db = get_db()
    cursor = db.execute(
        "UPDATE ext_announce_subscriptions SET active = 0 WHERE player_name = ? AND persona_id = ? AND active = 1",
        (player_name, persona_id)
    )
    db.commit()
    return cursor.rowcount > 0


def get_subscriptions(player_name: str) -> list[dict]:
    """Return active subscriptions for a player."""
    db = get_db()
    rows = db.execute(
        "SELECT persona_id, created_at FROM ext_announce_subscriptions "
        "WHERE player_name = ? AND active = 1 ORDER BY created_at",
        (player_name,)
    ).fetchall()
    return [{"persona_id": r[0], "created_at": r[1]} for r in rows]


def get_subscribers_for_persona(persona_id: str) -> list[str]:
    """Return all active subscribers for a given persona."""
    db = get_db()
    rows = db.execute(
        "SELECT player_name FROM ext_announce_subscriptions "
        "WHERE persona_id = ? AND active = 1",
        (persona_id,)
    ).fetchall()
    return [r[0] for r in rows]


def count_subscriptions(player_name: str) -> int:
    """Count active subscriptions for a player."""
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) FROM ext_announce_subscriptions WHERE player_name = ? AND active = 1",
        (player_name,)
    ).fetchone()
    return row[0] if row else 0


def persona_subscriber_count(persona_id: str) -> int:
    """Count how many players are subscribed to a persona."""
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) FROM ext_announce_subscriptions WHERE persona_id = ? AND active = 1",
        (persona_id,)
    ).fetchone()
    return row[0] if row else 0


# ── Announcement Queue ───────────────────────────────────────────────

def enqueue_announcement(
    player_name: str,
    persona_id: str,
    event_type: str,
    title: str,
    description: str = "",
    details: Optional[dict] = None,
    interestingness: int = 5
) -> int:
    """Queue an announcement for a player.  Returns the queue id."""
    db = get_db()
    details_json = json.dumps(details or {})
    cursor = db.execute(
        "INSERT INTO ext_announce_queue "
        "(player_name, persona_id, event_type, title, description, details, interestingness) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (player_name, persona_id, event_type, title, description, details_json, interestingness)
    )
    db.commit()
    queue_id = cursor.lastrowid
    log.info(EXT_NAME, f"Queued announcement #{queue_id} for {player_name} about {persona_id}: {title}")
    return queue_id


def get_undelivered_announcements(player_name: str, limit: int = 20) -> list[dict]:
    """Return undelivered announcements for a player, oldest first."""
    db = get_db()
    rows = db.execute(
        "SELECT id, persona_id, event_type, title, description, details, interestingness, created_at "
        "FROM ext_announce_queue "
        "WHERE player_name = ? AND delivered = 0 "
        "ORDER BY created_at ASC LIMIT ?",
        (player_name, limit)
    ).fetchall()
    result = []
    for r in rows:
        details = {}
        try:
            details = json.loads(r[5])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append({
            "id": r[0],
            "persona_id": r[1],
            "event_type": r[2],
            "title": r[3],
            "description": r[4],
            "details": details,
            "interestingness": r[6],
            "created_at": r[7],
        })
    return result


def mark_delivered(queue_id: int):
    """Mark a single announcement as delivered."""
    db = get_db()
    db.execute(
        "UPDATE ext_announce_queue SET delivered = 1 WHERE id = ?",
        (queue_id,)
    )
    db.commit()


def mark_all_delivered(player_name: str):
    """Mark all undelivered announcements as delivered for a player."""
    db = get_db()
    now = time.time()
    db.execute(
        "UPDATE ext_announce_queue SET delivered = 1 WHERE player_name = ? AND delivered = 0",
        (player_name,)
    )
    db.commit()


def get_announcement_count(player_name: str) -> int:
    """Count undelivered announcements for a player."""
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) FROM ext_announce_queue WHERE player_name = ? AND delivered = 0",
        (player_name,)
    ).fetchone()
    return row[0] if row else 0


# ── Logging ──────────────────────────────────────────────────────────

def log_delivery(player_name: str, persona_id: str, event_type: str,
                 title: str, interestingness: int):
    """Log a delivered announcement."""
    db = get_db()
    db.execute(
        "INSERT INTO ext_announce_log "
        "(player_name, persona_id, event_type, title, interestingness) "
        "VALUES (?, ?, ?, ?, ?)",
        (player_name, persona_id, event_type, title, interestingness)
    )
    db.commit()


def get_delivery_history(player_name: str, limit: int = 20) -> list[dict]:
    """Return recent delivered announcements for a player."""
    db = get_db()
    rows = db.execute(
        "SELECT persona_id, event_type, title, interestingness, delivered_at "
        "FROM ext_announce_log "
        "WHERE player_name = ? "
        "ORDER BY delivered_at DESC LIMIT ?",
        (player_name, limit)
    ).fetchall()
    return [
        {
            "persona_id": r[0],
            "event_type": r[1],
            "title": r[2],
            "interestingness": r[3],
            "delivered_at": r[4],
        }
        for r in rows
    ]


# ── Cleanup old data ─────────────────────────────────────────────────

def cleanup_old_announcements(max_age_days: int = 30):
    """Remove delivered announcements older than max_age_days."""
    db = get_db()
    cutoff = time.time() - (max_age_days * 86400)
    db.execute("DELETE FROM ext_announce_queue WHERE delivered = 1 AND created_at < ?", (cutoff,))
    db.execute("DELETE FROM ext_announce_log WHERE delivered_at < ?", (cutoff,))
    db.commit()



