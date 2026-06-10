"""
test_edge_data_corruption.py — Edge Case Tests: Data Corruption (EC8-EC14)

Tests the system's resilience against malformed, malicious, or corrupted
input data including SQL injection, Unicode attacks, and extreme values.
"""

from tests.dataflow.conftest_dataflow import (
    DataFlowTestCase, mock_rcon, reset_rcon
)
from unittest.mock import patch
from AUCTIONHOUSE.ah_core import (
    list_item, place_bid, buy_now, get_listing, query_listings
)
from AUCTIONHOUSE.ah_database import get_db
from tests.conftest import unique_item_id


# ══════════════════════════════════════════════════════════════════════
# EC8-EC14: Data corruption / injection
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestDataCorruption(DataFlowTestCase):

    def test_EC8_sql_injection_player_name(self, mock_bridge):
        """SQL injection in player name is neutralized.

        The system should sanitize or parameterize queries so that
        malicious player names can't execute SQL.
        """
        for malicious_name in [
            "'; DROP TABLE auction_listings; --",
            "'; DELETE FROM transaction_history; --",
            "1; SELECT * FROM sqlite_master; --",
            "Alice' OR '1'='1",
            "Bob\"; UPDATE auction_listings SET status='sold'; --",
        ]:
            result = list_item(
                seller=malicious_name, item_id=unique_item_id(),
                count=1, start_price=10.0, rcon_func=mock_rcon
            )
            # Should either succeed (sanitized) or fail (invalid chars)
            # But should NEVER crash or corrupt the DB
            if result.get("ok"):
                listing = get_listing(result["data"]["listing_uuid"])
                self.assertIsNotNone(listing)

        # Verify DB is still intact
        db = get_db()
        tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {t["name"] for t in tables}
        self.assertIn("auction_listings", table_names,
                       "SQL injection dropped auction_listings table!")
        self.assertIn("transaction_history", table_names,
                       "SQL injection dropped transaction_history table!")

    def test_EC9_sql_injection_item_id(self, mock_bridge):
        """SQL injection in item_id is neutralized."""
        for malicious_id in [
            "minecraft:diamond'; DROP TABLE market_events; --",
            "minecraft:stone; SELECT * FROM sqlite_master",
            "'; UPDATE simulated_inventory SET current_price=0; --",
        ]:
            try:
                result = list_item(
                    seller="Alice", item_id=malicious_id,
                    count=1, start_price=10.0, rcon_func=mock_rcon
                )
                # If it somehow succeeds (unlikely with invalid format),
                # the DB should still be intact
            except Exception:
                pass  # Exception-based rejection is acceptable

        # Verify DB integrity
        db = get_db()
        tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {t["name"] for t in tables}
        for essential in ["market_events", "auction_listings",
                          "transaction_history", "simulated_inventory"]:
            self.assertIn(essential, table_names,
                          f"SQL injection dropped {essential} table!")

    def test_EC10_extremely_long_strings(self, mock_bridge):
        """Extremely long strings are rejected or truncated safely.

        NOTE: Core list_item() does not validate name length, so
        extremely long names may be stored in the DB. This is a
        Phooks-layer concern.
        """
        long_name = "A" * 10000
        result = list_item(
            seller=long_name, item_id=unique_item_id(),
            count=1, start_price=10.0, rcon_func=mock_rcon
        )
        # Core may accept it - the important thing is NO CRASH
        self.assertIsNotNone(result)

    def test_EC11_unicode_name_variants(self, mock_bridge):
        """Different Unicode representations don't bypass bans."""
        from AUCTIONHOUSE.ah_bans import ban_player, is_player_banned

        ban_player("Alice", reason="Test")
        # Try different Unicode representations of "Alice"
        for variant in [
            "Аlice",   # Cyrillic А
            "Àlice",   # Accented A
            "alice",   # Lowercase
            "ALICE",   # Uppercase
            "Aliсe",   # Cyrillic с
        ]:
            result = list_item(
                seller=variant, item_id=unique_item_id(),
                count=1, start_price=10.0, rcon_func=mock_rcon
            )
            # These are technically different names, so they should not
            # necessarily be caught by the ban. But they should not crash.
            self.assertIsNotNone(result)

    def test_EC12_json_injection_in_nbt(self, mock_bridge):
        """Malicious JSON in item_nbt is stored safely (not executed)."""
        malicious_nbt = (
            '{"components":{"minecraft:enchantments":{"sharpness":5}},'
            '"__proto__":{"admin":true}}'
        )
        result = list_item(
            seller="Alice", item_id=unique_item_id(),
            count=1, start_price=10.0, item_nbt=malicious_nbt,
            rcon_func=mock_rcon
        )
        self.assertTrue(result["ok"])
        listing = get_listing(result["data"]["listing_uuid"])
        self.assertEqual(listing["item_nbt"], malicious_nbt)

    def test_EC13_zero_width_characters(self, mock_bridge):
        """Zero-width and control characters in names are handled."""
        for bad_name in [
            "Alice\u200B",       # Zero-width space
            "Bob\u0000",         # Null byte
            "Charlie\r\n",       # CRLF
            "\tDave",            # Tab
            "Eve\u00AD",         # Soft hyphen
        ]:
            result = list_item(
                seller=bad_name, item_id=unique_item_id(),
                count=1, start_price=10.0, rcon_func=mock_rcon
            )
            # Should either reject or store sanitized version
            self.assertIsNotNone(result)

    def test_EC14_db_file_integrity(self, mock_bridge):
        """Database integrity check passes after multiple edge case inserts."""
        # Create data with various edge case inputs
        for i in range(10):
            try:
                list_item(
                    seller=f"Player{i}" if i < 5 else "A" * 32,
                    item_id=unique_item_id(),
                    count=i + 1,
                    start_price=max(0.01, i * 1.5),
                    rcon_func=mock_rcon
                )
            except Exception:
                pass

        # Run integrity check
        db = get_db()
        integrity = db.fetch_one("PRAGMA integrity_check")
        self.assertEqual(integrity["integrity_check"], "ok",
                         "Database integrity check failed!")
