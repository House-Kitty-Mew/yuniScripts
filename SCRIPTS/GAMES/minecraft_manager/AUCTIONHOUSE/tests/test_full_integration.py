"""
test_full_integration.py — End-to-end data flow tests
"""

from conftest import AHTestCase, mock_rcon, reset_rcon, RCON_LOG
from unittest.mock import patch, MagicMock
from AUCTIONHOUSE.ah_core import (
    list_item, place_bid, buy_now, cancel_listing, expire_listings,
    query_listings, get_listing, get_bid_history, get_player_listings,
)
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_bans import ban_player, unban_player, is_player_banned


# ====================================================================
# Integration 1: 3-Player Auction
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestCompletePlayerEconomyFlow(AHTestCase):

    def test_three_player_auction_flow(self, mock_bridge):
        """Alice lists → Bob bids → Charlie outbids → expires → Charlie wins."""
        item = self.unique_item("minecraft:diamond_sword")
        lr = list_item(seller="Alice", item_id=item, count=1, start_price=10.0,
                       buy_now_price=50.0, duration_hours=48, rcon_func=mock_rcon)
        self.assertTrue(lr["ok"])
        uid = lr["data"]["listing_uuid"]

        self.assertTrue(place_bid(bidder="Bob", listing_uuid=uid, bid_amount=15.0)["ok"])
        r = place_bid(bidder="Charlie", listing_uuid=uid, bid_amount=20.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["previous_bidder"], "Bob")

        # Bob tries too low
        self.assertFalse(place_bid(bidder="Bob", listing_uuid=uid, bid_amount=18.0)["ok"])

        # Charlie raises
        self.assertTrue(place_bid(bidder="Charlie", listing_uuid=uid, bid_amount=25.0)["ok"])

        # Force expire (bids already placed, so it should be 'sold' to Charlie)
        get_db().execute("UPDATE auction_listings SET expires_at = '2020-01-01' WHERE listing_uuid = ?", (uid,))
        expire_listings()

        listing = get_listing(uid)
        self.assertEqual(listing["status"], "sold")
        self.assertEqual(listing["highest_bidder"], "Charlie")
        self.assertEqual(listing["sold_price"], 25.0)

        bids = get_bid_history(uid)
        self.assertGreaterEqual(len(bids), 3)


# ====================================================================
# Integration 2: Ban Lifecycle
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestBanLifecycle(AHTestCase):

    def test_ban_blocks_all_actions(self, mock_bridge):
        ban_player("BadSteve", reason="Test violation")

        # Cannot list
        r = list_item(seller="BadSteve", item_id=self.unique_item(), count=1,
                      start_price=5.0, rcon_func=mock_rcon)
        self.assertFalse(r["ok"])
        self.assertIn("banned", r["error"].lower())

        # Cannot bid
        list_item(seller="GoodPlayer", item_id=self.unique_item(), count=1,
                  start_price=5.0, rcon_func=mock_rcon)
        qr = query_listings(filter_type="my", filter_value="GoodPlayer")
        if qr["data"]["listings"]:
            uid = qr["data"]["listings"][0]["listing_uuid"]
            r = place_bid(bidder="BadSteve", listing_uuid=uid, bid_amount=10.0)
            self.assertFalse(r["ok"])
            self.assertIn("banned", r["error"].lower())

        # Unban
        unban_player("BadSteve")
        r = list_item(seller="BadSteve", item_id=self.unique_item(), count=1,
                      start_price=5.0, rcon_func=mock_rcon)
        self.assertTrue(r["ok"])


# ====================================================================
# Integration 3: Market Event → Price → Purchase
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestMarketEventIntegration(AHTestCase):

    def setUp(self):
        super().setUp()
        get_db().execute("DELETE FROM market_events")

    def test_event_price_change_affects_purchases(self, mock_bridge):
        from AUCTIONHOUSE.ah_market_events import start_event
        coal_before = get_db().fetch_one(
            "SELECT base_price, current_price FROM simulated_inventory WHERE item_id = 'minecraft:coal'")
        start_event(event_name="blizzard", event_title="❄ Blizzard!", event_flavor="Coal is scarce!",
                    event_type="disaster", rarity_tier="medium",
                    affected_items=["minecraft:coal"], price_multiplier=3.0)
        coal_after = get_db().fetch_one(
            "SELECT current_price FROM simulated_inventory WHERE item_id = 'minecraft:coal'")
        expected = (coal_before["base_price"] or 0.5) * 3.0
        self.assertAlmostEqual(coal_after["current_price"], expected, places=2)

    def test_event_resolves_when_goal_met(self, mock_bridge):
        from AUCTIONHOUSE.ah_market_events import start_event, check_event_progress
        r = start_event(event_name="test_resolve", event_title="Test Resolve", event_flavor="test",
                        event_type="seasonal", rarity_tier="small",
                        affected_items=["minecraft:coal"], goal_count=10)
        uid = r["data"]["event_uuid"]
        get_db().execute("UPDATE market_events SET current_count = 10 WHERE event_uuid = ?", (uid,))
        check_event_progress()
        event = get_db().fetch_one("SELECT is_active, ended_at FROM market_events WHERE event_uuid = ?", (uid,))
        self.assertEqual(event["is_active"], 0)
        self.assertIsNotNone(event["ended_at"])


# ====================================================================
# Integration 4: Price History
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestPriceHistoryIntegration(AHTestCase):

    def test_take_snapshot(self, mock_bridge):
        from AUCTIONHOUSE.ah_price_history import take_snapshot
        records = take_snapshot()
        self.assertGreater(records, 0)
        snapshots = get_db().fetch_all("SELECT DISTINCT item_id FROM price_history")
        self.assertGreaterEqual(len(snapshots), 15)

    def test_snapshot_with_transactions(self, mock_bridge):
        from AUCTIONHOUSE.ah_price_history import take_snapshot
        for i in range(3):
            item = self.unique_item("minecraft:diamond")
            lr = list_item(seller="S", item_id=item, count=1, start_price=10.0,
                           buy_now_price=20.0, rcon_func=mock_rcon)
            buy_now(buyer="B", listing_uuid=lr["data"]["listing_uuid"], rcon_func=mock_rcon)
        records = take_snapshot()
        self.assertGreater(records, 0)


# ====================================================================
# Integration 5: Admin + System
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestAdminAndSystemIntegration(AHTestCase):

    def test_force_adjust_then_reset(self, mock_bridge):
        from AUCTIONHOUSE.ah_admin import force_adjust_price, reset_simulated_inventory
        base = get_db().fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id = 'minecraft:diamond'")["base_price"]
        # Use a price within the valid range (sim_price_max is 500.0)
        force_adjust_price("minecraft:diamond", 250.0)
        self.assertEqual(get_db().fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id = 'minecraft:diamond'")["base_price"], 250.0)
        reset_simulated_inventory()
        self.assertEqual(get_db().fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id = 'minecraft:diamond'")["base_price"], base)


# ====================================================================
# Integration 6: Concurrency Stress
# ====================================================================

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestConcurrencyStress(AHTestCase):

    def test_rapid_list_and_buy(self, mock_bridge):
        uids = []
        for i in range(10):
            lr = list_item(seller=f"Player{i}", item_id=self.unique_item("minecraft:diamond"),
                           count=1, start_price=5.0, buy_now_price=15.0 + i, rcon_func=mock_rcon)
            self.assertTrue(lr["ok"])
            uids.append(lr["data"]["listing_uuid"])
        for uid in uids:
            r = buy_now(buyer="Buyer", listing_uuid=uid, rcon_func=mock_rcon)
            self.assertTrue(r["ok"])
            self.assertEqual(get_listing(uid)["status"], "sold")

    def test_rapid_bid_escalation(self, mock_bridge):
        lr = list_item(seller="Seller", item_id=self.unique_item(), count=1,
                       start_price=10.0, rcon_func=mock_rcon)
        uid = lr["data"]["listing_uuid"]
        for bidder, amount in [("A", 12.0), ("B", 15.0), ("C", 20.0), ("D", 25.0), ("E", 30.0)]:
            r = place_bid(bidder=bidder, listing_uuid=uid, bid_amount=amount)
            self.assertTrue(r["ok"], f"{bidder}'s bid of {amount} failed: {r.get('error')}")
        listing = get_listing(uid)
        self.assertEqual(listing["highest_bidder"], "E")
        self.assertEqual(listing["current_bid"], 30.0)
        self.assertEqual(listing["bids_count"], 5)


if __name__ == "__main__":
    from unittest import main
    main()
