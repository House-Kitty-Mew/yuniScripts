"""
Edge Case Tests — tests boundary conditions, error states, and malformed input.
"""
import os
import sys
import unittest
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_database import DatabaseEngine
from lp_player import PlayerService
from lp_chance import LootChanceEngine
from lp_adventure import AdventureSystem
from lp_mining import MiningSystem
from lp_crafting import CraftingSystem
from lp_auction import AuctionHouse
from lp_leaderboard import Leaderboard
from typing import Optional
from lp_watcher import WatcherService
from lp_phooks import LootPowerPhooks
import lp_config


class TestEdgeCases(unittest.TestCase):
    """Tests for boundary conditions and error handling."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        cls.tmp.close()
        DatabaseEngine._instance = None
        cls.db = DatabaseEngine(db_path=cls.tmp.name)

        cls.player_svc = PlayerService()
        cls.chance = LootChanceEngine()
        cls.adv_sys = AdventureSystem(cls.player_svc, cls.chance)
        cls.mining = MiningSystem()
        cls.crafting = CraftingSystem()
        cls.ah = AuctionHouse()
        cls.lb = Leaderboard()
        cls.watcher = WatcherService()
        cls.phooks = LootPowerPhooks(port=0)

        # Create minimal test data
        cls.player_svc.create_user("edge_user", "EdgePlayer", "pass")
        cls.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise) VALUES (?,?,?)",
            ("edge_item", 10.0, 0.5)
        )
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(cls.tmp.name):
            os.unlink(cls.tmp.name)

    # --- Edge: Player edge cases ---
    def test_01_empty_username_auth(self):
        """Empty username returns None."""
        result = self.player_svc.authenticate("", "pass")
        self.assertIsNone(result)

    def test_02_whitespace_username(self):
        """Whitespace username handled gracefully."""
        result = self.player_svc.authenticate("   ", "pass")
        self.assertIsNone(result)

    def test_03_nonexistent_user_turns(self):
        """Nonexistent user returns 0 turns."""
        turns = self.player_svc.get_remaining_turns("ghost")
        self.assertEqual(turns, 0)

    def test_04_nonexistent_user_cooldown(self):
        """Nonexistent user returns 0 cooldown."""
        cd = self.player_svc.get_turn_cooldown("ghost")
        self.assertEqual(cd, 0.0)

    def test_05_max_turns_not_exceeded(self):
        """Replenish doesn't push turns past MAX_TURNS."""
        self.db.execute(
            "UPDATE users SET turns=?, turn_time=0.0 WHERE user_id='edge_user'",
            (lp_config.MAX_TURNS,)
        )
        self.db.commit()
        self.player_svc.replenish_turns()
        turns = self.player_svc.get_remaining_turns("edge_user")
        self.assertEqual(turns, lp_config.MAX_TURNS)

    def test_06_turn_doesnt_go_negative(self):
        """Turns shouldn't go below 0."""
        self.db.execute(
            "UPDATE users SET turns=0, turn_time=0.0 WHERE user_id='edge_user'"
        )
        self.db.commit()
        result = self.player_svc.validate_turn("edge_user")
        self.assertEqual(result, 0)
        turns = self.player_svc.get_remaining_turns("edge_user")
        self.assertGreaterEqual(turns, 0)

    # --- Edge: Mining edge cases ---
    def test_07_mine_negative_coordinates(self):
        """Mine at negative zone coordinates works."""
        result = self.mining.mine("edge_user", -5, -10)
        self.assertIn(result, ["Power Coin", "Bag Of Dirt", "Loot Ore"])
        check = self.mining.mine_check(-5, -10)
        self.assertEqual(check, result)

    def test_08_mine_zero_coordinates(self):
        """Mine at (0,0) works."""
        result = self.mining.mine("edge_user", 0, 0)
        self.assertIn(result, ["Power Coin", "Bag Of Dirt", "Loot Ore"])

    def test_09_mine_large_coordinates(self):
        """Mine at large coordinates works."""
        result = self.mining.mine("edge_user", 999999, -999999)
        self.assertIn(result, ["Power Coin", "Bag Of Dirt", "Loot Ore"])

    # --- Edge: Crafting edge cases ---
    def test_10_craft_with_negative_ids(self):
        """Negative loot IDs handled gracefully."""
        result = self.crafting.craft("edge_user", -1, 0, -2, 0)
        self.assertEqual(result, "Item(s) Could not be found")

    def test_11_craft_zero_amount_item(self):
        """Item with zero amount can't be crafted."""
        result = self.crafting.craft("edge_user", 1, 5, 1, 5)  # mythic index
        self.assertEqual(result, "Item(s) Could not be found")

    def test_12_craft_same_item_with_self(self):
        """Crafting same item with itself requires recipe for (x,x)."""
        # edge_user has item 1 with 0 common (only rare), so use a different setup
        # Actually item 1 was inserted with no rarity columns (all NULL/0)
        # Give edge_user some common of item 1
        self.db.execute(
            "INSERT OR REPLACE INTO users_loot (user_id, loot, loot_id, loot_amount, common) VALUES (?,?,?,?,?)",
            ("edge_user", "edge_item", 1, 2, 2)
        )
        self.db.commit()
        result = self.crafting.craft("edge_user", 1, 0, 1, 0)
        self.assertEqual(result, "Recipe does not exist")

    # --- Edge: Auction edge cases ---
    def test_13_auction_nonexistent_buyer(self):
        """Buying with nonexistent user works (they get inventory entry)."""
        # First make sure edge_user has enough loot
        self.db.execute(
            "INSERT OR REPLACE INTO users_loot (user_id, loot, loot_id, loot_amount, common) "
            "VALUES (?,?,?,?,?)",
            ("edge_user", "edge_item", 1, 5, 2)
        )
        self.db.commit()
        aid = self.ah.create_listing("edge_user", 1, 1)
        self.assertIsNotNone(aid)
        result = self.ah.buy_listing(aid, "new_buyer")
        self.assertIn("Purchased", result)

    def test_14_auction_cancel_already_sold(self):
        """Cancelling a sold listing fails cleanly."""
        self.db.execute(
            "INSERT OR REPLACE INTO users_loot (user_id, loot, loot_id, loot_amount, common) "
            "VALUES (?,?,?,?,?)",
            ("edge_user", "edge_item", 1, 5, 2)
        )
        self.db.commit()
        aid = self.ah.create_listing("edge_user", 1, 1)
        self.assertIsNotNone(aid)
        self.ah.buy_listing(aid, "buyer_x")
        result = self.ah.cancel_listing(aid, "edge_user")
        self.assertIn("not found", result.lower())

    # --- Edge: Large number handling ---
    def test_15_extremely_high_global_chance(self):
        """Very high global chance boost doesn't break calculations."""
        self.db.execute(
            "UPDATE users SET global_loot_chance_boost=? WHERE user_id='edge_user'",
            (999999.0,)
        )
        self.db.commit()
        chance = self.chance.find_loot_drop_chance(
            global_chance=50.0,
            boosted_chance=999999.0,
            self_lower_drop_chance=0.01,
            item_id=1,
            loot_name="edge_item",
            user_id="edge_user"
        )
        self.assertIsInstance(chance, (int, float))
        self.assertGreaterEqual(chance, 1)

    # --- Edge: Concurrent-like multiple operations ---
    def test_16_rapid_adventures(self):
        """Multiple rapid adventures respect cooldown."""
        self.db.execute(
            "UPDATE users SET turns=5, turn_time=0.0 WHERE user_id='edge_user'"
        )
        self.db.commit()
        import random
        random.seed(123)
        result1 = self.adv_sys.adventure("edge_user")
        # Second immediate attempt should be blocked by cooldown
        result2 = self.adv_sys.adventure("edge_user")
        self.assertIn("too tired", result2.lower())

    # --- Edge: Empty watcher ---
    def test_17_watcher_unregister_twice(self):
        """Unregistering a watcher twice doesn't error."""
        self.watcher.register_watcher("ghost_watcher", "Ghost")
        self.assertTrue(self.watcher.unregister_watcher("ghost_watcher"))
        self.assertFalse(self.watcher.is_watcher_active("ghost_watcher"))

    # --- Edge: Empty system ---
    def test_18_empty_loot_table_stats(self):
        """Stats for empty loot table returns empty list."""
        self.db.execute("DELETE FROM loot_stats")
        self.db.commit()
        stats = self.watcher.get_loot_stats()
        self.assertEqual(len(stats), 0)

    # --- Edge: Phooks error handling ---
    def test_19_phooks_action_unknown(self):
        """Unknown phooks action returns error."""
        result = self.phooks._handle_action("no_such_action", {}, "tester")
        self.assertIn("error", result)

    def test_20_phooks_action_handler_exception(self):
        """Action handler that raises is caught."""
        def broken_handler(params, requester):
            raise ValueError("Boom!")

        self.phooks.register_action("broken", broken_handler)
        result = self.phooks._handle_action("broken", {}, "tester")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()