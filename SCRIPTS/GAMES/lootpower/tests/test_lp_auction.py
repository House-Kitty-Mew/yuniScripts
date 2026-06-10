"""
Tests for Auction House.
"""
import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_database import DatabaseEngine
from lp_auction import AuctionHouse


class TestAuctionHouse(unittest.TestCase):
    """Tests for auction operations."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        cls.tmp.close()
        DatabaseEngine._instance = None
        cls.db = DatabaseEngine(db_path=cls.tmp.name)
        cls.ah = AuctionHouse()

        # Create users and loot
        cls.db.execute(
            "INSERT INTO users (user_id, user_name) VALUES (?,?)",
            ("seller1", "Seller")
        )
        cls.db.execute(
            "INSERT INTO users (user_id, user_name) VALUES (?,?)",
            ("buyer1", "Buyer")
        )
        cls.db.execute(
            "INSERT INTO loot_table (loot, loot_chance, loot_chance_raise, loot_id) "
            "VALUES (?,?,?,?)",
            ("rare_sword", 100.0, 1.0, 1)
        )
        # Give seller loot
        cls.db.execute(
            "INSERT INTO users_loot (user_id, loot, loot_id, loot_amount) "
            "VALUES (?,?,?,?)",
            ("seller1", "rare_sword", 1, 10)
        )
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(cls.tmp.name):
            os.unlink(cls.tmp.name)

    def setUp(self):
        """Clean auction table and reset loot."""
        self.db.execute("DELETE FROM auction")
        self.db.execute("UPDATE users_loot SET loot_amount=10 WHERE user_id='seller1' AND loot_id=1")
        self.db.commit()

    def test_01_create_listing(self):
        """Create a valid auction listing."""
        aid = self.ah.create_listing("seller1", 1, 2, 0, 0, 100)
        self.assertIsNotNone(aid)
        self.assertGreater(aid, 0)

    def test_02_create_listing_insufficient_loot(self):
        """Listing with insufficient loot fails."""
        aid = self.ah.create_listing("seller1", 1, 999, 0, 0, 100)
        self.assertIsNone(aid)

    def test_03_create_listing_deducts_loot(self):
        """Creating a listing deducts loot from seller."""
        self.ah.create_listing("seller1", 1, 3, 0, 0, 100)
        row = self.db.fetchone(
            "SELECT loot_amount FROM users_loot WHERE user_id='seller1' AND loot_id=1"
        )
        self.assertEqual(row["loot_amount"], 7)  # Started at 10

    def test_04_get_active_listings(self):
        """Get all active listings."""
        self.ah.create_listing("seller1", 1, 1)
        self.ah.create_listing("seller1", 1, 2)
        listings = self.ah.get_active_listings()
        self.assertEqual(len(listings), 2)

    def test_05_buy_listing(self):
        """Buy a listing transfers loot."""
        aid = self.ah.create_listing("seller1", 1, 1)
        result = self.ah.buy_listing(aid, "buyer1")
        self.assertIn("Purchased", result)
        # Buyer should have the loot
        row = self.db.fetchone(
            "SELECT loot_amount FROM users_loot WHERE user_id='buyer1' AND loot_id=1"
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["loot_amount"], 1)

    def test_06_buy_own_listing(self):
        """Buying own listing fails."""
        aid = self.ah.create_listing("seller1", 1, 1)
        result = self.ah.buy_listing(aid, "seller1")
        self.assertIn("Cannot buy your own", result)

    def test_07_buy_nonexistent_listing(self):
        """Buying non-existent listing fails."""
        result = self.ah.buy_listing(999, "buyer1")
        self.assertIn("not found", result.lower())

    def test_08_cancel_listing(self):
        """Cancel listing returns loot to seller."""
        before = self.db.fetchone(
            "SELECT loot_amount FROM users_loot WHERE user_id='seller1' AND loot_id=1"
        )["loot_amount"]
        aid = self.ah.create_listing("seller1", 1, 1)
        result = self.ah.cancel_listing(aid, "seller1")
        self.assertIn("cancelled", result.lower())
        after = self.db.fetchone(
            "SELECT loot_amount FROM users_loot WHERE user_id='seller1' AND loot_id=1"
        )["loot_amount"]
        self.assertEqual(after, before)  # Loot returned

    def test_09_cancel_other_listing(self):
        """Cancel someone else's listing fails."""
        aid = self.ah.create_listing("seller1", 1, 1)
        result = self.ah.cancel_listing(aid, "buyer1")
        self.assertIn("Not your", result)


if __name__ == "__main__":
    unittest.main()