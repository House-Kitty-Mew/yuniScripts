"""
ah_bans.py — Player ban management for the Auction House.

Split from ah_admin.py to avoid circular imports with ah_core.py.
ah_core.py imports is_player_banned for real-time checks.
ah_admin.py imports ban/unban for admin management.
"""

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

_BANNED_PLAYERS_TABLE = """
CREATE TABLE IF NOT EXISTS ah_banned_players (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name    TEXT NOT NULL UNIQUE,
    banned_by      TEXT NOT NULL DEFAULT 'admin',
    reason         TEXT,
    banned_at      TEXT NOT NULL,
    expires_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_banned_player ON ah_banned_players(player_name);
"""


def _ensure_bans_table():
    """Create the banned players table if it doesn't exist."""
    db = get_db()
    exists = db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ah_banned_players'")
    if not exists:
        db.execute(
            "CREATE TABLE IF NOT EXISTS ah_banned_players ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "player_name TEXT NOT NULL UNIQUE, "
            "banned_by TEXT NOT NULL DEFAULT 'admin', "
            "reason TEXT, "
            "banned_at TEXT NOT NULL, "
            "expires_at TEXT)")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_banned_player ON ah_banned_players(player_name)")
        log.info("bans", "Created ah_banned_players table")


def _clean_expired_bans():
    """Remove expired bans from the database.

    Previously this was inside is_player_banned(), causing a write side-effect
    on every ban check (list_item, place_bid, buy_now). Moved to a separate
    function called on a timer. (BUG FIX M4)
    """
    db = get_db()
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    db.execute("DELETE FROM ah_banned_players WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))


def is_player_banned(player_name: str) -> bool:
    """Check if a player is currently banned from the Auction House."""
    _ensure_bans_table()
    db = get_db()
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    row = db.fetch_one(
        "SELECT player_name FROM ah_banned_players WHERE player_name = ? AND (expires_at IS NULL OR expires_at > ?)",
        (player_name, now)
    )
    return row is not None


def ban_player(player_name: str, reason: str = "Admin action",
               banned_by: str = "console", duration_hours: int = None) -> dict:
    """Ban a player from using the Auction House.

    Args:
        player_name: Minecraft player name
        reason: Why they were banned
        banned_by: Who issued the ban
        duration_hours: If set, ban auto-expires after this many hours

    Returns:
        {"ok": True} or {"ok": False, "error": "..."}
    """
    _ensure_bans_table()
    from AUCTIONHOUSE.ah_helper_db import add_note
    db = get_db()
    import uuid as _uuid
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    expires = None
    if duration_hours:
        expires = (__import__("datetime").datetime.now(__import__("datetime").timezone.utc) +
                   __import__("datetime").timedelta(hours=duration_hours)).isoformat()
    try:
        db.execute(
            "INSERT OR REPLACE INTO ah_banned_players (player_name, banned_by, reason, banned_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (player_name, banned_by, reason, now, expires)
        )
        log.info("bans", f"Player banned from AH: {player_name} ({reason})")
        add_note("observation", f"Player {player_name} banned from AH: {reason}",
                 importance=4, expires_in_days=90)
        return {"ok": True, "data": {"player": player_name, "reason": reason}}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def unban_player(player_name: str) -> dict:
    """Unban a player from the Auction House."""
    _ensure_bans_table()
    db = get_db()
    try:
        db.execute("DELETE FROM ah_banned_players WHERE player_name = ?", (player_name,))
        log.info("bans", f"Player unbanned: {player_name}")
        return {"ok": True, "data": {"player": player_name}}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_banned_players() -> list[dict]:
    """List all currently banned players."""
    _ensure_bans_table()
    db = get_db()
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    return db.fetch_all(
        "SELECT * FROM ah_banned_players WHERE expires_at IS NULL OR expires_at > ?",
        (now,)
    )
