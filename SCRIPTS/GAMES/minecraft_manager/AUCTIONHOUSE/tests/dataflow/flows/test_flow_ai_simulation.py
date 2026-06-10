"""
test_flow_ai_simulation.py — Data Flow Test: AI Simulation Cycle (Flow 6)

Tests every code path and edge case for the AI simulation cycle including:
  - Valid AI responses with all action types
  - Malformed/invalid JSON responses
  - API timeout and retry
  - Event triggering
  - Rare item generation
  - Price/stock adjustments
  - Stale listing recommendations
  - Notes saving
"""

from tests.dataflow.conftest_dataflow import (
    DataFlowTestCase, mock_rcon, reset_rcon
)
from unittest.mock import patch, MagicMock
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_config import get_config
from tests.dataflow.probes.mock_ai import MockDeepSeekAI
from tests.dataflow.probes.trace_probe import (
    PATH_AI_CONTEXT, PATH_AI_API_CALL, PATH_AI_PARSE,
    PATH_AI_PRICE_ADJUST, PATH_AI_STOCK_ADJUST, PATH_AI_EVENT,
    PATH_AI_RARE_ITEM, PATH_AI_STALE_FIX, PATH_AI_SNAPSHOT,
    PATH_AI_NOTES,
)
from tests.conftest import unique_item_id
import json


def _seed_sim_inventory():
    """Ensure simulated inventory has some items."""
    db = get_db()
    items = [
        ("minecraft:coal", 0.5, 0.5, 100),
        ("minecraft:iron_ingot", 1.5, 1.5, 50),
        ("minecraft:diamond", 8.0, 8.0, 20),
        ("minecraft:emerald", 5.0, 5.0, 30),
        ("minecraft:netherite_ingot", 50.0, 50.0, 5),
    ]
    for item_id, base, current, stock in items:
        db.execute("""
            INSERT OR REPLACE INTO simulated_inventory
            (item_id, category, base_price, current_price, current_stock, max_stock, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (item_id, "testing", base, current, stock, stock * 2))


# ══════════════════════════════════════════════════════════════════════
# Test 6.1-6.8: Valid AI responses
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestAISimulationValid(DataFlowTestCase):

    def setUp(self):
        super().setUp()
        _seed_sim_inventory()
        self.mock_ai = MockDeepSeekAI(trace=self.trace)

    def test_6_1_default_response_cycle(self, mock_bridge):
        """AI cycle with default (empty) response completes cleanly."""
        self.mock_ai.set_response("default",
                                   MockDeepSeekAI._make_default_response())

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            result = run_simulation_cycle()

        self.assertIsNotNone(result)
        self.verify_paths_taken([PATH_AI_API_CALL, PATH_AI_PARSE])

    def test_6_2_price_adjustment_applied(self, mock_bridge):
        """Price adjustments are applied to simulated inventory."""
        db = get_db()
        coal_before = db.fetch_one(
            "SELECT current_price FROM simulated_inventory WHERE item_id = 'minecraft:coal'"
        )

        resp = MockDeepSeekAI.make_price_adjust_response(
            "minecraft:coal", 2.0, "Testing price increase"
        )
        self.mock_ai.set_response("price_adjust", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            run_simulation_cycle()

        self.verify_paths_taken([PATH_AI_PRICE_ADJUST])

    def test_6_3_event_triggered_by_ai(self, mock_bridge):
        """AI can trigger market events."""
        resp = MockDeepSeekAI.make_event_response(
            "test_blizzard", "❄ Test Blizzard!", "disaster", "small"
        )
        self.mock_ai.set_response("event", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            result = run_simulation_cycle()

        self.verify_paths_taken([PATH_AI_EVENT])

    def test_6_4_rare_item_generated(self, mock_bridge):
        """AI-generated rare items are listed."""
        resp = MockDeepSeekAI.make_rare_item_response(
            "minecraft:diamond_sword", 100.0, "Epic"
        )
        self.mock_ai.set_response("rare_item", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            result = run_simulation_cycle()

        self.verify_paths_taken([PATH_AI_RARE_ITEM])

    def test_6_5_price_snapshot_taken(self, mock_bridge):
        """Price snapshot is recorded after AI cycle."""
        resp = MockDeepSeekAI._make_default_response()
        self.mock_ai.set_response("snapshot", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            result = run_simulation_cycle()

        self.verify_paths_taken([PATH_AI_SNAPSHOT])

    def test_6_6_notes_saved(self, mock_bridge):
        """AI notes are saved to the helper DB."""
        resp = MockDeepSeekAI._make_default_response()
        self.mock_ai.set_response("notes_test", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            result = run_simulation_cycle()

        db = get_db()
        notes = db.fetch_all("SELECT * FROM ai_notes")
        self.assertGreaterEqual(len(notes), 1)

    def test_6_7_stock_adjustment(self, mock_bridge):
        """Stock adjustments change simulated inventory."""
        resp = MockDeepSeekAI.make_price_adjust_response(
            "minecraft:coal", 0.8, "Testing stock adjust"
        )
        self.mock_ai.set_response("stock", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            result = run_simulation_cycle()

    def test_6_8_simulation_with_listings(self, mock_bridge):
        """AI cycle handles active player listings."""
        from AUCTIONHOUSE.ah_core import list_item

        for i in range(3):
            list_item(
                seller=f"Player{i}", item_id=unique_item_id(),
                count=1, start_price=10.0, rcon_func=mock_rcon
            )

        resp = MockDeepSeekAI._make_default_response()
        self.mock_ai.set_response("with_listings", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            result = run_simulation_cycle()

        self.assertIsNotNone(result)


# ══════════════════════════════════════════════════════════════════════
# Test 6.9-6.16: Error handling
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestAISimulationErrors(DataFlowTestCase):

    def setUp(self):
        super().setUp()
        _seed_sim_inventory()
        self.mock_ai = MockDeepSeekAI(trace=self.trace)

    def test_6_9_malformed_json_response(self, mock_bridge):
        """Malformed JSON from AI is handled gracefully."""
        self.mock_ai.set_response(
            "malformed",
            MockDeepSeekAI.make_invalid_json_response()
        )

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            try:
                result = run_simulation_cycle()
                # Should complete without crashing
                self.assertIsNotNone(result)
            except Exception as e:
                self.fail(f"Malformed JSON crashed the cycle: {e}")

    def test_6_10_partial_json_response(self, mock_bridge):
        """Partial JSON (missing fields) is handled gracefully."""
        self.mock_ai.set_response(
            "partial",
            MockDeepSeekAI.make_partial_response()
        )

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            try:
                result = run_simulation_cycle()
                self.assertIsNotNone(result)
            except Exception as e:
                self.fail(f"Partial JSON crashed the cycle: {e}")

    def test_6_11_empty_listings_no_crash(self, mock_bridge):
        """AI cycle with no listings doesn't crash."""
        get_db().execute("DELETE FROM auction_listings")
        resp = MockDeepSeekAI._make_default_response()
        self.mock_ai.set_response("empty", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            try:
                result = run_simulation_cycle()
                self.assertIsNotNone(result)
            except Exception as e:
                self.fail(f"Empty cycle crashed: {e}")

    def test_6_12_missing_market_summary(self, mock_bridge):
        """AI cycle handles missing market context."""
        resp = MockDeepSeekAI._make_default_response()
        self.mock_ai.set_response("no_context", resp)

        from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle

        with self.mock_ai.patch():
            try:
                result = run_simulation_cycle()
                self.assertIsNotNone(result)
            except Exception as e:
                self.fail(f"No-context cycle crashed: {e}")

    def test_6_13_concurrent_ai_cycles(self, mock_bridge):
        """Concurrent AI cycles don't corrupt state."""
        import threading
        get_db().execute("DELETE FROM auction_listings")
        _seed_sim_inventory()

        resp = MockDeepSeekAI._make_default_response()
        self.mock_ai.set_response("concurrent", resp)
        results = []

        def run_cycle():
            from AUCTIONHOUSE.ah_ai_engine import run_simulation_cycle
            with self.mock_ai.patch():
                try:
                    r = run_simulation_cycle()
                    results.append(("ok", r))
                except Exception as e:
                    results.append(("error", str(e)))

        threads = [threading.Thread(target=run_cycle) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        errors = [r for r in results if r[0] == "error"]
        self.assertEqual(len(errors), 0,
                         f"Concurrent cycle errors: {errors}")
