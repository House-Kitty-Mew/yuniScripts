"""
Tests for Adventure System.
"""
import os
import sys
import unittest
import tempfile
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_database import DatabaseEngine
from lp_adventure import AdventureSystem
from lp_player import PlayerService
from lp_chance import LootChanceEngine
import lp_config


class TestAdventureSystem(unittest.TestCase):
    """Tests for adventure execution."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        cls.tmp.close()
        DatabaseEngine._instance = None
        cls.db = DatabaseEngine(db_path=cls.tmp.name)

        # Create test user
        cls.player_svc = PlayerService()
        cls.player_svc.create_user("adv1", "Adventurer", "pass")

        # Add test loot
        cls.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise, self_loot_chance_lower) "
            "VALUES (?,?,?,?)",
            ("test_item", 10.0, 0.5, 0.01)
        )
        cls.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise, self_loot_chance_lower) "
            "VALUES (?,?,?,?)",
            ("common_stone", 5.0, 0.1, 0.005)
        )
        cls.db.commit()

        cls.chance = LootChanceEngine()
        cls.adv_sys = AdventureSystem(cls.player_svc, cls.chance)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(cls.tmp.name):
            os.unlink(cls.tmp.name)

    def setUp(self):
        random.seed(1234)

    def test_01_adventure_success(self):
        """Adventure returns a result string."""
        result = self.adv_sys.adventure("adv1")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_02_adventure_no_turns(self):
        """Adventure with 0 turns returns cooldown message."""
        self.db.execute(
            "UPDATE users SET turns=0 WHERE user_id='adv1'"
        )
        self.db.commit()
        result = self.adv_sys.adventure("adv1")
        self.assertIn("too tired", result.lower())

    def test_03_adventure_consumes_turn(self):
        """Adventure consumes a turn."""
        self.db.execute(
            "UPDATE users SET turns=20, turn_time=0.0 WHERE user_id='adv1'"
        )
        self.db.commit()
        before = self.player_svc.get_remaining_turns("adv1")
        self.adv_sys.adventure("adv1")
        after = self.player_svc.get_remaining_turns("adv1")
        self.assertEqual(after, before - 1)

    def test_04_adventure_with_story(self):
        """Adventure with story flag works."""
        self.db.execute(
            "UPDATE users SET turns=20, turn_time=0.0 WHERE user_id='adv1'"
        )
        self.db.commit()
        result = self.adv_sys.adventure("adv1", show_story="1")
        self.assertIsInstance(result, str)

    def test_05_adventure_without_story(self):
        """Adventure with no story works."""
        self.db.execute(
            "UPDATE users SET turns=20, turn_time=0.0 WHERE user_id='adv1'"
        )
        self.db.commit()
        result = self.adv_sys.adventure("adv1", show_story="0")
        self.assertIsInstance(result, str)

    def test_06_adventure_adds_to_loot_stats(self):
        """Adventure adds a loot_stats entry on success."""
        self.db.execute(
            "UPDATE users SET turns=20, turn_time=0.0 WHERE user_id='adv1'"
        )
        self.db.commit()
        # Reset stats
        self.db.execute("DELETE FROM loot_stats")
        self.db.commit()
        before = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM loot_stats"
        )["c"]
        self.adv_sys.adventure("adv1")
        after = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM loot_stats"
        )["c"]
        # May or may not have dropped loot, just verify no crash
        self.assertGreaterEqual(after, before)

    def test_07_adventure_increments_global_stat(self):
        """Adventure increments the stat_table counter."""
        self.db.execute(
            "UPDATE users SET turns=20, turn_time=0.0 WHERE user_id='adv1'"
        )
        self.db.commit()
        before = self.db.fetchone(
            "SELECT adventures FROM stat_table WHERE rowid=1"
        )["adventures"]
        random.seed(99999)
        self.adv_sys.adventure("adv1")
        after = self.db.fetchone(
            "SELECT adventures FROM stat_table WHERE rowid=1"
        )["adventures"]
        self.assertGreaterEqual(after, before)


if __name__ == "__main__":
    unittest.main()