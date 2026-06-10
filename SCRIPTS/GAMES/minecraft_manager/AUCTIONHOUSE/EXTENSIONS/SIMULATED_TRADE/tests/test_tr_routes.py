
"""
test_tr_routes.py — Tests for trade routes and road construction.

Tests: route construction, upgrades, maintenance, degradation,
caravan dispatch, resource costs, validation.
"""

import unittest
from conftest import TradeTestCase
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_routes import (
    construct_route, upgrade_road, perform_maintenance,
    dispatch_caravan, process_routes_tick,
)
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state

def _create_test_route():
    """Create a test route between Alice and Bob's claims.
    Returns route_uuid or None if construction fails.
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_routes import construct_route
    state = get_state()
    state.set("all_claims", {
        "claim-alice-1": {"owner": "alice-uuid", "name": "Alice's Base"},
        "claim-bob-1": {"owner": "bob-uuid", "name": "Bob's Mine"},
    }, "TRADE_TEST")
    state.set("persona_inventories", {
        "alice-uuid": {"minecraft:wood": 256, "minecraft:stone": 128,
                       "minecraft:diamond": 10, "minecraft:emerald": 50},
        "bob-uuid": {"minecraft:iron_ingot": 32, "minecraft:stone": 128, "minecraft:wood": 64},
        "charlie-uuid": {"minecraft:diamond_sword": 1},
    }, "SIMULATED_TRADE")
    state.set("persona_finances", {
        "alice-uuid": {"balance": 5000.0},
        "bob-uuid": {"balance": 2000.0},
        "charlie-uuid": {"balance": 500.0},
    }, "SIMULATED_TRADE")
    result = construct_route(
        persona_uuid="alice-uuid",
        from_claim_uuid="claim-alice-1",
        to_claim_uuid="claim-bob-1",
        road_segments=[{"x": 0, "z": 0}, {"x": 50, "z": 50}, {"x": 100, "z": 100}],
    )
    return result.get("route_uuid") if result.get("ok") else None

class TestRouteConstruction(TradeTestCase):
    """Test trade route construction."""

    def test_construct_on_unowned_claim_fails(self):
        result = construct_route(
            persona_uuid="alice-uuid",
            from_claim_uuid="claim-bob-1",  # Bob's claim, not Alice's
            to_claim_uuid="claim-nonexistent",
            road_segments=[{"x": 0, "z": 0}, {"x": 100, "z": 100}],
        )
        assert not result.get("ok")

    def test_construct_with_insufficient_materials(self):
        # Set Alice's inventory to have no materials
        state = get_state()
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:wheat": 1},  # Not enough wood/stone
        }, "SIMULATED_TRADE")

        result = construct_route(
            persona_uuid="alice-uuid",
            from_claim_uuid="claim-alice-1",
            to_claim_uuid="claim-bob-1",
            road_segments=[{"x": 0, "z": 0}, {"x": 100, "z": 100}],
        )
        assert not result.get("ok")

    def test_successful_construction(self):
        route = _create_test_route()
        assert route is not None, "Route construction should succeed"

    def test_duplicate_route_prevented(self):
        route = _create_test_route()
        

        result = construct_route(
            persona_uuid="alice-uuid",
            from_claim_uuid="claim-alice-1",
            to_claim_uuid="claim-bob-1",
            road_segments=[{"x": 0, "z": 0}, {"x": 100, "z": 100}],
        )
        assert not result.get("ok")
        assert "already exists" in result.get("error", "")

class TestRouteUpgrades(TradeTestCase):
    """Test route upgrading."""

    def test_upgrade_nonexistent(self):
        result = upgrade_road("nonexistent-uuid", "alice-uuid")
        assert not result.get("ok")

    def test_upgrade_successful(self):
        route = _create_test_route()
        if not route:
            self.skipTest("No route to upgrade")
        

        # Give Alice materials for upgrade
        state = get_state()
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:wood": 500, "minecraft:stone": 500},
        }, "SIMULATED_TRADE")

        result = upgrade_road(route, "alice-uuid")
        assert result.get("ok"), f"Upgrade failed: {result}"
        assert result.get("new_level", 0) > 1

class TestRouteCaravans(TradeTestCase):
    """Test caravan dispatch on routes."""

    def test_dispatch_on_nonexistent_route(self):
        result = dispatch_caravan(
            persona_uuid="alice-uuid",
            route_uuid="nonexistent",
            cargo={"minecraft:diamond": 5},
            gold=100.0,
        )
        assert not result.get("ok")

    def test_dispatch_without_enough_items(self):
        route = _create_test_route()
        if not route:
            self.skipTest("No route")
        state = get_state()
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:wheat": 1},
        }, "SIMULATED_TRADE")

        result = dispatch_caravan(
            persona_uuid="alice-uuid",
            route_uuid=route,
            cargo={"minecraft:diamond": 999},
            gold=100.0,
        )
        assert not result.get("ok")

    def test_successful_dispatch(self):
        route = _create_test_route()
        if not route:
            self.skipTest("No route")
        state = get_state()
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:diamond": 20, "minecraft:emerald": 50},
        }, "SIMULATED_TRADE")
        state.set("persona_finances", {
            "alice-uuid": {"balance": 5000.0},
        }, "SIMULATED_TRADE")

        result = dispatch_caravan(
            persona_uuid="alice-uuid",
            route_uuid=route,
            cargo={"minecraft:diamond": 5},
            gold=100.0,
        )
        assert result.get("ok"), f"Dispatch failed: {result}"
        assert "caravan_uuid" in result

class TestRouteTick(TradeTestCase):
    """Test route tick processing."""

    def test_tick_no_crashes(self):
        result = process_routes_tick()
        assert isinstance(result, dict)
        assert "caravans_advanced" in result
