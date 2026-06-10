"""Test market event lifecycle: start, end, progress, cooldowns."""
import unittest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["AH_DB_PATH"] = ":memory:"
from AUCTIONHOUSE.ah_database import initialize_database
from AUCTIONHOUSE.ah_market_events import (
    start_event, end_event, get_active_events, check_event_progress,
    can_start_event, get_price_multiplier_for_item
)

class TestMarketEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_database(force=True)
    def setUp(self):
        import AUCTIONHOUSE.ah_database as dbmod
        db = dbmod.get_db()
        db.execute("DELETE FROM market_events")
        db.execute("DELETE FROM simulated_inventory")
        # Re-seed base inventory
        db.execute("INSERT INTO simulated_inventory (item_id, category, current_stock, max_stock, base_price, current_price, volatility, trend_direction, trend_strength, last_updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("minecraft:coal", "common", 1000, 2000, 0.5, 0.5, 0.2, 0, 0.0, "2026-01-01"))

    def test_start_basic(self):
        r = start_event("winter", "Extreme Winter", "Cold!", "seasonal", "small",
                         ["minecraft:coal"], price_multiplier=2.0, goal_count=100)
        self.assertTrue(r["ok"])
        self.assertIn("event_uuid", r["data"])
    def test_start_invalid_type(self):
        r = start_event("test", "Test", "Test", "invalid", "small", [])
        self.assertFalse(r["ok"])
    def test_end_event(self):
        r = start_event("winter", "Winter", "Cold!", "seasonal", "small",
                         ["minecraft:coal"], price_multiplier=2.0)
        uuid = r["data"]["event_uuid"]
        r2 = end_event(uuid, reason="test")
        self.assertTrue(r2["ok"])
        active = get_active_events()
        self.assertEqual(len(active), 0)
    def test_get_active_events(self):
        start_event("s1", "Small", "S1", "seasonal", "small", ["minecraft:coal"])
        start_event("m1", "Medium", "M1", "seasonal", "medium", ["minecraft:coal"])
        active = get_active_events()
        self.assertEqual(len(active), 2)
    def test_check_progress_goal_reached(self):
        r = start_event("winter", "Winter", "Cold!", "seasonal", "small",
                         ["minecraft:coal"], price_multiplier=2.0, goal_count=50)
        uuid = r["data"]["event_uuid"]
        import AUCTIONHOUSE.ah_database as dbmod
        db = dbmod.get_db()
        db.execute("UPDATE market_events SET current_count = 60 WHERE event_uuid = ?", (uuid,))
        completed = check_event_progress()
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0]["goal_reached"])
    def test_price_multiplier_no_event(self):
        mult = get_price_multiplier_for_item("minecraft:diamond")
        self.assertEqual(mult, 1.0)
    def test_price_multiplier_active(self):
        start_event("winter", "Winter", "Cold!", "seasonal", "small",
                     ["minecraft:coal"], price_multiplier=3.0)
        mult = get_price_multiplier_for_item("minecraft:coal")
        self.assertAlmostEqual(mult, 3.0)
    def test_price_multiplier_multiple(self):
        start_event("e1", "E1", "", "seasonal", "small", ["minecraft:coal"], price_multiplier=2.0)
        start_event("e2", "E2", "", "seasonal", "medium", ["minecraft:coal"], price_multiplier=3.0)
        mult = get_price_multiplier_for_item("minecraft:coal")
        self.assertAlmostEqual(mult, 6.0)  # 2 * 3 = 6

if __name__ == "__main__":
    unittest.main()
