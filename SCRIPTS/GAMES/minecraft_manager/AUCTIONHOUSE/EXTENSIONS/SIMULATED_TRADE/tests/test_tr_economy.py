
"""
test_tr_economy.py — Tests for supply/demand economy simulation.

Tests: price calculation, supply/demand adjustments, elasticity,
economy tick processing, trade impact on economy.
"""

import unittest
from conftest import TradeTestCase
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_economy import (
    initialize_economy, calculate_price, adjust_supply, adjust_demand,
    process_trade_economy_impact, process_economy_tick,
    get_economy_report,
)

class TestEconomyInit(TradeTestCase):
    """Test economy initialization."""

    def test_initialize_economy(self):
        initialize_economy()
        report = get_economy_report("overworld")
        assert report["region"] == "overworld"

class TestPriceCalculation(TradeTestCase):
    """Test price calculation with various factors."""

    def test_base_price(self):
        initialize_economy()
        price = calculate_price("minecraft:diamond", "overworld", 50.0)
        assert price > 0

    def test_supply_demand_impact(self):
        initialize_economy()
        initial = calculate_price("minecraft:diamond", "overworld", 50.0)

        # Crash supply
        adjust_supply("minecraft:diamond", "overworld", -80.0)
        # Increase demand
        adjust_demand("minecraft:diamond", "overworld", 50.0)

        after = calculate_price("minecraft:diamond", "overworld", 50.0)
        assert after > initial, "Scarcity should increase price"

    def test_surplus_lowers_price(self):
        initialize_economy()
        initial = calculate_price("minecraft:wheat", "overworld", 1.0)

        adjust_supply("minecraft:wheat", "overworld", 200.0)
        adjust_demand("minecraft:wheat", "overworld", -50.0)

        after = calculate_price("minecraft:wheat", "overworld", 1.0)
        assert after <= initial, "Surplus should not increase price"

class TestEconomyTick(TradeTestCase):
    """Test economy tick processing."""

    def test_tick_reverts_supply(self):
        initialize_economy()

        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_economy import _supply
        initial = _supply.get("overworld", {}).get("minecraft:diamond", 100.0)
        adjust_supply("minecraft:diamond", "overworld", -80.0)
        level_before = _supply.get("overworld", {}).get("minecraft:diamond", 0)

        for _ in range(100):
            process_economy_tick()

        level_after = _supply.get("overworld", {}).get("minecraft:diamond", 0)
        # Supply should have increased from regen
        assert level_after > level_before, \
            f"Supply should regen from {level_before:.1f}, was {level_after:.1f}"

class TestTradeImpact(TradeTestCase):
    """Test trade economic impact."""

    def test_trade_impact_applies(self):
        initialize_economy()

        process_trade_economy_impact(
            trade_type="currency",
            items_offered={"minecraft:diamond": 5},
            items_received={"minecraft:iron_ingot": 20},
            region_id="overworld",
        )

        report = get_economy_report("overworld")
        assert report["total_items_tracked"] > 0
