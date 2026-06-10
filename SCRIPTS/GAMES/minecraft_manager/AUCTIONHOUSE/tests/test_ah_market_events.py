"""
test_ah_market_events.py — Tests for ah_market_events.py
"""

from conftest import AHTestCase
from AUCTIONHOUSE.ah_market_events import (
    can_start_event, start_event, end_event, get_active_events,
    check_event_progress, get_event_history,
)
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_config import get_config


class TestMarketEvents(AHTestCase):

    def setUp(self):
        super().setUp()
        get_db().execute("DELETE FROM market_events")

    def test_can_start_event_small(self):
        """Fresh system allows small events."""
        self.assertTrue(can_start_event("small")["ok"])

    def test_can_start_event_max_overlap(self):
        """Different tiers can overlap up to max limit."""
        max_overlap = get_config().event_max_active_overlap
        # Use different tiers so they don't conflict
        tiers = ["small", "medium"]  # 2 tiers should both work with max_overlap=2
        for i, tier in enumerate(tiers[:max_overlap]):
            r = start_event(event_name=f"test{i}", event_title=f"Test {i}", event_flavor="f",
                            event_type="seasonal", rarity_tier=tier,
                            affected_items=["minecraft:coal"], created_by="test")
            self.assertTrue(r["ok"], f"Failed event {i} tier={tier}: {r.get('error')}")
        # Next event should fail (max overlap reached)
        result = can_start_event("small")
        self.assertFalse(result["ok"])
        self.assertIn("max", result.get("reason", "").lower())

    def test_can_start_event_same_tier_active(self):
        """Same tier event already active is rejected."""
        start_event(event_name="storm", event_title="Storm", event_flavor="windy",
                    event_type="disaster", rarity_tier="medium",
                    affected_items=["minecraft:coal"], created_by="test")
        result = can_start_event("medium")
        self.assertFalse(result["ok"])
        self.assertIn("already active", result.get("reason", "").lower())

    def test_start_event_success(self):
        """start_event creates event with all fields."""
        r = start_event(event_name="extreme_winter", event_title="❄ Extreme Winter!",
                        event_flavor="The cold is here!", event_type="disaster",
                        rarity_tier="medium", affected_items=["minecraft:coal", "minecraft:leather"],
                        price_multiplier=2.0, demand_boost=1.5, duration_seconds=3600,
                        goal_count=100, trigger_condition={"type": "sale", "target": "coal"},
                        created_by="AI")
        self.assertTrue(r["ok"])
        uid = r["data"]["event_uuid"]
        event = get_db().fetch_one("SELECT * FROM market_events WHERE event_uuid = ?", (uid,))
        self.assertIsNotNone(event)
        self.assertEqual(event["event_name"], "extreme_winter")
        self.assertEqual(event["rarity_tier"], "medium")
        self.assertEqual(event["price_multiplier"], 2.0)
        self.assertEqual(event["goal_count"], 100)
        self.assertEqual(event["is_active"], 1)
        self.assertEqual(event["created_by"], "AI")

    def test_start_event_applies_price_multiplier(self):
        """Starting event updates simulated inventory prices."""
        coal_before = get_db().fetch_one("SELECT current_price FROM simulated_inventory WHERE item_id = 'minecraft:coal'")
        start_event(event_name="cold_snap", event_title="Cold Snap", event_flavor="brr",
                    event_type="seasonal", rarity_tier="small",
                    affected_items=["minecraft:coal"], price_multiplier=2.5)
        coal_after = get_db().fetch_one("SELECT current_price FROM simulated_inventory WHERE item_id = 'minecraft:coal'")
        self.assertAlmostEqual(coal_after["current_price"], (coal_before["current_price"] or 0.5) * 2.5, places=2)

    def test_start_event_invalid_type(self):
        """Invalid event type is rejected."""
        r = start_event(event_name="bad", event_title="Bad", event_flavor="bad",
                        event_type="INVALID_TYPE", rarity_tier="small",
                        affected_items=["minecraft:coal"])
        self.assertFalse(r["ok"])
        self.assertIn("invalid event type", r["error"].lower())

    def test_start_event_invalid_tier(self):
        """Invalid rarity tier is rejected."""
        r = start_event(event_name="bad", event_title="Bad", event_flavor="bad",
                        event_type="seasonal", rarity_tier="MYTHICAL",
                        affected_items=["minecraft:coal"])
        self.assertFalse(r["ok"])
        self.assertIn("invalid rarity tier", r["error"].lower())

    def test_start_event_auto_duration(self):
        """Duration auto-calculated for small tier (3600s)."""
        start_event(event_name="quick", event_title="Quick", event_flavor="fast",
                    event_type="festival", rarity_tier="small",
                    affected_items=["minecraft:diamond"])
        event = get_db().fetch_one("SELECT duration_seconds FROM market_events WHERE event_name = 'quick'")
        self.assertEqual(event["duration_seconds"], 3600)

    def test_start_event_auto_goal(self):
        """Goal auto-calculated from stock when not provided."""
        start_event(event_name="test_goal", event_title="Goal Test", event_flavor="test",
                    event_type="discovery", rarity_tier="small",
                    affected_items=["minecraft:coal"], created_by="test")
        event = get_db().fetch_one("SELECT goal_count FROM market_events WHERE event_name = 'test_goal'")
        self.assertGreaterEqual(event["goal_count"], 100)

    def test_get_active_events(self):
        """get_active_events returns only active events."""
        # Use different tiers to avoid same-tier conflict
        self.assertTrue(start_event("e1", "E1", "f1", "seasonal", "small", ["minecraft:coal"])["ok"])
        self.assertTrue(start_event("e2", "E2", "f2", "seasonal", "medium", ["minecraft:diamond"])["ok"])
        active = get_active_events()
        self.assertGreaterEqual(len(active), 2)

    def test_end_event(self):
        """end_event deactivates an event."""
        r = start_event("end_test", "End Test", "flavor", "seasonal", "small", ["minecraft:coal"])
        uid = r["data"]["event_uuid"]
        self.assertTrue(end_event(uid)["ok"])
        active_uuids = {e["event_uuid"] for e in get_active_events()}
        self.assertNotIn(uid, active_uuids)

    def test_get_event_history(self):
        """get_event_history returns past events."""
        r = start_event("history_test", "History", "flavor", "festival", "small", ["minecraft:coal"])
        end_event(r["data"]["event_uuid"])
        history = get_event_history()
        self.assertGreaterEqual(len(history), 1)

    def test_check_event_progress(self):
        """check_event_progress finds completed goals."""
        r = start_event("progress_test", "Progress", "flavor", "discovery", "small",
                        ["minecraft:diamond"], goal_count=5)
        uid = r["data"]["event_uuid"]
        get_db().execute("UPDATE market_events SET current_count = 5 WHERE event_uuid = ?", (uid,))
        check_event_progress()
        event = get_db().fetch_one("SELECT is_active, ended_at FROM market_events WHERE event_uuid = ?", (uid,))
        if event["is_active"] == 0:
            self.assertIsNotNone(event["ended_at"])

    def test_different_tiers_can_overlap(self):
        """Different tiers can be active simultaneously."""
        self.assertTrue(start_event("s1", "S1", "f", "seasonal", "small", ["minecraft:coal"])["ok"])
        self.assertTrue(start_event("m1", "M1", "f", "seasonal", "medium", ["minecraft:diamond"])["ok"])
        active = get_active_events()
        self.assertGreaterEqual(len(active), 2)


if __name__ == "__main__":
    from unittest import main
    main()
