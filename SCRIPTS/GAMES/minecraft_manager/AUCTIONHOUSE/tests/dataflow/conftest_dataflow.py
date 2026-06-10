"""
conftest_dataflow.py — Enhanced test infrastructure for data flow tests.

Extends the base conftest.py with:
  - DataFlowTrace integration (auto-setup/teardown per test)
  - DatabaseProbe integration
  - StateProbe integration (when extensions are loaded)
  - Mock AI engine integration
  - Comprehensive test case reporting
  - Chaos mode for random edge case injection

Usage:
    from tests.dataflow.conftest_dataflow import DataFlowTestCase
    
    class TestMyFlow(DataFlowTestCase):
        def test_something(self):
            with self.trace.path("core.listing"):
                result = list_item(...)
            self.verify_paths_taken(["core.listing", "db.insert"])
"""

import sys
import os
import json
import time
import threading
import random
from typing import Optional
from unittest.mock import patch, MagicMock

# ── Path setup (same as conftest.py) ───────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))          # .../AUCTIONHOUSE/tests/dataflow
_TESTS_DIR = os.path.dirname(_HERE)                          # .../AUCTIONHOUSE/tests
_AH_DIR = os.path.dirname(_TESTS_DIR)                        # .../AUCTIONHOUSE
_MANAGER_DIR = os.path.dirname(_AH_DIR)                     # .../minecraft_manager
for p in [_AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from unittest import TestCase

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_config import get_config

log = get_logger()

# Import shared test infrastructure from conftest
from tests.conftest import (
    unique_item_id, unique_uuid, mock_rcon, reset_rcon, RCON_LOG,
    AHTestCase, patch_eco_bridge
)

# Import data flow probes
from tests.dataflow.probes import (
    DataFlowTrace, DatabaseProbe, StateProbe, MockDeepSeekAI,
    get_trace, reset_trace,
)


# ══════════════════════════════════════════════════════════════════════
# Chaos Engine — injects random edge cases during tests
# ══════════════════════════════════════════════════════════════════════

class ChaosEngine:
    """Randomly injects edge cases to stress test error handling.

    When enabled, some operations get fuzzed input, concurrent access,
    or delayed responses to simulate real-world conditions.
    """

    def __init__(self, enabled: bool = False, chaos_level: float = 0.0):
        """
        Args:
            enabled: Whether chaos mode is active
            chaos_level: 0.0-1.0, probability of chaos per operation
        """
        self.enabled = enabled
        self.chaos_level = chaos_level
        self._injected = 0

    def maybe_fuzz_string(self, value: str) -> str:
        """Randomly fuzz a string input with edge cases."""
        if not self.enabled or random.random() > self.chaos_level:
            return value
        options = [
            value + "\x00nullbyte",
            value.upper(),
            value.lower(),
            value + " " * 100,
            value + "\n\t",
            value.replace("a", "а"),  # Cyrillic 'а'
        ]
        fuzzed = random.choice(options)
        self._injected += 1
        return fuzzed

    def maybe_delay(self, max_ms: int = 50):
        """Randomly inject a small delay to surface race conditions."""
        if self.enabled and random.random() < self.chaos_level * 0.5:
            delay = random.uniform(0.001, max_ms / 1000.0)
            time.sleep(delay)
            self._injected += 1

    def maybe_raise(self, exception_class=Exception, probability: float = 0.01):
        """Randomly raise an exception to test error handling."""
        if self.enabled and random.random() < probability:
            self._injected += 1
            raise exception_class("Chaos engine injected failure")

    @property
    def injected_count(self) -> int:
        return self._injected


# ══════════════════════════════════════════════════════════════════════
# Enhanced Test Case Base
# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════

class DataFlowTestCase(TestCase):
    """Enhanced test case with data flow tracing and probes.

    Features:
      - Auto-creates and manages DataFlowTrace per test
      - Auto-creates and manages DatabaseProbe per test
      - Auto-creates StateProbe (call attach_state_probe() to activate)
      - Helper methods for common assertions
      - Optional chaos mode for stress testing
    """

    # ── Class-level config ───────────────────────────────────────
    trace_enabled: bool = True
    db_probe_enabled: bool = True
    chaos_enabled: bool = False
    chaos_level: float = 0.0

    # ── SetUp / TearDown ─────────────────────────────────────────

    @classmethod
    def setUpClass(cls):
        """One-time: ensure DB schema."""
        from AUCTIONHOUSE.ah_database import initialize_database
        initialize_database()

    def setUp(self):
        """Per-test setup: reset probes, wipe test data, apply config."""
        # Reset the global trace
        reset_trace()
        self.trace = get_trace()
        if not self.trace_enabled:
            self.trace._enabled = False

        # Create database probe
        self.db_probe = DatabaseProbe(trace=self.trace)
        if self.db_probe_enabled:
            self.db_probe.attach()

        # Create state probe (not attached by default - extensions may not be loaded)
        self.state_probe = StateProbe(trace=self.trace)

        # Create mock AI
        self.mock_ai = MockDeepSeekAI(trace=self.trace)

        # Chaos engine
        self.chaos = ChaosEngine(self.chaos_enabled, self.chaos_level)

        # Clean test data
        self._wipe_test_data()
        reset_rcon()

        # Apply test config overrides
        self._apply_test_config()

    def tearDown(self):
        """Per-test teardown: detach probes."""
        if self.db_probe_enabled:
            try:
                self.db_probe.detach()
            except Exception:
                pass
        try:
            self.state_probe.detach()
        except Exception:
            pass

    def _wipe_test_data(self):
        """Clean all AH tables for test isolation."""
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        # Disable FK checks for clean wipe
        db.execute("PRAGMA foreign_keys = OFF")
        all_tables = [
            "transaction_history", "auction_listings", "player_balances",
            "ai_notes", "price_history", "market_events",
            "simulated_inventory", "ah_banned_players",
        ]
        for table in all_tables:
            try:
                db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        # Also clean extension tables
        ext_tables = [
            "ext_sp_profiles", "ext_sp_finances", "ext_sp_needs",
            "ext_sp_memory", "ext_sp_health", "ext_sp_life_events",
            "ext_sp_world_events", "ext_sp_world_areas",
            "ext_sp_persona_skills", "ext_sp_persona_location",
            "ext_sp_claims", "ext_sp_claims_buildings",
            "ext_sp_factions", "ext_sp_ecosystem",
            "ext_sp_ecosystem_resources",
            "ext_sp_crafting", "ext_sp_crafting_recipes",
            "ext_sp_behavior_memory", "ext_sp_behavior_event_log",
            "ext_sp_persona_events", "ext_sp_item_cache",
        ]
        for table in ext_tables:
            try:
                db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        db.execute("PRAGMA foreign_keys = ON")

    def _apply_test_config(self):
        """Apply safe config values for testing."""
        cfg = get_config()
        cfg.max_listings_per_player = 5
        cfg.min_bid_increment_pct = 10
        cfg.listing_fee_emeralds = 0.5
        cfg.auction_duration_default_hours = 48
        cfg.auction_duration_max_hours = 168
        cfg.sim_price_min = 0.01
        cfg.sim_price_max = 500.0
        cfg.stale_hours_threshold = 24

    # ── State probe helper ───────────────────────────────────────

    def attach_state_probe(self):
        """Attach the state registry probe (requires extensions loaded)."""
        try:
            self.state_probe.attach()
        except RuntimeError:
            self.skipTest("State registry not available (extensions not loaded)")

    # ── Mock patch helpers ───────────────────────────────────────

    def patch_economy_bridge(self):
        """Return a context manager that disables the economy bridge."""
        return patch("AUCTIONHOUSE.ah_core._get_eco_bridge",
                      return_value=None)

    def patch_eco_bridge_with_balance(self, balances: dict[str, float]):
        """Return a context manager with a mock bridge that has balances.

        Args:
            balances: Dict of player_name -> balance
        """
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True

        def mock_get_balance(player: str):
            return balances.get(player)

        def mock_deduct(player: str, amount: int, reason: str = "",
                        note: str = None) -> bool:
            if player in balances:
                balances[player] -= amount
                return True
            return False

        def mock_credit(player: str, amount: int, reason: str = "",
                        note: str = None) -> bool:
            balances[player] = balances.get(player, 0) + amount
            return True

        mock_bridge.get_balance = mock_get_balance
        mock_bridge.deduct = mock_deduct
        mock_bridge.credit = mock_credit

        return patch("AUCTIONHOUSE.ah_core._get_eco_bridge",
                      return_value=mock_bridge)

    # ── Verification helpers ─────────────────────────────────────

    def verify_paths_taken(self, expected_paths: list[str],
                           description: str = ""):
        """Assert that all expected data flow paths were taken."""
        self.trace.verify_path_taken(expected_paths, description)

    def verify_paths_not_taken(self, forbidden_paths: list[str],
                                description: str = ""):
        """Assert that forbidden paths were NOT taken."""
        self.trace.verify_path_not_taken(forbidden_paths, description)

    def verify_path_order(self, expected_sequence: list[str],
                           description: str = ""):
        """Assert that paths were taken in a specific order."""
        self.trace.verify_path_order(expected_sequence, description)

    def assert_node_count(self, path: str, expected: int):
        """Assert a specific path was taken exactly N times."""
        actual = self.trace.get_step_count(path)
        self.assertEqual(
            actual, expected,
            f"Path '{path}': expected {expected} occurrences, got {actual}"
        )

    def assert_db_state(self, table: str, expected_rows: int,
                         description: str = ""):
        """Assert a table has the expected row count."""
        self.db_probe.assert_table_state(table, expected_rows, description)

    def assert_atomic_update(self, listing_uuid: str):
        """Assert that an atomic UPDATE affected exactly 1 row."""
        self.db_probe.assert_atomic_update_succeeded(listing_uuid)

    def assert_no_db_violations(self):
        """Assert no FK violations or duplicate UUIDs in the database."""
        fk_violations = self.db_probe.verify_fk_integrity()
        dup_violations = self.db_probe.verify_no_duplicate_uuids()
        all_violations = fk_violations + dup_violations
        if all_violations:
            self.fail("Database integrity violations:\n  "
                      + "\n  ".join(all_violations))

    def assert_tx_count(self, count: int):
        """Assert the transaction_history table has exactly N rows."""
        self.assert_db_state("transaction_history", count)

    def assert_listing_count(self, count: int):
        """Assert the auction_listings table has exactly N rows."""
        self.assert_db_state("auction_listings", count)

    # ── Utilities ────────────────────────────────────────────────

    def unique_item(self, base: str = "minecraft:dirt") -> str:
        return unique_item_id(base)

    def unique_uuid(self) -> str:
        return unique_uuid()

    def print_trace_summary(self):
        """Print data flow trace summary to stdout."""
        print("\n" + self.trace.summary())
        print("\n" + self.db_probe.summary())

    def log_result(self, test_name: str, passed: bool,
                   details: str = ""):
        """Log a test result for the orchestrator report."""
        log.info("dataflow_test",
                 f"{'PASS' if passed else 'FAIL'}: {test_name}",
                 {"details": details})
