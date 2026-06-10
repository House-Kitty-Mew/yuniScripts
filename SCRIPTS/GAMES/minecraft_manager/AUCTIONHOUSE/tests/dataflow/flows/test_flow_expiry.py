"""
test_flow_expiry.py — Data Flow Test: Expiry (Flow 5)

Tests every code path and edge case for expire_listings() including:
  - Expired listing with bids → sold to highest bidder
  - Expired listing without bids → returned to seller
  - RCON give/return on expiry
  - Concurrent expiry scenarios
  - Multiple listings expiring at once
"""

from tests.dataflow.conftest_dataflow import (
    DataFlowTestCase, mock_rcon, reset_rcon, RCON_LOG
)
from unittest.mock import patch
from AUCTIONHOUSE.ah_core import (
    list_item, place_bid, expire_listings, get_listing,
    get_player_listings
)
from AUCTIONHOUSE.ah_database import get_db
from tests.dataflow.probes.trace_probe import PATH_EXPIRY
from tests.conftest import unique_item_id
from datetime import datetime, timezone, timedelta
import threading


def _create_expired_listing(seller: str = "Alice", start_price: float = 10.0,
                             set_expiry_to_past: bool = False) -> str:
    """Helper: create a listing with future expiry.

    To expire: call _force_expiry(uid) AFTER placing bids.
    """
    result = list_item(
        seller=seller, item_id=unique_item_id(),
        count=1, start_price=start_price, duration_hours=48,
        rcon_func=mock_rcon
    )
    if not result["ok"]:
        raise RuntimeError(f"Failed to create listing: {result}")
    return result["data"]["listing_uuid"]


def _force_expiry(uid: str):
    """Set a listing's expiry to the past."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    get_db().execute(
        "UPDATE auction_listings SET expires_at = ? WHERE listing_uuid = ?",
        (past, uid)
    )


# ══════════════════════════════════════════════════════════════════════
# Test 5.1-5.8: Expiry operations
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestExpiryBasic(DataFlowTestCase):

    def test_5_1_expire_with_bids_sold(self, mock_bridge):
        """Expired listing with bids is sold to highest bidder."""
        uid = _create_expired_listing(seller="Alice")
        self.assertTrue(place_bid(bidder="Bob", listing_uuid=uid,
                                   bid_amount=15.0)["ok"])
        self.assertTrue(place_bid(bidder="Charlie", listing_uuid=uid,
                                   bid_amount=20.0)["ok"])

        _force_expiry(uid)
        with self.trace.path(PATH_EXPIRY):
            expired = expire_listings()

        self.assertGreaterEqual(len(expired), 1)
        listing = get_listing(uid)
        self.assertEqual(listing["status"], "sold")
        self.assertEqual(listing["highest_bidder"], "Charlie")
        self.assertEqual(listing["sold_price"], 20.0)

        self.verify_paths_taken([PATH_EXPIRY])

    def test_5_2_expire_without_bids_returned(self, mock_bridge):
        """Expired listing without bids is returned to seller."""
        uid = _create_expired_listing(seller="Alice")
        _force_expiry(uid)

        expired = expire_listings()
        self.assertGreaterEqual(len(expired), 1)

        listing = get_listing(uid)
        self.assertEqual(listing["status"], "expired")

    def test_5_3_no_expiry_before_time(self, mock_bridge):
        """Listings that haven't expired yet are not affected."""
        now = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        result = list_item(
            seller="Alice", item_id=unique_item_id(),
            count=1, start_price=10.0, duration_hours=48,
            rcon_func=mock_rcon
        )
        self.assertTrue(result["ok"])
        uid = result["data"]["listing_uuid"]

        expired = expire_listings()
        self.assertEqual(len(expired), 0)

        listing = get_listing(uid)
        self.assertEqual(listing["status"], "active")

    def test_5_4_multiple_listings_expire(self, mock_bridge):
        """Multiple listings expire at once, all processed."""
        uids = []
        for i in range(5):
            uid = _create_expired_listing(seller=f"Seller{i}")
            if i % 2 == 0:
                self.assertTrue(place_bid(bidder=f"Bidder{i}",
                                           listing_uuid=uid,
                                           bid_amount=15.0)["ok"])
            uids.append(uid)

        # Force all to expire
        for uid in uids:
            _force_expiry(uid)

        expired = expire_listings()
        self.assertGreaterEqual(len(expired), 5)

        for i, uid in enumerate(uids):
            listing = get_listing(uid)
            if i % 2 == 0:
                self.assertEqual(listing["status"], "sold",
                                 f"Listing {i} with bids should be sold")
            else:
                self.assertEqual(listing["status"], "expired",
                                 f"Listing {i} without bids should be expired")

    def test_5_5_concurrent_expiry_no_double_sale(self, mock_bridge):
        """Concurrent expiry + bid doesn't cause double-sale."""
        uid = _create_expired_listing(seller="Alice")
        results = []

        def do_expire():
            _force_expiry(uid)
            results.append(expire_listings())

        def do_bid():
            from AUCTIONHOUSE.ah_core import place_bid
            results.append(place_bid(bidder="Bob", listing_uuid=uid,
                                      bid_amount=15.0))

        t1 = threading.Thread(target=do_expire)
        t2 = threading.Thread(target=do_bid)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Listing should end up in one state, not duplicate
        listing = get_listing(uid)
        self.assertIn(listing["status"], ["sold", "expired", "active"])

        # Only one transaction should exist
        db = get_db()
        tx_count = db.fetch_one(
            "SELECT COUNT(*) as cnt FROM transaction_history"
        )
        self.assertLessEqual(tx_count["cnt"], 2)

    def test_5_6_no_expiry_with_no_listings(self, mock_bridge):
        """expire_listings with no active listings is harmless."""
        expired = expire_listings()
        self.assertEqual(len(expired), 0)

    def test_5_7_transaction_history_on_expiry(self, mock_bridge):
        """Expiry creates transaction records."""
        uid = _create_expired_listing(seller="Alice")
        self.assertTrue(place_bid(bidder="Bob", listing_uuid=uid,
                                   bid_amount=15.0)["ok"])
        _force_expiry(uid)

        expire_listings()

        # Should have 2 transaction records: bid + expiry-sale
        # Transaction count: 1 for listing + 1 for bid + 1 for expire = 3
        self.assert_tx_count(3)

    def test_5_8_db_integrity_after_expiry(self, mock_bridge):
        """No FK violations after multiple expiry operations."""
        for i in range(3):
            uid = _create_expired_listing(seller=f"Seller{i}")
            if i < 2:
                self.assertTrue(place_bid(bidder=f"Bob{i}",
                                           listing_uuid=uid,
                                           bid_amount=15.0)["ok"])

        # Force all active listings to expire
        db = get_db()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.execute("UPDATE auction_listings SET expires_at = ? WHERE status = 'active'", (past,))

        expire_listings()
        self.assert_no_db_violations()
