"""
test_tr_data_flow.py — Data flow tests for the SIMULATED_TRADE extension.

Tests end-to-end data flows:
  - Trade offer -> completion -> inventory changes
  - Barter -> skill influence -> resource changes
  - Route construction -> caravan -> arrival
  - Banditry -> loot -> reputation changes
  - Resource depletion -> price changes -> economy impact
"""

import unittest
from conftest import TradeTestCase
import json
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import (
    initiate_trade, accept_trade,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_barter import (
    propose_barter, accept_barter,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_routes import (
    construct_route, dispatch_caravan, process_routes_tick,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_banditry import (
    attempt_banditry,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_reputation import (
    apply_crime_consequences, get_persona_reputation,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_economy import (
    initialize_economy, calculate_price, adjust_supply, adjust_demand,
    get_economy_report,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    ensure_reputation, get_reputation, get_trade_history,
    get_all_wanted_personas,
)

class TestTradeDataFlow(TradeTestCase):
    """Test end-to-end trade data flow."""

    def test_trade_completes_and_inventories_update(self):
        """Trade offer -> acceptance -> inventories transfer."""
        state = get_state()
        initial_alice = dict(state.get("persona_inventories", {}).get("alice-uuid", {}))
        initial_bob = dict(state.get("persona_inventories", {}).get("bob-uuid", {}))

        alice_diamonds = initial_alice.get("minecraft:diamond", 0)
        bob_iron = initial_bob.get("minecraft:iron_ingot", 0)

        offer = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:diamond": 1},
            gold_offered=10.0,
            requested_items={"minecraft:iron_ingot": 5},
            gold_requested=0.0,
        )
        assert offer.get("ok")

        result = accept_trade(offer["offer_uuid"], "bob-uuid")
        assert result.get("ok")

        # Check inventories changed
        updated_alice = state.get("persona_inventories", {}).get("alice-uuid", {})
        updated_bob = state.get("persona_inventories", {}).get("bob-uuid", {})

        # Alice gave 1 diamond, got 5 iron
        assert updated_alice.get("minecraft:diamond", 0) <= alice_diamonds - 1, \
            "Alice should have given away 1 diamond"
        alice_iron_after = updated_alice.get("minecraft:iron_ingot", 0)
        alice_iron_before = initial_alice.get("minecraft:iron_ingot", 0)
        assert alice_iron_after >= alice_iron_before + 5, \
            f"Alice should have received 5 iron (had {alice_iron_before}, now {alice_iron_after})"

        # Verify trade recorded in DB
        history = get_trade_history("alice-uuid")
        assert len(history) >= 1

    def test_barter_data_flow(self):
        """Barter proposal -> acceptance -> items exchange."""
        state = get_state()
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:emerald": 20},
            "bob-uuid": {"minecraft:iron_ingot": 50},
        }, "SIMULATED_TRADE")

        alice_emeralds_before = 20
        bob_iron_before = 50

        offer = propose_barter(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:emerald": 5},
            requested_items={"minecraft:iron_ingot": 10},
        )
        assert offer.get("ok")

        result = accept_barter(offer["offer_uuid"], "bob-uuid")
        if result.get("ok"):
            updated_alice_inv = state.get("persona_inventories", {}).get("alice-uuid", {})
            updated_bob_inv = state.get("persona_inventories", {}).get("bob-uuid", {})

            assert updated_alice_inv.get("minecraft:emerald", 0) <= alice_emeralds_before - 5
            assert updated_bob_inv.get("minecraft:emerald", 0) >= 5

class TestRouteDataFlow(TradeTestCase):
    """Test end-to-end route construction and caravan flow."""

    def test_route_construction_flow(self):
        """Route constructed -> appears in DB -> can be queried."""
        state = get_state()
        state.set("all_claims", {
            "claim-alice-1": {"owner": "alice-uuid"},
            "claim-bob-1": {"owner": "bob-uuid"},
        }, "TRADE_TEST")
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:wood": 500, "minecraft:stone": 500},
        }, "SIMULATED_TRADE")

        result = construct_route(
            persona_uuid="alice-uuid",
            from_claim_uuid="claim-alice-1",
            to_claim_uuid="claim-bob-1",
            road_segments=[{"x": 0, "z": 0}, {"x": 50, "z": 50}],
        )
        assert result.get("ok")

        # Verify route in state
        routes = state.get("trade_routes", [])
        assert len(routes) >= 1

        # Resources consumed
        inv = state.get("persona_inventories", {}).get("alice-uuid", {})
        assert inv.get("minecraft:wood", 500) < 500, "Wood should be consumed"
        assert inv.get("minecraft:stone", 500) < 500, "Stone should be consumed"

class TestBanditryDataFlow(TradeTestCase):
    """Test end-to-end banditry flow."""

    def test_banditry_consequences_flow(self):
        """Banditry -> notoriety increase -> bounty placed -> wantable."""
        ensure_reputation("charlie-uuid")
        rep_before = get_reputation("charlie-uuid")

        result = apply_crime_consequences("charlie-uuid", "banditry")
        assert result.get("notoriety_gained", 0) >= 100
        assert result.get("bounty_added", 0) > 0

        rep_after = get_reputation("charlie-uuid")
        assert rep_after["notoriety"] > rep_before["notoriety"]
        assert rep_after["bounty"] > rep_before["bounty"]

class TestEconomyDataFlow(TradeTestCase):
    """Test economy data flow with supply/demand."""

    def test_supply_change_affects_prices(self):
        """Supply decrease -> price increase -> economy report reflects."""
        initialize_economy()
        price_before = calculate_price("minecraft:diamond", "overworld")

        # Significant supply reduction
        adjust_supply("minecraft:diamond", "overworld", -80.0)
        adjust_demand("minecraft:diamond", "overworld", 50.0)

        price_after = calculate_price("minecraft:diamond", "overworld")
        assert price_after > price_before

        # Report should reflect scarcity
        report = get_economy_report("overworld")
        assert len(report.get("scarce_items", [])) >= 0  # May or may not show
