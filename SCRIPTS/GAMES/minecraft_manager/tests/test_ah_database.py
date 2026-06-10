"""Test database schema, seed data, and query helpers."""
import unittest, sys, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["AH_DB_PATH"] = ":memory:"
from AUCTIONHOUSE.ah_database import DatabaseManager, initialize_database, get_db

class TestDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = initialize_database(force=True)
    def test_tables_exist(self):
        tables = self.db.fetch_all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = {t["name"] for t in tables}
        required = {"auction_listings", "transaction_history", "price_history",
                     "market_events", "simulated_inventory", "ai_notes", "player_balances"}
        self.assertTrue(required.issubset(names), f"Missing tables: {required - names}")
    def test_seed_simulated_inventory(self):
        items = self.db.fetch_all("SELECT * FROM simulated_inventory ORDER BY item_id")
        self.assertGreaterEqual(len(items), 15)
    def test_seed_data_prices(self):
        coal = self.db.fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        self.assertIsNotNone(coal)
        self.assertAlmostEqual(coal["base_price"], 0.5)
    def test_fetch_one_none(self):
        r = self.db.fetch_one("SELECT * FROM auction_listings WHERE id=?", (9999,))
        self.assertIsNone(r)
    def test_fetch_all_empty(self):
        r = self.db.fetch_all("SELECT * FROM auction_listings")
        self.assertEqual(len(r), 0)
    def test_insert_and_query(self):
        self.db.execute("INSERT INTO auction_listings (listing_uuid, seller_name, item_id, item_count, start_price, status, listed_at) VALUES (?,?,?,?,?,?,?)",
                         ("test-uuid", "Steve", "minecraft:diamond", 1, 10.0, "active", "2026-01-01"))
        r = self.db.fetch_one("SELECT * FROM auction_listings WHERE listing_uuid=?", ("test-uuid",))
        self.assertIsNotNone(r)
        self.assertEqual(r["seller_name"], "Steve")
    def test_thread_safety(self):
        import threading
        errors = []
        def write(i):
            try:
                self.db.execute("INSERT INTO auction_listings (listing_uuid, seller_name, item_id, item_count, start_price, status, listed_at) VALUES (?,?,?,?,?,?,?)",
                                 (f"t-uuid-{i}", f"Player{i}", "minecraft:diamond", 1, 10.0, "active", "2026-01-01"))
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=write, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0)
        count = self.db.fetch_one("SELECT COUNT(*) as c FROM auction_listings WHERE listing_uuid LIKE 't-uuid-%'")
        self.assertEqual(count["c"], 10)

if __name__ == "__main__":
    unittest.main()
