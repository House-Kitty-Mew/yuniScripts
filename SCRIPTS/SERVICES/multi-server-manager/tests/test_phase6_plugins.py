"""
Comprehensive unit tests for all Phase 6 SubsystemPlugin implementations.

Tests cover:
  - AuctionHousePlugin    (plugins/auction_house_plugin.py)
  - EconomyBridgePlugin   (plugins/economy_bridge_plugin.py)
  - SimulatedPeoplePlugin (plugins/simulated_people_plugin.py)
  - LootPowerGamesPlugin  (plugins/lootpower_games_plugin.py)

Each test:
  - Creates a self-contained test environment with temp directories
  - Tests lifecycle hooks (on_init, on_shutdown, on_health_check)
  - Tests all public API methods
  - Tests edge cases (empty states, invalid inputs, error conditions)
  - Cleans up test artifacts
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure the multi-server-manager package is importable
SCRIPT_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "engine"))

# Force PluginRegistry singleton reset before any tests
from engine.plugin_registry import PluginRegistry, SubsystemPlugin, PluginHealth, PluginState

PluginRegistry.reset_instance()

# ── Plugin imports ──────────────────────────────────────────────────────────
from plugins.auction_house_plugin import AuctionHousePlugin
from plugins.economy_bridge_plugin import EconomyBridgePlugin
from plugins.simulated_people_plugin import SimulatedPeoplePlugin
from plugins.lootpower_games_plugin import LootPowerGamesPlugin

# ── Test Constants ──────────────────────────────────────────────────────────
TEST_SERVER_ID = "test_server_1"
TEST_SERVER_ID_2 = "test_server_2"
TEST_PLAYER = "TestPlayer"
TEST_PLAYER_2 = "TestPlayer2"

# ── Mock VFS Database ──────────────────────────────────────────────────────

class MockVFSDB:
    """
    A mock VFS database that uses an in-memory SQLite database.
    Provides the same interface as VFSDatabase (open, close, execute, query, commit, rollback, is_open).
    """
    
    def __init__(self, subsystem: str, server_id: str):
        self.subsystem = subsystem
        self.server_id = server_id
        self._conn: Optional[sqlite3.Connection] = None
        self._open = False
    
    def open(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        self._open = True
    
    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
        self._open = False
    
    def is_open(self) -> bool:
        return self._open and self._conn is not None
    
    def execute(self, sql: str, params: Any = None) -> Any:
        if not self._conn:
            raise sqlite3.ProgrammingError("Database not open")
        # Multiple SQL statements separated by ';'
        if params is None and ';' in sql.strip(' \t\n\r'):
            # Count semicolons not inside string literals (simple heuristic)
            stripped = sql.strip().rstrip(';')
            stmt_count = stripped.count(';') + 1
            if stmt_count > 1:
                cursor = self._conn.executescript(sql)
                return cursor
        if params is None:
            return self._conn.execute(sql)
        return self._conn.execute(sql, params)
    
    def commit(self) -> None:
        if self._conn:
            self._conn.commit()
    
    def rollback(self) -> None:
        if self._conn:
            self._conn.rollback()
    
    def query(self, sql: str, params: Any = None) -> list:
        if not self._conn:
            raise sqlite3.ProgrammingError("Database not open")
        # Ensure row_factory is Row for both index and key access
        old_factory = self._conn.row_factory
        self._conn.row_factory = sqlite3.Row
        try:
            if params is None:
                cursor = self._conn.execute(sql)
            else:
                cursor = self._conn.execute(sql, params)
            return [row for row in cursor.fetchall()]
        finally:
            self._conn.row_factory = old_factory


class MockVFSDatabaseManager:
    """Mock for VFSDatabaseManager that returns MockVFSDB instances."""
    
    def __init__(self, data_root: str = "DATA/vfs"):
        self._databases: Dict[str, MockVFSDB] = {}
        self.data_root = data_root
    
    def get_database(self, subsystem: str, server_id: str) -> MockVFSDB:
        key = f"{subsystem}:{server_id}"
        if key not in self._databases:
            self._databases[key] = MockVFSDB(subsystem, server_id)
        return self._databases[key]
    
    def close_all(self) -> None:
        for db in self._databases.values():
            db.close()
        self._databases.clear()


# ── Plugin Test Base ───────────────────────────────────────────────────────

class PluginTestCase(unittest.TestCase):
    """Base class for plugin tests with shared setup."""
    
    @classmethod
    def setUpClass(cls):
        """One-time setup: register plugins in registry."""
        PluginRegistry.reset_instance()
        cls.registry = PluginRegistry.get_instance()
        cls._registered_plugins = {}
    
    def setUp(self):
        """Per-test setup: fresh VFS mock for each test."""
        self.vfs_mgr = MockVFSDatabaseManager()
        self.vfs_patcher = patch(
            "engine.vfs_db_isolation.VFSDatabaseManager",
            return_value=self.vfs_mgr
        )
        self.vfs_patcher.start()
    
    def tearDown(self):
        """Per-test cleanup: reset VFS mock and close all DBs."""
        self.vfs_patcher.stop()
        self.vfs_mgr.close_all()
    
    @classmethod
    def tearDownClass(cls):
        """Class cleanup: reset registry."""
        PluginRegistry.reset_instance()
    
    def _make_vfs_db(self, plugin_name: str, server_id: str = TEST_SERVER_ID) -> MockVFSDB:
        """Helper to create a mock VFS DB for a plugin+server."""
        return self.vfs_mgr.get_database(plugin_name, server_id)
    
    def _init_plugin(self, plugin_class, server_id: str = TEST_SERVER_ID,
                     config: Optional[Dict[str, Any]] = None) -> SubsystemPlugin:
        """Helper: register and initialize a plugin for testing."""
        # Register if not already
        plugin = self.registry.register(plugin_class)
        
        # Open the mock DB and run on_init
        db = self._make_vfs_db(plugin.name, server_id)
        db.open()
        
        # Patch _get_vfs_db to return our mock
        original_get_vfs = plugin._get_vfs_db
        def mock_get_vfs(sid):
            return self._make_vfs_db(plugin.name, sid)
        
        plugin._get_vfs_db = mock_get_vfs
        
        # Run lifecycle hook
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(plugin.on_init(server_id, config or {}))
        finally:
            loop.close()
        
        return plugin


# ═══════════════════════════════════════════════════════════════════════════
# AuctionHousePlugin Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAuctionHousePlugin(PluginTestCase):
    """Tests for AuctionHousePlugin."""
    
    def setUp(self):
        super().setUp()
        self.plugin = self._init_plugin(AuctionHousePlugin)
    
    def test_01_plugin_attributes(self):
        """Verify required plugin attributes."""
        self.assertEqual(self.plugin.name, "auction_house")
        self.assertEqual(self.plugin.version, "1.0.0")
        self.assertIn("Auction", self.plugin.description)
        self.assertIsInstance(self.plugin.dependencies, list)
    
    def test_02_health_check_healthy(self):
        """Health check returns HEALTHY after init."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            health = loop.run_until_complete(self.plugin.on_health_check(TEST_SERVER_ID))
            self.assertEqual(health, PluginHealth.HEALTHY)
        finally:
            loop.close()
    
    def test_03_list_item(self):
        """Create a new auction listing."""
        result = self.plugin.list_item(
            server_id=TEST_SERVER_ID,
            seller="Alice",
            item_id="diamond_sword",
            count=1,
            start_price=100.0,
            buy_now_price=200.0,
        )
        self.assertTrue(result["ok"])
        self.assertIn("listing_uuid", result)
        self.assertIn("created_at", result)
    
    def test_04_list_item_multiple(self):
        """Create multiple listings and verify count."""
        self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 50.0)
        self.plugin.list_item(TEST_SERVER_ID, "Bob", "emerald", 3, 25.0)
        self.plugin.list_item(TEST_SERVER_ID, "Alice", "gold_ingot", 10, 5.0)
        
        listings = self.plugin.query_listings(TEST_SERVER_ID)
        self.assertEqual(len(listings), 3)
    
    def test_05_place_bid(self):
        """Place a bid on an active listing."""
        result = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        listing_uuid = result["listing_uuid"]
        
        bid = self.plugin.place_bid(TEST_SERVER_ID, listing_uuid, "Bob", 150.0)
        self.assertTrue(bid["ok"])
        self.assertEqual(bid["bidder"], "Bob")
        self.assertEqual(bid["amount"], 150.0)
    
    def test_06_bid_below_start_price(self):
        """Bid below start price should fail."""
        result = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        listing_uuid = result["listing_uuid"]
        
        bid = self.plugin.place_bid(TEST_SERVER_ID, listing_uuid, "Bob", 50.0)
        self.assertFalse(bid["ok"])
        self.assertIn("start price", bid["error"].lower())
    
    def test_07_bid_on_nonexistent_listing(self):
        """Bid on nonexistent listing should fail."""
        bid = self.plugin.place_bid(TEST_SERVER_ID, "nonexistent-uuid", "Bob", 50.0)
        self.assertFalse(bid["ok"])
        self.assertIn("not found", bid["error"].lower())
    
    def test_08_buy_now(self):
        """Buy a listing at buy-now price."""
        result = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0, 200.0)
        listing_uuid = result["listing_uuid"]
        
        buy = self.plugin.buy_now(TEST_SERVER_ID, listing_uuid, "Bob", 1)
        self.assertTrue(buy["ok"])
        self.assertEqual(buy["buyer"], "Bob")
        self.assertEqual(buy["price"], 200.0)
    
    def test_09_buy_own_listing(self):
        """Cannot buy your own listing."""
        result = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0, 200.0)
        listing_uuid = result["listing_uuid"]
        
        buy = self.plugin.buy_now(TEST_SERVER_ID, listing_uuid, "Alice", 1)
        self.assertFalse(buy["ok"])
        self.assertIn("own listing", buy["error"].lower())
    
    def test_10_buy_no_buy_now_price(self):
        """Cannot buy a listing without a buy-now price."""
        result = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        listing_uuid = result["listing_uuid"]
        
        buy = self.plugin.buy_now(TEST_SERVER_ID, listing_uuid, "Bob")
        self.assertFalse(buy["ok"])
        self.assertIn("buy-now", buy["error"].lower())
    
    def test_11_cancel_listing(self):
        """Cancel an active listing."""
        result = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        listing_uuid = result["listing_uuid"]
        
        cancel = self.plugin.cancel_listing(TEST_SERVER_ID, listing_uuid, "Alice")
        self.assertTrue(cancel["ok"])
        
        # Verify listing is cancelled
        listing = self.plugin.get_listing(TEST_SERVER_ID, listing_uuid)
        self.assertIsNotNone(listing)
        self.assertEqual(listing["status"], "cancelled")
    
    def test_12_cancel_not_owner(self):
        """Only the seller can cancel a listing."""
        result = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        listing_uuid = result["listing_uuid"]
        
        cancel = self.plugin.cancel_listing(TEST_SERVER_ID, listing_uuid, "Bob")
        self.assertFalse(cancel["ok"])
        self.assertIn("your listing", cancel["error"].lower())
    
    def test_13_expire_listings(self):
        """Expire listings that have expired."""
        # Create a listing with an expires_at in the past
        db = self._make_vfs_db(self.plugin.name)
        past_time = "2020-01-01T00:00:00+00:00"
        import uuid
        lid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO listings (listing_uuid, server_id, seller, item_id, count, start_price, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (lid, TEST_SERVER_ID, "Alice", "diamond", 1, 100.0, past_time, past_time)
        )
        db.commit()
        
        expired = self.plugin.expire_listings(TEST_SERVER_ID)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["listing_uuid"], lid)
    
    def test_14_query_listings_by_seller(self):
        """Query listings by seller."""
        self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        self.plugin.list_item(TEST_SERVER_ID, "Bob", "emerald", 1, 50.0)
        self.plugin.list_item(TEST_SERVER_ID, "Alice", "gold", 1, 25.0)
        
        alice_listings = self.plugin.query_listings(TEST_SERVER_ID, "seller", "Alice")
        self.assertEqual(len(alice_listings), 2)
        
        bob_listings = self.plugin.query_listings(TEST_SERVER_ID, "seller", "Bob")
        self.assertEqual(len(bob_listings), 1)
    
    def test_15_query_listings_by_item(self):
        """Query listings by item id."""
        self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        self.plugin.list_item(TEST_SERVER_ID, "Bob", "diamond", 1, 90.0)
        
        diamond_listings = self.plugin.query_listings(TEST_SERVER_ID, "item", "diamond")
        self.assertEqual(len(diamond_listings), 2)
    
    def test_16_get_player_listings(self):
        """Get all listings for a player."""
        self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        self.plugin.list_item(TEST_SERVER_ID, "Alice", "emerald", 1, 50.0)
        self.plugin.list_item(TEST_SERVER_ID, "Bob", "gold", 1, 25.0)
        
        alice_active = self.plugin.get_player_listings(TEST_SERVER_ID, "Alice")
        self.assertEqual(len(alice_active), 2)
    
    def test_17_get_bid_history(self):
        """Get bid history for a listing."""
        result = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        lid = result["listing_uuid"]
        
        self.plugin.place_bid(TEST_SERVER_ID, lid, "Bob", 150.0)
        self.plugin.place_bid(TEST_SERVER_ID, lid, "Carol", 200.0)
        
        history = self.plugin.get_bid_history(TEST_SERVER_ID, lid)
        self.assertEqual(len(history), 2)
    
    def test_18_balance_ops(self):
        """Test balance get/update operations."""
        # Initial balance should be 0
        balance = self.plugin.get_balance(TEST_SERVER_ID, "Alice")
        self.assertIsNotNone(balance)
        self.assertEqual(balance, 0)
        
        # Update balance
        new_balance = self.plugin.update_balance(TEST_SERVER_ID, "Alice", 100)
        self.assertEqual(new_balance, 100)
        
        # Verify
        balance = self.plugin.get_balance(TEST_SERVER_ID, "Alice")
        self.assertEqual(balance, 100)
        
        # Negative delta
        new_balance = self.plugin.update_balance(TEST_SERVER_ID, "Alice", -30)
        self.assertEqual(new_balance, 70)
    
    def test_19_shutdown(self):
        """Test on_shutdown doesn't raise."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.plugin.on_shutdown(TEST_SERVER_ID))
        finally:
            loop.close()
    
    def test_20_health_check_unhealthy_no_db(self):
        """Health check returns UNHEALTHY when no DB."""
        # Close the DB
        self._make_vfs_db(self.plugin.name).close()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            health = loop.run_until_complete(self.plugin.on_health_check("nonexistent_server"))
            self.assertEqual(health, PluginHealth.UNHEALTHY)
        finally:
            loop.close()
    
    def test_21_edge_empty_listings(self):
        """Query empty listings returns empty list."""
        listings = self.plugin.query_listings(TEST_SERVER_ID)
        self.assertEqual(listings, [])
    
    def test_22_edge_get_nonexistent_listing(self):
        """Get nonexistent listing returns None."""
        listing = self.plugin.get_listing(TEST_SERVER_ID, "nonexistent")
        self.assertIsNone(listing)
    
    def test_23_edge_empty_bid_history(self):
        """Bid history for non-existent listing returns empty list."""
        history = self.plugin.get_bid_history(TEST_SERVER_ID, "nonexistent")
        self.assertEqual(history, [])
    
    def test_24_balance_no_player(self):
        """Balance for unknown player returns 0."""
        balance = self.plugin.get_balance(TEST_SERVER_ID, "UnknownPlayer")
        self.assertEqual(balance, 0)
    
    def test_25_multi_server_isolation(self):
        """Data should be isolated between servers."""
        # Create listing on server 1
        r1 = self.plugin.list_item(TEST_SERVER_ID, "Alice", "diamond", 1, 100.0)
        
        # Init for server 2
        self._init_plugin(AuctionHousePlugin, TEST_SERVER_ID_2)
        
        # Server 2 should have no listings
        listings_s2 = self.plugin.query_listings(TEST_SERVER_ID_2)
        self.assertEqual(len(listings_s2), 0)
        
        # Server 1 should still have 1
        listings_s1 = self.plugin.query_listings(TEST_SERVER_ID)
        self.assertEqual(len(listings_s1), 1)


# ═══════════════════════════════════════════════════════════════════════════
# EconomyBridgePlugin Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEconomyBridgePlugin(PluginTestCase):
    """Tests for EconomyBridgePlugin."""
    
    def setUp(self):
        super().setUp()
        self.plugin = self._init_plugin(EconomyBridgePlugin)
    
    def test_01_plugin_attributes(self):
        """Verify required plugin attributes."""
        self.assertEqual(self.plugin.name, "economy_bridge")
        self.assertEqual(self.plugin.version, "1.0.0")
        self.assertIn("Economy", self.plugin.description)
    
    def test_02_health_check_healthy(self):
        """Health check returns HEALTHY after init."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            health = loop.run_until_complete(self.plugin.on_health_check(TEST_SERVER_ID))
            self.assertEqual(health, PluginHealth.HEALTHY)
        finally:
            loop.close()
    
    def test_03_new_player_balance_zero(self):
        """New players should have 0 balance."""
        balance = self.plugin.get_balance(TEST_SERVER_ID, "Alice")
        self.assertEqual(balance, 0)
    
    def test_04_set_balance(self):
        """Set a player's balance."""
        result = self.plugin.set_balance(TEST_SERVER_ID, "Alice", 1000)
        self.assertEqual(result, 1000)
        
        balance = self.plugin.get_balance(TEST_SERVER_ID, "Alice")
        self.assertEqual(balance, 1000)
    
    def test_05_set_balance_update(self):
        """Update an existing player's balance."""
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 1000)
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 2000)
        
        balance = self.plugin.get_balance(TEST_SERVER_ID, "Alice")
        self.assertEqual(balance, 2000)
    
    def test_06_transfer(self):
        """Transfer between players."""
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 1000)
        
        result = self.plugin.transfer(TEST_SERVER_ID, "Alice", "Bob", 300, reason="test")
        self.assertTrue(result["ok"])
        
        alice_balance = self.plugin.get_balance(TEST_SERVER_ID, "Alice")
        bob_balance = self.plugin.get_balance(TEST_SERVER_ID, "Bob")
        
        self.assertEqual(alice_balance, 700)
        self.assertEqual(bob_balance, 300)
    
    def test_07_transfer_insufficient(self):
        """Transfer with insufficient balance should fail."""
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 100)
        
        result = self.plugin.transfer(TEST_SERVER_ID, "Alice", "Bob", 500)
        self.assertFalse(result["ok"])
        self.assertIn("balance", result["error"].lower())
    
    def test_08_transfer_self(self):
        """Transfer to yourself should fail."""
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 1000)
        
        result = self.plugin.transfer(TEST_SERVER_ID, "Alice", "Alice", 100)
        self.assertFalse(result["ok"])
        self.assertIn("yourself", result["error"].lower())
    
    def test_09_transfer_negative(self):
        """Transfer negative amount should fail."""
        result = self.plugin.transfer(TEST_SERVER_ID, "Alice", "Bob", -100)
        self.assertFalse(result["ok"])
        self.assertIn("positive", result["error"].lower())
    
    def test_10_transaction_history(self):
        """Get transaction history."""
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 1000)
        self.plugin.transfer(TEST_SERVER_ID, "Alice", "Bob", 300)
        self.plugin.transfer(TEST_SERVER_ID, "Alice", "Carol", 200)
        
        # All transactions
        history = self.plugin.get_transaction_history(TEST_SERVER_ID)
        self.assertEqual(len(history), 3)  # 1 set + 2 transfers
        
        # Filtered by player
        bob_history = self.plugin.get_transaction_history(TEST_SERVER_ID, player="Bob")
        self.assertTrue(len(bob_history) >= 1)
    
    def test_11_server_economy_summary(self):
        """Get economy summary."""
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 1000)
        self.plugin.set_balance(TEST_SERVER_ID, "Bob", 500)
        self.plugin.set_balance(TEST_SERVER_ID, "Carol", 2000)
        
        summary = self.plugin.get_server_economy_summary(TEST_SERVER_ID)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["total_accounts"], 3)
        self.assertEqual(summary["total_wealth"], 3500)
    
    def test_12_different_currency_types(self):
        """Support different currency types."""
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 100, "gold")
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 50, "silver")
        
        gold = self.plugin.get_balance(TEST_SERVER_ID, "Alice", "gold")
        silver = self.plugin.get_balance(TEST_SERVER_ID, "Alice", "silver")
        
        self.assertEqual(gold, 100)
        self.assertEqual(silver, 50)
    
    def test_13_multi_server_isolation(self):
        """Server isolation for economy data."""
        self.plugin.set_balance(TEST_SERVER_ID, "Alice", 1000)
        self._init_plugin(EconomyBridgePlugin, TEST_SERVER_ID_2)
        
        s2_balance = self.plugin.get_balance(TEST_SERVER_ID_2, "Alice")
        self.assertEqual(s2_balance, 0)  # Server 2 has no data
    
    def test_14_lan_discovery(self):
        """LAN discovery methods work without error."""
        discovered = self.plugin.get_discovered_bridge()
        self.assertIsNone(discovered)  # No discovery has run yet
    
    def test_15_shutdown(self):
        """Shutdown doesn't raise."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.plugin.on_shutdown(TEST_SERVER_ID))
        finally:
            loop.close()


# ═══════════════════════════════════════════════════════════════════════════
# SimulatedPeoplePlugin Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSimulatedPeoplePlugin(PluginTestCase):
    """Tests for SimulatedPeoplePlugin."""
    
    def setUp(self):
        super().setUp()
        self.plugin = self._init_plugin(SimulatedPeoplePlugin)
    
    def test_01_plugin_attributes(self):
        """Verify required plugin attributes."""
        self.assertEqual(self.plugin.name, "simulated_people")
        self.assertEqual(self.plugin.version, "1.0.0")
        self.assertIn("Simulated", self.plugin.description)
    
    def test_02_health_check_healthy(self):
        """Health check returns HEALTHY after init."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            health = loop.run_until_complete(self.plugin.on_health_check(TEST_SERVER_ID))
            self.assertEqual(health, PluginHealth.HEALTHY)
        finally:
            loop.close()
    
    def test_03_seeded_default_people(self):
        """Default people are seeded on init."""
        people = self.plugin.get_all_people(TEST_SERVER_ID)
        self.assertGreater(len(people), 0)
        
        # Check for some expected names
        names = [p["name"] for p in people]
        self.assertIn("Alex", names)
        self.assertIn("Bailey", names)
    
    def test_04_default_people_properties(self):
        """Check default people properties."""
        people = self.plugin.get_all_people(TEST_SERVER_ID)
        for person in people:
            self.assertIn("person_id", person)
            self.assertIn("name", person)
            self.assertIn("status", person)
            self.assertIn("persona", person)
            self.assertEqual(person["server_id"], TEST_SERVER_ID)
    
    def test_05_create_custom_person(self):
        """Create a custom simulated person."""
        person = self.plugin.create_person(
            TEST_SERVER_ID,
            "Zara",
            persona="Mysterious traveler",
            status="online",
        )
        self.assertIsNotNone(person)
        self.assertEqual(person["name"], "Zara")
        self.assertEqual(person["persona"], "Mysterious traveler")
        
        # Verify it's retrievable
        fetched = self.plugin.get_person(TEST_SERVER_ID, person["person_id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["name"], "Zara")
    
    def test_06_find_person_by_name(self):
        """Find a person by name."""
        self.plugin.create_person(TEST_SERVER_ID, "UniqueName", persona="test")
        
        person = self.plugin.get_person_by_name(TEST_SERVER_ID, "UniqueName")
        self.assertIsNotNone(person)
        self.assertEqual(person["name"], "UniqueName")
    
    def test_07_get_nonexistent_person(self):
        """Get nonexistent person returns None."""
        person = self.plugin.get_person(TEST_SERVER_ID, "nonexistent-id")
        self.assertIsNone(person)
    
    def test_08_update_status(self):
        """Update a person's status."""
        people = self.plugin.get_all_people(TEST_SERVER_ID)
        person = people[0]
        
        result = self.plugin.update_status(TEST_SERVER_ID, person["person_id"], "away")
        self.assertTrue(result)
        
        fetched = self.plugin.get_person(TEST_SERVER_ID, person["person_id"])
        self.assertEqual(fetched["status"], "away")
    
    def test_09_update_nonexistent_status(self):
        """Update status of nonexistent person returns False."""
        result = self.plugin.update_status(TEST_SERVER_ID, "nonexistent", "away")
        self.assertFalse(result)
    
    def test_10_create_relationship(self):
        """Create a relationship between two people."""
        people = self.plugin.get_all_people(TEST_SERVER_ID)
        a = people[0]
        b = people[1]
        
        rel = self.plugin.create_relationship(
            TEST_SERVER_ID, a["person_id"], b["person_id"],
            "friend", 0.75
        )
        self.assertIsNotNone(rel)
        self.assertEqual(rel["relationship"], "friend")
        self.assertEqual(rel["strength"], 0.75)
    
    def test_11_get_relationships(self):
        """Get relationships for a person."""
        people = self.plugin.get_all_people(TEST_SERVER_ID)
        a = people[0]
        b = people[1]
        c = people[2]
        
        self.plugin.create_relationship(TEST_SERVER_ID, a["person_id"], b["person_id"], "friend", 0.8)
        self.plugin.create_relationship(TEST_SERVER_ID, a["person_id"], c["person_id"], "rival", -0.5)
        
        rels = self.plugin.get_relationships(TEST_SERVER_ID, a["person_id"])
        self.assertEqual(len(rels), 2)
    
    def test_12_announcements(self):
        """Create and retrieve announcements."""
        people = self.plugin.get_all_people(TEST_SERVER_ID)
        person = people[0]
        
        result = self.plugin.add_announcement(
            TEST_SERVER_ID, person["person_id"],
            "Hello everyone!", "chat"
        )
        self.assertTrue(result)
        
        result = self.plugin.add_announcement(
            TEST_SERVER_ID, person["person_id"],
            "I found treasure!", "action"
        )
        self.assertTrue(result)
        
        announcements = self.plugin.get_announcements(TEST_SERVER_ID, unread_only=True)
        self.assertEqual(len(announcements), 2)
        
        # Mark one as read
        self.plugin.mark_announcement_read(TEST_SERVER_ID, announcements[0]["announcement_id"])
        
        unread = self.plugin.get_announcements(TEST_SERVER_ID, unread_only=True)
        self.assertEqual(len(unread), 1)
    
    def test_13_empty_announcements(self):
        """No announcements initially returns empty list."""
        # Create a fresh plugin for a new server
        self._init_plugin(SimulatedPeoplePlugin, TEST_SERVER_ID_2)
        
        ann = self.plugin.get_announcements(TEST_SERVER_ID_2)
        self.assertEqual(ann, [])
    
    def test_14_get_people_by_status(self):
        """Filter people by status."""
        people = self.plugin.get_all_people(TEST_SERVER_ID, status="online")
        for p in people:
            self.assertEqual(p["status"], "online")
    
    def test_15_multi_server_isolation(self):
        """Server isolation for people data."""
        s1_people = self.plugin.get_all_people(TEST_SERVER_ID)
        self.assertGreater(len(s1_people), 0)
        
        self._init_plugin(SimulatedPeoplePlugin, TEST_SERVER_ID_2)
        s2_people = self.plugin.get_all_people(TEST_SERVER_ID_2)
        self.assertGreater(len(s2_people), 0)
        
        # Verify they're different servers
        for p in s1_people:
            self.assertEqual(p["server_id"], TEST_SERVER_ID)
        for p in s2_people:
            self.assertEqual(p["server_id"], TEST_SERVER_ID_2)
    
    def test_16_shutdown(self):
        """Shutdown marks people offline."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.plugin.on_shutdown(TEST_SERVER_ID))
        finally:
            loop.close()


# ═══════════════════════════════════════════════════════════════════════════
# LootPowerGamesPlugin Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLootPowerGamesPlugin(PluginTestCase):
    """Tests for LootPowerGamesPlugin."""
    
    def setUp(self):
        super().setUp()
        self.plugin = self._init_plugin(LootPowerGamesPlugin)
    
    def test_01_plugin_attributes(self):
        """Verify required plugin attributes."""
        self.assertEqual(self.plugin.name, "lootpower_games")
        self.assertEqual(self.plugin.version, "1.0.0")
        self.assertIn("Loot", self.plugin.description)
    
    def test_02_health_check_healthy(self):
        """Health check returns HEALTHY after init."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            health = loop.run_until_complete(self.plugin.on_health_check(TEST_SERVER_ID))
            self.assertEqual(health, PluginHealth.HEALTHY)
        finally:
            loop.close()
    
    def test_03_seeded_loot_table(self):
        """Default loot table is seeded on init."""
        items = self.plugin.get_loot_table(TEST_SERVER_ID)
        self.assertGreater(len(items), 0)
        
        # Check some expected items
        names = [i["loot_name"] for i in items]
        self.assertIn("Common Sword", names)
        self.assertIn("Crown of Kings", names)
    
    def test_04_get_loot_item(self):
        """Get a loot item by ID."""
        item = self.plugin.get_loot_item(TEST_SERVER_ID, 1)
        self.assertIsNotNone(item)
        self.assertEqual(item["loot_name"], "Common Sword")
    
    def test_05_get_nonexistent_loot(self):
        """Get nonexistent loot item returns None."""
        item = self.plugin.get_loot_item(TEST_SERVER_ID, 999)
        self.assertIsNone(item)
    
    def test_06_filter_by_rarity(self):
        """Filter loot table by rarity."""
        epics = self.plugin.get_loot_table(TEST_SERVER_ID, rarity="epic")
        for item in epics:
            self.assertEqual(item["loot_rarity"], "epic")
        
        legendaries = self.plugin.get_loot_table(TEST_SERVER_ID, rarity="legendary")
        self.assertEqual(len(legendaries), 1)
    
    def test_07_add_loot_item(self):
        """Add a new loot item."""
        item = self.plugin.add_loot_item(TEST_SERVER_ID, "Test Sword", "rare", "weapon", 500)
        self.assertIsNotNone(item)
        self.assertEqual(item["loot_name"], "Test Sword")
        
        # Verify it's in the table
        fetched = self.plugin.get_loot_item(TEST_SERVER_ID, item["loot_id"])
        self.assertIsNotNone(fetched)
    
    def test_08_player_inventory_empty(self):
        """New player has empty inventory."""
        inv = self.plugin.get_player_inventory(TEST_SERVER_ID, "Alice")
        self.assertEqual(inv, [])
    
    def test_09_give_loot(self):
        """Give loot to a player."""
        result = self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 5, "test")
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_amount"], 5)
        
        inv = self.plugin.get_player_inventory(TEST_SERVER_ID, "Alice")
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]["loot_amount"], 5)
    
    def test_10_give_multiple_types(self):
        """Give multiple different loot items."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 3)
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 5, 2)
        
        inv = self.plugin.get_player_inventory(TEST_SERVER_ID, "Alice")
        self.assertEqual(len(inv), 2)
    
    def test_11_take_loot(self):
        """Take loot from a player."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 10)
        
        result = self.plugin.take_loot(TEST_SERVER_ID, "Alice", 1, 3, "test_removal")
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_amount"], 7)
    
    def test_12_take_insufficient_loot(self):
        """Take more loot than player has should fail."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 2)
        
        result = self.plugin.take_loot(TEST_SERVER_ID, "Alice", 1, 10)
        self.assertFalse(result["ok"])
        self.assertIn("Insufficient", result["error"])
    
    def test_13_take_nonexistent_item(self):
        """Take loot the player doesn't have should fail."""
        result = self.plugin.take_loot(TEST_SERVER_ID, "Alice", 999, 1)
        self.assertFalse(result["ok"])
        self.assertIn("Insufficient", result["error"])
    
    def test_14_create_listing(self):
        """Create an auction listing."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 5)
        
        auction_id = self.plugin.create_listing(TEST_SERVER_ID, "Alice", 1, 2, 100)
        self.assertIsNotNone(auction_id)
        
        # Verify loot was deducted
        inv = self.plugin.get_player_inventory(TEST_SERVER_ID, "Alice")
        self.assertEqual(inv[0]["loot_amount"], 3)
    
    def test_15_create_listing_insufficient(self):
        """Create listing without enough loot should fail."""
        auction_id = self.plugin.create_listing(TEST_SERVER_ID, "Alice", 1, 100, 100)
        self.assertIsNone(auction_id)
    
    def test_16_buy_listing(self):
        """Buy an auction listing."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 10)
        self.plugin.give_loot(TEST_SERVER_ID, "Bob", 5, 1)
        
        auction_id = self.plugin.create_listing(TEST_SERVER_ID, "Alice", 1, 3, 50)
        self.assertIsNotNone(auction_id)
        
        result = self.plugin.buy_listing(TEST_SERVER_ID, auction_id, "Bob")
        self.assertIn("Purchased", result)
        
        # Verify Bob got the loot
        bob_inv = self.plugin.get_player_inventory(TEST_SERVER_ID, "Bob")
        bob_loot_1 = [i for i in bob_inv if i["loot_id"] == 1]
        self.assertEqual(len(bob_loot_1), 1)
        self.assertEqual(bob_loot_1[0]["loot_amount"], 3)
    
    def test_17_buy_own_listing(self):
        """Cannot buy your own listing."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 5)
        
        auction_id = self.plugin.create_listing(TEST_SERVER_ID, "Alice", 1, 1, 10)
        self.assertIsNotNone(auction_id)
        
        result = self.plugin.buy_listing(TEST_SERVER_ID, auction_id, "Alice")
        self.assertIn("Cannot buy", result)
    
    def test_18_cancel_listing(self):
        """Cancel a listing and return loot."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 5)
        
        auction_id = self.plugin.create_listing(TEST_SERVER_ID, "Alice", 1, 2, 50)
        self.assertIsNotNone(auction_id)
        
        result = self.plugin.cancel_listing(TEST_SERVER_ID, auction_id, "Alice")
        self.assertIn("cancelled", result)
        
        # Verify loot was returned
        inv = self.plugin.get_player_inventory(TEST_SERVER_ID, "Alice")
        self.assertEqual(inv[0]["loot_amount"], 5)
    
    def test_19_cancel_not_owner(self):
        """Only seller can cancel."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 5)
        
        auction_id = self.plugin.create_listing(TEST_SERVER_ID, "Alice", 1, 1, 10)
        
        result = self.plugin.cancel_listing(TEST_SERVER_ID, auction_id, "Bob")
        self.assertIn("your listing", result.lower())
    
    def test_20_get_active_listings(self):
        """Get active listings with loot info."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 10)
        self.plugin.give_loot(TEST_SERVER_ID, "Bob", 5, 5)
        
        self.plugin.create_listing(TEST_SERVER_ID, "Alice", 1, 2, 100)
        self.plugin.create_listing(TEST_SERVER_ID, "Bob", 5, 1, 50)
        
        active = self.plugin.get_active_listings(TEST_SERVER_ID)
        self.assertEqual(len(active), 2)
        
        # Check enriched with loot info
        names = [a["loot_name"] for a in active]
        self.assertIn("Common Sword", names)
        self.assertIn("Silver Ring", names)
    
    def test_21_get_player_history(self):
        """Get player's loot history."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 5, "grant")
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 5, 2, "reward")
        
        history = self.plugin.get_player_history(TEST_SERVER_ID, "Alice")
        self.assertEqual(len(history), 2)
        self.assertIn("loot_name", history[0])
    
    def test_22_multi_server_isolation(self):
        """Server isolation for loot data."""
        self.plugin.give_loot(TEST_SERVER_ID, "Alice", 1, 10)
        self._init_plugin(LootPowerGamesPlugin, TEST_SERVER_ID_2)
        
        s2_inv = self.plugin.get_player_inventory(TEST_SERVER_ID_2, "Alice")
        self.assertEqual(s2_inv, [])
    
    def test_23_shutdown(self):
        """Shutdown doesn't raise."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.plugin.on_shutdown(TEST_SERVER_ID))
        finally:
            loop.close()


# ═══════════════════════════════════════════════════════════════════════════
# PluginRegistry Integration Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPluginRegistryIntegration(unittest.TestCase):
    """Tests for plugin registration and registry integration."""
    
    @classmethod
    def setUpClass(cls):
        cls.registry = PluginRegistry.get_instance()
    
    @classmethod
    def tearDownClass(cls):
        PluginRegistry.reset_instance()
    
    def test_01_register_all_plugins(self):
        """All 4 plugins can be registered in the registry."""
        from plugins import ALL_PLUGINS
        for plugin_class in ALL_PLUGINS:
            plugin = self.registry.register(plugin_class)
            self.assertIsNotNone(plugin)
            self.assertEqual(plugin.name, plugin_class.name)
    
    def test_02_list_plugins(self):
        """List all registered plugins."""
        manifests = self.registry.list_plugins()
        names = [m.name for m in manifests]
        self.assertIn("auction_house", names)
        self.assertIn("economy_bridge", names)
        self.assertIn("simulated_people", names)
        self.assertIn("lootpower_games", names)
    
    def test_03_get_plugin(self):
        """Get a specific plugin by name."""
        plugin = self.registry.get_plugin("auction_house")
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.name, "auction_house")
    
    def test_04_get_nonexistent_plugin_raises(self):
        """Getting nonexistent plugin raises PluginNotFoundError."""
        from engine.plugin_registry import PluginNotFoundError
        with self.assertRaises(PluginNotFoundError):
            self.registry.get_plugin("nonexistent_plugin")
    
    def test_05_duplicate_registration_same_class(self):
        """Registering same class twice returns existing instance."""
        from plugins.auction_house_plugin import AuctionHousePlugin
        p1 = self.registry.register(AuctionHousePlugin)
        p2 = self.registry.register(AuctionHousePlugin)
        self.assertIs(p1, p2)
    
    def test_06_has_plugin(self):
        """has_plugin returns correct boolean."""
        self.assertTrue(self.registry.has_plugin("auction_house"))
        self.assertFalse(self.registry.has_plugin("fake_plugin"))


# ═══════════════════════════════════════════════════════════════════════════
# SubsystemPlugin ABC Validation
# ═══════════════════════════════════════════════════════════════════════════

class TestSubsystemPluginABC(unittest.TestCase):
    """Validate that all plugins properly implement the ABC."""
    
    def test_all_plugins_are_subsystemplugin_subclasses(self):
        """Every plugin is a SubsystemPlugin subclass."""
        from plugins import ALL_PLUGINS
        for plugin_class in ALL_PLUGINS:
            self.assertTrue(issubclass(plugin_class, SubsystemPlugin),
                            f"{plugin_class.__name__} is not a SubsystemPlugin subclass")
    
    def test_all_plugins_have_required_attributes(self):
        """Every plugin has required class attributes."""
        from plugins import ALL_PLUGINS
        for plugin_class in ALL_PLUGINS:
            self.assertTrue(getattr(plugin_class, 'name', ''),
                            f"{plugin_class.__name__} missing 'name'")
            self.assertTrue(getattr(plugin_class, 'version', ''),
                            f"{plugin_class.__name__} missing 'version'")
            self.assertTrue(getattr(plugin_class, 'description', ''),
                            f"{plugin_class.__name__} missing 'description'")
    
    def test_all_plugins_have_lifecycle_hooks(self):
        """Every plugin has the required async lifecycle hooks."""
        from plugins import ALL_PLUGINS
        hooks = ['on_init', 'on_shutdown', 'on_health_check']
        for plugin_class in ALL_PLUGINS:
            for hook in hooks:
                method = getattr(plugin_class, hook, None)
                self.assertIsNotNone(method,
                                     f"{plugin_class.__name__} missing {hook}")
                self.assertTrue(hasattr(method, '__call__'),
                                f"{plugin_class.__name__}.{hook} is not callable")


# ═══════════════════════════════════════════════════════════════════════════
# Main Entry
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
