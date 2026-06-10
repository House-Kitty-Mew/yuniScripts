
"""
test_tr_world_events.py — Tests for world events and resource depletion.

Tests: event triggering, resource state updates, regeneration,
price multiplier calculations, event expiry.
"""

import unittest
from conftest import TradeTestCase
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_world_events import (
    initialize_resource_states, check_and_trigger_events,
    process_world_events_tick, get_region_price_multiplier,
    get_resource_abundance, is_resource_scarce,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    get_all_resource_states, get_active_world_events,
    ensure_resource_state, update_resource_abundance,
)

class TestResourceInit(TradeTestCase):
    """Test resource state initialization."""

    def test_initialize_resource_states(self):
        result = initialize_resource_states()
        assert result is True

        states = get_all_resource_states()
        assert len(states) > 0

    def test_resource_state_values(self):
        initialize_resource_states()
        states = get_all_resource_states()
        for s in states:
            assert s["base_abundance"] == 1.0
            assert s["current_abundance"] == 1.0
            assert s["depletion_mult"] == 1.0

class TestResourceTracking(TradeTestCase):
    """Test resource state tracking."""

    def test_get_resource_abundance(self):
        initialize_resource_states()
        abundance = get_resource_abundance("overworld", "minecraft:iron_ore")
        assert abundance == 1.0

    def test_scarcity_detection(self):
        initialize_resource_states()
        assert not is_resource_scarce("overworld", "minecraft:iron_ore")
        update_resource_abundance("overworld", "minecraft:iron_ore", 0.3)
        assert is_resource_scarce("overworld", "minecraft:iron_ore")

    def test_price_multiplier_with_depletion(self):
        initialize_resource_states()
        mult = get_region_price_multiplier("overworld", "minecraft:iron_ore")
        assert mult == 1.0

        update_resource_abundance("overworld", "minecraft:iron_ore", 0.2)
        mult = get_region_price_multiplier("overworld", "minecraft:iron_ore")
        assert mult > 1.0

class TestEventTriggering(TradeTestCase):
    """Test world event triggering."""

    def test_events_can_trigger(self):
        initialize_resource_states()
        result = check_and_trigger_events()
        assert "triggered" in result
        # May or may not trigger (random), but should not error

    def test_many_checks_not_crash(self):
        initialize_resource_states()
        for _ in range(100):
            result = check_and_trigger_events()
            assert "triggered" in result

class TestEventTick(TradeTestCase):
    """Test world event tick processing."""

    def test_tick_no_crashes(self):
        initialize_resource_states()
        result = process_world_events_tick()
        assert isinstance(result, dict)
        assert "events_expired" in result
        assert "resources_regenerated" in result
