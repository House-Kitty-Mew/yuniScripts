"""
Tests for Leaderboard.
"""
import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_database import DatabaseEngine
from lp_leaderboard import Leaderboard


class TestLeaderboard(unittest.TestCase):
    """Tests for leaderboard rankings."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        cls.tmp.close()
        DatabaseEngine._instance = None
        cls.db = DatabaseEngine(db_path=cls.tmp.name)
        cls.lb = Leaderboard()

        # Create test users
        for i in range(5):
            cls.db.execute(
                "INSERT INTO users (user_id, user_name, global_loot_chance_boost, is_active) "
                "VALUES (?,?,?,1)",
                (f"lb_user{i}", f"Player{i}", float(i * 10),)
            )
            cls.db.execute(
                "INSERT OR IGNORE INTO lootpower_table (user_ID, lootpower) VALUES (?,?)",
                (f"lb_user{i}", float(i * 5))
            )
            # Give them some loot
            for j in range(3):
                cls.db.execute(
                    "INSERT OR IGNORE INTO loot_table (loot, loot_chance, loot_chance_raise, loot_id) "
                    "VALUES (?,?,?,?)",
                    (f"item_{j}", 50.0, 0.5, j)
                )
                cls.db.execute(
                    "INSERT OR IGNORE INTO users_loot (user_id, loot, loot_id, loot_amount) "
                    "VALUES (?,?,?,?)",
                    (f"lb_user{i}", f"item_{j}", j, i + 1)
                )
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(cls.tmp.name):
            os.unlink(cls.tmp.name)

    def test_01_get_rankings(self):
        """Rankings returns sorted list of players."""
        rankings = self.lb.get_rankings(limit=10)
        self.assertGreaterEqual(len(rankings), 1)
        self.assertLessEqual(len(rankings), 5)

    def test_02_rankings_sorted_descending(self):
        """Rankings are sorted by score descending."""
        rankings = self.lb.get_rankings(limit=10)
        for i in range(len(rankings) - 1):
            self.assertGreaterEqual(
                rankings[i]["score"], rankings[i + 1]["score"]
            )

    def test_03_rankings_have_required_fields(self):
        """Each ranking entry has required fields."""
        rankings = self.lb.get_rankings(limit=1)
        entry = rankings[0]
        for key in ["name", "score", "lootpower", "item_count", "rank"]:
            self.assertIn(key, entry)

    def test_04_rankings_respects_limit(self):
        """Limit parameter is respected."""
        rankings = self.lb.get_rankings(limit=2)
        self.assertLessEqual(len(rankings), 2)

    def test_05_get_player_rank(self):
        """Get rank for a specific player."""
        rank = self.lb.get_player_rank("lb_user2")
        self.assertIsNotNone(rank)
        self.assertEqual(rank["rank"], 3)  # Player2 has score (20*3)+10=70

    def test_06_get_player_rank_nonexistent(self):
        """Nonexistent player returns None."""
        rank = self.lb.get_player_rank("ghost")
        self.assertIsNone(rank)


if __name__ == "__main__":
    unittest.main()