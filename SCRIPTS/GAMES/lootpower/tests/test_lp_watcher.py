"""
Tests for Watcher/Observer Service.
"""
import os
import sys
import unittest
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_database import DatabaseEngine
from lp_watcher import WatcherService


class TestWatcherService(unittest.TestCase):
    """Tests for watcher/observer functionality."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        cls.tmp.close()
        DatabaseEngine._instance = None
        cls.db = DatabaseEngine(db_path=cls.tmp.name)
        cls.watcher = WatcherService()

        # Create test data
        cls.db.execute(
            "INSERT INTO users (user_id, user_name) VALUES (?,?)",
            ("w_user", "WatchedPlayer")
        )
        cls.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise) VALUES (?,?,?)",
            ("obsidian", 150.0, 0.8)
        )
        # stat_table has default row from schema, just update it
        cls.db.execute("UPDATE stat_table SET adventures=42, sold_items=7 WHERE rowid=1")
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(cls.tmp.name):
            os.unlink(cls.tmp.name)

    def test_01_register_watcher(self):
        """Register a watcher session."""
        result = self.watcher.register_watcher("obs1", "Observer1", '{"filter":"all"}')
        self.assertTrue(result)
        self.assertTrue(self.watcher.is_watcher_active("obs1"))

    def test_02_unregister_watcher(self):
        """Unregister a watcher deactivates it."""
        self.watcher.register_watcher("obs2", "Observer2")
        self.watcher.unregister_watcher("obs2")
        self.assertFalse(self.watcher.is_watcher_active("obs2"))

    def test_03_is_watcher_active_nonexistent(self):
        """Nonexistent watcher is not active."""
        self.assertFalse(self.watcher.is_watcher_active("ghost"))

    def test_04_get_loot_table(self):
        """Get loot table returns items."""
        items = self.watcher.get_loot_table()
        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(items[0]["loot"], "obsidian")

    def test_05_get_loot_stats_empty(self):
        """Get loot stats returns empty list when none."""
        stats = self.watcher.get_loot_stats(limit=10)
        self.assertEqual(len(stats), 0)

    def test_06_get_loot_stats_with_data(self):
        """Get loot stats returns inserted data."""
        self.db.execute(
            "INSERT INTO loot_stats (user_id, loot_name, loot_rarity, year, month, day, hour, minute) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("w_user", "obsidian", "rare", 2026, 6, 8, 10, 30)
        )
        self.db.commit()
        stats = self.watcher.get_loot_stats(limit=10)
        self.assertGreaterEqual(len(stats), 1)
        self.assertEqual(stats[0]["loot_name"], "obsidian")

    def test_07_get_recent_drops(self):
        """Recent drops includes username."""
        drops = self.watcher.get_recent_drops(limit=5)
        self.assertGreaterEqual(len(drops), 1)
        self.assertIn("user_name", drops[0])

    def test_08_get_system_stats(self):
        """System stats returns all fields."""
        stats = self.watcher.get_system_stats()
        for key in ["total_players", "total_adventures", "total_drops", "active_watchers"]:
            self.assertIn(key, stats)
        self.assertEqual(stats["total_adventures"], 42)

    def test_09_watcher_can_see_leaderboard_data(self):
        """Watcher can query leaderboard-relevant data."""
        items = self.watcher.get_loot_table()
        self.assertTrue(len(items) > 0)


if __name__ == "__main__":
    unittest.main()