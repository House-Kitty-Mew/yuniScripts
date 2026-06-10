"""
Mining System — zone-based mining with ore generation.
"""
import random
from lp_database import get_db


class MiningSystem:
    """Handles zone mining, ore generation, and inventory."""

    def __init__(self):
        self.db = get_db()

    def mine_check(self, zone_x: int, zone_y: int) -> str:
        """Check if a zone has already been mined. Returns '' if clear."""
        row = self.db.fetchone(
            "SELECT ore FROM areas WHERE zone_x=? AND zone_y=?",
            (zone_x, zone_y)
        )
        return row["ore"] if row else ""

    def mine(self, user_id: str, zone_x: int, zone_y: int) -> str:
        """
        Mine a zone, record the ore, and update user ore inventory.

        Returns the ore type found.
        """
        # Check if already mined
        existing = self.mine_check(zone_x, zone_y)
        if existing:
            return f"Zone ({zone_x},{zone_y}) already mined: {existing}"

        # Determine ore type (original algorithm)
        ore_choice = random.randint(1, 3)
        if ore_choice == 1:
            ore = "Power Coin"
        elif ore_choice == 2:
            ore = "Bag Of Dirt"
        else:
            ore = "Loot Ore"

        # Record mining claim
        self.db.execute(
            "INSERT INTO areas (zone_x, zone_y, user_id, ore) VALUES (?,?,?,?)",
            (zone_x, zone_y, user_id, ore)
        )

        # Update user ore inventory
        ore_row = self.db.fetchone(
            "SELECT * FROM user_ore WHERE user_id=?", (user_id,)
        )
        if not ore_row:
            self.db.execute(
                "INSERT INTO user_ore (user_id, power_coin, bag_of_dirt, loot_ore) VALUES (?,0,0,0)",
                (user_id,)
            )

        col = {"Power Coin": "power_coin", "Bag Of Dirt": "bag_of_dirt", "Loot Ore": "loot_ore"}
        self.db.execute(
            f"UPDATE user_ore SET {col[ore]} = {col[ore]} + 1 WHERE user_id=?",
            (user_id,)
        )
        self.db.commit()
        return ore

    def get_ore_inventory(self, user_id: str) -> dict:
        """Get player's ore inventory."""
        row = self.db.fetchone(
            "SELECT * FROM user_ore WHERE user_id=?", (user_id,)
        )
        if not row:
            return {"power_coin": 0, "bag_of_dirt": 0, "loot_ore": 0}
        return {"power_coin": row["power_coin"],
                "bag_of_dirt": row["bag_of_dirt"],
                "loot_ore": row["loot_ore"]}