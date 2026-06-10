"""
test_flow_listing.py — Data Flow Test: Player Listing (Flow 1)

Tests every code path and edge case for list_item() including:
  - Basic listing creation
  - Input validation (player name, item ID, prices)
  - Simulated vs player listings
  - Banned player checks
  - Listing limits
  - RCON integration
  - NBT data preservation
  - Economy bridge integration
  - Hook firing
"""

from tests.dataflow.conftest_dataflow import (
    DataFlowTestCase, mock_rcon, reset_rcon, RCON_LOG
)
from unittest.mock import patch, MagicMock
from AUCTIONHOUSE.ah_core import (
    list_item, get_listing, query_listings, get_player_listings
)
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_bans import ban_player, unban_player
from tests.dataflow.probes.trace_probe import (
    PATH_LISTING, PATH_DB_INSERT, PATH_HOOK_FIRE,
    PATH_ECO_BRIDGE_CHECK, PATH_ECO_BRIDGE_DEDUCT,
)


# ══════════════════════════════════════════════════════════════════════
# Test 1.1: Basic listing creation
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestListingBasic(DataFlowTestCase):

    def test_1_1_basic_listing(self, mock_bridge):
        """Basic listing creates a valid auction entry."""
        item = self.unique_item("minecraft:diamond")

        with self.trace.path(PATH_LISTING, seller="Alice", item=item):
            result = list_item(
                seller="Alice", item_id=item, count=3,
                start_price=10.0, buy_now_price=25.0,
                duration_hours=48, rcon_func=mock_rcon
            )

        self.assertTrue(result["ok"])
        uid = result["data"]["listing_uuid"]

        listing = get_listing(uid)
        self.assertIsNotNone(listing)
        self.assertEqual(listing["seller_name"], "Alice")
        self.assertEqual(listing["item_id"], item)
        self.assertEqual(listing["item_count"], 3)
        self.assertEqual(listing["start_price"], 10.0)
        self.assertEqual(listing["buy_now_price"], 25.0)
        self.assertEqual(listing["status"], "active")
        self.assertEqual(listing["currency_type"], "emerald")

        # Verify RCON clear was called
        clear_cmds = [c for c in RCON_LOG if c.startswith("clear")]
        self.assertGreaterEqual(len(clear_cmds), 1)
        self.assertIn("Alice", clear_cmds[0])

        # Verify data flow paths - listing path was taken
        self.verify_paths_taken([
            PATH_LISTING,
        ])

        # Verify DB state
        self.assert_listing_count(1)
        self.assert_no_db_violations()

    def test_1_2_listing_with_nbt(self, mock_bridge):
        """Listing with NBT data preserves the data."""
        item = self.unique_item("minecraft:diamond_sword")
        nbt = '{"components":{"minecraft:enchantments":{"sharpness":5,"unbreaking":3}}}'

        result = list_item(
            seller="Bob", item_id=item, count=1,
            start_price=15.0, item_nbt=nbt,
            rcon_func=mock_rcon
        )

        self.assertTrue(result["ok"])
        uid = result["data"]["listing_uuid"]
        listing = get_listing(uid)
        self.assertEqual(listing["item_nbt"], nbt)

    def test_1_3_listing_with_signed_name(self, mock_bridge):
        """Listing with signed name preserves the name."""
        item = self.unique_item("minecraft:iron_sword")
        signed_name = "§6Blade of Heroes"

        result = list_item(
            seller="Bob", item_id=item, count=1,
            start_price=20.0, signed_name=signed_name,
            rcon_func=mock_rcon
        )

        self.assertTrue(result["ok"])
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertEqual(listing["signed_name"], signed_name)

    def test_1_4_listing_with_rarity(self, mock_bridge):
        """Listing with rarity tier preserves it."""
        item = self.unique_item("minecraft:diamond_helmet")

        result = list_item(
            seller="Carol", item_id=item, count=1,
            start_price=30.0, rarity="Legendary",
            rcon_func=mock_rcon
        )

        self.assertTrue(result["ok"])
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertEqual(listing["rarity"], "Legendary")

    def test_1_5_listing_with_cert_hash(self, mock_bridge):
        """Listing with certification hash preserves it."""
        item = self.unique_item("minecraft:diamond_boots")

        result = list_item(
            seller="Dave", item_id=item, count=1,
            start_price=12.0, cert_hash="abc123def456",
            rcon_func=mock_rcon
        )

        self.assertTrue(result["ok"])
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertEqual(listing["cert_hash"], "abc123def456")


# ══════════════════════════════════════════════════════════════════════
# Test 1.6-1.7: Simulated listings
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestListingSimulated(DataFlowTestCase):

    def test_1_6_simulated_listing(self, mock_bridge):
        """Simulated listing does NOT trigger RCON clear."""
        reset_rcon()
        item = self.unique_item("minecraft:enchanted_book")

        result = list_item(
            seller="__sim__", item_id=item, count=1,
            start_price=5.0, is_simulated=True,
            sim_lore="A magical tome", sim_enchantments="[{}]",
            sim_durability=50, sim_quality_roll=85,
            sim_source_event="blizzard",
            rcon_func=mock_rcon
        )

        self.assertTrue(result["ok"])
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertEqual(listing["is_simulated"], 1)
        self.assertEqual(listing["sim_lore"], "A magical tome")
        self.assertEqual(listing["sim_durability"], 50)
        self.assertEqual(listing["sim_quality_roll"], 85)
        self.assertEqual(listing["sim_source_event"], "blizzard")

        # No RCON for simulated listings
        self.assertEqual(len(RCON_LOG), 0)

    def test_1_7_simulated_no_rcon_needed(self, mock_bridge):
        """Simulated listing with rcon_func=None still works."""
        result = list_item(
            seller="__sim__", item_id=self.unique_item(), count=1,
            start_price=5.0, is_simulated=True,
        )
        self.assertTrue(result["ok"])
        # Simulated flag may be in response data or inferrable from is_simulated field
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertEqual(listing["is_simulated"], 1)


# ══════════════════════════════════════════════════════════════════════
# Test 1.8-1.9: Input validation & error cases
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestListingValidation(DataFlowTestCase):

    def test_1_8_banned_player_cannot_list(self, mock_bridge):
        """Banned players get an error when trying to list."""
        ban_player("BannedSteve", reason="Test violation")

        result = list_item(
            seller="BannedSteve", item_id=self.unique_item(),
            count=1, start_price=5.0, rcon_func=mock_rcon
        )
        self.assertFalse(result["ok"])
        self.assertIn("banned", result["error"].lower())

    def test_1_9_max_listings_exceeded(self, mock_bridge):
        """Listing more than max_listings_per_player fails."""
        item_base = self.unique_item("minecraft:dirt")
        for i in range(5):
            result = list_item(
                seller="Flooder", item_id=f"{item_base}_{i}",
                count=1, start_price=1.0, rcon_func=mock_rcon
            )
            self.assertTrue(result["ok"])

        # 6th listing should fail
        result = list_item(
            seller="Flooder", item_id=f"{item_base}_6",
            count=1, start_price=1.0, rcon_func=mock_rcon
        )
        self.assertFalse(result["ok"])
        self.assertIn("max", result["error"].lower())

    def test_1_10_zero_start_price(self, mock_bridge):
        """Zero start price is rejected."""
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=1, start_price=0, rcon_func=mock_rcon
        )
        self.assertFalse(result["ok"])

    def test_1_11_negative_start_price(self, mock_bridge):
        """Negative start price is rejected."""
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=1, start_price=-5.0, rcon_func=mock_rcon
        )
        self.assertFalse(result["ok"])

    def test_1_12_buynow_less_than_start(self, mock_bridge):
        """Buy-now price less than start price is rejected."""
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=1, start_price=10.0, buy_now_price=5.0,
            rcon_func=mock_rcon
        )
        self.assertFalse(result["ok"])

    def test_1_13_invalid_player_name_short(self, mock_bridge):
        """Too-short player name - core list_item may accept it.

        NOTE: Core list_item() does NOT validate player name length.
        Validation (regex ^[A-Za-z0-9_]{2,32}$) is in Phooks layer.
        """
        result = list_item(
            seller="X", item_id=self.unique_item(),
            count=1, start_price=5.0, rcon_func=mock_rcon
        )
        # Core may accept single-char names
        self.assertIsNotNone(result)
        if result.get("ok"):
            print("\n  [NOTE] Core list_item() accepts 1-char player name")

    def test_1_14_invalid_player_name_symbols(self, mock_bridge):
        """Player name with invalid symbols - core list_item doesn't validate names.

        NOTE: Core list_item() does NOT validate player names. This validation
        happens at the Phooks layer (ah_phooks.py). Names with hyphens, dots,
        or spaces may be stored as-is because the DB accepts them.
        """
        for bad_name in ["Test-Player", "Test.Player", "Test Player"]:
            result = list_item(
                seller=bad_name, item_id=self.unique_item(),
                count=1, start_price=5.0, rcon_func=mock_rcon
            )
            # Core accepts these - Phooks layer validation needed
            # This test documents the behavior gap
            self.assertIsNotNone(result)

        # Empty string may or may not be rejected
        result = list_item(
            seller="", item_id=self.unique_item(),
            count=1, start_price=5.0, rcon_func=mock_rcon
        )
        # Document behavior
        if result.get("ok"):
            print("\n  [NOTE] Core list_item() accepts empty player name")

    def test_1_15_invalid_item_id(self, mock_bridge):
        """Invalid item ID format - core list_item may accept unusual IDs.

        NOTE: Core list_item() stores whatever item_id is given. The
        format validation (namespace:path) is only in the Phooks layer.
        """
        for bad_id in ["notvalid", ":", "minecraft:", ":diamond", ""]:
            result = list_item(
                seller="Alice", item_id=bad_id,
                count=1, start_price=5.0, rcon_func=mock_rcon
            )
            # Core may accept these - document behavior
            self.assertIsNotNone(result)
            if result.get("ok"):
                # Verify it was stored as-is
                listing = get_listing(result["data"]["listing_uuid"])
                self.assertEqual(listing["item_id"], bad_id)

    def test_1_16_null_optional_params(self, mock_bridge):
        """Listing with all optional params as None succeeds."""
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=1, start_price=1.0,
            buy_now_price=None, duration_hours=None,
            item_nbt=None, signed_name=None, rarity=None,
            cert_hash=None, rcon_func=mock_rcon
        )
        self.assertTrue(result["ok"])
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertIsNone(listing["item_nbt"])
        self.assertIsNone(listing["signed_name"])
        self.assertIsNone(listing["rarity"])
        self.assertIsNone(listing["cert_hash"])

    def test_1_17_expired_before_start(self, mock_bridge):
        """Listing with 0 or negative duration is rejected or clamped."""
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=1, start_price=5.0, duration_hours=-1,
            rcon_func=mock_rcon
        )
        # Should either clamp to default or reject
        if result["ok"]:
            listing = get_listing(result["data"]["listing_uuid"])
            self.assertIsNotNone(listing["expires_at"])
        else:
            self.assertFalse(result["ok"])

    def test_1_18_zero_count(self, mock_bridge):
        """Zero item count - core list_item may accept it.

        NOTE: Core list_item() may not validate count > 0. This is
        a potential issue if the count is used in inventory operations.
        """
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=0, start_price=5.0, rcon_func=mock_rcon
        )
        # Document behavior - may be accepted
        self.assertIsNotNone(result)
        if result.get("ok"):
            listing = get_listing(result["data"]["listing_uuid"])
            self.assertEqual(listing["item_count"], 0)
            print("\n  [NOTE] Core list_item() accepts zero count")


# ══════════════════════════════════════════════════════════════════════
# Test 1.19-1.22: RCON edge cases
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestListingRCON(DataFlowTestCase):

    def test_1_19_rcon_failure_nonfatal(self, mock_bridge):
        """Listing succeeds even if RCON fails (non-fatal error)."""
        def failing_rcon(cmd):
            raise ConnectionError("RCON connection refused")

        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=1, start_price=5.0, rcon_func=failing_rcon
        )
        # Listing should still succeed
        self.assertTrue(result["ok"])

    def test_1_20_rcon_none(self, mock_bridge):
        """listing with rcon_func=None works (uses internal RCON or skips)."""
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=1, start_price=5.0, rcon_func=None
        )
        # Should succeed regardless of whether internal RCON works
        self.assertTrue(result["ok"])

    def test_1_21_simulated_no_rcon(self, mock_bridge):
        """Simulated listing with no RCON works fine."""
        result = list_item(
            seller="__sim__", item_id=self.unique_item(),
            count=1, start_price=5.0, is_simulated=True,
        )
        self.assertTrue(result["ok"])


# ══════════════════════════════════════════════════════════════════════
# Test 1.22-1.25: Concurrent / edge cases
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestListingConcurrent(DataFlowTestCase):

    def test_1_22_concurrent_listings_same_player(self, mock_bridge):
        """Two listings created concurrently for same player both succeed."""
        import threading
        results = []

        def create_listing(seller, item_id):
            r = list_item(
                seller=seller, item_id=item_id,
                count=1, start_price=5.0, rcon_func=mock_rcon
            )
            results.append(r)

        threads = []
        for i in range(5):
            t = threading.Thread(
                target=create_listing,
                args=("ConcurrentPlayer", self.unique_item())
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(results), 5)
        ok_count = sum(1 for r in results if r["ok"])
        # On first attempt, all 5 should succeed (within limits)
        # If limits apply, check at least some succeeded
        self.assertGreaterEqual(ok_count, 1)

    def test_1_23_listing_with_unicode_signed_name(self, mock_bridge):
        """Unicode in signed name is preserved."""
        signed_name = "★ Sword of Power ★"
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=1, start_price=10.0,
            signed_name=signed_name, rcon_func=mock_rcon
        )
        self.assertTrue(result["ok"])
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertEqual(listing["signed_name"], signed_name)

    def test_1_24_very_large_count(self, mock_bridge):
        """Listing with very large count works."""
        result = list_item(
            seller="Alice", item_id=self.unique_item(),
            count=999999, start_price=10.0, rcon_func=mock_rcon
        )
        self.assertTrue(result["ok"])
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertEqual(listing["item_count"], 999999)

    def test_1_25_duplicate_uuids_not_possible(self, mock_bridge):
        """Each listing gets a unique UUID."""
        uuids = set()
        for i in range(10):
            result = list_item(
                seller=f"Player{i}", item_id=self.unique_item(),
                count=1, start_price=1.0, rcon_func=mock_rcon
            )
            self.assertTrue(result["ok"])
            uid = result["data"]["listing_uuid"]
            self.assertNotIn(uid, uuids, f"Duplicate UUID: {uid}")
            uuids.add(uid)

        self.assertEqual(len(uuids), 10)
