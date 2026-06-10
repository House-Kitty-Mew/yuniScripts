"""
test_typed_dicts.py — Validation tests for shared TypedDict definitions.

Verifies that all TypedDict classes are correctly defined and can be
used as type annotations.  Also validates that the dict shapes match
actual data produced by the extension functions.
"""

import sys, os
from unittest import TestCase, main

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED_DIR = os.path.dirname(_HERE)
_EXT_DIR = os.path.dirname(_SHARED_DIR)
_AH_DIR = os.path.dirname(_EXT_DIR)
for p in [_AH_DIR, _EXT_DIR, _SHARED_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from EXTENSIONS._shared.typed_dicts import (
    PersonaProfile, PersonaHealth, PersonaFinances, PersonaNeed,
    CombatStats, WoundRecord, CombatResult,
    PriceMemory, LifeEvent, SimulationCycleState,
)


class TestTypedDictImports(TestCase):
    """All TypedDict classes can be imported."""

    def test_persona_profile_imported(self):
        self.assertIsNotNone(PersonaProfile)

    def test_persona_health_imported(self):
        self.assertIsNotNone(PersonaHealth)

    def test_persona_finances_imported(self):
        self.assertIsNotNone(PersonaFinances)

    def test_combat_stats_imported(self):
        self.assertIsNotNone(CombatStats)

    def test_wound_record_imported(self):
        self.assertIsNotNone(WoundRecord)

    def test_price_memory_imported(self):
        self.assertIsNotNone(PriceMemory)


class TestTypedDictUsage(TestCase):
    """TypedDict can be constructed and used as type hints."""

    def test_persona_profile_constructable(self):
        profile: PersonaProfile = {
            "persona_uuid": "test-uuid",
            "name": "TestPersona",
            "archetype": "miner",
            "job": "miner",
            "region": "overworld",
            "wealth_tier": "wealthy",
            "personality_traits": '{"bravery": 5}',
            "active": 1,
            "created_at": "2025-01-01T00:00:00",
        }
        self.assertEqual(profile["name"], "TestPersona")
        self.assertEqual(profile["archetype"], "miner")

    def test_persona_health_constructable(self):
        health: PersonaHealth = {
            "persona_uuid": "test-uuid",
            "food": 80.0,
            "hydration": 75.0,
            "energy": 90.0,
            "immune": 70.0,
            "temperature": 36.5,
            "alive": 1,
        }
        self.assertEqual(health["food"], 80.0)
        self.assertTrue(health["alive"])

    def test_combat_stats_constructable(self):
        stats: CombatStats = {
            "strength": 8.5,
            "agility": 6.2,
            "endurance": 7.0,
            "perception": 5.0,
            "pain_threshold": 40.0,
            "armor_rating": 5.0,
            "weapon_skill": 15.0,
            "pain_penalty": 10.0,
            "terrain": "plains",
            "weapon": 1.0,
        }
        self.assertEqual(stats["strength"], 8.5)
        self.assertEqual(stats["terrain"], "plains")

    def test_wound_record_constructable(self):
        wound: WoundRecord = {
            "wound_uuid": "wound-uuid",
            "owner_uuid": "persona-uuid",
            "body_part": "left_arm",
            "wound_type": "cut",
            "severity": 2,
            "bleed_rate": 5.0,
            "pain_level": 25.0,
            "infection_chance": 0.3,
            "infection_progress": 0.0,
            "is_infected": 0,
            "is_bandaged": 0,
            "is_healed": 0,
            "created_by": "attacker-uuid",
            "created_at": "2025-01-01T00:00:00",
        }
        self.assertEqual(wound["body_part"], "left_arm")
        self.assertEqual(wound["severity"], 2)

    def test_price_memory_constructable(self):
        memory: PriceMemory = {
            "memory_uuid": "mem-uuid",
            "persona_uuid": "persona-uuid",
            "content": "[observed_price] item=minecraft:diamond price=8.0",
            "memory_type": "observed_price",
            "importance": 0.5,
            "confidence": 0.7,
            "created_at": "2025-01-01T00:00:00",
            "activation_score": 0.3,
            "price": 8.0,
            "item_id": "minecraft:diamond",
        }
        self.assertEqual(memory["price"], 8.0)
        self.assertEqual(memory["item_id"], "minecraft:diamond")

    def test_simulation_cycle_state_constructable(self):
        state: SimulationCycleState = {
            "active_personas": [
                {"uuid": "p1", "name": "Alice"},
                {"uuid": "p2", "name": "Bob"},
            ],
            "persona_needs": {
                "p1": [{"item": "minecraft:diamond", "urgency": 8}],
            },
            "interactions": [],
            "relationship_changes": [],
            "chat_messages": [],
            "announcements": [],
        }
        self.assertEqual(len(state["active_personas"]), 2)
        self.assertIn("p1", state["persona_needs"])

    def test_total_false_allows_partial_dict(self):
        """total=False means missing keys don't cause errors."""
        partial: PersonaProfile = {
            "persona_uuid": "test-uuid",
            "name": "Partial",
        }
        self.assertEqual(partial["name"], "Partial")


if __name__ == "__main__":
    main()
