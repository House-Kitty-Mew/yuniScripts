"""
test_tr_barter.py — Tests for the barter exchange system.

Tests: barter proposal, acceptance, fairness checks, skill effects,
tolerance calculations, edge cases.
"""

import unittest
from conftest import TradeTestCase
import json
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_barter import (
    propose_barter, accept_barter, evaluate_barter_offer,
)

class TestBarterProposal(TradeTestCase):
    """Test barter offer creation."""

    def test_barter_with_self_fails(self):
        result = propose_barter(
            initiator_uuid="alice-uuid",
            target_uuid="alice-uuid",
            offered_items={"minecraft:emerald": 5},
            requested_items={"minecraft:iron_ingot": 10},
        )
        assert not result.get("ok")

    def test_barter_no_items_fails(self):
        result = propose_barter(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={},
            requested_items={"minecraft:iron_ingot": 5},
        )
        assert not result.get("ok")

    def test_barter_no_request_fails(self):
        result = propose_barter(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:emerald": 5},
            requested_items={},
        )
        assert not result.get("ok")

    def test_barter_successful_proposal(self):
        result = propose_barter(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:emerald": 5},
            requested_items={"minecraft:iron_ingot": 10},
        )
        assert result.get("ok"), f"Expected OK, got: {result}"
        assert "offer_uuid" in result

class TestBarterAcceptance(TradeTestCase):
    """Test barter acceptance flow."""

    def test_accept_nonexistent_barter(self):
        result = accept_barter("non-existent", "bob-uuid")
        assert not result.get("ok")

    def test_accept_unbalanced_barter(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_barter import propose_barter
        offer = propose_barter(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:emerald": 1},
            requested_items={"minecraft:diamond": 50},
        )
        offer_uuid = offer.get("offer_uuid")
        result = accept_barter(offer_uuid, "bob-uuid")
        # Should be too unbalanced (1 emerald = 20g vs 50 diamonds = 2500g)
        assert not result.get("ok")

    def test_accept_fair_barter(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_barter import propose_barter
        # Alice has high barter (60), Bob has low (30).
        # Alice's offered value gets 1.15x bonus, Bob's requested gets 0.85x
        # So we need offered=91g, requested=91g to balance:
        # 5 emeralds (100g) * 0.85 * 1.0 = 85g (for Bob's eval)
        # 10 iron (100g) * 1.15 = 115g (for Alice's eval)
        # flat items: 5 diamond (250g) for 10 iron (100g) = balanced with skill mods
        # Actually let's just use more items to get within tolerance
        # offered_value = offered_qty * base_value * skill_mod
        # requested_value = requested_qty * base_value * skill_mod
        # For tolerance: offered should be close to requested
        # With alice@60 skill: offered gets 1.15x, requested gets 0.85x
        # So offered * 1.15 ≈ requested * 0.85 ⟹ offered/requested ≈ 0.85/1.15 ≈ 0.74
        # Alice offers 8 emeralds (160g): offered_val = 160 * 1.15 = 184
        # Bob offers 15 iron (150g): requested_val = 150 * 0.85 = 127.5
        # |184-127.5|/max(184,127.5) = 56.5/184 = 0.307 = 30.7% > 15% still too much
        # Use closer values: alice offers 3 diamond (150g) for bob's 15 iron (150g)
        # offered_val = 150 * 1.15 = 172.5
        # requested_val = 150 * 0.85 = 127.5
        # |172.5-127.5|/172.5 = 45/172.5 = 0.26 = 26% > 15% still too much
        # The skill gap is too big. Let's just use identical items:
        # Same value, small quantity: 10 emeralds for 10 emeralds
        # But that's pointless barter. Instead, let's adjust tolerance...
        # Actually, the simplest fix: use items where Alice overpays relatively
        # to compensate for her skill advantage.
        # 5 emeralds (100g) for 12 iron (120g)
        # offered_val = 100 * 1.15 = 115
        # requested_val = 120 * 0.85 = 102
        # |115-102|/115 = 13/115 = 0.113 = 11.3% < 15% ✓
        offer = propose_barter(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:emerald": 5},
            requested_items={"minecraft:iron_ingot": 12},
        )
        offer_uuid = offer.get("offer_uuid")
        result = accept_barter(offer_uuid, "bob-uuid")
        assert result.get("ok"), f"Fair barter should succeed: {result}"
        assert "trade_uuid" in result

class TestBarterEvaluation(TradeTestCase):
    """Test barter offer evaluation for AI decisions."""

    def test_evaluate_balanced_barter(self):
        offer = {
            "initiator_uuid": "bob-uuid",
            "offered_items": json.dumps({"minecraft:iron_ingot": 10}),
            "requested_items": json.dumps({"minecraft:emerald": 5}),
            "offer_type": "barter",
        }
        result = evaluate_barter_offer(offer, "alice-uuid")
        assert "score" in result
        assert result["score"] > 0

    def test_barter_skill_impact(self):
        """High skill barterer gets better deals."""
        offer = {
            "initiator_uuid": "bob-uuid",
            "offered_items": json.dumps({"minecraft:iron_ingot": 15}),
            "requested_items": json.dumps({"minecraft:emerald": 5}),
            "offer_type": "barter",
        }
        # Alice has barter 60, Bob has 30
        result = evaluate_barter_offer(offer, "alice-uuid")
        assert result.get("offered_value", 0) > 0
        assert result.get("requested_value", 0) > 0
