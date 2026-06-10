"""Test Phooks event dispatch and input validation."""
import unittest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["AH_DB_PATH"] = ":memory:"
from AUCTIONHOUSE.ah_database import initialize_database
from AUCTIONHOUSE.ah_phooks import (
    dispatch_event, PHOOKS_EVENTS_LISTEN, PHOOKS_EVENTS_EMIT,
    validate_player_name, validate_listing_uuid, validate_item_id, sanitize_request
)

class TestPhooksEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_database(force=True)
    def test_event_declarations(self):
        self.assertIn("ah_list", PHOOKS_EVENTS_LISTEN)
        self.assertIn("ah_bid", PHOOKS_EVENTS_LISTEN)
        self.assertIn("ah_buy", PHOOKS_EVENTS_LISTEN)
        self.assertIn("ah_remove", PHOOKS_EVENTS_LISTEN)
        self.assertIn("ah_query", PHOOKS_EVENTS_LISTEN)
        self.assertIn("ah_test", PHOOKS_EVENTS_LISTEN)
        self.assertIn("ah_list_response", PHOOKS_EVENTS_EMIT)
        self.assertIn("ah_test_response", PHOOKS_EVENTS_EMIT)
    def test_dispatch_unknown(self):
        captured = []
        dispatch_event("unknown_event", {}, emit_fn=lambda e, d: captured.append(d))
        self.assertEqual(len(captured), 0)
    def test_dispatch_ah_query(self):
        captured = []
        dispatch_event("ah_query", {"request_uuid": "t1", "player_name": "Steve", "filter_type": "all"},
                        emit_fn=lambda e, d: captured.append({"event": e, "data": d}))
        self.assertGreater(len(captured), 0)

class TestInputValidation(unittest.TestCase):
    def test_validate_player_name_ok(self):
        self.assertIsNone(validate_player_name("Steve"))
        self.assertIsNone(validate_player_name("Coochie_Tickler"))
        self.assertIsNone(validate_player_name("Alex123"))
    def test_validate_player_name_fail(self):
        self.assertIsNotNone(validate_player_name(""))
        self.assertIsNotNone(validate_player_name(None))
        self.assertIsNotNone(validate_player_name("a"))  # too short
        self.assertIsNotNone(validate_player_name("player with spaces"))
    def test_validate_uuid_ok(self):
        self.assertIsNone(validate_listing_uuid("550e8400-e29b-41d4-a716-446655440000"))
    def test_validate_uuid_fail(self):
        self.assertIsNotNone(validate_listing_uuid("not-a-uuid"))
        self.assertIsNotNone(validate_listing_uuid(""))
    def test_validate_item_id_ok(self):
        self.assertIsNone(validate_item_id("minecraft:diamond"))
        self.assertIsNone(validate_item_id("minecraft:diamond_sword"))
        self.assertIsNone(validate_item_id("minecraft:bow"))
    def test_validate_item_id_fail(self):
        self.assertIsNotNone(validate_item_id("diamond"))
        self.assertIsNotNone(validate_item_id(""))
    def test_sanitize_request_ok(self):
        self.assertIsNone(sanitize_request({"a": 1, "b": 2}, ["a", "b"]))
    def test_sanitize_request_missing(self):
        self.assertIsNotNone(sanitize_request({"a": 1}, ["a", "b"]))

if __name__ == "__main__":
    unittest.main()
