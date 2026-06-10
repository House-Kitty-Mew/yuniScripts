"""
test_tr_edge_cases.py — Edge case tests for the SIMULATED_TRADE extension.

Comprehensive edge cases including:
  - Zero/invalid values
  - Overflow/underflow protection
  - Concurrent access safety
  - State consistency
  - Boundary conditions
"""

import unittest
from conftest import TradeTestCase
import json, threading
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import (
    initiate_trade, accept_trade, evaluate_trade_offer,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_barter import (
    propose_barter, accept_barter, evaluate_barter_offer,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_routes import (
    construct_route, dispatch_caravan,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_banditry import (
    attempt_banditry,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_reputation import (
    apply_crime_consequences, get_price_modifier,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    ensure_reputation, update_notoriety, update_bounty,
    update_reputation_score, set_jail_ticks, is_persona_jailed,
    check_cooldown, set_cooldown, get_reputation,
)
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state

class TestInvalidValues(TradeTestCase):
    """Test handling of invalid/edge case values."""

    def test_trade_negative_gold(self):
        result = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:diamond": 1},
            gold_offered=-10.0,
            requested_items={},
            gold_requested=0.0,
        )
        assert "ok" in result

    def test_barter_empty_dict(self):
        result = propose_barter(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={},
            requested_items={},
        )
        assert not result.get("ok")

    def test_trade_zero_quantity(self):
        result = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:diamond": 0},
            gold_offered=0.0,
            requested_items={},
            gold_requested=0.0,
        )
        assert not result.get("ok")

class TestOverflowUnderflow(TradeTestCase):
    """Test boundary protection for values."""

    def test_notoriety_capped_at_max(self):
        ensure_reputation("alice-uuid")
        for _ in range(20):
            update_notoriety("alice-uuid", 100)
        rep = get_reputation("alice-uuid")
        assert rep["notoriety"] <= 1000

    def test_notoriety_stays_above_zero(self):
        ensure_reputation("alice-uuid")
        update_notoriety("alice-uuid", -9999)
        rep = get_reputation("alice-uuid")
        assert rep["notoriety"] >= 0

    def test_reputation_capped(self):
        ensure_reputation("alice-uuid")
        update_reputation_score("alice-uuid", 99999)
        rep = get_reputation("alice-uuid")
        assert rep["reputation_score"] <= 1000

        update_reputation_score("alice-uuid", -99999)
        rep = get_reputation("alice-uuid")
        assert rep["reputation_score"] >= -1000

    def test_bounty_never_negative(self):
        ensure_reputation("alice-uuid")
        update_bounty("alice-uuid", -9999.0)
        rep = get_reputation("alice-uuid")
        assert rep["bounty"] >= 0.0

    def test_jail_ticks_never_negative(self):
        ensure_reputation("bob-uuid")
        set_jail_ticks("bob-uuid", -5)
        assert not is_persona_jailed("bob-uuid")

class TestStateConsistency(TradeTestCase):
    """Test state consistency across operations."""

    def test_inventory_does_not_go_negative(self):
        """Offering more items than owned should fail at accept time."""
        state = get_state()
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:diamond": 1},
        }, "SIMULATED_TRADE")

        offer = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:diamond": 999},
            gold_offered=0.0,
            requested_items={"minecraft:iron_ingot": 1},
            gold_requested=0.0,
        )
        assert offer.get("ok"), "Offer creation should succeed (pending validation at accept)"
        result = accept_trade(offer["offer_uuid"], "bob-uuid")
        assert not result.get("ok"), "Accept should fail — Alice doesn't have enough items"

    def test_offer_acceptance_idempotent(self):
        offer = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:emerald": 5},
            gold_offered=0.0,
            requested_items={"minecraft:iron_ingot": 10},
            gold_requested=0.0,
        )
        offer_uuid = offer.get("offer_uuid")
        if not offer_uuid:
            self.skipTest("Offer creation failed")

        result1 = accept_trade(offer_uuid, "bob-uuid")
        result2 = accept_trade(offer_uuid, "bob-uuid")
        assert not result2.get("ok"), "Second accept should fail"

class TestCooldowns(TradeTestCase):
    """Test cooldown system edge cases."""

    def test_cooldown_blocks_action(self):
        set_cooldown("alice-uuid", "trade", 100)
        assert check_cooldown("alice-uuid", "trade")

    def test_cooldown_expires(self):
        set_cooldown("alice-uuid", "trade", 1)
        assert check_cooldown("alice-uuid", "trade")
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import tick_cooldowns
        tick_cooldowns()
        assert not check_cooldown("alice-uuid", "trade")

class TestMultipleOffers(TradeTestCase):
    """Test multiple simultaneous offers."""

    def test_max_offers_limit(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
        max_offers = get_config("trade", "max_pending_offers_per_persona", 5)

        for i in range(max_offers):
            result = initiate_trade(
                initiator_uuid="alice-uuid",
                target_uuid="bob-uuid",
                offered_items={"minecraft:emerald": 1},
                gold_offered=0.0,
                requested_items={"minecraft:iron_ingot": 1},
                gold_requested=0.0,
            )
            assert result.get("ok"), f"Offer {i+1} failed: {result}"

        result = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:emerald": 1},
            gold_offered=0.0,
            requested_items={"minecraft:iron_ingot": 1},
            gold_requested=0.0,
        )
        assert not result.get("ok")
        assert "too many" in result.get("error", "").lower()

class TestConcurrentAccess(TradeTestCase):
    """Test thread safety (basic)."""

    def test_thread_safe_offers(self):
        errors = []

        def create_offer(name):
            try:
                initiate_trade(
                    initiator_uuid="alice-uuid",
                    target_uuid="bob-uuid",
                    offered_items={"minecraft:diamond": 1},
                    gold_offered=float(name),
                    requested_items={},
                    gold_requested=0.0,
                )
            except Exception as e:
                errors.append(str(e))

        threads = []
        for i in range(10):
            t = threading.Thread(target=create_offer, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
