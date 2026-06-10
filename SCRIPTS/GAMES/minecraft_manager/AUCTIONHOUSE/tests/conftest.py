"""
conftest.py — Shared test infrastructure for Auction House tests.

Provides:
  - Path setup so AUCTIONHOUSE module can be imported
  - Mock RCON function
  - Database cleanup helpers
  - Unique item ID generation
  - Test base classes
"""

import sys, os, json, uuid

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))      # .../AUCTIONHOUSE/tests
_AH_DIR = os.path.dirname(_HERE)                        # .../AUCTIONHOUSE
_MANAGER_DIR = os.path.dirname(_AH_DIR)                 # .../minecraft_manager
for p in [_AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from unittest import TestCase
from unittest.mock import patch


# ── Shared counters ───────────────────────────────────────────────
_listing_counter = 0


def unique_item_id(base="minecraft:dirt") -> str:
    """Return a unique Minecraft item ID for test isolation."""
    global _listing_counter
    _listing_counter += 1
    return f"{base}_tc{_listing_counter}"


def unique_uuid() -> str:
    """Return a unique valid UUID string."""
    return str(uuid.uuid4())


# ── Mock RCON ─────────────────────────────────────────────────────
RCON_LOG: list[str] = []


def mock_rcon(cmd: str) -> str:
    """Mock RCON: logs commands and returns plausible responses."""
    try:
        RCON_LOG.append(cmd)

    except Exception as e:
        logger.error(f"mock_rcon failed: {e}")
        return ""
    parts = cmd.split()
    if parts[0] == "clear":
        return f"Cleared the inventory of {parts[1]}, removing the item."
    if parts[0] == "give":
        return f"Gave {parts[1]} some {parts[2]}."
    if parts[0] == "tellraw":
        return "ok"
    return "ok"


def reset_rcon():
    """Clear the RCON log."""
    RCON_LOG.clear()


# ── Test base class ───────────────────────────────────────────────
class AHTestCase(TestCase):
    """Base test case with database setup and common helpers."""

    @classmethod
    def setUpClass(cls):
        """One-time: ensure DB schema exists."""
        from AUCTIONHOUSE.ah_database import initialize_database
        initialize_database()

    def setUp(self):
        """Before each test: reset RCON and wipe test data."""
        reset_rcon()
        self._wipe_test_data()

    def _wipe_test_data(self):
        """Clean all AH tables for test isolation."""
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        for table in [
            "transaction_history", "auction_listings", "player_balances",
            "ai_notes", "price_history", "market_events",
        ]:
            db.execute(f"DELETE FROM {table}")

    def unique_item(self, base="minecraft:dirt") -> str:
        return unique_item_id(base)

    def unique_uuid(self) -> str:
        return unique_uuid()


# ── Mocks for external dependencies ──────────────────────────────
def patch_eco_bridge(test_func):
    """Decorator: patch _get_eco_bridge to return None."""
    return patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)(test_func)

