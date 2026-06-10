"""Test item generation: rarity rolls, enchantments, lore, pricing."""
import unittest, sys, os, collections
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from AUCTIONHOUSE.ah_item_gen import (
    roll_rarity, generate_enchantments, generate_lore,
    generate_simulated_item, should_generate_rare, pick_item_for_rarity
)

class TestItemGeneration(unittest.TestCase):
    def test_roll_rarity_valid(self):
        r = roll_rarity()
        self.assertIn("name", r)
        self.assertIn("quality", r)
        self.assertIn("color", r)
        self.assertIn("color_name", r)
        self.assertGreaterEqual(r["quality"], 1)
        self.assertLessEqual(r["quality"], 100)
    def test_roll_rarity_distribution(self):
        counts = collections.Counter()
        for _ in range(10000):
            r = roll_rarity()
            counts[r["name"]] += 1
        total = sum(counts.values())
        # Garbage should be the most common
        self.assertGreater(counts.get("Garbage", 0), counts.get("Rare", 0))
        # Cosmic Perfection should be extremely rare
        self.assertLess(counts.get("Cosmic Perfection", 0), total * 0.01)
    def test_roll_rarity_bias(self):
        counts = collections.Counter()
        for _ in range(1000):
            r = roll_rarity(bias_up=True)
            counts[r["name"]] += 1
        # Bias up should reduce Garbage count
        biased = counts.get("Garbage", 0)
        counts2 = collections.Counter()
        for _ in range(1000):
            r = roll_rarity(bias_up=False)
            counts2[r["name"]] += 1
        self.assertLessEqual(biased, counts2.get("Garbage", 1000))
    def test_enchantments_garbage(self):
        r = {"name": "Garbage", "quality": 10}
        ench = generate_enchantments("minecraft:diamond_sword", r)
        self.assertEqual(len(ench), 0)
    def test_enchantments_legendary(self):
        r = {"name": "Legendary", "quality": 90}
        ench = generate_enchantments("minecraft:diamond_sword", r)
        self.assertGreater(len(ench), 0)
        self.assertLessEqual(len(ench), 5)
        for e in ench:
            self.assertIn("id", e)
            self.assertIn("level", e)
    def test_enchantments_bow(self):
        r = {"name": "Rare", "quality": 50}
        ench = generate_enchantments("minecraft:bow", r)
        for e in ench:
            self.assertTrue(e["id"].startswith("minecraft:"))
    def test_generate_lore(self):
        r = {"name": "Rare", "quality": 60, "color": "§9", "color_name": "blue"}
        lore = generate_lore("minecraft:diamond_sword", r, "Test Sword")
        self.assertGreater(len(lore), 0)
        for line in lore:
            self.assertIsInstance(line, str)
            self.assertGreater(len(line), 5)
    def test_generate_lore_epic(self):
        r = {"name": "Epic", "quality": 80, "color": "§d", "color_name": "light_purple"}
        lore = generate_lore("minecraft:diamond_chestplate", r, "Epic Chest")
        self.assertGreaterEqual(len(lore), 2)  # Epic gets 2+ lines
    def test_generate_simulated_item(self):
        item = generate_simulated_item("minecraft:diamond_sword")
        self.assertIn("item_id", item)
        self.assertIn("price", item)
        self.assertIn("rarity", item)
        self.assertIn("enchantments", item)
        self.assertIn("lore", item)
        self.assertGreater(item["price"], 0)
    def test_generate_simulated_item_elytra(self):
        item = generate_simulated_item("minecraft:elytra")
        self.assertEqual(item["item_id"], "minecraft:elytra")
        self.assertGreater(item["price"], 0)
    def test_should_generate_rare(self):
        results = [should_generate_rare() for _ in range(200)]
        ultra = results.count("ultra_rare")
        rare = results.count("rare")
        uncommon = results.count("uncommon")
        none = results.count("none")
        self.assertGreaterEqual(none, 100, "Most cycles should generate nothing")
    def test_pick_item_for_rarity(self):
        item = pick_item_for_rarity("ultra_rare")
        self.assertIsNotNone(item)
        self.assertIn("minecraft:", item)

if __name__ == "__main__":
    unittest.main()
