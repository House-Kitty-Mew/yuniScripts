"""
Watcher/Observer Mode — allows non-playing users to observe
the lootpower ecosystem in real-time.

Watchers can:
  - See live loot drop feeds
  - Query leaderboards
  - Browse auction listings
  - Watch loot tables and chance values
  - Subscribe to phooks events

They CANNOT:
  - Adventure/mine/craft (that requires active player status)
  - Modify any data
"""
import time
from typing import Optional, List
from lp_database import get_db


class WatcherService:
    """Manages watcher sessions and observer queries."""

    def __init__(self):
        self.db = get_db()

    # --- Session management ---
    def register_watcher(self, watcher_id: str, label: str = "",
                         filters: str = "") -> bool:
        """Register a new watcher session."""
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO watchers (watcher_id, label, filters, active, created_at) VALUES (?,?,?,1,?)",
                (watcher_id, label, filters, time.time())
            )
            self.db.commit()
            return True
        except Exception:
            return False

    def unregister_watcher(self, watcher_id: str) -> bool:
        """Deactivate a watcher session."""
        try:
            self.db.execute(
                "UPDATE watchers SET active=0 WHERE watcher_id=?",
                (watcher_id,)
            )
            self.db.commit()
            return True
        except Exception:
            return False

    def is_watcher_active(self, watcher_id: str) -> bool:
        """Check if a watcher session is active."""
        row = self.db.fetchone(
            "SELECT active FROM watchers WHERE watcher_id=?",
            (watcher_id,)
        )
        return bool(row and row["active"])

    # --- Read-only queries for watchers ---
    def get_loot_table(self) -> List[dict]:
        """Get the full loot table (read-only)."""
        rows = self.db.fetchall("SELECT * FROM loot_table ORDER BY loot_id")
        return [dict(r) for r in rows]

    def get_loot_stats(self, limit: int = 100) -> List[dict]:
        """Get recent loot drop stats."""
        rows = self.db.fetchall(
            "SELECT * FROM loot_stats ORDER BY rowid DESC LIMIT ?",
            (limit,)
        )
        return [dict(r) for r in rows]

    def get_recent_drops(self, limit: int = 20) -> List[dict]:
        """Get most recent loot drops with user info."""
        rows = self.db.fetchall(
            """SELECT ls.*, u.user_name
               FROM loot_stats ls
               JOIN users u ON u.user_id = ls.user_id
               ORDER BY ls.rowid DESC LIMIT ?""",
            (limit,)
        )
        return [dict(r) for r in rows]

    def get_system_stats(self) -> dict:
        """Get overall system statistics."""
        total_players = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM users WHERE is_active=1"
        )
        total_adventures = self.db.fetchone(
            "SELECT COALESCE(adventures, 0) AS c FROM stat_table WHERE rowid=1"
        )
        total_drops = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM loot_stats"
        )
        active_watchers = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM watchers WHERE active=1"
        )
        return {
            "total_players": total_players["c"] if total_players else 0,
            "total_adventures": total_adventures["c"] if total_adventures else 0,
            "total_drops": total_drops["c"] if total_drops else 0,
            "active_watchers": active_watchers["c"] if active_watchers else 0,
        }