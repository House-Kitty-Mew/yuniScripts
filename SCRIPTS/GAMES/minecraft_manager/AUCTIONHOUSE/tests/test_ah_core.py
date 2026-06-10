"""
test_ah_core.py — Comprehensive unit tests for ah_core.py

Covers the FULL item lifecycle and every possible edge case:
  list_item() → place_bid() → buy_now() → cancel_listing() → expire_listings()
  query operations → balance operations → balance sync

All tests use mock RCON and mock economy bridge (patched to None).
The database is the real auctionhouse.db3 with unique item IDs for isolation.
"""

from conftest import AHTestCase, mock_rcon, reset_rcon, RCON_LOG, unique_item_id, unique_uuid
from unittest.mock import patch, MagicMock
import json, time
from datetime import datetime, timezone
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_core import (
    list_item, place_bid, buy_now, cancel_listing, expire_listings,
    query_listings, get_listing, get_player_listings, get_player_purchases,
    get_player_sales, get_bid_history, get_recent_prices, get_balance,
    update_balance, sync_balances, _update_event_progress,
)
from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_bans import ban_player, is_player_banned


# ====================================================================
# Database Tests
# ====================================================================

class TestDatabase(AHTestCase):

    def test_schema_all_tables_exist(self):
        tables = get_db().fetch_all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = {t["name"] for t in tables}
        required = {
            "auction_listings", "transaction_history", "price_history",
            "market_events", "simulated_inventory", "ai_notes", "player_balances",
        }
        for t in required:
            self.assertIn(t, names, f"Missing table: {t}")

    def test_seed_data_loaded(self):
        rows = get_db().fetch_all("SELECT item_id FROM simulated_inventory ORDER BY item_id")
        self.assertGreaterEqual(len(rows), 15)
        ids = [r["item_id"] for r in rows]
        self.assertIn("minecraft:coal", ids)
        self.assertIn("minecraft:diamond", ids)
        self.assertIn("minecraft:netherite_ingot", ids)

    def test_fetch_one_returns_none(self):
        self.assertIsNone(get_db().fetch_one("SELECT * FROM auction_listings WHERE 1=0"))

    def test_fetch_all_empty(self):
        self.assertEqual(get_db().fetch_all("SELECT * FROM transaction_history WHERE 1=0"), [])

    def test_insert_and_get_uuid(self):
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        uid = db.insert_and_get_uuid(
            "INSERT INTO auction_listings (listing_uuid, seller_name, item_id, item_count, "
            "start_price, status, listed_at, currency_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("test_player", "minecraft:diamond", 1, 10.0, "active", now, "emerald")
        )
        self.assertIsInstance(uid, str)
        self.assertEqual(len(uid), 36)
        row = db.fetch_one("SELECT * FROM auction_listings WHERE listing_uuid = ?", (uid,))
        self.assertIsNotNone(row)
        self.assertEqual(row["seller_name"], "test_player")


# ====================================================================
# list_item() Tests
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestListItem(AHTestCase):

    def test_list_item_success(self, mock_bridge):
        item = self.unique_item("minecraft:diamond")
        result = list_item(seller="test_player", item_id=item, count=3, start_price=10.0,
                           buy_now_price=25.0, duration_hours=48, rcon_func=mock_rcon)
        self.assertTrue(result["ok"])
        uid = result["data"]["listing_uuid"]

        listing = get_listing(uid)
        self.assertIsNotNone(listing)
        self.assertEqual(listing["seller_name"], "test_player")
        self.assertEqual(listing["item_id"], item)
        self.assertEqual(listing["item_count"], 3)
        self.assertEqual(listing["start_price"], 10.0)
        self.assertEqual(listing["buy_now_price"], 25.0)
        self.assertEqual(listing["status"], "active")

        clear_cmds = [c for c in RCON_LOG if c.startswith("clear")]
        self.assertGreaterEqual(len(clear_cmds), 1)
        self.assertIn("test_player", clear_cmds[0])
        self.assertIn(item, clear_cmds[0])
        self.assertIn("clear_ok", result["data"])
        self.assertIn("fee_deducted", result["data"])

    def test_list_item_simulated_no_rcon(self, mock_bridge):
        result = list_item(seller="AuctionHouse_AI", item_id=self.unique_item("minecraft:diamond"),
                           count=1, start_price=50.0, is_simulated=True)
        self.assertTrue(result["ok"])
        self.assertEqual(len(RCON_LOG), 0)

    def test_list_item_price_too_low(self, mock_bridge):
        r = list_item(seller="test_player", item_id=self.unique_item(), count=1, start_price=0.001)
        self.assertFalse(r["ok"])
        self.assertIn("too low", r["error"].lower())

    def test_list_item_bin_below_start(self, mock_bridge):
        r = list_item(seller="test_player", item_id=self.unique_item(), count=1,
                      start_price=10.0, buy_now_price=5.0)
        self.assertFalse(r["ok"])
        self.assertIn("buy-it-now", r["error"].lower())

    def test_list_item_duplicate_blocked(self, mock_bridge):
        item = self.unique_item()
        self.assertTrue(list_item(seller="test_player", item_id=item, count=1, start_price=5.0, rcon_func=mock_rcon)["ok"])
        dup = list_item(seller="test_player", item_id=item, count=1, start_price=5.0, rcon_func=mock_rcon)
        self.assertFalse(dup["ok"])
        self.assertIn("already", dup["error"].lower())

    def test_list_item_different_player_same_item_ok(self, mock_bridge):
        item = self.unique_item()
        self.assertTrue(list_item(seller="alice", item_id=item, count=1, start_price=5.0, rcon_func=mock_rcon)["ok"])
        self.assertTrue(list_item(seller="bob", item_id=item, count=1, start_price=5.0, rcon_func=mock_rcon)["ok"])

    def test_list_item_max_limit(self, mock_bridge):
        max_n = get_config().max_listings_per_player
        base = self.unique_item("minecraft:test")
        for i in range(max_n):
            r = list_item(seller="test_player", item_id=f"{base}_{i}", count=1, start_price=5.0, rcon_func=mock_rcon)
            self.assertTrue(r["ok"], f"Item {i} failed: {r.get('error')}")
        over = list_item(seller="test_player", item_id=f"{base}_over", count=1, start_price=5.0, rcon_func=mock_rcon)
        self.assertFalse(over["ok"])
        self.assertIn("max", over["error"].lower())

    def test_list_item_duration_clamped(self, mock_bridge):
        max_h = get_config().auction_duration_max_hours
        r = list_item(seller="test_player", item_id=self.unique_item(), count=1,
                      start_price=5.0, duration_hours=max_h + 100, rcon_func=mock_rcon)
        self.assertTrue(r["ok"])
        listing = get_listing(r["data"]["listing_uuid"])
        listed = datetime.fromisoformat(listing["listed_at"])
        expires = datetime.fromisoformat(listing["expires_at"])
        diff_hours = (expires - listed).total_seconds() / 3600
        self.assertAlmostEqual(diff_hours, max_h, delta=1)

    def test_list_item_banned_player_blocked(self, mock_bridge):
        ban_player("bad_player", reason="test")
        r = list_item(seller="bad_player", item_id=self.unique_item(), count=1, start_price=5.0, rcon_func=mock_rcon)
        self.assertFalse(r["ok"])
        self.assertIn("banned", r["error"].lower())

    def test_list_item_simulated_ignores_ban(self, mock_bridge):
        ban_player("bad_sim", reason="test")
        r = list_item(seller="bad_sim", item_id=self.unique_item(), count=1, start_price=5.0, is_simulated=True)
        self.assertTrue(r["ok"])

    def test_list_item_with_nbt_and_signed(self, mock_bridge):
        nbt = '{"components":{"minecraft:unbreakable":{}}}'
        r = list_item(seller="test_player", item_id=self.unique_item("minecraft:diamond_sword"),
                      count=1, start_price=10.0, item_nbt=nbt, signed_name="§bExcalibur",
                      rarity="Epic", cert_hash="abc123", rcon_func=mock_rcon)
        self.assertTrue(r["ok"])
        listing = get_listing(r["data"]["listing_uuid"])
        self.assertEqual(listing["item_nbt"], nbt)
        self.assertEqual(listing["signed_name"], "§bExcalibur")
        self.assertEqual(listing["rarity"], "Epic")
        self.assertEqual(listing["cert_hash"], "abc123")


# ====================================================================
# place_bid() Tests
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestPlaceBid(AHTestCase):

    def _create_listing(self, seller="seller", price=10.0, bin_price=None):
        r = list_item(seller=seller, item_id=self.unique_item("minecraft:diamond"),
                      count=1, start_price=price, buy_now_price=bin_price, rcon_func=mock_rcon)
        return r["data"]["listing_uuid"]

    def test_place_bid_success(self, mock_bridge):
        uid = self._create_listing()
        reset_rcon()
        r = place_bid(bidder="buyer", listing_uuid=uid, bid_amount=15.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["new_current_bid"], 15.0)
        self.assertIsNone(r["data"]["previous_bidder"])
        listing = get_listing(uid)
        self.assertEqual(listing["current_bid"], 15.0)
        self.assertEqual(listing["highest_bidder"], "buyer")
        self.assertEqual(listing["bids_count"], 1)

    def test_place_bid_too_low(self, mock_bridge):
        uid = self._create_listing(price=10.0)
        r = place_bid(bidder="buyer", listing_uuid=uid, bid_amount=10.5)
        self.assertFalse(r["ok"])
        self.assertIn("too low", r["error"].lower())

    def test_place_bid_on_own_listing(self, mock_bridge):
        uid = self._create_listing(seller="self_bidder")
        r = place_bid(bidder="self_bidder", listing_uuid=uid, bid_amount=20.0)
        self.assertFalse(r["ok"])
        self.assertIn("own listing", r["error"].lower())

    def test_place_bid_expired_listing(self, mock_bridge):
        uid = self._create_listing()
        get_db().execute("UPDATE auction_listings SET expires_at = '2000-01-01T00:00:00' WHERE listing_uuid = ?", (uid,))
        r = place_bid(bidder="buyer", listing_uuid=uid, bid_amount=20.0)
        self.assertFalse(r["ok"])
        self.assertIn("expired", r["error"].lower())

    def test_place_bid_nonexistent(self, mock_bridge):
        r = place_bid(bidder="buyer", listing_uuid=unique_uuid(), bid_amount=20.0)
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["error"].lower())

    def test_place_bid_banned_player(self, mock_bridge):
        ban_player("bad_bidder", reason="test")
        uid = self._create_listing()
        r = place_bid(bidder="bad_bidder", listing_uuid=uid, bid_amount=20.0)
        self.assertFalse(r["ok"])
        self.assertIn("banned", r["error"].lower())

    def test_place_bid_multiple_bids(self, mock_bridge):
        uid = self._create_listing(price=5.0)
        r1 = place_bid(bidder="alice", listing_uuid=uid, bid_amount=10.0)
        self.assertTrue(r1["ok"])
        r2 = place_bid(bidder="bob", listing_uuid=uid, bid_amount=15.0)
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["data"]["previous_bidder"], "alice")
        r_low = place_bid(bidder="charlie", listing_uuid=uid, bid_amount=14.0)
        self.assertFalse(r_low["ok"])

    def test_place_bid_race_condition(self, mock_bridge):
        uid = self._create_listing(price=5.0)
        get_db().execute("UPDATE auction_listings SET status='sold' WHERE listing_uuid=?", (uid,))
        r = place_bid(bidder="buyer", listing_uuid=uid, bid_amount=10.0)
        self.assertFalse(r["ok"])
        self.assertIn("sold", r["error"].lower())


# ====================================================================
# buy_now() Tests
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestBuyNow(AHTestCase):

    def _create_listing(self, seller="seller", price=10.0, bin_price=50.0, count=1):
        r = list_item(seller=seller, item_id=self.unique_item("minecraft:diamond"),
                      count=count, start_price=price, buy_now_price=bin_price, rcon_func=mock_rcon)
        return r["data"]["listing_uuid"]

    def test_buy_now_success(self, mock_bridge):
        uid = self._create_listing()
        reset_rcon()
        r = buy_now(buyer="buyer_player", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertTrue(r["ok"])
        data = r["data"]
        self.assertEqual(data["price"], 50.0)
        self.assertEqual(data["seller"], "seller")
        self.assertEqual(data["buyer"], "buyer_player")
        self.assertIn("seller_payout", data)
        self.assertIn("fee", data)
        self.assertEqual(get_listing(uid)["status"], "sold")
        self.assertGreaterEqual(len([c for c in RCON_LOG if c.startswith("give")]), 1)

    def test_buy_now_no_bin_price(self, mock_bridge):
        lr = list_item(seller="seller", item_id=self.unique_item(), count=1,
                       start_price=5.0, rcon_func=mock_rcon)
        r = buy_now(buyer="buyer", listing_uuid=lr["data"]["listing_uuid"], rcon_func=mock_rcon)
        self.assertFalse(r["ok"])
        self.assertIn("no buy-it-now", r["error"].lower())

    def test_buy_now_own_listing(self, mock_bridge):
        uid = self._create_listing(seller="self_buyer")
        r = buy_now(buyer="self_buyer", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertFalse(r["ok"])
        self.assertIn("own listing", r["error"].lower())

    def test_buy_now_already_sold(self, mock_bridge):
        uid = self._create_listing()
        buy_now(buyer="alice", listing_uuid=uid, rcon_func=mock_rcon)
        r2 = buy_now(buyer="bob", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertFalse(r2["ok"])
        # The exact error depends on test ordering (listing may be sold/purchased/already claimed)

    def test_buy_now_partial_quantity(self, mock_bridge):
        uid = self._create_listing(count=5)
        r = buy_now(buyer="buyer", listing_uuid=uid, quantity=2, rcon_func=mock_rcon)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["item_count"], 2)

    def test_buy_now_not_enough_quantity(self, mock_bridge):
        uid = self._create_listing(count=3)
        r = buy_now(buyer="buyer", listing_uuid=uid, quantity=10, rcon_func=mock_rcon)
        self.assertFalse(r["ok"])
        self.assertIn("only", r["error"].lower())

    def test_buy_now_banned_buyer(self, mock_bridge):
        ban_player("bad_buyer", reason="test")
        uid = self._create_listing()
        r = buy_now(buyer="bad_buyer", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertFalse(r["ok"])
        self.assertIn("banned", r["error"].lower())


# ====================================================================
# cancel_listing() Tests
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestCancelListing(AHTestCase):

    def _create_listing(self, seller="seller", price=10.0, bin_price=None):
        r = list_item(seller=seller, item_id=self.unique_item("minecraft:diamond"),
                      count=1, start_price=price, buy_now_price=bin_price, rcon_func=mock_rcon)
        return r["data"]["listing_uuid"]

    def test_cancel_success(self, mock_bridge):
        uid = self._create_listing()
        reset_rcon()
        r = cancel_listing(player="seller", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertTrue(r["ok"])
        self.assertTrue("minecraft:diamond" in r["data"]["item_id"])
        self.assertEqual(get_listing(uid)["status"], "cancelled")
        self.assertGreaterEqual(len([c for c in RCON_LOG if c.startswith("give")]), 1)

    def test_cancel_wrong_seller(self, mock_bridge):
        uid = self._create_listing(seller="real_seller")
        r = cancel_listing(player="imposter", listing_uuid=uid)
        self.assertFalse(r["ok"])
        self.assertIn("seller", r["error"].lower())

    def test_cancel_already_sold(self, mock_bridge):
        uid = self._create_listing(bin_price=50.0)
        buy_now(buyer="buyer", listing_uuid=uid, rcon_func=mock_rcon)
        r = cancel_listing(player="seller", listing_uuid=uid)
        self.assertFalse(r["ok"])
        self.assertIn("sold", r["error"].lower())

    def test_cancel_not_found(self, mock_bridge):
        r = cancel_listing(player="seller", listing_uuid=unique_uuid())
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["error"].lower())

    def test_cancel_with_bids_fee(self, mock_bridge):
        uid = self._create_listing(price=5.0)
        place_bid(bidder="bidder", listing_uuid=uid, bid_amount=10.0)
        reset_rcon()
        r = cancel_listing(player="seller", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertTrue(r["ok"])
        self.assertTrue(r["data"]["had_bids"])
        self.assertGreater(r["data"]["cancel_fee"], 0)


# ====================================================================
# expire_listings() Tests
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestExpireListings(AHTestCase):

    def _create_expired(self, seller="seller", price=10.0, bidder=None, bid_amount=None):
        r = list_item(seller=seller, item_id=self.unique_item("minecraft:diamond"),
                      count=1, start_price=price, duration_hours=1, rcon_func=mock_rcon)
        uid = r["data"]["listing_uuid"]
        # Place bid FIRST, THEN expire the listing
        if bidder and bid_amount:
            place_bid(bidder=bidder, listing_uuid=uid, bid_amount=bid_amount)
        get_db().execute("UPDATE auction_listings SET expires_at = '2020-01-01T00:00:00' WHERE listing_uuid = ?", (uid,))
        return uid

    def test_expire_no_bids(self, mock_bridge):
        uid = self._create_expired()
        results = expire_listings()
        sold = [r for r in results if r["listing_uuid"] == uid]
        self.assertEqual(len(sold), 1)
        self.assertEqual(sold[0]["outcome"], "expired")
        self.assertEqual(get_listing(uid)["status"], "expired")

    def test_expire_with_bids(self, mock_bridge):
        uid = self._create_expired(price=5.0, bidder="winner", bid_amount=10.0)
        results = expire_listings()
        sold = [r for r in results if r["listing_uuid"] == uid]
        self.assertEqual(len(sold), 1)
        self.assertEqual(sold[0]["outcome"], "sold")
        self.assertEqual(sold[0]["winner"], "winner")
        self.assertEqual(get_listing(uid)["status"], "sold")

    def test_expire_idempotent(self, mock_bridge):
        self._create_expired()
        r1 = expire_listings()
        r2 = expire_listings()
        self.assertIsInstance(r1, list)
        self.assertIsInstance(r2, list)


# ====================================================================
# Query Tests
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestQuery(AHTestCase):

    def test_query_all(self, mock_bridge):
        list_item(seller="alice", item_id=self.unique_item(), count=1, start_price=5.0, rcon_func=mock_rcon)
        list_item(seller="bob", item_id=self.unique_item(), count=1, start_price=10.0, rcon_func=mock_rcon)
        r = query_listings(filter_type="all")
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["data"]["total"], 2)

    def test_query_my_listings(self, mock_bridge):
        list_item(seller="alice", item_id=self.unique_item(), count=1, start_price=5.0, rcon_func=mock_rcon)
        list_item(seller="bob", item_id=self.unique_item(), count=1, start_price=5.0, rcon_func=mock_rcon)
        r = query_listings(filter_type="my", filter_value="alice")
        self.assertEqual(r["data"]["total"], 1)

    def test_query_by_item(self, mock_bridge):
        target = self.unique_item("minecraft:netherite_sword")
        list_item(seller="alice", item_id=target, count=1, start_price=5.0, rcon_func=mock_rcon)
        list_item(seller="bob", item_id=self.unique_item("minecraft:dirt"), count=1, start_price=5.0, rcon_func=mock_rcon)
        r = query_listings(filter_type="item", filter_value="netherite")
        self.assertGreaterEqual(r["data"]["total"], 1)

    def test_query_simulated(self, mock_bridge):
        list_item(seller="AI", item_id=self.unique_item(), count=1, start_price=5.0, is_simulated=True)
        list_item(seller="player", item_id=self.unique_item(), count=1, start_price=5.0, rcon_func=mock_rcon)
        r = query_listings(filter_type="simulated")
        self.assertGreaterEqual(r["data"]["total"], 1)

    def test_query_pagination(self, mock_bridge):
        for i in range(5):
            list_item(seller=f"player{i}", item_id=self.unique_item(), count=1, start_price=5.0, rcon_func=mock_rcon)
        r = query_listings(filter_type="all", page=1, per_page=2)
        self.assertEqual(r["data"]["per_page"], 2)
        self.assertEqual(len(r["data"]["listings"]), 2)

    def test_get_player_listings(self, mock_bridge):
        list_item(seller="alice", item_id=self.unique_item(), count=1, start_price=5.0, rcon_func=mock_rcon)
        listings = get_player_listings("alice")
        self.assertGreaterEqual(len(listings), 1)
        self.assertEqual(listings[0]["seller_name"], "alice")

    def test_get_player_purchases_and_sales(self, mock_bridge):
        item = self.unique_item()
        lr = list_item(seller="seller", item_id=item, count=1, start_price=5.0,
                       buy_now_price=20.0, rcon_func=mock_rcon)
        buy_now(buyer="buyer", listing_uuid=lr["data"]["listing_uuid"], rcon_func=mock_rcon)
        purchases = get_player_purchases("buyer")
        self.assertGreaterEqual(len(purchases), 1)
        sales = get_player_sales("seller")
        self.assertGreaterEqual(len(sales), 1)

    def test_get_bid_history(self, mock_bridge):
        lr = list_item(seller="seller", item_id=self.unique_item(), count=1, start_price=5.0, rcon_func=mock_rcon)
        uid = lr["data"]["listing_uuid"]
        place_bid(bidder="alice", listing_uuid=uid, bid_amount=10.0)
        place_bid(bidder="bob", listing_uuid=uid, bid_amount=15.0)
        bids = get_bid_history(uid)
        self.assertGreaterEqual(len(bids), 2)
        self.assertEqual(bids[0]["transaction_type"], "bid")

    def test_get_recent_prices(self, mock_bridge):
        item = self.unique_item()
        lr = list_item(seller="seller", item_id=item, count=1, start_price=5.0,
                       buy_now_price=20.0, rcon_func=mock_rcon)
        buy_now(buyer="buyer", listing_uuid=lr["data"]["listing_uuid"], rcon_func=mock_rcon)
        prices = get_recent_prices(item, days=30)
        self.assertGreaterEqual(len(prices), 1)


# ====================================================================
# Balance Tests
# ====================================================================

class TestBalances(AHTestCase):

    def test_get_balance_no_bridge_no_record(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            bal = get_balance("unknown_player", use_bridge=True)
            self.assertIsNone(bal)

    def test_update_balance_credit(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            new_bal = update_balance("test_player", 100, reason="TEST_CREDIT")
            self.assertEqual(new_bal, 100)
            bal = get_balance("test_player")
            self.assertEqual(bal, 100)

    def test_update_balance_debit(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            update_balance("test_player", 200, reason="INIT")
            update_balance("test_player", -50, reason="TEST_DEBIT")
            row = get_db().fetch_one("SELECT balance, lifetime_spent FROM player_balances WHERE player_name = ?", ("test_player",))
            self.assertEqual(row["balance"], 150)
            self.assertEqual(row["lifetime_spent"], 50)

    def test_get_balance_with_bridge(self):
        mock_bridge = MagicMock()
        mock_bridge.get_balance.return_value = 500
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=mock_bridge):
            bal = get_balance("player_with_bridge", use_bridge=True)
            self.assertEqual(bal, 500)

    def test_get_balance_bridge_no_player(self):
        mock_bridge = MagicMock()
        mock_bridge.get_balance.return_value = None
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=mock_bridge):
            bal = get_balance("new_player", use_bridge=True)
            self.assertIsNone(bal)

    def test_sync_balances_no_bridge(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            result = sync_balances()
            self.assertEqual(result["status"], "unavailable")


# ====================================================================
# Full Lifecycle Tests
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestFullLifecycle(AHTestCase):

    def test_list_cancel_relist_buy(self, mock_bridge):
        item = self.unique_item("minecraft:diamond")
        r1 = list_item(seller="alice", item_id=item, count=1, start_price=5.0,
                       buy_now_price=15.0, rcon_func=mock_rcon)
        self.assertTrue(r1["ok"])
        uid = r1["data"]["listing_uuid"]

        reset_rcon()
        r2 = cancel_listing(player="alice", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertTrue(r2["ok"])
        self.assertEqual(get_listing(uid)["status"], "cancelled")

        reset_rcon()
        r3 = list_item(seller="alice", item_id=item, count=1, start_price=5.0,
                       buy_now_price=15.0, rcon_func=mock_rcon)
        self.assertTrue(r3["ok"])
        uid2 = r3["data"]["listing_uuid"]

        reset_rcon()
        r4 = buy_now(buyer="bob", listing_uuid=uid2, rcon_func=mock_rcon)
        self.assertTrue(r4["ok"])
        self.assertEqual(get_listing(uid2)["status"], "sold")
        give_cmds = [c for c in RCON_LOG if c.startswith("give")]
        self.assertIn("bob", give_cmds[0])

    def test_three_player_market(self, mock_bridge):
        item = self.unique_item("minecraft:netherite_sword")
        lr = list_item(seller="alice", item_id=item, count=1, start_price=10.0, rcon_func=mock_rcon)
        uid = lr["data"]["listing_uuid"]

        r1 = place_bid(bidder="charlie", listing_uuid=uid, bid_amount=15.0)
        self.assertTrue(r1["ok"])
        r2 = place_bid(bidder="bob", listing_uuid=uid, bid_amount=20.0)
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["data"]["previous_bidder"], "charlie")
        r3 = place_bid(bidder="charlie", listing_uuid=uid, bid_amount=18.0)
        self.assertFalse(r3["ok"])

        listing = get_listing(uid)
        self.assertEqual(listing["highest_bidder"], "bob")
        self.assertEqual(listing["current_bid"], 20.0)

    def test_sim_item_full_flow(self, mock_bridge):
        item = self.unique_item("minecraft:diamond_pickaxe")
        lr = list_item(seller="AH_Sim", item_id=item, count=1, start_price=30.0,
                       buy_now_price=60.0, is_simulated=True, sim_lore="A test pickaxe",
                       sim_enchantments=[{"id": "minecraft:efficiency", "level": 3}],
                       sim_durability=80, sim_quality_roll=75)
        self.assertTrue(lr["ok"])
        uid = lr["data"]["listing_uuid"]
        listing = get_listing(uid)
        self.assertEqual(listing["is_simulated"], 1)
        self.assertEqual(listing["sim_lore"], "A test pickaxe")
        self.assertEqual(listing["sim_durability"], 80)
        self.assertEqual(listing["sim_quality_roll"], 75)

        reset_rcon()
        r = buy_now(buyer="player", listing_uuid=uid, quantity=1)
        self.assertTrue(r["ok"])


# ====================================================================
# Race Condition Stress Tests
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestRaceConditions(AHTestCase):

    def test_double_buy_race(self, mock_bridge):
        lr = list_item(seller="seller", item_id=self.unique_item(), count=1,
                       start_price=5.0, buy_now_price=20.0, rcon_func=mock_rcon)
        uid = lr["data"]["listing_uuid"]
        r1 = buy_now(buyer="alice", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertTrue(r1["ok"])
        r2 = buy_now(buyer="bob", listing_uuid=uid, rcon_func=mock_rcon)
        self.assertFalse(r2["ok"])

    def test_bid_after_buy_race(self, mock_bridge):
        lr = list_item(seller="seller", item_id=self.unique_item(), count=1,
                       start_price=5.0, buy_now_price=20.0, rcon_func=mock_rcon)
        uid = lr["data"]["listing_uuid"]
        buy_now(buyer="alice", listing_uuid=uid, rcon_func=mock_rcon)
        r = place_bid(bidder="bob", listing_uuid=uid, bid_amount=25.0)
        self.assertFalse(r["ok"])

    def test_cancel_after_buy_race(self, mock_bridge):
        lr = list_item(seller="seller", item_id=self.unique_item(), count=1,
                       start_price=5.0, buy_now_price=20.0, rcon_func=mock_rcon)
        uid = lr["data"]["listing_uuid"]
        buy_now(buyer="buyer", listing_uuid=uid, rcon_func=mock_rcon)
        r = cancel_listing(player="seller", listing_uuid=uid)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    from unittest import main
    main()
