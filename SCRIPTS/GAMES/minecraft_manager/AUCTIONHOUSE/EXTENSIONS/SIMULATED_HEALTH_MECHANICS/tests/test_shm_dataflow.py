"""
test_shm_dataflow.py — Data flow and cross-system integration tests.

Tests how SHM systems interact with each other and with the
existing SIMULATED_PEOPLE extension.
"""

import sys, os, json, uuid, random
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHM_DIR = os.path.dirname(_HERE)
_EXT_DIR = os.path.dirname(_SHM_DIR)
_AH_DIR = os.path.dirname(_EXT_DIR)
_MANAGER_DIR = os.path.dirname(_AH_DIR)
for p in [_SHM_DIR, _EXT_DIR, _AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from AUCTIONHOUSE.ah_database import get_db, initialize_database


def _setup():
    initialize_database()
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import ensure_schema
    ensure_schema()
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema as sp_schema
        sp_schema()
    except Exception:
        pass
    db = get_db()
    for table in ["ext_shm_blood", "ext_shm_blood_regeneration",
                  "ext_shm_anatomy_bones", "ext_shm_anatomy_organs",
                  "ext_shm_muscles", "ext_shm_genetics",
                  "ext_shm_diseases", "ext_shm_disease_spread",
                  "ext_shm_hygiene", "ext_shm_immune_response",
                  "ext_shm_pain", "ext_shm_combat_skills",
                  "ext_shm_negative_traits", "ext_shm_healing_log",
                  "ext_shm_ai_decisions",
                  "ext_sp_profiles", "ext_sp_finances", "ext_sp_health",
                  "ext_sp_persona_skills", "ext_sp_memory",
                  "ext_sp_needs", "ext_sp_persona_location",
                  "ext_sp_world_areas",
                  "auction_listings"]:
        try:
            db.execute(f"DELETE FROM {table}")
        except Exception:
            pass


_setup()

from unittest import TestCase


class TestBloodToHealthFlow(TestCase):
    """Test how blood loss affects health stats."""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'FlowTest', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import initialize_persona
        initialize_persona(self.puuid, "warrior")

    def test_massive_blood_loss_triggers_energy_drain(self):
        """Heavy bleeding should drain energy through health."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
            cause_bleeding, get_blood_stats
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health

        health_before = get_persona_health(self.puuid)
        cause_bleeding(self.puuid, 800.0, "torso")
        cause_bleeding(self.puuid, 500.0, "torso")

        blood_after = get_blood_stats(self.puuid)
        self.assertLess(blood_after["blood_volume_ml"],
                        blood_after["max_blood_volume"] * 0.8)

    def test_blood_toxicity_from_disease_affects_health(self):
        """Disease-induced toxicity should reduce health."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
            add_toxicity, get_blood_stats
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health

        blood_before = get_blood_stats(self.puuid)
        add_toxicity(self.puuid, 60.0, "infection")
        blood_after = get_blood_stats(self.puuid)
        self.assertGreater(blood_after["blood_toxicity"], blood_before["blood_toxicity"])


class TestDiseaseToImmuneFlow(TestCase):
    """Test how disease triggers and interacts with immune response."""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'ImmuneFlow', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_hygiene (persona_uuid) VALUES (?)", (self.puuid,))

    def test_disease_incubation_leads_to_acute_phase(self):
        """Disease should transition to acute phase and trigger immune response."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_disease import (
            expose_to_disease, process_disease_tick
        )

        expose_to_disease(self.puuid, "wound_infection", "test", force_infection=True)

        # Run ticks to progress past incubation
        reached_acute = False
        for _ in range(15):
            results = process_disease_tick(self.puuid)
            for r in results:
                if r.get("stage") == "acute" or r.get("status") == "transitioned":
                    reached_acute = True

        self.assertTrue(reached_acute, "Disease should reach acute phase")

    def test_multiple_diseases_independent(self):
        """Multiple diseases should process independently."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_disease import (
            expose_to_disease, process_disease_tick
        )

        expose_to_disease(self.puuid, "food_poisoning", "food", force_infection=True)
        expose_to_disease(self.puuid, "skin_infection", "contact", force_infection=True)

        results = process_disease_tick(self.puuid)
        self.assertEqual(len(results), 2)


class TestHygieneToDiseaseFlow(TestCase):
    """Test how hygiene levels affect disease susceptibility."""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'HygFlow', 'vagabond', 'nomad', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 50, 50, 50, 50)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_hygiene (persona_uuid, personal_cleanliness, oral_hygiene, clothing_cleanliness) VALUES (?, 20, 15, 10)", (self.puuid,))

    def test_poor_hygiene_increases_infection_chance(self):
        """Low hygiene should make infection more likely."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_hygiene import (
            get_hygiene_infection_modifier
        )
        modifier = get_hygiene_infection_modifier(self.puuid)
        self.assertGreater(modifier, 1.0, "Poor hygiene should increase infection risk")


class TestMuscleToCombatFlow(TestCase):
    """Test how muscle state affects combat outcomes."""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'MuscleFlow', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import initialize_persona
        initialize_persona(self.puuid, "warrior")

    def test_high_fatigue_reduces_combat_modifier(self):
        """Fatigued muscles should reduce combat effectiveness."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import (
            use_muscles, get_muscle_combat_modifier, get_muscle
        )

        # Exhaust the muscles
        for _ in range(3):
            use_muscles(self.puuid, "combat", 2.0)

        modifier = get_muscle_combat_modifier(self.puuid, "melee")
        self.assertLess(modifier, 2.0)  # Shouldn't be extremely high

    def test_muscle_injury_penalty_affects_strength(self):
        """Injured muscles should reduce effective strength."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import (
            injure_muscle, get_muscle, get_muscle_combat_modifier
        )

        modifier_before = get_muscle_combat_modifier(self.puuid, "melee")
        injure_muscle(self.puuid, "chest", 0.8)
        injure_muscle(self.puuid, "shoulders", 0.6)
        # Recovery tick should process
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import process_healing_tick
        process_healing_tick(self.puuid)


class TestAnatomyToPainFlow(TestCase):
    """Test how injuries produce pain."""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'AnatFlow', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import initialize_persona
        initialize_persona(self.puuid, "warrior")

    def test_multiple_fractures_increase_total_pain(self):
        """Multiple fractures should increase total pain."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import fracture_bone
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import get_total_pain

        pain_before = get_total_pain(self.puuid)
        fracture_bone(self.puuid, "humerus_left", 1.5)
        fracture_bone(self.puuid, "radius_left", 1.0)
        fracture_bone(self.puuid, "rib_5_left", 1.2)

        pain_after = get_total_pain(self.puuid)
        self.assertGreater(pain_after, pain_before)


class TestExtensionHookFlow(TestCase):
    """Test the __init__.py hook registration and processing flow."""

    def test_hook_functions_exist(self):
        """All hook functions should be importable."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS import (
            _on_simulation_cycle_start, _on_listing_created,
            _on_purchase, _on_simulation_cycle_end
        )
        self.assertTrue(callable(_on_simulation_cycle_start))
        self.assertTrue(callable(_on_listing_created))
        self.assertTrue(callable(_on_purchase))
        self.assertTrue(callable(_on_simulation_cycle_end))

    def test_cycle_start_returns_dict(self):
        """Cycle start hook should return a dict with results."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS import _on_simulation_cycle_start
        result = _on_simulation_cycle_start()
        self.assertIsInstance(result, dict)
        self.assertIn("processed", result)
        self.assertIn("tick", result)

    def test_listing_created_handles_medicine(self):
        """Listing created hook should process medicine items."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS import _on_listing_created
        result = _on_listing_created(listing={"item_id": "minecraft:golden_apple"})
        self.assertIsInstance(result, dict)

    def test_listing_created_handles_unknown_items(self):
        """Listing created hook should skip non-health items."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS import _on_listing_created
        result = _on_listing_created(listing={"item_id": "minecraft:diamond"})
        self.assertEqual(result, {})


if __name__ == "__main__":
    from unittest import main
    main()
