"""
test_tr_banditry.py — Tests for banditry system.

Tests: attack resolution, combat outcomes, loot calculation,
reputation consequences, guard response, edge cases.
"""

import unittest
from conftest import TradeTestCase
import json
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_banditry import (
    attempt_banditry, get_banditry_risk, process_guard_response,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    ensure_reputation, get_reputation,
    create_caravan, get_active_caravans_on_route,
)
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state

class TestBanditryValidation(TradeTestCase):
    """Test banditry pre-checks."""

    def test_banditry_low_skill_fails(self):
        # Alice has combat 10, below min 20
        result = attempt_banditry(
            attacker_uuid="alice-uuid",
            route_uuid="some-route",
        )
        assert not result.get("ok")
        assert "skill" in result.get("error", "").lower()

    def test_banditry_nonexistent_route(self):
        state = get_state()
        state.set("persona_skills", {
            "charlie-uuid": {"combat": 70.0, "stealth": 50.0},
        }, "SIMULATED_TRADE")

        result = attempt_banditry(
            attacker_uuid="charlie-uuid",
            route_uuid="nonexistent-route",
        )
        assert not result.get("ok")

class TestBanditryCombat(TradeTestCase):
    """Test combat resolution outcomes."""

    def _setup_route_with_caravans(self):
        """Helper: create a route with caravans for banditry testing."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db

        # Create route directly in DB for testing
        db = get_db()
        now = "2026-01-01T00:00:00"
        db.execute("""
            INSERT INTO ext_tr_routes
                (route_uuid, owner_uuid, from_claim_uuid, to_claim_uuid,
                 road_segments, distance, level, is_active,
                 guard_combat_power, bandit_activity, built_at, last_maintained)
            VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?)
        """, ("test-route-1", "bob-uuid", "claim-bob-1", "claim-alice-1",
              json.dumps([{"x": 0, "z": 0}, {"x": 100, "z": 100}]),
              141.4, 5.0, 0.1, now, now))
        db.conn.commit()

        # Create a caravan on this route
        create_caravan(
            route_uuid="test-route-1",
            owner_uuid="bob-uuid",
            cargo={"minecraft:diamond": 10, "minecraft:emerald": 20},
            gold_carried=500.0,
            guard_count=2,
            guard_combat_power=10.0,
            total_segments=2,
        )

        return "test-route-1"

    def test_overwhelming_victory(self):
        route_uuid = self._setup_route_with_caravans()

        state = get_state()
        state.set("persona_skills", {
            "charlie-uuid": {"combat": 80.0, "stealth": 60.0},
        }, "SIMULATED_TRADE")

        ensure_reputation("charlie-uuid")

        result = attempt_banditry(
            attacker_uuid="charlie-uuid",
            route_uuid=route_uuid,
        )
        assert result.get("ok"), f"Attack failed: {result}"
        assert result.get("outcome") in ("victory", "costly_victory"), \
            f"Expected victory, got: {result.get('outcome')}"
        assert result.get("loot_value", 0) > 0
        assert result.get("notoriety_gained", 0) > 0
        assert result.get("bounty_placed", 0) > 0

    def test_defeat_outcome(self):
        route_uuid = self._setup_route_with_caravans()

        # Charlie with combat just above minimum but still weak
        state = get_state()
        state.set("persona_skills", {
            "charlie-uuid": {"combat": 25.0, "stealth": 5.0},
        }, "SIMULATED_TRADE")

        ensure_reputation("charlie-uuid")

        result = attempt_banditry(
            attacker_uuid="charlie-uuid",
            route_uuid=route_uuid,
        )
        assert result.get("ok"), f"Attack should process: {result}"
        # 25 combat vs 10 defense + activity should be costly victory or defeat
        assert "outcome" in result

class TestGuardResponse(TradeTestCase):
    """Test guard patrol and arrest system."""

    def test_guard_response_processes(self):
        # First set up a wanted persona
        ensure_reputation("charlie-uuid")
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
            update_notoriety, update_bounty
        )
        update_notoriety("charlie-uuid", 200)
        update_bounty("charlie-uuid", 500.0)

        result = process_guard_response()
        assert isinstance(result, dict)
        assert "arrests" in result
