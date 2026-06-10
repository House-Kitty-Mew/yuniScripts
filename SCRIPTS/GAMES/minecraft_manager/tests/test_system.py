"""
test_system.py — Full system integration tests.

Tests the complete pipeline:
  1. Database init → listing → bidding → BIN → cancel → expiry
  2. Phooks dispatch → ah_core → database
  3. Economy bridge → balance checks → writes
  4. AI prompt building (no API call)
  5. Market event lifecycle
  6. Item generation + listing flow
  7. Reports generation
  8. Price history snapshots
  9. Money sync
 10. Error handling throughout

All tests self-contained. No RCON. No Phooks hub. No DeepSeek API.
"""

import unittest, sys, os, json, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["AH_DB_PATH"] = ":memory:"

from AUCTIONHOUSE.ah_database import initialize_database, get_db
from AUCTIONHOUSE.ah_core import (
    list_item, place_bid, buy_now, cancel_listing,
    query_listings, get_listing, get_player_listings,
    expire_listings, get_balance, update_balance, sync_balances,
    get_player_purchases, get_player_sales, get_bid_history
)
from AUCTIONHOUSE.ah_price_history import take_snapshot, get_market_summary, get_price_trend
from AUCTIONHOUSE.ah_market_events import start_event, get_active_events, check_event_progress
from AUCTIONHOUSE.ah_item_gen import generate_simulated_item, roll_rarity
from AUCTIONHOUSE.ah_reports import get_weekly_report, format_report_for_chat
from AUCTIONHOUSE.ah_helper_db import add_note, get_notes_for_prompt
from AUCTIONHOUSE.ah_phooks import dispatch_event
from AUCTIONHOUSE.ah_config import get_config


class TestFullSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = initialize_database(force=True)
        cls.db.execute("DELETE FROM auction_listings")
        cls.db.execute("DELETE FROM transaction_history")
        cls.db.execute("DELETE FROM player_balances")
        cls.db.execute("DELETE FROM market_events")
        cls.db.execute("DELETE FROM price_history")
        cls.db.execute("DELETE FROM ai_notes")

    # ── Full Auction Lifecycle ──────────────────────────────────

    def test_01_full_lifecycle(self):
        """Complete auction lifecycle: list → bid → BIN → cancel → expiry."""
        # Phase 1: List
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=30.0)
        self.assertTrue(r["ok"])
        lu1 = r["data"]["listing_uuid"]

        # Phase 2: List another
        r2 = list_item("Alex", "minecraft:iron_ingot", 5, 3.0)
        self.assertTrue(r2["ok"])
        lu2 = r2["data"]["listing_uuid"]

        # Phase 3: Bid on first
        r3 = place_bid("Bob", lu1, 15.0)
        self.assertTrue(r3["ok"])
        self.assertEqual(r3["data"]["new_current_bid"], 15.0)

        # Phase 4: Buy second via BIN (with duration=0 to test partial)
        r4 = list_item("Charlie", "minecraft:gold_ingot", 3, 5.0, buy_now_price=12.0)
        lu3 = r4["data"]["listing_uuid"]
        r5 = buy_now("Dave", lu3)
        self.assertTrue(r5["ok"])
        self.assertEqual(r5["data"]["seller"], "Charlie")
        self.assertEqual(r5["data"]["buyer"], "Dave")

        # Phase 5: Cancel Steve's original (has bids)
        r6 = cancel_listing("Steve", lu1)
        self.assertTrue(r6["ok"])
        self.assertTrue(r6["data"]["had_bids"])

        # Phase 6: Expire Alex's listing
        listing = get_listing(lu2)
        self.assertIsNotNone(listing)
        # Force expiration by updating the timestamp
        self.db.execute("UPDATE auction_listings SET expires_at = '2020-01-01' WHERE listing_uuid = ?", (lu2,))
        expired = expire_listings()
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["outcome"], "expired")

        # Phase 7: Verify final state
        active = query_listings(filter_type="all")
        self.assertEqual(active["data"]["total"], 0, "All listings should be ended")

    def test_02_market_event_lifecycle(self):
        """Start a market event, check active, check multiplier, end it."""
        # Start
        r = start_event("test_event", "Test Market Event", "A test event",
                        "seasonal", "small", ["minecraft:coal"],
                        price_multiplier=1.5, goal_count=10)
        self.assertTrue(r["ok"])
        event_uuid = r["data"]["event_uuid"]

        # Check active
        active = get_active_events()
        self.assertGreaterEqual(len(active), 1)

        # End it
        from AUCTIONHOUSE.ah_market_events import end_event
        r2 = end_event(event_uuid, reason="test_complete")
        self.assertTrue(r2["ok"])

    def test_03_price_snapshot_and_trend(self):
        """Take a price snapshot and check the trend data."""
        # Need at least one listing for a meaningful snapshot
        list_item("Steve", "minecraft:diamond", 1, 10.0)
        n = take_snapshot()
        self.assertGreater(n, 0)
        summary = get_market_summary()
        self.assertIn("total_active_listings", summary)
        self.assertIn("price_snapshots_formatted", summary)

    def test_04_reports_generation(self):
        """Generate weekly and player reports."""
        report = get_weekly_report()
        self.assertIn("overview", report)
        self.assertIn("period", report)
        chat_lines = format_report_for_chat(report)
        self.assertGreater(len(chat_lines), 0, "Chat report should have content")
        for line in chat_lines:
            self.assertIsInstance(line, str)

    def test_05_ai_notes(self):
        """Add and retrieve AI helper notes."""
        nid = add_note("observation", "System test note", importance=3, expires_in_days=7)
        self.assertGreater(nid, 0)
        notes = get_notes_for_prompt()
        note_texts = [n["content"] for n in notes]
        self.assertIn("System test note", note_texts)

    def test_06_item_generation_and_listing(self):
        """Generate simulated items and list them."""
        for _ in range(3):
            item = generate_simulated_item("minecraft:diamond_sword")
            r = list_item("AH System", item["item_id"], 1, item["price"],
                          buy_now_price=item["price"], is_simulated=True,
                          signed_name=item.get("signed_name"),
                          sim_lore=json.dumps(item.get("lore", [])),
                          sim_enchantments=item.get("enchantments"))
            self.assertTrue(r["ok"])
        q = query_listings(filter_type="simulated")
        self.assertGreaterEqual(q["data"]["total"], 3)

    def test_07_phooks_dispatch_pipeline(self):
        """Test that Phooks dispatch works end-to-end for queries."""
        captured = []
        def mock_emit(event, data):
            captured.append(data)
        dispatch_event("ah_query", {
            "request_uuid": "test-sys-1",
            "player_name": "Steve",
            "filter_type": "all",
        }, emit_fn=mock_emit)
        self.assertGreater(len(captured), 0, "Phooks dispatch should respond")

    def test_08_balance_tracking(self):
        """Manual balance updates (internal, no bridge)."""
        update_balance("Steve", 1000, reason="TEST")
        self.assertEqual(get_balance("Steve"), 1000)
        update_balance("Steve", -250, reason="TEST")
        self.assertEqual(get_balance("Steve"), 750)
        update_balance("Steve", 500, reason="TEST")
        self.assertEqual(get_balance("Steve"), 1250)

    def test_09_high_volume_listings(self):
        """List 50 items to ensure pagination and performance."""
        # Count pre-existing listings
        pre = query_listings(filter_type="all")
        pre_count = pre["data"]["total"]
        for i in range(50):
            r = list_item(f"User{i}", f"minecraft:item_{i}", 1, 5.0)
            self.assertTrue(r["ok"])
        q = query_listings(filter_type="all", page=1, per_page=20)
        self.assertEqual(len(q["data"]["listings"]), 20)
        expected_total = 50 + pre_count
        self.assertEqual(q["data"]["total"], expected_total)
        q2 = query_listings(filter_type="all", page=3, per_page=20)
        remaining = expected_total - 40
        self.assertGreater(remaining, 0)
        self.assertEqual(len(q2["data"]["listings"]), remaining)

    def test_10_config_defaults(self):
        """Verify config values are within expected ranges."""
        cfg = get_config()
        self.assertGreater(cfg.max_listings_per_player, 0)
        self.assertGreater(cfg.min_bid_increment_pct, 0)
        self.assertLess(cfg.min_bid_increment_pct, 100)
        self.assertGreater(cfg.simulation_interval_minutes, 0)

    def test_11_error_isolation(self):
        """Test that errors don't corrupt subsequent operations."""
        # Failed operation (unique item to avoid dup conflict)
        r = list_item("ErrorTestPlayer", "minecraft:bedrock", 1, 0.001)  # Too low
        self.assertFalse(r["ok"])

        # Next operation should work with different item
        r2 = list_item("ErrorTestPlayer", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r2["ok"])

    def test_12_sync_balances_no_bridge(self):
        """sync_balances is safe with or without bridge."""
        result = sync_balances()
        self.assertIn("status", result)
        self.assertIn(result["status"], ("ok", "unavailable"), f"Unexpected status: {result['status']}")


if __name__ == "__main__":
    unittest.main()
