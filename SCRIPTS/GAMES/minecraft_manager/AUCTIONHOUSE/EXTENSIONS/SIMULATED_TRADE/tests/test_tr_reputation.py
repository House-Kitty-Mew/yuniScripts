
"""
test_tr_reputation.py — Tests for reputation and law enforcement.

Tests: initialization, notoriety decay, bounty management,
jail system, price modifiers, guard hostility detection.
"""

import unittest
from conftest import TradeTestCase
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_reputation import (
    init_persona_reputation, get_persona_reputation,
    apply_crime_consequences, clear_bounty,
    process_reputation_tick, get_price_modifier, is_guard_hostile,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    ensure_reputation, get_reputation,
    update_notoriety, update_bounty, update_reputation_score,
    set_jail_ticks, is_persona_jailed,
)

class TestReputationInit(TradeTestCase):
    """Test reputation initialization."""

    def test_init_new_persona(self):
        result = init_persona_reputation("new-persona-uuid")
        assert result.get("ok"), f"Init failed: {result}"
        assert "reputation" in result

    def test_init_does_not_overwrite(self):
        init_persona_reputation("alice-uuid")
        rep = get_reputation("alice-uuid")
        assert rep is not None

class TestReputationQuery(TradeTestCase):
    """Test reputation querying."""

    def test_get_reputation_new(self):
        rep = get_persona_reputation("nonexistent")
        assert rep.get("reputation_score") == 0

    def test_get_reputation_existing(self):
        ensure_reputation("alice-uuid")
        rep = get_persona_reputation("alice-uuid")
        assert "reputation_score" in rep
        assert "in_jail" in rep

class TestCrimeConsequences(TradeTestCase):
    """Test crime consequence application."""

    def test_theft_consequences(self):
        ensure_reputation("alice-uuid")
        result = apply_crime_consequences("alice-uuid", "theft")
        assert result.get("notoriety_gained", 0) > 0
        assert result.get("bounty_added", 0) > 0

        rep = get_reputation("alice-uuid")
        assert rep["notoriety"] >= result["notoriety_gained"]
        assert rep["bounty"] >= result["bounty_added"]

    def test_banditry_consequences(self):
        ensure_reputation("bob-uuid")
        result = apply_crime_consequences("bob-uuid", "banditry")
        assert result.get("notoriety_gained", 0) >= 100
        assert result.get("reputation_lost", 0) < 0

    def test_murder_consequences_severe(self):
        ensure_reputation("charlie-uuid")
        result = apply_crime_consequences("charlie-uuid", "murder")
        assert result.get("notoriety_gained", 0) >= 200

class TestBountyClear(TradeTestCase):
    """Test bounty clearing."""

    def test_clear_bounty_no_funds(self):
        ensure_reputation("alice-uuid")
        update_bounty("alice-uuid", 500.0)

        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
        state = get_state()
        state.set("persona_finances", {
            "alice-uuid": {"balance": 10.0},  # Not enough for 750g bounty clear cost
        }, "SIMULATED_TRADE")

        result = clear_bounty("alice-uuid")
        assert not result.get("ok")

    def test_clear_bounty_success(self):
        ensure_reputation("alice-uuid")
        update_bounty("alice-uuid", 100.0)

        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
        state = get_state()
        state.set("persona_finances", {
            "alice-uuid": {"balance": 500.0},  # Enough for 150g cost
        }, "SIMULATED_TRADE")

        result = clear_bounty("alice-uuid")
        assert result.get("ok"), f"Clear bounty failed: {result}"
        assert result.get("cost", 0) > 0

class TestReputationTick(TradeTestCase):
    """Test reputation tick processing."""

    def test_notoriety_decay(self):
        ensure_reputation("alice-uuid")
        update_notoriety("alice-uuid", 100)

        process_reputation_tick()
        rep = get_reputation("alice-uuid")
        assert rep["notoriety"] < 100, "Notoriety should decay"

    def test_jail_time_reduction(self):
        ensure_reputation("bob-uuid")
        set_jail_ticks("bob-uuid", 10)

        assert is_persona_jailed("bob-uuid")
        process_reputation_tick()
        rep = get_reputation("bob-uuid")
        assert rep["jail_ticks_remaining"] < 10

class TestPriceModifier(TradeTestCase):
    """Test reputation-based price modifiers."""

    def test_positive_reputation_discount(self):
        ensure_reputation("alice-uuid")
        update_reputation_score("alice-uuid", 500)

        mod = get_price_modifier("alice-uuid")
        assert mod < 1.0, f"Positive rep should give discount, got {mod}"

    def test_negative_reputation_markup(self):
        ensure_reputation("bob-uuid")
        update_reputation_score("bob-uuid", -500)

        mod = get_price_modifier("bob-uuid")
        assert mod > 1.0, f"Negative rep should add markup, got {mod}"

class TestGuardHostility(TradeTestCase):
    """Test guard hostility detection."""

    def test_not_hostile_by_default(self):
        ensure_reputation("alice-uuid")
        assert not is_guard_hostile("alice-uuid")

    def test_hostile_with_low_rep_and_bounty(self):
        ensure_reputation("charlie-uuid")
        update_reputation_score("charlie-uuid", -300)
        update_bounty("charlie-uuid", 100.0)

        assert is_guard_hostile("charlie-uuid")
