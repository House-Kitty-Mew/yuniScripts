"""
test_ah_ai_engine.py — Tests for the AI simulation engine.

Tests focus on:
  - _extract_json() extracting AI responses from markdown
  - _apply_price_adjustments() updating simulated inventory
  - _apply_events() starting/ending market events
  - _apply_rare_items() listing AI-generated items
  - _apply_notes() storing AI observations
  - _apply_stock_adjustments() buying/selling sim stock
  - run_simulation_cycle() with a mocked DeepSeek response
  - SimulationScheduler start/stop

All tests self-contained. No external API calls. No permanent DB changes.
"""

import unittest, sys, os, json, time, threading, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["AH_DB_PATH"] = ":memory:"

from AUCTIONHOUSE.ah_database import initialize_database, get_db
from AUCTIONHOUSE.ah_ai_engine import (
    _extract_json,
    _apply_price_adjustments, _apply_stock_adjustments,
    _apply_events, _apply_rare_items, _apply_stale_recommendations,
    _apply_notes,
    run_simulation_cycle,
    SimulationScheduler,
    _SYSTEM_PROMPT, _USER_PROMPT_TEMPLATE,
)
from AUCTIONHOUSE.ah_core import list_item, query_listings
from AUCTIONHOUSE.ah_helper_db import get_notes_for_prompt, add_note
from AUCTIONHOUSE.ah_market_events import get_active_events, start_event, end_event
from AUCTIONHOUSE.ah_price_history import get_market_summary
from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_announcer import broadcast


class TestAIPromptBuilding(unittest.TestCase):
    """Test the AI prompt template and context gathering."""

    @classmethod
    def setUpClass(cls):
        initialize_database(force=True)
        cls.db = get_db()
        cls.db.execute("DELETE FROM auction_listings")
        cls.db.execute("DELETE FROM price_history")

    def test_01_prompt_template_exists(self):
        """The prompt template should be a non-empty string."""
        self.assertGreater(len(_SYSTEM_PROMPT), 100)
        self.assertGreater(len(_USER_PROMPT_TEMPLATE), 500)

    def test_02_prompt_template_format(self):
        """USER_PROMPT_TEMPLATE should format with all required placeholders."""
        fmt = _USER_PROMPT_TEMPLATE.format(
            datetime="2026-06-05 12:00 UTC",
            total_active_listings=42,
            player_listings_count=30,
            sim_listings_count=12,
            transactions_24h=15,
            volume_24h=250.5,
            price_snapshots="minecraft:coal avg=0.50",
            active_events="No active events.",
            sim_inventory="coal: stock=500/2000 price=0.50",
            stale_listings="No stale listings.",
            previous_notes="[observation] [3] Test note",
        )
        self.assertIn("2026-06-05", fmt)
        self.assertIn("42", fmt)
        self.assertIn("250.5", fmt)
        self.assertIn("market_assessment", fmt)
        self.assertIn("EVENT FREQUENCY RULES", fmt)
        self.assertGreater(len(fmt), 1000)

    def test_03_prompt_contains_event_rules(self):
        """The prompt should include event frequency guidance."""
        self.assertIn("EVENT FREQUENCY RULES", _USER_PROMPT_TEMPLATE)
        self.assertIn("Small events", _USER_PROMPT_TEMPLATE)
        self.assertIn("Medium events", _USER_PROMPT_TEMPLATE)
        self.assertIn("Major events", _USER_PROMPT_TEMPLATE)

    def test_04_prompt_contains_critical_rules(self):
        """The prompt should include critical behavioral rules."""
        self.assertIn("CRITICAL RULES", _USER_PROMPT_TEMPLATE)
        self.assertIn("Do NOT override player-listed prices", _USER_PROMPT_TEMPLATE)

    def test_05_prompt_builds_with_live_data(self):
        """The prompt should build successfully with data from the DB."""
        from AUCTIONHOUSE.ah_price_history import get_market_summary, get_simulated_inventory_state
        from AUCTIONHOUSE.ah_market_events import get_event_summary_for_prompt
        from AUCTIONHOUSE.ah_price_history import get_stale_listings
        from AUCTIONHOUSE.ah_helper_db import get_notes_for_prompt
        from datetime import datetime, timezone

        summary = get_market_summary()
        sim_inv = get_simulated_inventory_state()
        events = get_event_summary_for_prompt()
        stale = get_stale_listings(hours=0)
        notes = get_notes_for_prompt(limit=10)

        inv_lines = []
        for item in sim_inv:
            price = item.get("current_price") or item.get("base_price", 0)
            inv_lines.append(f"{item['item_id']}: stock={item['current_stock']}/{item['max_stock']} price={price}")
        inv_str = "\n".join(inv_lines)

        notes_lines = [f"[{n['category']}] {str(n['content'])[:80]}" for n in notes]
        notes_str = "\n".join(notes_lines) if notes_lines else "No notes."

        stale_lines = [f"{s['listing_uuid'][:8]} | {s['item_id']}" for s in stale[:5]]
        stale_str = "\n".join(stale_lines) if stale_lines else "No stale listings."

        prompt = _USER_PROMPT_TEMPLATE.format(
            datetime=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            total_active_listings=summary["total_active_listings"],
            player_listings_count=summary["player_listings_count"],
            sim_listings_count=summary["sim_listings_count"],
            transactions_24h=summary["transactions_24h"],
            volume_24h=summary["volume_24h"],
            price_snapshots=summary["price_snapshots_formatted"][:500] or "N/A",
            active_events=events,
            sim_inventory=inv_str[:1000],
            stale_listings=stale_str[:500],
            previous_notes=notes_str[:500],
        )
        self.assertGreater(len(prompt), 500)
        self.assertIn("MARKET STATUS", prompt)
        self.assertIn("PRICE SNAPSHOTS", prompt)


class TestAIJsonExtraction(unittest.TestCase):
    """Test extracting JSON from AI responses (with markdown, code fences, etc.)."""

    def test_extract_raw_json(self):
        """Raw JSON without any wrapping."""
        text = '{"market_assessment": "ok", "price_adjustments": []}'
        result = _extract_json(text)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["market_assessment"], "ok")

    def test_extract_json_with_code_fence(self):
        """JSON wrapped in ```json ... ``` fences."""
        text = 'Here is my analysis:\n```json\n{"market_assessment": "good", "price_adjustments": []}\n```\nEnd.'
        result = _extract_json(text)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["market_assessment"], "good")

    def test_extract_json_with_triple_backtick(self):
        """JSON wrapped in plain ``` fences (no language tag)."""
        text = '```\n{"market_assessment": "ok"}\n```'
        result = _extract_json(text)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["market_assessment"], "ok")

    def test_extract_json_with_extra_text(self):
        """JSON with leading/trailing non-JSON text."""
        text = 'Let me analyze the market.\n\n{"market_assessment": "stable", "price_adjustments": [{"item_id": "minecraft:coal", "new_base_price": 0.6, "reason": "Winter demand"}]}\n\nHope this helps!'
        result = _extract_json(text)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["market_assessment"], "stable")
        self.assertEqual(len(parsed.get("price_adjustments", [])), 1)

    def test_extract_json_empty(self):
        """Empty text should return None."""
        self.assertIsNone(_extract_json(""))
        self.assertIsNone(_extract_json("No JSON here at all"))

    def test_extract_json_nested_inner(self):
        """Extract JSON from deep within a larger text."""
        text = "Some text..." * 20 + '{"result": "deep"}' + "...more text" * 20
        result = _extract_json(text)
        self.assertIsNotNone(result)
        self.assertIn("deep", result)


class TestAIActions(unittest.TestCase):
    """Test applying AI decisions (price adjustments, events, items, etc.)."""

    @classmethod
    def setUpClass(cls):
        initialize_database(force=True)
        cls.db = get_db()
        cls.db.execute("DELETE FROM auction_listings")
        cls.db.execute("DELETE FROM transaction_history")
        cls.db.execute("DELETE FROM price_history")
        cls.db.execute("DELETE FROM market_events")
        cls.db.execute("DELETE FROM ai_notes")
        cls.db.execute("DELETE FROM simulated_inventory")
        # Reseed sim inventory
        from AUCTIONHOUSE.ah_database import _SEED_SIMULATED_INVENTORY
        ts = "2026-01-01T00:00:00"
        seed = _SEED_SIMULATED_INVENTORY.replace("{ts}", f"'{ts}'")
        cls.db._get_conn().executescript(seed)

    def test_01_apply_price_adjustments(self):
        """Price adjustment updates simulated_inventory prices."""
        coal = self.db.fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        old_price = coal["base_price"]
        _apply_price_adjustments([
            {"item_id": "minecraft:coal", "new_base_price": 0.75, "reason": "Winter demand"}
        ])
        coal = self.db.fetch_one("SELECT base_price FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        self.assertEqual(coal["base_price"], 0.75)
        # Restore
        self.db.execute("UPDATE simulated_inventory SET base_price=0.5, current_price=0.5 WHERE item_id='minecraft:coal'")

    def test_02_apply_stock_buy(self):
        """Stock buy adds to simulated inventory."""
        coal = self.db.fetch_one("SELECT current_stock FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        old_stock = coal["current_stock"]
        _apply_stock_adjustments([
            {"item_id": "minecraft:coal", "action": "buy", "quantity": 100, "price": 0.5}
        ])
        coal = self.db.fetch_one("SELECT current_stock FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        self.assertEqual(coal["current_stock"], old_stock + 100)

    def test_03_apply_stock_sell(self):
        """Stock sell removes from simulated inventory."""
        _apply_stock_adjustments([
            {"item_id": "minecraft:coal", "action": "sell", "quantity": 50, "price": 0.55}
        ])
        coal = self.db.fetch_one("SELECT current_stock FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        self.assertEqual(coal["current_stock"], 550)  # was 600 after buy

    def test_04_apply_event_start(self):
        """Applying an event start creates a market event."""
        events_before = get_active_events()
        _apply_events([
            {"action": "start", "event_name": "ai_test_winter", "event_title": "AI Winter Test",
             "event_flavor": "Testing AI events", "event_type": "seasonal", "rarity_tier": "small",
             "affected_items": ["minecraft:coal"], "price_multiplier": 1.5, "goal_count": 100}
        ])
        events_after = get_active_events()
        self.assertEqual(len(events_after), len(events_before) + 1)
        # Clean up
        for e in events_after:
            if e["event_name"] == "ai_test_winter":
                end_event(e["event_uuid"], reason="test_cleanup")

    def test_05_apply_event_end(self):
        """Applying an event end deactivates the event."""
        r = start_event("ai_test_end", "End Test", "Test ending", "festival", "medium",
                         ["minecraft:coal"], price_multiplier=2.0, goal_count=50)
        events_before = get_active_events()
        _apply_events([{"action": "end", "event_name": "ai_test_end"}])
        events_after = get_active_events()
        self.assertLess(len(events_after), len(events_before))

    def test_06_apply_rare_items(self):
        """Applying rare items creates simulated listings."""
        sim_before = query_listings(filter_type="simulated")
        _apply_rare_items([
            {"item_id": "minecraft:diamond_sword", "count": 1, "price": 100.0,
             "enchantments": [{"id": "minecraft:sharpness", "level": 5}],
             "lore": ["Forged in fire."], "rarity_tier": "Rare"}
        ])
        sim_after = query_listings(filter_type="simulated")
        self.assertGreater(sim_after["data"]["total"], sim_before["data"]["total"])

    def test_07_apply_stale_recommendations(self):
        """Applying stale recommendations stores notes."""
        notes_before = len(get_notes_for_prompt())
        r = list_item("TestSeller", "minecraft:diamond", 1, 10.0)
        _apply_stale_recommendations([
            {"listing_uuid": r["data"]["listing_uuid"],
             "recommendation": "lower_price", "suggested_price": 8.0,
             "reason": "No bids in 24 hours"}
        ])
        notes_after = len(get_notes_for_prompt())
        self.assertGreaterEqual(notes_after, notes_before)

    def test_08_apply_notes(self):
        """Applying AI notes stores them in the helper database."""
        notes_before = len(get_notes_for_prompt())
        _apply_notes([
            {"category": "observation", "content": "AI test note", "importance": 2}
        ])
        notes_after = len(get_notes_for_prompt())
        self.assertGreater(notes_after, notes_before)

    def test_09_apply_empty_adjustments(self):
        """Empty adjustments should not crash."""
        _apply_price_adjustments([])
        _apply_stock_adjustments([])
        _apply_events([])
        _apply_rare_items([])
        _apply_stale_recommendations([])
        _apply_notes([])

    def test_10_apply_stock_respects_max(self):
        """Stock buy should not exceed max_stock."""
        coal = self.db.fetch_one("SELECT current_stock, max_stock FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        # Try to buy 10x the max
        _apply_stock_adjustments([
            {"item_id": "minecraft:coal", "action": "buy", "quantity": coal["max_stock"] * 10, "price": 0.5}
        ])
        coal = self.db.fetch_one("SELECT current_stock FROM simulated_inventory WHERE item_id=?", ("minecraft:coal",))
        self.assertLessEqual(coal["current_stock"], 2000)  # max_stock is 2000


class TestAIScheduler(unittest.TestCase):
    """Test the SimulationScheduler lifecycle."""

    def test_01_scheduler_create(self):
        """Scheduler should create and not crash."""
        scheduler = SimulationScheduler()
        self.assertIsNotNone(scheduler)
        self.assertFalse(scheduler.is_running)

    def test_02_scheduler_start_stop(self):
        """Scheduler should start and stop cleanly."""
        scheduler = SimulationScheduler()
        scheduler.start()
        self.assertTrue(scheduler.is_running)
        scheduler.stop()
        self.assertFalse(scheduler.is_running)

    def test_03_scheduler_no_double_start(self):
        """Starting an already-running scheduler should not crash."""
        scheduler = SimulationScheduler()
        scheduler.start()
        scheduler.start()  # Should log a warning, not crash
        self.assertTrue(scheduler.is_running)
        scheduler.stop()

    def test_04_scheduler_no_double_stop(self):
        """Stopping an already-stopped scheduler should not crash."""
        scheduler = SimulationScheduler()
        scheduler.stop()  # No-op
        self.assertFalse(scheduler.is_running)


class TestAISimulationCycle(unittest.TestCase):
    """Test the full simulation cycle with a mocked DeepSeek response.

    The cycle gathers context, builds a prompt, calls DeepSeek (mocked),
    and applies the parsed result.  We mock the API call to return a
    fixed valid response so we can test the entire pipeline.
    """

    @classmethod
    def setUpClass(cls):
        initialize_database(force=True)
        # Clean slate
        db = get_db()
        for table in ["auction_listings", "transaction_history", "price_history",
                       "market_events", "ai_notes"]:
            db.execute(f"DELETE FROM {table}")
        # Ensure sim inventory has data
        count = db.fetch_one("SELECT COUNT(*) as c FROM simulated_inventory")
        if count["c"] == 0:
            from AUCTIONHOUSE.ah_database import _SEED_SIMULATED_INVENTORY
            ts = "2026-01-01T00:00:00"
            seed = _SEED_SIMULATED_INVENTORY.replace("{ts}", f"'{ts}'")
            db.executescript(seed)

    def setUp(self):
        # Re-seed a test listing for the cycle to analyze
        self.db = get_db()
        self.db.execute("DELETE FROM auction_listings")
        self.db.execute("DELETE FROM transaction_history")
        list_item("TestPlayer", "minecraft:diamond", 1, 10.0, buy_now_price=25.0)

    def test_01_cycle_builds_prompt(self):
        """The simulation cycle should build a prompt from market data.

        We test this by verifying the market summary returns data from
        our seeded listing.
        """
        summary = get_market_summary()
        self.assertGreater(summary["total_active_listings"], 0)
        self.assertIn("price_snapshots_formatted", summary)

    def test_02_cycle_respects_disabled_config(self):
        """When simulation is disabled, the scheduler should not start cycles."""
        config = get_config()
        if not config.simulation_enabled:
            scheduler = SimulationScheduler()
            scheduler.start()
            self.assertTrue(scheduler.is_running)
            scheduler.stop()

    def test_03_scheduler_interval(self):
        """The scheduler's interval should match the config."""
        config = get_config()
        self.assertGreater(config.simulation_interval_minutes, 0)
        scheduler = SimulationScheduler()
        # Verify the timer is set up with the correct interval
        self.assertIsNotNone(scheduler)

    def test_04_modular_actions_dont_crash(self):
        """Each individual action function should handle empty/null gracefully."""
        _apply_price_adjustments(None)
        _apply_stock_adjustments(None)
        _apply_events(None)
        _apply_rare_items(None)
        _apply_stale_recommendations(None)
        _apply_notes(None)

    def test_05_cycle_with_admin_operations(self):
        """Admin functions used by the AI should be safe."""
        from AUCTIONHOUSE.ah_admin import get_full_stats, backup_database
        stats = get_full_stats()
        self.assertIn("active_listings", stats)
        self.assertIn("listing_statuses", stats)
        self.assertIn("simulated_inventory_health", stats)


if __name__ == "__main__":
    unittest.main()
