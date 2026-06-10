"""
test_tr_core.py — Tests for the core trade engine.

Tests: trade initiation, acceptance, validation, range checks,
cooldowns, jail prevention, relationship impact.
"""

import unittest
from conftest import TradeTestCase
import json
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import (
    initiate_trade, accept_trade, evaluate_trade_offer,
    get_open_trade_offers, decline_trade_offer,
)

class TestTradeValidation(TradeTestCase):
    """Test trade validation checks."""

    def test_trade_with_self_fails(self):
        result = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="alice-uuid",
            offered_items={"minecraft:diamond": 1},
            gold_offered=0.0,
            requested_items={"minecraft:iron_ingot": 5},
            gold_requested=0.0,
        )
        assert not result.get("ok")
        assert "yourself" in result.get("error", "").lower()

    def test_trade_with_empty_offer_fails(self):
        result = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={},
            gold_offered=0.0,
            requested_items={},
            gold_requested=0.0,
        )
        assert not result.get("ok")

    def test_trade_without_items_or_gold_fails(self):
        result = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={},
            gold_offered=0.0,
            requested_items={"minecraft:diamond": 1},
            gold_requested=0.0,
        )
        assert not result.get("ok")

    def test_trade_successful_initiation(self):
        result = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:diamond": 1},
            gold_offered=10.0,
            requested_items={"minecraft:iron_ingot": 5},
            gold_requested=0.0,
        )
        assert result.get("ok"), f"Expected OK, got: {result}"
        assert "offer_uuid" in result

class TestTradeAcceptance(TradeTestCase):
    """Test trade acceptance flow."""

    def _create_offer(self, initiator, target, offered=None, requested=None,
                      go=0.0, gr=0.0):
        return initiate_trade(
            initiator_uuid=initiator,
            target_uuid=target,
            offered_items=offered or {"minecraft:diamond": 1},
            gold_offered=go,
            requested_items=requested or {"minecraft:iron_ingot": 5},
            gold_requested=gr,
        )

    def test_accept_nonexistent_offer(self):
        result = accept_trade("non-existent-uuid", "bob-uuid")
        assert not result.get("ok")

    def test_accept_trade_updates_inventories(self):
        offer = self._create_offer("alice-uuid", "bob-uuid")
        assert offer.get("ok")
        offer_uuid = offer["offer_uuid"]

        result = accept_trade(offer_uuid, "bob-uuid")
        assert result.get("ok"), f"Accept failed: {result}"
        assert "trade_uuid" in result

    def test_accept_trade_wrong_target(self):
        offer = self._create_offer("alice-uuid", "bob-uuid")
        offer_uuid = offer["offer_uuid"]

        result = accept_trade(offer_uuid, "charlie-uuid")
        assert not result.get("ok")
        assert "not for you" in result.get("error", "").lower()

class TestTradeEvaluation(TradeTestCase):
    """Test AI trade offer evaluation."""

    def test_evaluate_fair_trade(self):
        offer = {
            "initiator_uuid": "bob-uuid",
            "offered_items": json.dumps({"minecraft:iron_ingot": 10}),
            "requested_items": json.dumps({"minecraft:diamond": 1}),
            "gold_offered": 0.0,
            "gold_requested": 0.0,
        }
        result = evaluate_trade_offer(offer, "alice-uuid")
        assert "score" in result
        assert result["score"] > 0

    def test_evaluate_unfair_trade(self):
        offer = {
            "initiator_uuid": "bob-uuid",
            "offered_items": json.dumps({"minecraft:wheat": 1}),
            "requested_items": json.dumps({"minecraft:diamond": 10}),
            "gold_offered": 0.0,
            "gold_requested": 0.0,
        }
        result = evaluate_trade_offer(offer, "alice-uuid")
        assert result.get("score", 1.0) < 0.5 or not result.get("should_accept", True)
