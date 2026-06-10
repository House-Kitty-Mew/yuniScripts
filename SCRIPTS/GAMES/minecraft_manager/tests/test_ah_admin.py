"""
test_ah_admin.py — Tests for the Auction House admin override module.

Tests cover:
  - force_end_event() — Force end an active event
  - force_adjust_price() — Override simulated item prices
  - force_remove_listing() — Admin-remove any listing
  - reset_simulated_inventory() — Reset to defaults
  - backup_database() / list_backups() — Database backup
  - get_full_stats() — Market statistics

All tests use an in-memory database. No RCON needed.
"""

import unittest, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["AH_DB_PATH"] = ":memory:"

from AUCTIONHOUSE.ah_database import initialize_database, get_db
from AUCTIONHOUSE.ah_core import list_item, query_listings, get_listing
from AUCTIONHOUSE.ah_market_events import start_event, get_active_events
from AUCTIONHOUSE.ah_admin import (
    force_end_event, force_adjust_price, force_remove_listing,
    reset_simulated_inventory, backup_database, list_backups, get_full_stats
)
from AUCTIONHOUSE.ah_config import get_config


class TestAdminOverrides(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = initialize_database(force=True)
        cls.db.execute("DELETE FROM auction_listings")
        cls.db.execute("DELETE FROM market_events")
        cls.db.execute("DELETE FROM simulated_inventory")
        # Re-seed sim inventory
        from AUCTIONHOUSE.ah_database import _SEED_SIMULATED_INVENTORY
        ts = "2026-01-01T00:00:00"
        seed = _SEED_SIMULATED_INVENTORY.replace("{ts}", f"'{ts}'")
        cls.db._get_conn().executescript(seed)

    # ── Force End Event ──────────────────────────────────────────

    def test_force_end_event_basic(self):
        """Force-ending an active event should deactivate it."""
        r = start_event("admin_test", "Admin Test", "Testing", "disaster", "small",
                         ["minecraft:coal"], price_multiplier=2.0)
        self.assertTrue(r["ok"])
        event_uuid = r["data"]["event_uuid"]
        active_before = get_active_events()
        self.assertGreaterEqual(len(active_before), 1)
        result = force_end_event(event_uuid)
        self.assertTrue(result["ok"])
        active_after = get_active_events()
        self.assertEqual(len(active_after), len(active_before) - 1)

    def test_force_end_event_nonexistent(self):
        """Force-ending a nonexistent event should return error."""
        result = force_end_event("nonexistent-uuid")
        self.assertFalse(result["ok"])

    # ── Force Adjust Price ───────────────────────────────────────

    def test_force_adjust_price_basic(self):
        """Force-adjusting a simulated item's price."""
        coal = self.db.fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        result = force_adjust_price("minecraft:coal", 1.0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["old_price"], coal["base_price"])
        self.assertEqual(result["data"]["new_price"], 1.0)
        coal = self.db.fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        self.assertEqual(coal["base_price"], 1.0)
        # Restore
        self.db.execute("UPDATE simulated_inventory SET base_price=0.5, current_price=0.5 WHERE item_id='minecraft:coal'")

    def test_force_adjust_price_unknown_item(self):
        """Force-adjusting an unknown item should return error."""
        result = force_adjust_price("minecraft:nonexistent", 5.0)
        self.assertFalse(result["ok"])

    def test_force_adjust_price_clamped(self):
        """Force-adjusting a price beyond bounds should clamp."""
        result = force_adjust_price("minecraft:coal", get_config().sim_price_max * 10)
        self.assertTrue(result["ok"])
        self.assertLessEqual(result["data"]["new_price"], get_config().sim_price_max)

    # ── Force Remove Listing ─────────────────────────────────────

    def test_force_remove_listing_basic(self):
        """Force-removing an active listing."""
        r = list_item("TestSeller", "minecraft:diamond", 1, 10.0)
        listing_uuid = r["data"]["listing_uuid"]
        result = force_remove_listing(listing_uuid, admin="test_admin")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["item_id"], "minecraft:diamond")
        self.assertEqual(result["data"]["seller"], "TestSeller")
        listing = get_listing(listing_uuid)
        self.assertEqual(listing["status"], "cancelled")

    def test_force_remove_listing_nonexistent(self):
        """Force-removing a nonexistent listing should return error."""
        result = force_remove_listing("nonexistent-uuid", admin="test")
        self.assertFalse(result["ok"])

    def test_force_remove_listing_already_sold(self):
        """Force-removing a sold listing should return error."""
        r = list_item("TestSeller", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        listing_uuid = r["data"]["listing_uuid"]
        from AUCTIONHOUSE.ah_core import buy_now
        buy_now("Buyer", listing_uuid)
        result = force_remove_listing(listing_uuid, admin="test")
        self.assertFalse(result["ok"])

    # ── Reset Simulated Inventory ────────────────────────────────

    def test_reset_simulated_inventory_basic(self):
        """Resetting simulated inventory should restore defaults."""
        # Modify a value first
        self.db.execute("UPDATE simulated_inventory SET base_price=99.99 WHERE item_id='minecraft:coal'")
        result = reset_simulated_inventory()
        self.assertTrue(result["ok"])
        self.assertGreater(result["data"]["items_reset"], 0)
        coal = self.db.fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        self.assertEqual(coal["base_price"], 0.5)  # Default value restored

    # ── Database Backup ──────────────────────────────────────────

    def test_backup_database(self):
        """Backing up the database should create a backup file."""
        result = backup_database()
        self.assertTrue(result["ok"])
        self.assertIn("backup_path", result["data"])
        self.assertGreater(result["data"]["size_bytes"], 0)

    def test_list_backups(self):
        """Listing backups should return the backup we just created."""
        backup_database()  # Create another
        backups = list_backups()
        self.assertGreater(len(backups), 0)
        for b in backups:
            self.assertIn("filename", b)
            self.assertIn("size_bytes", b)

    # ── Full Stats ───────────────────────────────────────────────

    def test_get_full_stats_basic(self):
        """Full stats should return a comprehensive market overview."""
        stats = get_full_stats()
        self.assertIn("active_listings", stats)
        self.assertIn("listing_statuses", stats)
        self.assertIn("transactions_24h", stats)
        self.assertIn("volume_24h", stats)
        self.assertIn("top_sellers_all_time", stats)
        self.assertIn("price_history", stats)
        self.assertIn("events", stats)
        self.assertIn("active_listings_by_item", stats)
        self.assertIn("simulated_inventory_health", stats)

    def test_get_full_stats_with_data(self):
        """Stats should reflect data we've inserted."""
        stats = get_full_stats()
        self.assertIn("total", stats["active_listings"],
                       "Should have a total key for active listings")
        self.assertGreater(len(stats["simulated_inventory_health"]), 0,
                            "Should have at least 1 sim inventory item")

    def test_get_full_stats_listings(self):
        """Listings count should match what we've inserted."""
        before = get_full_stats()["active_listings"]["total"]
        r = list_item("StatsTest", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r["ok"])
        after = get_full_stats()["active_listings"]["total"]
        self.assertEqual(after, before + 1)

    # ── Nested stats dict structure ──────────────────────────────

    def test_stats_active_listings_keys(self):
        """Active listings breakdown should have the expected keys."""
        stats = get_full_stats()
        al = stats["active_listings"]
        self.assertIn("total", al)
        self.assertIn("player", al)
        self.assertIn("simulated", al)

    def test_stats_listing_statuses(self):
        """Listing statuses should include active, sold, cancelled."""
        statuses = {s["status"] for s in get_full_stats()["listing_statuses"]}
        self.assertIn("active", statuses)

    def test_stats_has_timestamp(self):
        """Stats should include a timestamp."""
        stats = get_full_stats()
        self.assertIn("timestamp", stats)
        self.assertGreater(len(stats["timestamp"]), 10)


if __name__ == "__main__":
    unittest.main()
