"""
Tests for Player Service.
"""
import os
import sys
import unittest
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_database import DatabaseEngine
from lp_player import PlayerService
import lp_config


class TestPlayerService(unittest.TestCase):
    """Tests for player authentication and turn management."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        cls.tmp.close()
        DatabaseEngine._instance = None
        cls.db = DatabaseEngine(db_path=cls.tmp.name)
        cls.svc = PlayerService()

        # Create test users
        cls.svc.create_user("p1", "Alice", "pass_alice")
        cls.svc.create_user("p2", "Bob", "pass_bob")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(cls.tmp.name):
            os.unlink(cls.tmp.name)

    # --- Authentication ---
    def test_01_authenticate_success(self):
        """Valid credentials return user_id."""
        uid = self.svc.authenticate("Alice", "pass_alice")
        self.assertEqual(uid, "p1")

    def test_02_authenticate_fail_wrong_password(self):
        """Wrong password returns None."""
        uid = self.svc.authenticate("Alice", "wrong_pass")
        self.assertIsNone(uid)

    def test_03_authenticate_fail_unknown_user(self):
        """Unknown username returns None."""
        uid = self.svc.authenticate("Unknown", "pass")
        self.assertIsNone(uid)

    def test_04_get_password_hash(self):
        """Get password hash for existing user."""
        pwh = self.svc.get_password_hash("Alice")
        self.assertEqual(pwh, "pass_alice")

    def test_05_get_password_hash_nonexistent(self):
        """Nonexistent user returns None."""
        pwh = self.svc.get_password_hash("Ghost")
        self.assertIsNone(pwh)

    def test_06_user_exists(self):
        """Check existing and non-existing users."""
        self.assertTrue(self.svc.user_exists("Alice"))
        self.assertFalse(self.svc.user_exists("Ghost"))

    def test_07_create_duplicate_user(self):
        """Creating a duplicate user fails."""
        result = self.svc.create_user("p3", "Alice", "pass")
        self.assertFalse(result)

    # --- Turn management ---
    def test_08_initial_turns(self):
        """New players start with MAX_TURNS."""
        turns = self.svc.get_remaining_turns("p1")
        self.assertEqual(turns, lp_config.MAX_TURNS)

    def test_09_validate_turn_consumes(self):
        """validate_turn consumes a turn."""
        before = self.svc.get_remaining_turns("p1")
        if before > 0:
            result = self.svc.validate_turn("p1")
            self.assertGreater(result, 0)
            after = self.svc.get_remaining_turns("p1")
            self.assertEqual(after, before - 1)

    def test_10_validate_turn_cooldown(self):
        """validate_turn returns 0 during cooldown."""
        # Set turn_time far in the future
        self.db.execute(
            "UPDATE users SET turn_time=? WHERE user_id=?",
            (time.time() + 9999, "p1")
        )
        self.db.commit()
        result = self.svc.validate_turn("p1")
        self.assertEqual(result, 0)

    def test_11_replenish_turns(self):
        """Replenish increments turns for players below max."""
        # Set Alice's turns low
        self.db.execute(
            "UPDATE users SET turns=?, turn_time=? WHERE user_id=?",
            (5, 0.0, "p1")
        )
        self.db.commit()
        count = self.svc.replenish_turns()
        self.assertGreaterEqual(count, 1)
        turns = self.svc.get_remaining_turns("p1")
        self.assertEqual(turns, 6)

    def test_12_turn_cooldown_ready(self):
        """get_turn_cooldown returns 0 when turn_time is in the past."""
        self.db.execute(
            "UPDATE users SET turn_time=? WHERE user_id=?",
            (0.0, "p2")
        )
        self.db.commit()
        cd = self.svc.get_turn_cooldown("p2")
        self.assertEqual(cd, 0.0)

    def test_13_turn_cooldown_active(self):
        """get_turn_cooldown returns >0 when turn_time is in the future."""
        self.db.execute(
            "UPDATE users SET turn_time=? WHERE user_id=?",
            (time.time() + 30, "p2")
        )
        self.db.commit()
        cd = self.svc.get_turn_cooldown("p2")
        self.assertGreater(cd, 0.0)

    # --- Profile ---
    def test_14_get_profile(self):
        """Get player profile returns all fields."""
        prof = self.svc.get_profile("p1")
        self.assertIsNotNone(prof)
        self.assertEqual(prof["user_name"], "Alice")
        self.assertIn("lootpower", prof)
        self.assertIn("total_items", prof)

    def test_15_get_profile_nonexistent(self):
        """Nonexistent user returns None."""
        prof = self.svc.get_profile("ghost")
        self.assertIsNone(prof)

    def test_16_get_inventory_empty(self):
        """New player has empty inventory."""
        inv = self.svc.get_inventory("p2")
        self.assertEqual(len(inv), 0)


if __name__ == "__main__":
    unittest.main()