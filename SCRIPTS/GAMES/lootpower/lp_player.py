"""
Player Service — authentication, turn validation, profile management.
"""
import time
from typing import Optional
import lp_config
from lp_database import get_db


class PlayerService:
    """Handles player authentication and turn validation."""

    def __init__(self):
        self.db = get_db()

    # --- Authentication ---
    def authenticate(self, username: str, password_hash: str) -> Optional[str]:
        """Authenticate by username+password. Returns user_id or None."""
        row = self.db.fetchone(
            "SELECT user_id, password FROM users WHERE user_name=?",
            (username,)
        )
        if row and row["password"] == password_hash:
            return row["user_id"]
        return None

    def get_password_hash(self, username: str) -> Optional[str]:
        """Get password hash for a username."""
        row = self.db.fetchone(
            "SELECT password FROM users WHERE user_name=?", (username,)
        )
        return row["password"] if row else None

    def user_exists(self, username: str) -> bool:
        """Check if a username is already taken."""
        row = self.db.fetchone(
            "SELECT 1 FROM users WHERE user_name=?", (username,)
        )
        return row is not None

    def create_user(
        self, user_id: str, username: str, password_hash: str
    ) -> bool:
        """Create a new player account."""
        try:
            self.db.execute(
                """INSERT INTO users
                   (user_id, user_name, password, turns,
                    global_loot_chance_boost, turn_time, is_active)
                   VALUES (?,?,?, ?,?,?, 1)""",
                (user_id, username, password_hash,
                 lp_config.MAX_TURNS, 0.0, time.time())
            )
            # Also initialize lootpower entry
            self.db.execute(
                "INSERT OR IGNORE INTO lootpower_table (user_ID, lootpower) VALUES (?, 0.0)",
                (user_id,)
            )
            self.db.commit()
            return True
        except Exception:
            return False

    # --- Turn management ---
    def validate_turn(self, user_id: str) -> int:
        """
        Check if player can take an adventure turn.
        Returns remaining turns if successful, 0 if cannot.
        Consumes a turn on success.
        """
        row = self.db.fetchone(
            "SELECT turns, turn_time FROM users WHERE user_id=?",
            (user_id,)
        )
        if row is None or row["turns"] <= 0:
            return 0

        current_time = time.time()
        try:
            if float(row["turn_time"]) >= current_time:
                # Cooldown not expired
                new_turn_time = current_time + lp_config.TURN_COOLDOWN
                self.db.execute(
                    "UPDATE users SET turn_time=? WHERE user_id=?",
                    (new_turn_time, user_id)
                )
                self.db.commit()
                return 0
            else:
                # Cooldown expired, consume a turn
                new_turn_time = current_time + lp_config.TURN_COOLDOWN
                self.db.execute(
                    "UPDATE users SET turns=turns-1, turn_time=? WHERE user_id=?",
                    (new_turn_time, user_id)
                )
                self.db.commit()
                return row["turns"]
        except Exception:
            new_turn_time = current_time + lp_config.TURN_COOLDOWN
            self.db.execute(
                "UPDATE users SET turn_time=? WHERE user_id=?",
                (new_turn_time, user_id)
            )
            self.db.commit()
            return 0

    def get_remaining_turns(self, user_id: str) -> int:
        """Get player's remaining turns (without consuming)."""
        row = self.db.fetchone(
            "SELECT turns FROM users WHERE user_id=?", (user_id,)
        )
        return row["turns"] if row else 0

    def get_turn_cooldown(self, user_id: str) -> float:
        """Seconds until next turn can be taken. 0 if ready."""
        row = self.db.fetchone(
            "SELECT turn_time FROM users WHERE user_id=?", (user_id,)
        )
        if not row:
            return 0.0
        remaining = float(row["turn_time"]) - time.time()
        return max(0.0, remaining)

    def replenish_turns(self) -> int:
        """Increment turns for all users below MAX_TURNS. Returns count."""
        cur = self.db.execute(
            "UPDATE users SET turns = turns + 1 WHERE turns < ?",
            (lp_config.MAX_TURNS,)
        )
        self.db.commit()
        return cur.rowcount if cur else 0

    # --- Profile ---
    def get_profile(self, user_id: str) -> Optional[dict]:
        """Get full player profile."""
        row = self.db.fetchone(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        )
        if not row:
            return None
        profile = dict(row)
        # Get lootpower
        lp_row = self.db.fetchone(
            "SELECT lootpower FROM lootpower_table WHERE user_ID=?",
            (user_id,)
        )
        profile["lootpower"] = float(lp_row["lootpower"]) if lp_row else 0.0
        # Get inventory count
        profile["total_items"] = self.get_item_count(user_id)
        return profile

    def get_item_count(self, user_id: str) -> int:
        """Count total unique loot items player owns."""
        row = self.db.fetchone(
            "SELECT COALESCE(SUM(loot_amount), 0) AS c FROM users_loot WHERE user_id=?",
            (user_id,)
        )
        return row["c"] if row else 0

    def get_inventory(self, user_id: str) -> list:
        """Get player's inventory entries."""
        rows = self.db.fetchall(
            "SELECT * FROM users_loot WHERE user_id=?", (user_id,)
        )
        return [dict(r) for r in rows]