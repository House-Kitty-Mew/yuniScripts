"""
Tests for Crafting System.
"""
import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_database import DatabaseEngine
from lp_crafting import CraftingSystem


class TestCraftingSystem(unittest.TestCase):
    """Tests for crafting operations."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        cls.tmp.close()
        DatabaseEngine._instance = None
        cls.db = DatabaseEngine(db_path=cls.tmp.name)
        cls.crafting = CraftingSystem()

        # Create test user
        cls.db.execute(
            "INSERT INTO users (user_id, user_name) VALUES (?,?)",
            ("crafter1", "Crafter")
        )
        cls.db.execute(
            "INSERT INTO users (user_id, user_name) VALUES (?,?)",
            ("crafter2", "Crafter2")
        )

        # Create loot items
        cls.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise, loot_id) "
            "VALUES (?,?,?,?)",
            ("iron_ore", 50.0, 0.5, 1)
        )
        cls.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise, loot_id) "
            "VALUES (?,?,?,?)",
            ("coal", 50.0, 0.5, 2)
        )
        cls.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise, loot_id) "
            "VALUES (?,?,?,?)",
            ("steel_ingot", 100.0, 1.0, 3)
        )

        # Give user some loot
        cls.db.execute(
            "INSERT INTO users_loot (user_id, loot, loot_id, loot_amount, common) "
            "VALUES (?,?,?,?,?)",
            ("crafter1", "iron_ore", 1, 5, 5)
        )
        cls.db.execute(
            "INSERT INTO users_loot (user_id, loot, loot_id, loot_amount, common) "
            "VALUES (?,?,?,?,?)",
            ("crafter1", "coal", 2, 3, 3)
        )

        # Create a recipe: iron_ore(1) + coal(2) = steel_ingot(3)
        cls.db.execute(
            "INSERT INTO craft_rep (item_one_id, item_two_id, rep_item, rep_item_id) "
            "VALUES (?,?,?,?)",
            (1, 2, "steel_ingot", 3)
        )
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(cls.tmp.name):
            os.unlink(cls.tmp.name)

    def setUp(self):
        """Reset user loot and crafts before each test."""
        self.db.execute(
            "UPDATE users_loot SET loot_amount=5, common=5 WHERE user_id='crafter1' AND loot_id=1"
        )
        self.db.execute(
            "UPDATE users_loot SET loot_amount=3, common=3 WHERE user_id='crafter1' AND loot_id=2"
        )
        self.db.execute(
            "DELETE FROM user_craft WHERE user_id='crafter1'"
        )
        self.db.commit()

    def test_01_craft_success(self):
        """Successful craft returns rarity and item name."""
        result = self.crafting.craft("crafter1", 1, 0, 2, 0)
        self.assertIn("common", result)
        self.assertIn("steel_ingot", result)

    def test_02_craft_consumes_items(self):
        """Crafting consumes input items."""
        self.crafting.craft("crafter1", 1, 0, 2, 0)
        iron = self.db.fetchone(
            "SELECT loot_amount FROM users_loot WHERE user_id='crafter1' AND loot_id=1"
        )
        coal = self.db.fetchone(
            "SELECT loot_amount FROM users_loot WHERE user_id='crafter1' AND loot_id=2"
        )
        self.assertEqual(iron["loot_amount"], 4)  # Was 5
        self.assertEqual(coal["loot_amount"], 2)   # Was 3

    def test_03_craft_rarity_mismatch(self):
        """Items with different rarities fail."""
        # Set up user with both common and uncommon of both items
        self.db.execute(
            "INSERT INTO users_loot (user_id, loot, loot_id, loot_amount, common, uncommon) "
            "VALUES (?,?,?,?,?,?)",
            ("crafter2", "iron_ore", 1, 2, 1, 1)
        )
        self.db.execute(
            "INSERT INTO users_loot (user_id, loot, loot_id, loot_amount, common, uncommon) "
            "VALUES (?,?,?,?,?,?)",
            ("crafter2", "coal", 2, 2, 1, 1)
        )
        self.db.commit()
        result = self.crafting.craft("crafter2", 1, 0, 2, 1)  # common + uncommon
        self.assertEqual(result, "Item raritys do not match")

    def test_04_craft_no_recipe(self):
        """Items with no matching recipe fail."""
        # Use items that exist but have no recipe together (1+1 -> needs recipe for x,x)
        # User has 5 common of item 1, need to ensure we have enough for the check
        result = self.crafting.craft("crafter1", 1, 0, 1, 0)
        self.assertEqual(result, "Recipe does not exist")

    def test_05_craft_insufficient_items(self):
        """Not having enough items fails."""
        result = self.crafting.craft("crafter1", 1, 0, 2, 5)  # mythic index
        self.assertEqual(result, "Item(s) Could not be found")

    def test_06_craft_invalid_rarity_index(self):
        """Invalid rarity index fails."""
        result = self.crafting.craft("crafter1", 1, 99, 2, 0)
        self.assertEqual(result, "Turning rarity to name failed")

    def test_07_craft_non_integer_values(self):
        """Non-integer values fail."""
        result = self.crafting.craft("crafter1", "abc", "xyz", 2, 0)
        self.assertEqual(result, "Items Could not be INT")

    def test_08_get_recipes(self):
        """Get all recipes returns correct count."""
        recipes = self.crafting.get_recipes()
        self.assertGreaterEqual(len(recipes), 1)
        self.assertEqual(recipes[0]["rep_item"], "steel_ingot")

    def test_09_get_user_crafts(self):
        """Get user's crafted items after crafting."""
        self.crafting.craft("crafter1", 1, 0, 2, 0)
        crafts = self.crafting.get_user_crafts("crafter1")
        self.assertEqual(len(crafts), 1)
        self.assertEqual(crafts[0]["craft_id"], 3)

    def test_10_craft_twice_increments_amount(self):
        """Crafting same recipe twice increments amount."""
        self.crafting.craft("crafter1", 1, 0, 2, 0)
        # Need 2 more iron and 1 more coal for second craft
        self.db.execute(
            "UPDATE users_loot SET loot_amount=6, common=6 WHERE user_id='crafter1' AND loot_id=1"
        )
        self.db.execute(
            "UPDATE users_loot SET loot_amount=3, common=3 WHERE user_id='crafter1' AND loot_id=2"
        )
        self.db.commit()
        self.crafting.craft("crafter1", 1, 0, 2, 0)
        crafts = self.crafting.get_user_crafts("crafter1")
        self.assertEqual(crafts[0]["amount"], 2)


if __name__ == "__main__":
    unittest.main()