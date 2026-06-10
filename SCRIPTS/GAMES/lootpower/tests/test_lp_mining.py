"""
Tests for Mining System.
"""
import os
import sys
import unittest
import tempfile
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_database import DatabaseEngine
from lp_mining import MiningSystem


class TestMiningSystem(unittest.TestCase):
    """Tests for mining operations."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        cls.tmp.close()
        DatabaseEngine._instance = None
        cls.db = DatabaseEngine(db_path=cls.tmp.name)
        cls.mining = MiningSystem()

        # Create test user
        cls.db.execute(
            "INSERT INTO users (user_id, user_name) VALUES (?,?)",
            ("miner1", "Miner")
        )
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(cls.tmp.name):
            os.unlink(cls.tmp.name)

    def setUp(self):
        """Reset areas for clean test."""
        self.db.execute("DELETE FROM areas")
        self.db.execute("DELETE FROM user_ore")
        self.db.commit()
        random.seed(42)

    def test_01_mine_check_empty(self):
        """Unmined zone returns empty string."""
        result = self.mining.mine_check(10, 20)
        self.assertEqual(result, "")

    def test_02_mine_check_mined(self):
        """Previously mined zone returns ore type."""
        self.mining.mine("miner1", 10, 20)
        result = self.mining.mine_check(10, 20)
        self.assertNotEqual(result, "")
        self.assertIn(result, ["Power Coin", "Bag Of Dirt", "Loot Ore"])

    def test_03_mine_returns_ore_type(self):
        """mine() returns one of three ore types."""
        result = self.mining.mine("miner1", 5, 10)
        self.assertIn(result, ["Power Coin", "Bag Of Dirt", "Loot Ore"])

    def test_04_mine_twice_same_zone(self):
        """Mining same zone twice returns error message."""
        self.mining.mine("miner1", 3, 7)
        result = self.mining.mine("miner1", 3, 7)
        self.assertIn("already mined", result.lower())

    def test_05_mine_increments_ore_inventory(self):
        """Mining adds ore to player inventory."""
        random.seed(42)
        ore1 = self.mining.mine("miner1", 1, 1)
        inv = self.mining.get_ore_inventory("miner1")
        mapping = {"Power Coin": "power_coin",
                   "Bag Of Dirt": "bag_of_dirt",
                   "Loot Ore": "loot_ore"}
        key = mapping[ore1]
        self.assertGreaterEqual(inv[key], 1)

    def test_06_get_ore_inventory_empty(self):
        """Player with no ore gets zeros."""
        inv = self.mining.get_ore_inventory("new_player")
        self.assertEqual(inv["power_coin"], 0)
        self.assertEqual(inv["bag_of_dirt"], 0)
        self.assertEqual(inv["loot_ore"], 0)

    def test_07_get_ore_inventory_accumulates(self):
        """Multiple mines accumulate ore."""
        self.mining.mine("miner1", 100, 100)
        self.mining.mine("miner1", 101, 100)
        inv = self.mining.get_ore_inventory("miner1")
        total = inv["power_coin"] + inv["bag_of_dirt"] + inv["loot_ore"]
        self.assertEqual(total, 2)

    def test_08_multiple_players_independent_zones(self):
        """Different players can mine different zones."""
        # Create second player
        self.db.execute(
            "INSERT OR IGNORE INTO users (user_id, user_name) VALUES (?,?)",
            ("miner2", "Miner2")
        )
        self.db.commit()
        ore_a = self.mining.mine("miner1", 50, 50)
        ore_b = self.mining.mine("miner2", 50, 60)
        self.assertIn(ore_a, ["Power Coin", "Bag Of Dirt", "Loot Ore"])
        self.assertIn(ore_b, ["Power Coin", "Bag Of Dirt", "Loot Ore"])

    def test_09_random_distribution(self):
        """Over many mines, all three ore types appear."""
        random.seed(12345)
        results = set()
        for i in range(50):
            results.add(self.mining.mine("miner1", i, i))
        self.assertEqual(len(results), 3)  # All three types
        self.assertIn("Power Coin", results)
        self.assertIn("Bag Of Dirt", results)
        self.assertIn("Loot Ore", results)


if __name__ == "__main__":
    unittest.main()