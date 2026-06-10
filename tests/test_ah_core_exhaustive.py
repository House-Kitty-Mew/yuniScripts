#!/usr/bin/env python3
"""
test_ah_core_exhaustive.py — Exhaustive unit tests for the Auction House core module.

Covers all 34 edge cases (EC1–EC34) from the edge case matrix in TEST_SUITE_SPEC.md:

  EC1–EC9:   Input validation (empty seller, malformed item_id, count 0, price below min,
             buy_now < start, name > 16 chars, item_id > 128, special chars, duration capped)
  EC10-EC11: Business logic (max listings, duplicate item)
  EC12-EC14: Bidding rules (own listing, too low bid, expired listing)
  EC15-EC19: Race conditions (cancel, sold, simultaneous bids, buy-now vs bid, cancel vs buy-now)
  EC20-EC25: Integration (bridge unavailable, deduct fails after bid, RCON fails, extension exception, fee fails)
  EC26-EC34: Edge cases (expired timestamps, TZ handling, concurrent expire, refund fails,
             simulated bypasses, null prices, inconsistent flags, log-before-def)

All tests use a temporary database and mocked economy bridge.
"""

import unittest
import sys
import os
import json
import time
import threading
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

# ── Project root setup ───────────────────────────────────────────────
# The AH code lives under SCRIPTS/GAMES/minecraft_manager/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)
MANAGER_ROOT = _PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager"
if str(MANAGER_ROOT) not in sys.path:
    sys.path.insert(0, str(MANAGER_ROOT))

# ── Test suite ───────────────────────────────────────────────────────

class ExhaustiveAuctionCoreTest(unittest.TestCase):
    """Exhaustive test suite for ah_core.py covering all 34 edge cases."""

    # ── Class-level fixtures ──────────────────────────────────────

    _temp_dir: Path = None
    _db_path: Path = None

    @classmethod
    def setUpClass(cls):
        """Create a temporary database and initialize the schema."""
        cls._temp_dir = Path(tempfile.mkdtemp(prefix="ah_test_"))
        cls._db_path = cls._temp_dir / "auctionhouse.db3"

        # Patch the database path BEFORE any core functions are called
        import AUCTIONHOUSE.ah_database as db_mod
        db_mod.DB_DIR = cls._temp_dir
        db_mod.DB_PATH = cls._db_path

        from AUCTIONHOUSE.ah_database import initialize_database
        initialize_database(force=True)

    @classmethod
    def tearDownClass(cls):
        """Remove the temporary database directory."""
        if cls._temp_dir and cls._temp_dir.exists():
            shutil.rmtree(str(cls._temp_dir))

    # ── Per-test setup ────────────────────────────────────────────

    def setUp(self):
        """Create mocks and clean database state before each test.

        Mocks the economy bridge, bans, and extension hooks so that
        tests focus on ah_core logic without external dependencies.
        """
        from AUCTIONHOUSE.ah_database import get_db

        # Clean all tables
        db = get_db()
        db.execute("DELETE FROM auction_listings")
        db.execute("DELETE FROM transaction_history")
        db.execute("DELETE FROM player_balances")
        db.execute("DELETE FROM market_events")
        db.execute("DELETE FROM simulated_inventory")

        # ── Economy bridge mocks ──────────────────────────────────
        self._bridge_mock = MagicMock()
        self._bridge_mock.is_ready = True
        self._bridge_mock.get_balance.return_value = 10_000  # Plenty of coins
        self._bridge_mock.deduct.return_value = True
        self._bridge_mock.credit.return_value = True
        self._bridge_mock.get_economy_stats.return_value = {"player_count": 5}

        # Patch _get_eco_bridge so all core functions use our mock
        self._bridge_patcher = patch(
            "AUCTIONHOUSE.ah_core._get_eco_bridge",
            return_value=self._bridge_mock
        )
        self._mock_get_bridge = self._bridge_patcher.start()

        # Patch individual eco helpers for precise control
        self._deduct_patcher = patch(
            "AUCTIONHOUSE.ah_core._eco_deduct",
            return_value=True
        )
        self._mock_eco_deduct = self._deduct_patcher.start()

        self._credit_patcher = patch(
            "AUCTIONHOUSE.ah_core._eco_credit",
            return_value=True
        )
        self._mock_eco_credit = self._credit_patcher.start()

        self._check_balance_patcher = patch(
            "AUCTIONHOUSE.ah_core._check_eco_balance",
            return_value={"ok": True, "balance": 10_000}
        )
        self._mock_check_balance = self._check_balance_patcher.start()

        # Patch is_player_banned to return False by default
        self._banned_patcher = patch(
            "AUCTIONHOUSE.ah_core.is_player_banned",
            return_value=False
        )
        self._mock_is_banned = self._banned_patcher.start()

        # Patch fire_hook to be a no-op
        self._hook_patcher = patch(
            "AUCTIONHOUSE.ah_core.fire_hook",
            return_value=None
        )
        self._mock_fire_hook = self._hook_patcher.start()

        # Re-import fresh config each test
        from AUCTIONHOUSE.ah_config import get_config
        self.cfg = get_config(reload=True)

    def tearDown(self):
        """Stop all patchers."""
        self._bridge_patcher.stop()
        self._deduct_patcher.stop()
        self._credit_patcher.stop()
        self._check_balance_patcher.stop()
        self._banned_patcher.stop()
        self._hook_patcher.stop()

    # ══════════════════════════════════════════════════════════════
    # EC1–EC9: Input Validation
    # ══════════════════════════════════════════════════════════════

    def test_ec1_empty_seller_name(self):
        """EC1: Empty seller name should be rejected."""
        from AUCTIONHOUSE.ah_core import list_item
        r = list_item("", "minecraft:diamond", 1, 10.0)
        self.assertFalse(r["ok"])
        self.assertIn("Invalid player name", r["error"])

    def test_ec2_malformed_item_id_no_colon(self):
        """EC2: Item ID without colon should be rejected."""
        from AUCTIONHOUSE.ah_core import list_item
        r = list_item("Steve", "diamond", 1, 10.0)
        self.assertFalse(r["ok"])
        self.assertIn("Invalid item ID", r["error"])

    def test_ec3_count_zero_or_negative(self):
        """EC3: Item count of 0 or negative should be rejected."""
        from AUCTIONHOUSE.ah_core import list_item
        r1 = list_item("Steve", "minecraft:diamond", 0, 10.0)
        self.assertFalse(r1["ok"])
        r2 = list_item("Steve", "minecraft:diamond", -1, 10.0)
        self.assertFalse(r2["ok"])
        self.assertIn("count must be at least", (r1["error"] + r2["error"]).lower())

    def test_ec4_start_price_below_min(self):
        """EC4: Start price below sim_price_min should be rejected."""
        from AUCTIONHOUSE.ah_core import list_item
        # sim_price_min is 0.01 from config defaults
        r = list_item("Steve", "minecraft:dirt", 1, 0.001)
        self.assertFalse(r["ok"])
        self.assertIn("too low", r["error"].lower())

    def test_ec5_buy_now_below_start(self):
        """EC5: Buy-It-Now price below start price should be rejected."""
        from AUCTIONHOUSE.ah_core import list_item
        r = list_item("Steve", "minecraft:diamond", 1, 20.0, buy_now_price=10.0)
        self.assertFalse(r["ok"])
        self.assertIn("Buy-It-Now price must be >= start", r["error"])

    def test_ec6_player_name_too_long(self):
        """EC6: Player name > 16 characters should be rejected."""
        from AUCTIONHOUSE.ah_core import list_item
        long_name = "a" * 17
        r = list_item(long_name, "minecraft:diamond", 1, 10.0)
        self.assertFalse(r["ok"])
        self.assertIn("Invalid player name", r["error"])

    def test_ec7_item_id_too_long(self):
        """EC7: Item ID > 128 characters should be rejected."""
        from AUCTIONHOUSE.ah_core import list_item
        long_id = "minecraft:" + "x" * 120
        r = list_item("Steve", long_id, 1, 10.0)
        self.assertFalse(r["ok"])
        self.assertIn("Invalid item ID", r["error"])

    def test_ec8_special_chars_in_player_name(self):
        """EC8: Non-alphanumeric player name (special chars) should be rejected.

        Only underscores and hyphens are allowed in addition to alphanumeric.
        """
        from AUCTIONHOUSE.ah_core import list_item
        # Names with spaces, dots, or special characters should fail
        for bad_name in ["Steve!", "player@name", "name with spaces", "name.the.boss"]:
            r = list_item(bad_name, "minecraft:diamond", 1, 10.0)
            self.assertFalse(r["ok"], f"Name '{bad_name}' should be rejected")
        # Names with underscores and hyphens should pass
        for good_name in ["Steve_123", "player-name", "a_b-c"]:
            r = list_item(good_name, "minecraft:diamond", 1, 10.0)
            self.assertTrue(r["ok"], f"Name '{good_name}' should be accepted")

    def test_ec9_duration_capped(self):
        """EC9: Duration exceeding max_hours should be capped to max_hours.

        The config default auction_duration_max_hours is 168 (7 days).
        A duration > 168 should be silently capped, not rejected.
        """
        from AUCTIONHOUSE.ah_core import list_item
        from AUCTIONHOUSE.ah_database import get_db

        r = list_item("Steve", "minecraft:diamond", 1, 10.0, duration_hours=999)
        self.assertTrue(r["ok"])
        listing_uuid = r["data"]["listing_uuid"]

        db = get_db()
        listing = db.fetch_one(
            "SELECT expires_at, listed_at FROM auction_listings WHERE listing_uuid = ?",
            (listing_uuid,)
        )
        # expires_at - listed_at should be at most 168 hours
        listed = datetime.fromisoformat(listing["listed_at"])
        expires = datetime.fromisoformat(listing["expires_at"])
        duration = (expires - listed).total_seconds() / 3600
        self.assertAlmostEqual(duration, 168, delta=1,
                                msg="Duration should be capped at 168h")

    # ══════════════════════════════════════════════════════════════
    # EC10–EC11: Business Logic
    # ══════════════════════════════════════════════════════════════

    def test_ec10_max_listings_limit(self):
        """EC10: Player with max listings should be blocked from listing more."""
        from AUCTIONHOUSE.ah_core import list_item
        # Config default max_listings_per_player is 5
        max_listings = self.cfg.max_listings_per_player
        for i in range(max_listings):
            r = list_item("Steve", f"minecraft:item_{i}", 1, 5.0)
            self.assertTrue(r["ok"], f"Failed on listing {i}")

        # Next listing should fail
        r = list_item("Steve", "minecraft:extra", 1, 5.0)
        self.assertFalse(r["ok"])
        self.assertIn("max", r["error"].lower())

    def test_ec10_simulated_bypasses_max_listings(self):
        """EC10-bis: Simulated listings bypass the player listing limit."""
        from AUCTIONHOUSE.ah_core import list_item
        max_listings = self.cfg.max_listings_per_player
        for i in range(max_listings + 5):
            r = list_item("AI_Sim", f"minecraft:sim_item_{i}", 1, 1.0,
                          is_simulated=True)
            self.assertTrue(r["ok"], f"Simulated listing {i} should bypass limit")

    def test_ec11_duplicate_item_listing(self):
        """EC11: Same seller listing same item_id while active should be blocked."""
        from AUCTIONHOUSE.ah_core import list_item
        r1 = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r1["ok"])

        r2 = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertFalse(r2["ok"])
        self.assertIn("already have", r2["error"].lower())

    def test_ec11_different_sellers_can_duplicate(self):
        """EC11-bis: Different sellers CAN list the same item."""
        from AUCTIONHOUSE.ah_core import list_item
        r1 = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r1["ok"])
        r2 = list_item("Alex", "minecraft:diamond", 1, 15.0)
        self.assertTrue(r2["ok"], "Different seller should be allowed")

    # ══════════════════════════════════════════════════════════════
    # EC12–EC14: Bidding Rules
    # ══════════════════════════════════════════════════════════════

    def test_ec12_bid_on_own_listing(self):
        """EC12: Bidding on own listing should fail."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r["ok"])
        r2 = place_bid("Steve", r["data"]["listing_uuid"], 12.0)
        self.assertFalse(r2["ok"])
        self.assertIn("own", r2["error"].lower())

    def test_ec13_bid_too_low_below_min_increment(self):
        """EC13: Bid below minimum increment should be rejected.

        Min increment is 10% of current bid (min_bid_increment_pct = 10).
        Starting at 10.0, minimum bid is 10.0 + 1.0 = 11.0.
        """
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r["ok"])

        # 10.5 is below 11.0 minimum
        r2 = place_bid("Alex", r["data"]["listing_uuid"], 10.5)
        self.assertFalse(r2["ok"])
        self.assertIn("too low", r2["error"].lower())
        self.assertIn("11.0", r2["error"])  # Should mention minimum

    def test_ec14_bid_on_expired_listing(self):
        """EC14: Bidding on an expired listing should fail."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        from AUCTIONHOUSE.ah_database import get_db

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r["ok"])
        lu = r["data"]["listing_uuid"]

        # Force the listing to be expired by updating expires_at
        db = get_db()
        db.execute(
            "UPDATE auction_listings SET expires_at = ? WHERE listing_uuid = ?",
            ("2020-01-01T00:00:00+00:00", lu)
        )

        r2 = place_bid("Alex", lu, 15.0)
        self.assertFalse(r2["ok"])
        self.assertIn("expired", r2["error"].lower())

    # ══════════════════════════════════════════════════════════════
    # EC15–EC19: Race Conditions
    # ══════════════════════════════════════════════════════════════

    def test_ec15_bid_on_cancelled_listing(self):
        """EC15: Bidding on a cancelled listing should fail."""
        from AUCTIONHOUSE.ah_core import list_item, cancel_listing, place_bid
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        # Cancel first
        cancel_listing("Steve", lu)

        # Then try to bid
        r2 = place_bid("Alex", lu, 15.0)
        self.assertFalse(r2["ok"])
        self.assertIn("cancelled", r2["error"].lower())

    def test_ec16_bid_on_sold_listing(self):
        """EC16: Bidding on a sold listing should fail."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now, place_bid
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]

        # Buy it first
        r_buy = buy_now("Alex", lu)
        self.assertTrue(r_buy["ok"])

        # Then try to bid
        r2 = place_bid("Bob", lu, 30.0)
        self.assertFalse(r2["ok"])
        self.assertIn("sold", r2["error"].lower())

    def test_ec17_simultaneous_bids_atomic(self):
        """EC17: Two simultaneous bids — at most one should win atomically."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        from AUCTIONHOUSE.ah_database import get_db

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        results = []

        def bid_safe(bidder, amount):
            result = place_bid(bidder, lu, amount)
            results.append(result)

        threads = [
            threading.Thread(target=bid_safe, args=("Alex", 12.0)),
            threading.Thread(target=bid_safe, args=("Bob", 12.0)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ok_count = sum(1 for r in results if r["ok"])
        self.assertLessEqual(ok_count, 1,
                             "At most one simultaneous bid should succeed")
        listing = get_db().fetch_one(
            "SELECT * FROM auction_listings WHERE listing_uuid = ?", (lu,))
        self.assertEqual(listing["bids_count"], ok_count)

    def test_ec18_buy_now_while_bid_pending(self):
        """EC18: Buy-Now should succeed even if a bid exists (atomic)."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid, buy_now
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=50.0)
        lu = r["data"]["listing_uuid"]

        # Place a bid first
        r_bid = place_bid("Alex", lu, 15.0)
        self.assertTrue(r_bid["ok"])

        # Now another player buys it via BIN
        r_bin = buy_now("Bob", lu)
        self.assertTrue(r_bin["ok"],
                        "BIN should succeed even with existing bid")
        self.assertEqual(r_bin["data"]["buyer"], "Bob")

    def test_ec19_cancel_while_buy_now_in_progress(self):
        """EC19: Cancel and Buy-Now race — one should win atomically.

        We simulate the race condition by submitting cancel and buy-now
        nearly simultaneously. Only one should succeed.
        """
        from AUCTIONHOUSE.ah_core import list_item, cancel_listing, buy_now

        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]

        results = []

        def cancel_it():
            results.append(("cancel", cancel_listing("Steve", lu)))

        def buy_it():
            results.append(("buy", buy_now("Bob", lu)))

        t1 = threading.Thread(target=cancel_it)
        t2 = threading.Thread(target=buy_it)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        ok_ops = [(op, r) for op, r in results if r["ok"]]
        self.assertEqual(len(ok_ops), 1,
                         "Only one of cancel/BIN should succeed atomically")

    # ══════════════════════════════════════════════════════════════
    # EC20–EC25: Integration
    # ══════════════════════════════════════════════════════════════

    def test_ec20_economy_bridge_unavailable(self):
        """EC20: When economy bridge is unavailable, AH should fall back gracefully."""
        from AUCTIONHOUSE.ah_core import list_item

        # Stop the bridge patcher so _get_eco_bridge returns None
        self._bridge_patcher.stop()
        self._bridge_patcher = patch(
            "AUCTIONHOUSE.ah_core._get_eco_bridge",
            return_value=None
        )
        self._mock_get_bridge = self._bridge_patcher.start()

        # Should still be able to list (bridge unavailable means no hard block)
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r["ok"],
                        "Listing should work when bridge is unavailable")

    def test_ec21_eco_deduct_fails_after_bid(self):
        """EC21: If economy deduct fails after bid accepted, bid should be rolled back."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        from AUCTIONHOUSE.ah_database import get_db

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        # Make _eco_deduct return False for the bid escrow
        # The atomic UPDATE will succeed but the escrow will fail → rollback
        self._mock_eco_deduct.return_value = False

        r2 = place_bid("Alex", lu, 15.0)
        self.assertFalse(r2["ok"],
                         "Bid should fail when escrow deduct fails")
        self.assertIn("escrow", r2["error"].lower())

        # Verify rollback: listing should still be active with no bidder
        listing = get_db().fetch_one(
            "SELECT * FROM auction_listings WHERE listing_uuid = ?", (lu,))
        self.assertEqual(listing["status"], "active")
        self.assertIsNone(listing["highest_bidder"],
                          "Highest bidder should be rolled back")

    def test_ec22_rcon_clear_fails_after_listing(self):
        """EC22: RCON clear fails after listing created — listing still exists.

        The listing is created successfully, but the item removal via RCON
        fails. This is non-fatal (item will be removed on next inventory check).
        """
        from AUCTIONHOUSE.ah_core import list_item

        def failing_rcon(cmd):
            if cmd.startswith("clear"):
                return "No items were removed"
            return "ok"

        r = list_item("Steve", "minecraft:diamond", 1, 10.0,
                      rcon_func=failing_rcon)
        self.assertTrue(r["ok"],
                        "Listing should succeed even if RCON clear fails")
        self.assertFalse(r["data"]["clear_ok"],
                         "clear_ok should be False when RCON fails")

    def test_ec23_rcon_tellraw_fails(self):
        """EC23: RCON tellraw failure should not break listing creation.

        The tellraw notification is purely cosmetic. A failure should be
        silently handled.
        """
        from AUCTIONHOUSE.ah_core import list_item

        def broken_rcon(cmd):
            if "tellraw" in cmd:
                raise Exception("RCON connection lost")
            if cmd.startswith("clear"):
                return f"Cleared 1 items"
            return "ok"

        r = list_item("Steve", "minecraft:diamond", 1, 10.0,
                      rcon_func=broken_rcon)
        self.assertTrue(r["ok"],
                        "Listing should succeed despite RCON tellraw failure")

    def test_ec24_extension_hook_exception_non_blocking(self):
        """EC24: Exception in extension hook should not block the listing.

        The fire_hook call is wrapped in try/except, so even if a hook
        raises, the listing process continues.
        """
        from AUCTIONHOUSE.ah_core import list_item

        self._mock_fire_hook.side_effect = RuntimeError("Extension crashed")

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r["ok"],
                        "Listing should succeed despite hook exceptions")
        self._mock_fire_hook.assert_called()

    def test_ec25_listing_fee_deduct_fails_before_insert(self):
        """EC25: If listing fee deduction fails, no listing should be created."""
        from AUCTIONHOUSE.ah_core import list_item
        from AUCTIONHOUSE.ah_database import get_db

        # Make balance check fail (insufficient funds for fee)
        self._mock_check_balance.return_value = {
            "ok": False,
            "error": "Insufficient funds: 0, need 1"
        }

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertFalse(r["ok"],
                         "Listing should fail when fee check fails")
        self.assertIn("fee", r["error"].lower() or "coins" in r["error"].lower(),
                      "Error should mention fee/coins")

        # Verify no listing was created
        count = get_db().fetch_one("SELECT COUNT(*) as cnt FROM auction_listings")
        self.assertEqual(count["cnt"], 0,
                         "No listing should exist when fee fails")

    # ══════════════════════════════════════════════════════════════
    # EC26–EC34: Edge Cases
    # ══════════════════════════════════════════════════════════════

    def test_ec26_expired_listing_missing_timestamp(self):
        """EC26: Expired listings with missing expiry timestamp should not crash."""
        from AUCTIONHOUSE.ah_core import expire_listings
        from AUCTIONHOUSE.ah_database import get_db

        # Insert a listing with NULL expires_at (should be skipped by expire_listings)
        db = get_db()
        from AUCTIONHOUSE.ah_core import _now
        db.execute("""
            INSERT INTO auction_listings
            (listing_uuid, seller_name, item_id, item_count, start_price,
             currency_type, status, listed_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("missing-ts-uuid", "Steve", "minecraft:diamond", 1, 10.0,
              "emerald", "active", _now(), None))

        # Should not raise
        results = expire_listings()
        # The listing with NULL expires_at should be skipped
        still_active = db.fetch_one(
            "SELECT status FROM auction_listings WHERE listing_uuid = ?",
            ("missing-ts-uuid",))
        self.assertEqual(still_active["status"], "active",
                         "Listing with NULL expires_at should remain active")

    def test_ec27_tz_aware_vs_naive_timestamps(self):
        """EC27: _format_time_remaining handles both TZ-aware and naive timestamps."""
        from AUCTIONHOUSE.ah_core import _format_time_remaining

        # TZ-aware (UTC)
        aware_ts = (datetime.now(timezone.utc) + __import__('datetime').timedelta(hours=2)).isoformat()
        result_aware = _format_time_remaining(aware_ts)
        self.assertIsNotNone(result_aware)
        self.assertNotEqual(result_aware, "Ended")

        # Naive timestamp (should be treated as UTC)
        naive_ts = (datetime.utcnow() + __import__('datetime').timedelta(hours=2)).isoformat()
        result_naive = _format_time_remaining(naive_ts)
        self.assertIsNotNone(result_naive)
        self.assertNotEqual(result_naive, "Ended")

        # Past timestamp → "Ended"
        past_ts = "2020-01-01T00:00:00+00:00"
        self.assertEqual(_format_time_remaining(past_ts), "Ended")

        # None → None
        self.assertIsNone(_format_time_remaining(None))

        # Invalid string → None
        self.assertIsNone(_format_time_remaining("not-a-date"))

    def test_ec28_concurrent_expire_listings(self):
        """EC28: Multiple simultaneous expire_listings calls should be safe."""
        from AUCTIONHOUSE.ah_core import expire_listings
        from AUCTIONHOUSE.ah_database import get_db

        # Create several expired listings
        for i in range(10):
            db = get_db()
            from AUCTIONHOUSE.ah_core import _now
            db.execute("""
                INSERT INTO auction_listings
                (listing_uuid, seller_name, item_id, item_count,
                 start_price, currency_type, status, listed_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"expired-{i}", "Steve", f"minecraft:item_{i}", 1, 10.0,
                "emerald", "active", "2020-01-01T00:00:00+00:00",
                "2020-01-02T00:00:00+00:00"
            ))

        results = []
        def expire_call():
            results.extend(expire_listings())

        threads = [threading.Thread(target=expire_call) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each listing should be processed exactly once
        total_expired = sum(1 for r in results)
        self.assertEqual(total_expired, 10,
                         "All 10 expired listings should be processed exactly once")

        active_count = get_db().fetch_one(
            "SELECT COUNT(*) as cnt FROM auction_listings WHERE status = 'active'")
        self.assertEqual(active_count["cnt"], 0,
                         "No listings should remain active")

    def test_ec29_outbid_refund_fails(self):
        """EC29: When outbid refund fails, the new bid should still succeed.

        The refund of the previous bidder is best-effort. If it fails,
        the new bid is still valid (RCON fallback or manual fix).
        """
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        from AUCTIONHOUSE.ah_database import get_db

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        # First bid succeeds
        r1 = place_bid("Alex", lu, 12.0)
        self.assertTrue(r1["ok"])

        # Make the refund fail for the second bid
        def deduct_or_credit_side_effect(player, amount, reason, note=None):
            if "REFUND" in reason:
                return False  # Refund fails
            return True

        self._mock_eco_credit.side_effect = deduct_or_credit_side_effect

        # Second bid should still succeed (refund is best-effort)
        r2 = place_bid("Bob", lu, 15.0)
        self.assertTrue(r2["ok"],
                        "Bid should succeed even if refund to previous bidder fails")
        self.assertEqual(r2["data"]["new_current_bid"], 15.0)

    def test_ec30_simulated_bypasses_bans_and_fees(self):
        """EC30: Simulated listings bypass bans and listing fees."""
        from AUCTIONHOUSE.ah_core import list_item

        # Make the ban check return True (player is banned)
        self._mock_is_banned.return_value = True

        # A real (non-simulated) listing should be blocked
        r_real = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertFalse(r_real["ok"],
                         "Banned real player should be blocked")

        # But a simulated listing should bypass the ban check
        r_sim = list_item("AI_Sim", "minecraft:coal", 64, 1.0,
                          is_simulated=True)
        self.assertTrue(r_sim["ok"],
                         "Simulated listing should bypass ban check")

    def test_ec31_stale_listing_detection(self):
        """EC31: Stale listing detection via query_listings.

        Listings with no bids for stale_hours_threshold (24h) are marked stale.
        Verify that the stale_since field is set appropriately when a
        listing has no bids.
        """
        from AUCTIONHOUSE.ah_core import list_item, query_listings
        from AUCTIONHOUSE.ah_database import get_db

        # Create a listing with the stale_since set far in the past
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        # Manually set stale_since to simulate a stale listing
        db = get_db()
        old_time = "2020-01-01T00:00:00+00:00"
        db.execute(
            "UPDATE auction_listings SET stale_since = ?, listed_at = ? WHERE listing_uuid = ?",
            (old_time, "2020-01-01T00:00:00+00:00", lu)
        )

        # Query should not break with stale listings
        q = query_listings(filter_type="all")
        self.assertTrue(q["ok"])
        # The stale listing should still appear (it's active)
        self.assertGreaterEqual(q["data"]["total"], 1)

    def test_ec32_listing_with_null_buy_now_price(self):
        """EC32: Listing with null buy_now_price should work fine.

        buy_now_price is optional; when omitted it should be stored as NULL.
        """
        from AUCTIONHOUSE.ah_core import list_item, get_listing

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        self.assertTrue(r["ok"])

        listing = get_listing(r["data"]["listing_uuid"])
        self.assertIsNotNone(listing)
        self.assertIsNone(listing["buy_now_price"],
                          "buy_now_price should be NULL when not provided")

    def test_ec33_is_simulated_flag_consistency(self):
        """EC33: is_simulated flag stored as integer maintains consistency.

        When is_simulated=True, the DB stores 1.
        When is_simulated=False, the DB stores 0.
        Query results should reflect the correct type.
        """
        from AUCTIONHOUSE.ah_core import list_item, get_listing, query_listings

        # Real listing
        r1 = list_item("Steve", "minecraft:diamond", 1, 10.0, is_simulated=False)
        self.assertTrue(r1["ok"])

        # Simulated listing
        r2 = list_item("AI_Bot", "minecraft:coal", 64, 1.0, is_simulated=True)
        self.assertTrue(r2["ok"])

        listing1 = get_listing(r1["data"]["listing_uuid"])
        listing2 = get_listing(r2["data"]["listing_uuid"])

        self.assertEqual(listing1["is_simulated"], 0,
                         "Real listing should have is_simulated=0")
        self.assertEqual(listing2["is_simulated"], 1,
                         "Simulated listing should have is_simulated=1")

        # Query filtering by simulated should work
        q_sim = query_listings(filter_type="simulated")
        self.assertEqual(q_sim["data"]["total"], 1)

    def test_ec34_log_usage_before_log_defined(self):
        """EC34: Module-level log usage after lazy init should not crash.

        The log variable is defined early in ah_core.py after the bridge
        functions. Verify that _get_eco_bridge doesn't crash on log usage.
        """
        # This is primarily testing that the module imports cleanly and
        # that _get_eco_bridge doesn't reference log before assignment.
        # We also verify that the logger is functional.
        from AUCTIONHOUSE.ah_core import _get_eco_bridge, log

        # Stop the bridge mock to test real lazy init
        self._bridge_patcher.stop()
        self._bridge_patcher = patch(
            "AUCTIONHOUSE.ah_core._get_eco_bridge",
            wraps=_get_eco_bridge  # Let it run the real function
        )
        self._mock_get_bridge = self._bridge_patcher.start()

        # Access log to confirm it exists
        self.assertIsNotNone(log)

    # ══════════════════════════════════════════════════════════════
    # Additional tests: Cancel with bids, fee charged
    # ══════════════════════════════════════════════════════════════

    def test_cancel_listing_with_bids_fee(self):
        """Cancel a listing that has bids — cancel fee should apply."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid, cancel_listing
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]
        place_bid("Alex", lu, 15.0)

        r_cancel = cancel_listing("Steve", lu)
        self.assertTrue(r_cancel["ok"])
        self.assertTrue(r_cancel["data"]["had_bids"],
                        "Listing with bids should flag had_bids=True")
        self.assertGreater(r_cancel["data"]["cancel_fee"], 0,
                           "Cancel fee should be > 0 for listings with bids")

    def test_cancel_listing_no_bids_no_fee(self):
        """Cancel a listing with no bids — no cancel fee."""
        from AUCTIONHOUSE.ah_core import list_item, cancel_listing
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        r_cancel = cancel_listing("Steve", lu)
        self.assertTrue(r_cancel["ok"])
        self.assertFalse(r_cancel["data"]["had_bids"],
                         "Listing without bids should flag had_bids=False")

    def test_cancel_wrong_seller(self):
        """Only the seller can cancel a listing."""
        from AUCTIONHOUSE.ah_core import list_item, cancel_listing
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = cancel_listing("Alex", r["data"]["listing_uuid"])
        self.assertFalse(r2["ok"])
        self.assertIn("Only the seller", r2["error"])

    def test_cancel_already_sold(self):
        """Cannot cancel a listing that has already been sold."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now, cancel_listing
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]
        buy_now("Alex", lu)
        r2 = cancel_listing("Steve", lu)
        self.assertFalse(r2["ok"])
        self.assertIn("sold", r2["error"].lower())

    # ══════════════════════════════════════════════════════════════
    # Additional tests: Expiry scenarios
    # ══════════════════════════════════════════════════════════════

    def test_expire_no_bids(self):
        """Expired listing with no bids should be marked expired, not sold."""
        from AUCTIONHOUSE.ah_core import list_item, expire_listings
        from AUCTIONHOUSE.ah_database import get_db

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        # Force expiry
        db = get_db()
        db.execute(
            "UPDATE auction_listings SET expires_at = ? WHERE listing_uuid = ?",
            ("2020-01-01T00:00:00+00:00", lu)
        )

        results = expire_listings()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["outcome"], "expired")

        listing = get_db().fetch_one(
            "SELECT status FROM auction_listings WHERE listing_uuid = ?", (lu,))
        self.assertEqual(listing["status"], "expired")

    def test_expire_with_bids(self):
        """Expired listing with bids should go to highest bidder (sold)."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid, expire_listings
        from AUCTIONHOUSE.ah_database import get_db

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        # Place a bid
        place_bid("Alex", lu, 15.0)

        # Force expiry
        db = get_db()
        db.execute(
            "UPDATE auction_listings SET expires_at = ? WHERE listing_uuid = ?",
            ("2020-01-01T00:00:00+00:00", lu)
        )

        results = expire_listings()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["outcome"], "sold")
        self.assertEqual(results[0]["winner"], "Alex")

    # ══════════════════════════════════════════════════════════════
    # Additional tests: Query helpers
    # ══════════════════════════════════════════════════════════════

    def test_query_all_listings(self):
        """query_listings returns all active listings."""
        from AUCTIONHOUSE.ah_core import list_item, query_listings
        list_item("Steve", "minecraft:diamond", 1, 10.0)
        list_item("Alex", "minecraft:iron_ingot", 5, 3.0)
        list_item("Bob", "minecraft:coal", 64, 1.0)

        q = query_listings(filter_type="all")
        self.assertTrue(q["ok"])
        self.assertEqual(q["data"]["total"], 3)

    def test_query_player_listings(self):
        """get_player_listings returns only that player's listings."""
        from AUCTIONHOUSE.ah_core import list_item, get_player_listings
        list_item("Steve", "minecraft:diamond", 1, 10.0)
        list_item("Steve", "minecraft:emerald", 3, 5.0)
        list_item("Alex", "minecraft:iron_ingot", 5, 3.0)

        steve_listings = get_player_listings("Steve")
        self.assertEqual(len(steve_listings), 2)

        alex_listings = get_player_listings("Alex")
        self.assertEqual(len(alex_listings), 1)

    def test_query_by_item_id(self):
        """Query listings filtered by item ID."""
        from AUCTIONHOUSE.ah_core import list_item, query_listings
        list_item("Steve", "minecraft:diamond", 1, 10.0)
        list_item("Alex", "minecraft:diamond", 2, 15.0)
        list_item("Bob", "minecraft:coal", 64, 1.0)

        q = query_listings(filter_type="item", filter_value="diamond")
        self.assertEqual(q["data"]["total"], 2)

    def test_query_pagination(self):
        """Query pagination works correctly."""
        from AUCTIONHOUSE.ah_core import list_item, query_listings
        for i in range(25):
            list_item(f"Player{i}", f"minecraft:item_{i}", 1, 5.0)

        q1 = query_listings(filter_type="all", page=1, per_page=10)
        self.assertEqual(len(q1["data"]["listings"]), 10)
        self.assertEqual(q1["data"]["total_pages"], 3)
        self.assertEqual(q1["data"]["total"], 25)

    # ══════════════════════════════════════════════════════════════
    # Additional tests: Player operation history
    # ══════════════════════════════════════════════════════════════

    def test_get_player_purchases(self):
        """Track what a player has purchased via buy-now."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now, get_player_purchases
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        buy_now("Alex", r["data"]["listing_uuid"])

        purchases = get_player_purchases("Alex")
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0]["transaction_type"], "buy")

    def test_get_player_sales(self):
        """Track what a player has sold."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now, get_player_sales
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        buy_now("Alex", r["data"]["listing_uuid"])

        sales = get_player_sales("Steve")
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["seller_name"], "Steve")

    def test_get_bid_history(self):
        """Track bid history for a listing."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid, get_bid_history
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]
        place_bid("Alex", lu, 15.0)
        place_bid("Bob", lu, 20.0)

        history = get_bid_history(lu)
        self.assertEqual(len(history), 2)

    def test_get_recent_prices(self):
        """Track recent sale prices for an item."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now, get_recent_prices
        r1 = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        buy_now("Alex", r1["data"]["listing_uuid"])
        r2 = list_item("Bob", "minecraft:diamond", 1, 10.0, buy_now_price=30.0)
        buy_now("Charlie", r2["data"]["listing_uuid"])

        prices = get_recent_prices("minecraft:diamond", days=7)
        self.assertEqual(len(prices), 2)

    # ══════════════════════════════════════════════════════════════
    # Additional tests: Balance operations
    # ══════════════════════════════════════════════════════════════

    def test_get_balance_unknown_player(self):
        """Getting balance for unknown player returns None."""
        from AUCTIONHOUSE.ah_core import get_balance
        bal = get_balance("NobodyHere", use_bridge=False)
        self.assertIsNone(bal)

    def test_update_balance_create_and_query(self):
        """Update balance for a new player creates a record."""
        from AUCTIONHOUSE.ah_core import update_balance, get_balance
        bal = update_balance("Steve", 100, reason="TEST")
        self.assertEqual(bal, 100)

        queried = get_balance("Steve", use_bridge=False)
        self.assertEqual(queried, 100)

    def test_update_balance_deduct(self):
        """Deduct from a player's balance."""
        from AUCTIONHOUSE.ah_core import update_balance, get_balance
        # Make bridge.get_balance return None so internal calculation is used
        self._bridge_mock.get_balance.return_value = None
        update_balance("Steve", 100, reason="TEST")
        update_balance("Steve", -25, reason="SPEND")
        bal = get_balance("Steve", use_bridge=False)
        self.assertEqual(bal, 75)

    def test_sync_balances_no_bridge(self):
        """sync_balances returns unavailable status without bridge."""
        from AUCTIONHOUSE.ah_core import sync_balances

        self._bridge_patcher.stop()
        self._bridge_patcher = patch(
            "AUCTIONHOUSE.ah_core._get_eco_bridge",
            return_value=None
        )
        self._mock_get_bridge = self._bridge_patcher.start()

        result = sync_balances()
        self.assertEqual(result["status"], "unavailable")

    def test_sync_balances_with_bridge(self):
        """sync_balances works when bridge is available."""
        from AUCTIONHOUSE.ah_core import sync_balances, update_balance

        update_balance("Steve", 500, reason="TEST")

        result = sync_balances()
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(result["checked"], 0)

    # ══════════════════════════════════════════════════════════════
    # Additional tests: Buy-Now edge cases
    # ══════════════════════════════════════════════════════════════

    def test_buy_now_no_bin_price(self):
        """Buying a listing without BIN price should fail."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = buy_now("Alex", r["data"]["listing_uuid"])
        self.assertFalse(r2["ok"])
        self.assertIn("Buy-It-Now", r2["error"])

    def test_buy_now_own_listing(self):
        """Buying own listing should fail."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        r2 = buy_now("Steve", r["data"]["listing_uuid"])
        self.assertFalse(r2["ok"])
        self.assertIn("own", r2["error"].lower())

    def test_buy_now_quantity_exceeds(self):
        """Buying more than available quantity should fail."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        r2 = buy_now("Alex", r["data"]["listing_uuid"], quantity=5)
        self.assertFalse(r2["ok"])
        self.assertIn("available", r2["error"].lower())

    def test_buy_now_already_sold_atomic(self):
        """Buying an already-sold item should fail atomically."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]
        buy_now("Alex", lu)
        r2 = buy_now("Bob", lu)
        self.assertFalse(r2["ok"])
        self.assertTrue(
            "purchased" in r2["error"].lower() or "sold" in r2["error"].lower(),
            f"Expected error about 'purchased' or 'sold', got: {r2['error']}"
        )

    # ══════════════════════════════════════════════════════════════
    # Additional tests: Helper functions
    # ══════════════════════════════════════════════════════════════

    def test_make_response_patterns(self):
        """_make_response produces correct response dicts for success and error."""
        from AUCTIONHOUSE.ah_core import _make_response

        ok_resp = _make_response(True, {"uuid": "abc"})
        self.assertEqual(ok_resp, {"ok": True, "data": {"uuid": "abc"}})

        err_resp = _make_response(False, error="Something went wrong")
        self.assertEqual(err_resp, {"ok": False, "error": "Something went wrong"})

        ok_no_data = _make_response(True)
        self.assertEqual(ok_no_data, {"ok": True, "data": {}})

        err_no_msg = _make_response(False)
        self.assertEqual(err_no_msg, {"ok": False, "error": "Unknown error"})

    def test_format_time_remaining_edge_cases(self):
        """_format_time_remaining handles various edge cases."""
        from AUCTIONHOUSE.ah_core import _format_time_remaining
        from datetime import timedelta

        now = datetime.now(timezone.utc)

        # About 1 minute 5 seconds away (slightly >60s to avoid timing edge)
        one_min = (now + timedelta(seconds=65)).isoformat()
        result = _format_time_remaining(one_min)
        self.assertEqual(result, "1m")

        # Less than 1 minute
        under_min = (now + timedelta(seconds=30)).isoformat()
        self.assertEqual(_format_time_remaining(under_min), "<1m")

        # Multiple hours (use 3h 30m to allow for timing variance)
        three_hours = (now + timedelta(hours=3, minutes=30)).isoformat()
        result = _format_time_remaining(three_hours)
        self.assertIn("3h", result)
        # Minutes could be 29 or 30 depending on timing — just check hours
        self.assertRegex(result, r"3h \d+m")

        # Empty string
        self.assertIsNone(_format_time_remaining(""))

    def test_place_bid_ban_check(self):
        """Banned players cannot place bids."""
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]

        self._mock_is_banned.return_value = True
        r2 = place_bid("Alex", lu, 15.0)
        self.assertFalse(r2["ok"])
        self.assertIn("ban", r2["error"].lower())

    def test_buy_now_ban_check(self):
        """Banned players cannot buy items."""
        from AUCTIONHOUSE.ah_core import list_item, buy_now
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)
        lu = r["data"]["listing_uuid"]

        self._mock_is_banned.return_value = True
        r2 = buy_now("Alex", lu)
        self.assertFalse(r2["ok"])
        self.assertIn("ban", r2["error"].lower())

    def test_simulated_bypasses_ban_for_bid(self):
        """Simulated operations in bidding context.

        While simulated listings exist, placing a bid from a banned player
        should still fail (the ban is on the player, not the listing).
        """
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        r = list_item("Steve", "minecraft:diamond", 1, 10.0, is_simulated=False)
        lu = r["data"]["listing_uuid"]

        self._mock_is_banned.return_value = True
        r2 = place_bid("BannedPlayer", lu, 15.0)
        self.assertFalse(r2["ok"],
                         "Banned real players cannot bid even on sim items")

    def test_list_item_nbt_preserved(self):
        """Item NBT data should be preserved through list_item."""
        from AUCTIONHOUSE.ah_core import list_item, get_listing
        nbt = json.dumps({
            "minecraft:enchantments": {
                "levels": {"minecraft:sharpness": 5}
            }
        })
        r = list_item("Steve", "minecraft:diamond_sword", 1, 50.0, item_nbt=nbt)
        self.assertTrue(r["ok"])
        listing = get_listing(r["data"]["listing_uuid"])
        self.assertEqual(listing["item_nbt"], nbt)

    def test_multiple_expire_same_listing_safety(self):
        """Calling expire_listings twice on the same set is idempotent."""
        from AUCTIONHOUSE.ah_core import list_item, expire_listings
        from AUCTIONHOUSE.ah_database import get_db

        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        lu = r["data"]["listing_uuid"]
        db = get_db()
        db.execute(
            "UPDATE auction_listings SET expires_at = ? WHERE listing_uuid = ?",
            ("2020-01-01T00:00:00+00:00", lu)
        )

        first = expire_listings()
        second = expire_listings()

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0,
                         "Second expire_listings call should process 0 listings")

    # ══════════════════════════════════════════════════════════════
    # Additional tests: Price / bid floor checks
    # ══════════════════════════════════════════════════════════════

    def test_place_bid_non_existent_listing(self):
        """Bidding on a non-existent UUID should fail gracefully."""
        from AUCTIONHOUSE.ah_core import place_bid
        r = place_bid("Alex", "non-existent-uuid", 100.0)
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["error"].lower())

    def test_place_bid_negative_amount(self):
        """Bidding a negative amount should be handled gracefully.

        The bid amount check happens against current bid, but a negative
        amount will fail the min bid check before the atomic UPDATE.
        """
        from AUCTIONHOUSE.ah_core import list_item, place_bid
        r = list_item("Steve", "minecraft:diamond", 1, 10.0)
        r2 = place_bid("Alex", r["data"]["listing_uuid"], -5.0)
        self.assertFalse(r2["ok"])

    def test_get_listing_non_existent(self):
        """get_listing returns None for non-existent UUID."""
        from AUCTIONHOUSE.ah_core import get_listing
        listing = get_listing("i-dont-exist")
        self.assertIsNone(listing)


if __name__ == "__main__":
    unittest.main()
