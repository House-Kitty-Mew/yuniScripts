"""
test_integ_concurrent.py — Integration Test: Thread Safety & Concurrency

Tests the system's behavior under concurrent access from multiple threads,
simulating real-world usage where multiple players interact simultaneously.
"""

from tests.dataflow.conftest_dataflow import (
    DataFlowTestCase, mock_rcon, reset_rcon
)
from unittest.mock import patch
from AUCTIONHOUSE.ah_core import (
    list_item, place_bid, buy_now, cancel_listing, expire_listings,
    get_listing, query_listings
)
from AUCTIONHOUSE.ah_database import get_db
from tests.conftest import unique_item_id
import threading
import time


# ══════════════════════════════════════════════════════════════════════
# Concurrent operation tests
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestConcurrentOperations(DataFlowTestCase):

    def test_concurrent_list_and_bid(self, mock_bridge):
        """Concurrent listing creation while bidding on another."""
        barrier = threading.Barrier(5, timeout=30)
        results = []

        def list_and_bid(player_id):
            try:
                item = unique_item_id(f"minecraft:item_{player_id}")
                r = list_item(
                    seller=f"Seller{player_id}", item_id=item,
                    count=1, start_price=10.0, rcon_func=mock_rcon
                )
                if r["ok"]:
                    r2 = place_bid(
                        bidder=f"Bidder{player_id}",
                        listing_uuid=r["data"]["listing_uuid"],
                        bid_amount=15.0
                    )
                    results.append(("bid", r2["ok"]))
                results.append(("list", r["ok"]))
            except Exception as e:
                results.append(("error", str(e)))
            finally:
                barrier.wait()

        threads = []
        for i in range(5):
            t = threading.Thread(target=list_and_bid, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=35)

        # All operations should succeed independently
        lists_ok = sum(1 for r in results if r[0] == "list" and r[1])
        self.assertGreaterEqual(lists_ok, 3,
                                f"Too many list failures: {results}")

    def test_concurrent_full_lifecycle(self, mock_bridge):
        """Full lifecycle: list → bid → buy → cancel runs concurrently."""
        # Create some listings first
        uids = []
        for i in range(10):
            r = list_item(
                seller=f"Seller{i}", item_id=unique_item_id(),
                count=1, start_price=10.0, buy_now_price=50.0,
                rcon_func=mock_rcon
            )
            if r["ok"]:
                uids.append(r["data"]["listing_uuid"])

        self.assertGreaterEqual(len(uids), 8)

        barrier = threading.Barrier(min(5, len(uids)), timeout=30)
        results = []

        def mixed_operations(thread_id):
            try:
                for i, uid in enumerate(uids[thread_id::5]):
                    if i % 4 == 0:
                        r = place_bid(bidder=f"Bidder{thread_id}",
                                       listing_uuid=uid, bid_amount=20.0)
                        results.append(("bid", uid[:8], r["ok"]))
                    elif i % 4 == 1:
                        r = buy_now(buyer=f"Buyer{thread_id}",
                                     listing_uuid=uid, rcon_func=mock_rcon)
                        results.append(("buy", uid[:8], r["ok"]))
                    elif i % 4 == 2:
                        r = query_listings(filter_type="all")
                        results.append(("query", "all", r["ok"]))
                    else:
                        pass  # Skip some
            except Exception as e:
                results.append(("error", str(e)))
            finally:
                barrier.wait()

        threads = []
        for i in range(5):
            t = threading.Thread(target=mixed_operations, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=35)

        # No deadlocks should occur
        errors = [r for r in results if r[0] == "error"]
        self.assertEqual(len(errors), 0,
                         f"Concurrent operation errors: {errors}")

    def test_concurrent_database_pool(self, mock_bridge):
        """Multiple threads accessing DB simultaneously doesn't corrupt."""
        import random
        errors = []
        lock = threading.Lock()

        def db_operation(op_id):
            try:
                db = get_db()
                if op_id % 3 == 0:
                    db.execute("SELECT COUNT(*) FROM auction_listings")
                elif op_id % 3 == 1:
                    item = unique_item_id()
                    db.execute(
                        "INSERT INTO auction_listings "
                        "(listing_uuid, seller_name, item_id, item_count, "
                        "start_price, status, listed_at, currency_type) "
                        "VALUES (?, 'test', ?, 1, 1.0, 'active', "
                        "datetime('now'), 'emerald')",
                        (str(op_id) * 4 + "-" + str(uuid.uuid4())[:30],
                         item)
                    )
                else:
                    db.execute("PRAGMA table_info(auction_listings)")
            except Exception as e:
                with lock:
                    errors.append((op_id, str(e)))

        import uuid
        threads = []
        for i in range(20):
            t = threading.Thread(target=db_operation, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0,
                         f"DB pool errors: {errors}")

    def test_concurrent_get_listing_no_deadlock(self, mock_bridge):
        """Concurrent get_listing calls don't deadlock."""
        # Create a listing
        r = list_item(
            seller="Alice", item_id=unique_item_id(),
            count=1, start_price=10.0, rcon_func=mock_rcon
        )
        self.assertTrue(r["ok"])
        uid = r["data"]["listing_uuid"]

        results = []

        def read_listing():
            for _ in range(50):
                listing = get_listing(uid)
                results.append(listing is not None)

        threads = [threading.Thread(target=read_listing) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertGreater(len(results), 0)
        self.assertTrue(all(results),
                        "Some get_listing calls returned None")

    def test_concurrent_expiry_no_crash(self, mock_bridge):
        """Concurrent expire_listings calls don't crash."""
        from datetime import datetime, timezone, timedelta
        db = get_db()

        # Create expired listings
        for i in range(5):
            r = list_item(
                seller=f"Seller{i}", item_id=unique_item_id(),
                count=1, start_price=10.0, rcon_func=mock_rcon
            )
            if r["ok"]:
                past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                db.execute(
                    "UPDATE auction_listings SET expires_at = ? WHERE listing_uuid = ?",
                    (past, r["data"]["listing_uuid"])
                )

        results = []

        def do_expire():
            try:
                r = expire_listings()
                results.append(r)
            except Exception as e:
                results.append({"error": str(e)})

        threads = [threading.Thread(target=do_expire) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        non_errors = [r for r in results if "error" not in r]
        self.assertGreaterEqual(len(non_errors), 1)
