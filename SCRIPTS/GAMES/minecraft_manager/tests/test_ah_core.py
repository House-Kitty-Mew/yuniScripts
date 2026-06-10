"""
test_ah_core.py — Comprehensive tests for the core auction CRUD operations.

Tests cover:
  - Basic listing, bidding, buying, cancelling
  - Atomic race condition prevention
  - Duplicate item detection
  - Player listing limits
  - Ban enforcement
  - Balance checking (when bridge connected)
  - Expiry processing
  - Error handling and edge cases
  - Money sync

All tests use an in-memory database. No RCON, no network, no permanent changes.
"""

import unittest, sys, os, json, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force test DB
os.environ["AH_DB_PATH"] = ":memory:"

from AUCTIONHOUSE.ah_database import get_db, initialize_database
from AUCTIONHOUSE.ah_core import (
    list_item, place_bid, buy_now, cancel_listing,
    query_listings, get_listing, get_player_listings,
    expire_listings, get_balance, update_balance, sync_balances,
    get_player_purchases, get_player_sales, get_bid_history, get_recent_prices
)
from AUCTIONHOUSE.ah_config import get_config


class TestAuctionCore(unittest.TestCase):
    """Test all core auction operations with edge cases."""

    @classmethod
    def setUpClass(cls):
        initialize_database(force=True)
        cls.cfg = get_config(reload=True)

    def setUp(self):
        # Start each test with a clean listing slate
        self.db = get_db()
        self.db.execute("DELETE FROM auction_listings")
        self.db.execute("DELETE FROM transaction_history")
        self.db.execute("DELETE FROM player_balances")

    # ── Listing ─────────────────────────────────────────────────

    def test_list_item_basic(self):
        """Basic listing of an item."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r["ok"])
        self.assertIn("listing_uuid", r["data"])
        listing = get_listing(r["data"]["listing_uuid"])
        self.assertIsNotNone(listing)
        self.assertEqual(listing["seller_name"], "Steve")
        self.assertEqual(listing["item_id"], "minecraft:diamond")
        self.assertEqual(listing["start_price"], 10.0)
        self.assertEqual(listing["status"], "active")

    def test_list_item_with_bin(self):
        """Listing with Buy-It-Now price."""
        r = list_item("Alex", "minecraft:iron_ingot", 5, 3.0, buy_now_price=8.0)
        self.assertTrue(r["ok"])
        listing = get_listing(r["data"]["listing_uuid"])
        self.assertEqual(listing["buy_now_price"], 8.0)

    def test_list_item_invalid_price(self):
        """Listing with too-low start price should fail."""
        r = list_item("Steve", "minecraft:dirt", 1, 0.001)
        self.assertFalse(r["ok"])
        self.assertIn("too low", r["error"].lower())

    def test_list_item_bin_lower_than_start(self):
        """BIN lower than start price should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 20.0, buy_now_price=10.0)
        self.assertFalse(r["ok"])

    def test_list_item_duplicate(self):
        """Same player listing the same item twice should fail."""
        r1 = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r1["ok"])
        r2 = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertFalse(r2["ok"])
        self.assertIn("already have", r2["error"].lower())

    def test_list_item_max_limit(self):
        """Player with max listings should be blocked."""
        cfg = get_config()
        for i in range(cfg.max_listings_per_player):
            r = list_item("Steve", f"minecraft:item_{i}", 1, 5.0)
            self.assertTrue(r["ok"], f"Failed on listing {i}")
        r = list_item("Steve", "minecraft:extra", 1, 5.0)
        self.assertFalse(r["ok"])
        self.assertIn("max", r["error"].lower())

    def test_list_item_simulated_no_limit(self):
        """Simulated items bypass the player limit."""
        cfg = get_config()
        for i in range(cfg.max_listings_per_player + 2):
            r = list_item("AI", "minecraft:coal", 64, 0.5, is_simulated=True)
            self.assertTrue(r["ok"])

    def test_list_item_different_sellers(self):
        """Different players can list the same item."""
        r1 = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = list_item("Alex", "minecraft:diamond", 1, 15.0)
        self.assertTrue(r1["ok"])
        self.assertTrue(r2["ok"])

    def test_list_item_nbt_preserved(self):
        """Item NBT data should be preserved."""
        nbt = '{"components":{"minecraft:enchantments":{"levels":{"sharpness":5}}}}'
        r = list_item("Steve", "minecraft:diamond_sword", 1, 50.0, item_nbt=nbt)
        self.assertTrue(r["ok"])
        listing = get_listing(r["data"]["listing_uuid"])
        self.assertEqual(listing["item_nbt"], nbt)

    # ── Bidding ─────────────────────────────────────────────────

    def test_place_bid_basic(self):
        """Basic bid placement."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        listing_uuid = r["data"]["listing_uuid"]
        r2 = place_bid("Alex", listing_uuid, 12.0)
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["data"]["new_current_bid"], 12.0)

    def test_place_bid_too_low(self):
        """Bid below minimum should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = place_bid("Alex", r["data"]["listing_uuid"], 10.5)
        self.assertFalse(r2["ok"])
        self.assertIn("too low", r2["error"].lower())

    def test_place_bid_own_listing(self):
        """Bidding on own listing should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = place_bid("Steve", r["data"]["listing_uuid"], 12.0)
        self.assertFalse(r2["ok"])
        self.assertIn("own", r2["error"].lower())

    def test_place_bid_expired_listing(self):
        """Bidding on expired listing should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, duration_hours=0)
        import time; time.sleep(0.1)
        r2 = place_bid("Alex", r["data"]["listing_uuid"], 12.0)
        self.assertFalse(r2["ok"])

    def test_place_bid_atomic_race(self):
        """Simulate race condition: two bids at once should be safe."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        results = []
        def bid_safe(bidder, amount):
            r = place_bid(bidder, lu, amount)
            results.append(r)

        t1 = threading.Thread(target=bid_safe, args=("Alex", 12.0))
        t2 = threading.Thread(target=bid_safe, args=("Bob", 12.0))
        t1.start(); t2.start()
        t1.join(); t2.join()

        ok_count = sum(1 for r in results if r["ok"])
        self.assertLessEqual(ok_count, 1, "Only one bid should succeed atomic")
        listing = get_listing(lu)
        self.assertIsNotNone(listing)
        self.assertEqual(listing["bids_count"], ok_count)

    # ── Buy-It-Now ──────────────────────────────────────────────

    def test_buy_now_basic(self):
        """Basic Buy-It-Now purchase."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]
        r2 = buy_now("Alex", lu)
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["data"]["price"], 25.0)
        self.assertEqual(r2["data"]["buyer"], "Alex")
        self.assertEqual(r2["data"]["seller"], "Steve")
        listing = get_listing(lu)
        self.assertEqual(listing["status"], "sold")

    def test_buy_now_no_bin(self):
        """Buying a listing without BIN should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = buy_now("Alex", r["data"]["listing_uuid"])
        self.assertFalse(r2["ok"])
        self.assertIn("Buy-It-Now", r2["error"])

    def test_buy_now_own_listing(self):
        """Buying own listing should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        r2 = buy_now("Steve", r["data"]["listing_uuid"])
        self.assertFalse(r2["ok"])
        self.assertIn("own", r2["error"].lower())

    def test_buy_now_already_sold(self):
        """Buying an already-sold item should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]
        buy_now("Alex", lu)
        r2 = buy_now("Bob", lu)
        self.assertFalse(r2["ok"])
        self.assertIn("sold", r2["error"].lower())

    def test_buy_now_atomic_race(self):
        """Simulate race condition: two BINs at once should be safe."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]

        results = []
        def buy_safe(buyer):
            r = buy_now(buyer, lu)
            results.append(r)

        t1 = threading.Thread(target=buy_safe, args=("Alex",))
        t2 = threading.Thread(target=buy_safe, args=("Bob",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        ok_count = sum(1 for r in results if r["ok"])
        self.assertEqual(ok_count, 1, "Only one BIN should succeed")

    # ── Cancelling ──────────────────────────────────────────────

    def test_cancel_basic(self):
        """Basic cancellation."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]
        r2 = cancel_listing("Steve", lu)
        self.assertTrue(r2["ok"])
        listing = get_listing(lu)
        self.assertEqual(listing["status"], "cancelled")

    def test_cancel_wrong_seller(self):
        """Only the seller can cancel."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = cancel_listing("Alex", r["data"]["listing_uuid"])
        self.assertFalse(r2["ok"])

    def test_cancel_already_sold(self):
        """Sold items can't be cancelled."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]
        buy_now("Alex", lu)
        r2 = cancel_listing("Steve", lu)
        self.assertFalse(r2["ok"])

    # ── Expiry ──────────────────────────────────────────────────

    def test_expire_no_bids(self):
        """Expired listing with no bids should return item."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, duration_hours=0)
        import time; time.sleep(0.1)
        results = expire_listings()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["outcome"], "expired")

    def test_expire_with_bids(self):
        """Expired listing with bids should go to highest bidder."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, duration_hours=24)
        lu = r["data"]["listing_uuid"]
        place_bid("Alex", lu, 12.0)
        # Force expiry by updating the timestamp directly
        self.db.execute("UPDATE auction_listings SET expires_at = '2020-01-01' WHERE listing_uuid = ?", (lu,))
        results = expire_listings()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["outcome"], "sold")

    # ── Querying ────────────────────────────────────────────────

    def test_query_all(self):
        """Query all active listings."""
        list_item("Steve", "minecraft:diamond", 1, 10.0)
        list_item("Alex", "minecraft:iron_ingot", 5, 3.0)
        q = query_listings(filter_type="all")
        self.assertTrue(q["ok"])
        self.assertEqual(q["data"]["total"], 2)

    def test_query_my_listings(self):
        """Query a specific player's listings."""
        list_item("Steve", "minecraft:diamond", 1, 10.0)
        list_item("Steve", "minecraft:emerald", 3, 5.0)
        list_item("Alex", "minecraft:iron_ingot", 5, 3.0)
        listings = get_player_listings("Steve")
        self.assertEqual(len(listings), 2)

    def test_query_by_item(self):
        """Query listings by item ID."""
        list_item("Steve", "minecraft:diamond", 1, 10.0)
        list_item("Alex", "minecraft:diamond", 2, 15.0)
        list_item("Bob", "minecraft:coal", 64, 1.0)
        q = query_listings(filter_type="item", filter_value="diamond")
        self.assertEqual(q["data"]["total"], 2)

    def test_query_simulated(self):
        """Query only simulated listings."""
        list_item("AI", "minecraft:coal", 64, 0.5, is_simulated=True)
        list_item("Steve", "minecraft:diamond", 1, 10.0)
        q = query_listings(filter_type="simulated")
        self.assertEqual(q["data"]["total"], 1)

    def test_query_paginated(self):
        """Query pagination."""
        for i in range(25):
            list_item(f"Player{i}", f"minecraft:item_{i}", 1, 5.0)
        q1 = query_listings(filter_type="all", page=1, per_page=10)
        self.assertEqual(len(q1["data"]["listings"]), 10)
        self.assertEqual(q1["data"]["total_pages"], 3)

    # ── Player Operations History ───────────────────────────────

    def test_get_player_purchases(self):
        """Track what a player has bought."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        buy_now("Alex", r["data"]["listing_uuid"])
        purchases = get_player_purchases("Alex")
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0]["transaction_type"], "buy")

    def test_get_player_sales(self):
        """Track what a player has sold."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        buy_now("Alex", r["data"]["listing_uuid"])
        sales = get_player_sales("Steve")
        self.assertEqual(len(sales), 1)

    def test_get_bid_history(self):
        """Track bid history for a listing."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]
        place_bid("Alex", lu, 15.0)
        place_bid("Bob", lu, 20.0)
        history = get_bid_history(lu)
        self.assertEqual(len(history), 2)

    def test_get_recent_prices(self):
        """Track recent sale prices for an item."""
        r1 = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        buy_now("Alex", r1["data"]["listing_uuid"])
        r2 = list_item("Bob", "minecraft:diamond", 1, 10.0, buy_now_price=30.0)
        buy_now("Charlie", r2["data"]["listing_uuid"])
        prices = get_recent_prices("minecraft:diamond", days=7)
        self.assertEqual(len(prices), 2)

    # ── Balance & Money Sync ────────────────────────────────────

    def test_get_balance_no_record(self):
        """Getting balance for unknown player returns None."""
        bal = get_balance("NobodyHere")
        self.assertIsNone(bal)

    def test_update_balance_create(self):
        """Updating a new player's balance creates a record."""
        bal = update_balance("Steve", 100, reason="TEST_CREDIT")
        self.assertIsNotNone(bal)
        self.assertEqual(bal, 100)

    def test_update_balance_deduct(self):
        """Deducting from a player's balance."""
        update_balance("Steve", 100, reason="TEST_CREDIT")
        bal = update_balance("Steve", -25, reason="TEST_DEDUCT")
        self.assertEqual(bal, 75)

    def test_update_balance_get(self):
        """get_balance returns the stored balance."""
        update_balance("Steve", 500, reason="TEST")
        bal = get_balance("Steve")
        self.assertEqual(bal, 500)

    def test_sync_balances_no_bridge(self):
        """sync_balances is safe with or without bridge."""
        result = sync_balances()
        # If bridge available: sync returns checked+fixed; if not: unavailable
        self.assertIn("status", result)
        self.assertIn(result["status"], ("ok", "unavailable"), f"Unexpected status: {result['status']}")

    # ── Edge Cases ──────────────────────────────────────────────

    def test_list_item_zero_price(self):
        """Zero price listing should fail."""
        r = list_item("Steve", "minecraft:dirt", 1, 0.0)
        self.assertFalse(r["ok"])

    def test_list_item_negative_price(self):
        """Negative price should fail."""
        r = list_item("Steve", "minecraft:dirt", 1, -5.0)
        self.assertFalse(r["ok"])

    def test_place_bid_negative(self):
        """Negative bid should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = place_bid("Alex", r["data"]["listing_uuid"], -5.0)
        self.assertFalse(r2["ok"])

    def test_buy_now_quantity_exceed(self):
        """Buying more than available should fail."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        r2 = buy_now("Alex", r["data"]["listing_uuid"], quantity=5)
        self.assertFalse(r2["ok"])

    def test_multiple_listings(self):
        """Multiple listings by different players."""
        for i, seller in enumerate(["Steve", "Alex", "Bob"]):
            r = list_item(seller, f"minecraft:item_{i}", 1, 10.0)
            self.assertTrue(r["ok"])
        q = query_listings(filter_type="all")
        self.assertEqual(q["data"]["total"], 3)

    def test_cancel_listing_with_bids(self):
        """Cancel a listing that has bids (fee applies)."""
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]
        place_bid("Alex", lu, 15.0)
        r2 = cancel_listing("Steve", lu)
        self.assertTrue(r2["ok"])
        self.assertTrue(r2["data"]["had_bids"])
        self.assertGreater(r2["data"]["cancel_fee"], 0)

    def test_ban_check_via_core(self):
        """Verifying that banned players can't list items."""
        from AUCTIONHOUSE.ah_bans import ban_player
        ban_player("BannedSteve", "Testing ban enforcement")
        r = list_item("BannedSteve", "minecraft:diamond", 1, 10.0)
        self.assertFalse(r["ok"])
        self.assertIn("ban", r["error"].lower())
        from AUCTIONHOUSE.ah_bans import unban_player
        unban_player("BannedSteve")


if __name__ == "__main__":
    unittest.main()
