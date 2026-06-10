"""
test_ah_admin.py — Tests for ah_admin.py
"""

from conftest import AHTestCase, mock_rcon
from unittest.mock import patch
from AUCTIONHOUSE.ah_admin import (
    force_end_event, force_adjust_price, force_remove_listing,
    reset_simulated_inventory, get_full_stats,
)
from AUCTIONHOUSE.ah_market_events import start_event
from AUCTIONHOUSE.ah_core import list_item, buy_now
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_config import get_config


@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestAdmin(AHTestCase):

    def setUp(self):
        super().setUp()
        get_db().execute("DELETE FROM market_events")

    def test_force_end_event(self, mock_bridge):
        r = start_event("admin_test", "Admin Test", "flavor", "seasonal", "small", ["minecraft:coal"])
        result = force_end_event(r["data"]["event_uuid"])
        self.assertTrue(result["ok"])

    def test_force_adjust_price(self, mock_bridge):
        result = force_adjust_price("minecraft:coal", 99.99)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["item_id"], "minecraft:coal")
        updated = get_db().fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id = 'minecraft:coal'")
        self.assertEqual(updated["base_price"], 99.99)

    def test_force_adjust_price_clamping(self, mock_bridge):
        cfg = get_config()
        r = force_adjust_price("minecraft:coal", 0.0001)
        self.assertGreaterEqual(r["data"]["new_price"], cfg.sim_price_min)
        r = force_adjust_price("minecraft:netherite_ingot", 99999)
        self.assertLessEqual(r["data"]["new_price"], cfg.sim_price_max)

    def test_force_adjust_price_nonexistent_item(self, mock_bridge):
        r = force_adjust_price("minecraft:nonexistent_item_xyz123", 10.0)
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["error"].lower())

    def test_force_remove_listing(self, mock_bridge):
        lr = list_item(seller="test_player", item_id=self.unique_item(), count=1,
                       start_price=10.0, rcon_func=mock_rcon)
        result = force_remove_listing(lr["data"]["listing_uuid"], admin="test_admin")
        self.assertTrue(result["ok"])

    def test_force_remove_nonexistent(self, mock_bridge):
        from conftest import unique_uuid
        r = force_remove_listing(unique_uuid())
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["error"].lower())

    def test_force_remove_already_sold(self, mock_bridge):
        lr = list_item(seller="seller", item_id=self.unique_item(), count=1,
                       start_price=5.0, buy_now_price=20.0, rcon_func=mock_rcon)
        buy_now(buyer="buyer", listing_uuid=lr["data"]["listing_uuid"], rcon_func=mock_rcon)
        r = force_remove_listing(lr["data"]["listing_uuid"])
        self.assertFalse(r["ok"])

    def test_reset_simulated_inventory(self, mock_bridge):
        get_db().execute("UPDATE simulated_inventory SET current_price = 999 WHERE item_id = 'minecraft:coal'")
        r = reset_simulated_inventory()
        self.assertTrue(r["ok"])
        self.assertGreater(r["data"]["items_reset"], 0)
        coal = get_db().fetch_one("SELECT current_price FROM simulated_inventory WHERE item_id = 'minecraft:coal'")
        self.assertEqual(coal["current_price"], 0.5)

    def test_get_full_stats(self, mock_bridge):
        stats = get_full_stats()
        for key in ["active_listings", "transactions_24h", "volume_24h",
                      "listing_statuses", "price_history"]:
            self.assertIn(key, stats, f"Missing key: {key}")

    def test_get_full_stats_with_data(self, mock_bridge):
        for i in range(3):
            list_item(seller=f"player{i}", item_id=self.unique_item(), count=1,
                      start_price=10.0, rcon_func=mock_rcon)
        stats = get_full_stats()
        self.assertGreaterEqual(stats["active_listings"]["total"], 3)


if __name__ == "__main__":
    from unittest import main
    main()
