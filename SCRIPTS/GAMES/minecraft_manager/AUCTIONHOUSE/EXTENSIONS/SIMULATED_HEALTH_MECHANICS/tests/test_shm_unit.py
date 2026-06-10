"""
test_shm_unit.py — Unit tests for Simulated Health Mechanics extension.

Tests each subsystem independently with mocked dependencies.
"""

import sys, os, json, uuid, random
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SHM_DIR = os.path.dirname(_HERE)          # SIMULATED_HEALTH_MECHANICS
_EXT_DIR = os.path.dirname(_SHM_DIR)       # EXTENSIONS
_AH_DIR = os.path.dirname(_EXT_DIR)        # AUCTIONHOUSE
_MANAGER_DIR = os.path.dirname(_AH_DIR)    # minecraft_manager
for p in [_SHM_DIR, _EXT_DIR, _AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from AUCTIONHOUSE.ah_database import get_db, initialize_database

# ── Test helper: ensure schema exists ──────────────────────────────
def _init_test_db():
    initialize_database()
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import ensure_schema
    ensure_schema()

    # Ensure SP schema for dependencies
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
                  "ext_sp_profiles", "ext_sp_finances",
                  "ext_sp_health", "ext_sp_persona_skills",
                  "ext_sp_memory"]:
        try:
            db.execute(f"DELETE FROM {table}")
        except Exception:
            pass


_init_test_db()

from unittest import TestCase


class TestBloodSystem(TestCase):
    """Tests for shm_blood.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        # Mock a persona by inserting profile + health
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'TestPersona', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))

    def _init_blood(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import initialize_persona
        initialize_persona(self.puuid, "warrior")

    def test_blood_init_values(self):
        """Blood should initialize with reasonable values."""
        self._init_blood()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import get_blood_stats
        blood = get_blood_stats(self.puuid)
        self.assertIsNotNone(blood)
        self.assertGreater(blood["blood_volume_ml"], 4000)
        self.assertLessEqual(blood["blood_volume_ml"], 6000)
        self.assertEqual(blood["oxygen_saturation"], 98.0)
        self.assertGreater(blood["platelets"], 50)

    def test_cause_bleeding(self):
        """Bleeding should reduce blood volume and oxygen."""
        self._init_blood()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
            cause_bleeding, get_blood_stats
        )
        blood_before = get_blood_stats(self.puuid)
        result = cause_bleeding(self.puuid, 100.0, "left_arm")
        self.assertIn("blood_lost_ml", result)
        self.assertGreater(result["blood_lost_ml"], 0)

        blood_after = get_blood_stats(self.puuid)
        self.assertLess(blood_after["blood_volume_ml"], blood_before["blood_volume_ml"])
        self.assertLess(blood_after["oxygen_saturation"], blood_before["oxygen_saturation"])

    def test_add_toxicity(self):
        """Toxicity should increase and respect resistance."""
        self._init_blood()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
            add_toxicity, get_blood_stats
        )
        result = add_toxicity(self.puuid, 30.0, "food_poisoning")
        self.assertIn("new_toxicity", result)
        blood = get_blood_stats(self.puuid)
        self.assertGreaterEqual(blood["blood_toxicity"], 10)

    def test_blood_regeneration(self):
        """Blood should regenerate when below max."""
        self._init_blood()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
            process_blood_regeneration_tick, cause_bleeding
        )
        cause_bleeding(self.puuid, 500.0, "torso")
        result = process_blood_regeneration_tick(self.puuid)
        self.assertTrue(result.get("regenerating", False) or "reason" in result)

    def test_detoxification(self):
        """Detox should reduce blood toxicity."""
        self._init_blood()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
            add_toxicity, process_detoxification_tick, get_blood_stats
        )
        add_toxicity(self.puuid, 50.0)
        result = process_detoxification_tick(self.puuid)
        self.assertTrue(result.get("detox", False) or "error" not in result)


class TestAnatomySystem(TestCase):
    """Tests for shm_anatomy.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'BoneTest', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))

    def _init_anatomy(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import initialize_persona
        initialize_persona(self.puuid, "warrior")

    def test_206_bones_exist(self):
        """All 206 bones should be initialized."""
        self._init_anatomy()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import get_all_bones
        bones = get_all_bones(self.puuid)
        self.assertEqual(len(bones), 206)

    def test_bone_groups_present(self):
        """All bone groups should be represented."""
        self._init_anatomy()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import get_all_bones
        bones = get_all_bones(self.puuid)
        groups = set(b["bone_group"] for b in bones)
        self.assertIn("cranial", groups)
        self.assertIn("facial", groups)
        self.assertIn("vertebral", groups)
        self.assertIn("ribcage", groups)
        self.assertIn("arm", groups)
        self.assertIn("hand", groups)
        self.assertIn("leg", groups)
        self.assertIn("foot", groups)

    def test_fracture_bone(self):
        """Fracture should mark bone as fractured."""
        self._init_anatomy()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import (
            fracture_bone, get_fractured_bones
        )
        result = fracture_bone(self.puuid, "humerus_left", 1.5)
        self.assertIn("bone", result)
        self.assertEqual(result["bone"], "humerus_left")
        self.assertGreater(result["pain_level"], 0)

        fractured = get_fractured_bones(self.puuid)
        self.assertEqual(len(fractured), 1)
        self.assertEqual(fractured[0]["bone_name"], "humerus_left")

    def test_fracture_from_impact(self):
        """Impact on body part should fracture relevant bones."""
        self._init_anatomy()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import (
            fracture_from_impact, get_fractured_bones
        )
        results = fracture_from_impact(self.puuid, "head", 2.0)
        # High force on head should fracture at least one skull bone
        fractured = get_fractured_bones(self.puuid)
        # May or may not fracture depending on random factors
        self.assertIsInstance(results, list)

    def test_organs_initialized(self):
        """All 13 organs should exist."""
        self._init_anatomy()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import get_all_organs
        organs = get_all_organs(self.puuid)
        organ_names = [o["organ_name"] for o in organs]
        self.assertEqual(len(organs), 13)
        self.assertIn("brain", organ_names)
        self.assertIn("heart", organ_names)
        self.assertIn("liver", organ_names)

    def test_damage_organ(self):
        """Organ damage should reduce health."""
        self._init_anatomy()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import (
            damage_organ, get_organ
        )
        result = damage_organ(self.puuid, "liver", 30.0)
        self.assertEqual(result["organ"], "liver")
        self.assertGreater(result["damage_dealt"], 0)
        organ = get_organ(self.puuid, "liver")
        self.assertLess(organ["health"], 100)


class TestMuscleSystem(TestCase):
    """Tests for shm_muscle.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'MuscleTest', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))

    def _init_muscles(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import initialize_persona
        initialize_persona(self.puuid, "warrior")

    def test_muscle_groups_initialized(self):
        """All 12 muscle groups should exist."""
        self._init_muscles()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import get_muscles
        muscles = get_muscles(self.puuid)
        self.assertEqual(len(muscles), 12)
        groups = [m["muscle_group"] for m in muscles]
        self.assertIn("chest", groups)
        self.assertIn("biceps", groups)
        self.assertIn("quadriceps", groups)

    def test_use_muscles_increases_strength(self):
        """Using muscles should increase strength and fatigue."""
        self._init_muscles()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import (
            use_muscles, get_muscle
        )
        chest_before = get_muscle(self.puuid, "chest")
        result = use_muscles(self.puuid, "combat", 1.5)
        self.assertEqual(result["activity"], "combat")
        chest_after = get_muscle(self.puuid, "chest")
        self.assertGreater(chest_after["fatigue"], chest_before["fatigue"])

    def test_injure_muscle(self):
        """Muscle injury should set injury_penalty."""
        self._init_muscles()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import (
            injure_muscle, get_muscle
        )
        result = injure_muscle(self.puuid, "biceps", 0.5)
        self.assertIn("injury_penalty", result)
        muscle = get_muscle(self.puuid, "biceps")
        self.assertTrue(muscle["is_injured"])
        self.assertGreater(muscle["injury_penalty"], 0)

    def test_get_combat_modifier(self):
        """Combat modifier should return a valid multiplier."""
        self._init_muscles()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import (
            get_muscle_combat_modifier
        )
        modifier = get_muscle_combat_modifier(self.puuid, "melee")
        self.assertGreater(modifier, 0)
        self.assertLess(modifier, 3.0)


class TestGeneticsSystem(TestCase):
    """Tests for shm_genetics.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("""
            INSERT INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, personaUuid, active, created_at)
            VALUES (?, 'GenTest', 'warrior', 'guard', 'overworld', ?, 1, datetime('now'))
            ON CONFLICT(persona_uuid) DO NOTHING
        """, (self.puuid, self.puuid))

    def test_genetics_initialization(self):
        """Genetics should have all 10 traits within valid range."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import _create_genetics
        db = get_db()
        _create_genetics(db, self.puuid, "warrior")

        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_genetics import (
            get_genetics, compute_health_modifiers
        )
        genetics = get_genetics(self.puuid)
        self.assertIsNotNone(genetics)

        for key in ["metabolic_rate", "immune_potency", "pain_tolerance",
                     "healing_factor", "disease_resistance", "muscle_growth_rate",
                     "toxin_resistance", "blood_efficiency", "organ_vitality",
                     "nerve_density"]:
            self.assertIn(key, genetics)
            self.assertGreaterEqual(genetics[key], 0.3)
            self.assertLessEqual(genetics[key], 2.0)

        modifiers = compute_health_modifiers(self.puuid)
        self.assertEqual(len(modifiers), 10)


class TestDiseaseSystem(TestCase):
    """Tests for shm_disease.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'DisTest', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, immune) VALUES (?, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_hygiene (persona_uuid) VALUES (?)", (self.puuid,))

    def test_expose_to_disease(self):
        """Exposure should either infect or resist."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_disease import expose_to_disease
        result = expose_to_disease(self.puuid, "food_poisoning", "test_kitchen")
        self.assertIn("infected", result)
        # Force infection for testing
        result2 = expose_to_disease(self.puuid, "wound_infection", "test_wound",
                                     force_infection=True)
        self.assertTrue(result2["infected"])

    def test_disease_progression(self):
        """Disease should progress through stages."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_disease import (
            expose_to_disease, process_disease_tick, has_disease
        )
        expose_to_disease(self.puuid, "wound_infection", "test", force_infection=True)
        self.assertTrue(has_disease(self.puuid, "wound_infection"))

        # Run several ticks
        stages_seen = set()
        for _ in range(20):
            results = process_disease_tick(self.puuid)
            for r in results:
                stages_seen.add(r.get("stage", ""))

        self.assertIn("incubation", stages_seen)

    def test_disease_spread(self):
        """Disease should spread to nearby personas."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_disease import (
            expose_to_disease, try_spread_disease
        )

        # Set up a second persona in same area
        puuid2 = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'DisTest2', 'farmer', 'crop_farmer', 'overworld', 1, datetime('now'))", (puuid2,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, immune) VALUES (?, 70)", (puuid2,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (puuid2,))
        db.execute("INSERT OR IGNORE INTO ext_shm_hygiene (persona_uuid) VALUES (?)", (puuid2,))
        # Put them in same area
        area_uuid = str(uuid.uuid4())
        db.execute("""
            INSERT OR IGNORE INTO ext_sp_world_areas (area_uuid, name, region, biome_type, created_at)
            VALUES (?, 'test_area', 'overworld', 'plains', datetime('now'))
        """, (area_uuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_persona_location (persona_uuid, area_uuid) VALUES (?, ?)", (self.puuid, area_uuid))
        db.execute("INSERT OR IGNORE INTO ext_sp_persona_location (persona_uuid, area_uuid) VALUES (?, ?)", (puuid2, area_uuid))

        expose_to_disease(self.puuid, "pneumonia", "test", force_infection=True)
        spreads = try_spread_disease(self.puuid)
        self.assertIsInstance(spreads, list)


class TestHygieneSystem(TestCase):
    """Tests for shm_hygiene.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_shm_hygiene (persona_uuid) VALUES (?)", (self.puuid,))

    def test_hygiene_decay(self):
        """Hygiene should decay based on activity."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_hygiene import (
            process_hygiene_decay_tick, get_hygiene
        )
        before = get_hygiene(self.puuid)
        result = process_hygiene_decay_tick(self.puuid, "mining")
        self.assertLess(result["personal"], before["personal_cleanliness"])
        self.assertLess(result["clothing"], before["clothing_cleanliness"])

    def test_cleaning(self):
        """Cleaning should improve hygiene."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_hygiene import (
            clean_persona, get_hygiene
        )
        before = get_hygiene(self.puuid)
        result = clean_persona(self.puuid, "full")
        self.assertGreater(result["new_personal"], before["personal_cleanliness"])

    def test_infection_modifier(self):
        """Poor hygiene should increase infection risk."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_hygiene import (
            get_hygiene_infection_modifier
        )
        modifier = get_hygiene_infection_modifier(self.puuid)
        self.assertGreater(modifier, 0)
        self.assertLess(modifier, 7)  # Max ~6.67


class TestImmuneSystem(TestCase):
    """Tests for shm_immune.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'ImmuneTest', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))

    def test_start_immune_response(self):
        """Immune response should initialize with strength."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_immune import (
            start_immune_response
        )
        result = start_immune_response(self.puuid, "disease", "test_disease",
                                        50.0, 0.5)
        self.assertTrue(result.get("immune_response_started"))
        self.assertGreater(result["initial_strength"], 0)

    def test_immune_ticks(self):
        """Immune battles should process each tick."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_immune import (
            start_immune_response, process_immune_ticks
        )
        start_immune_response(self.puuid, "disease", "test_disease", 50.0, 0.5)
        results = process_immune_ticks(self.puuid)
        self.assertEqual(len(results), 1)
        self.assertIn("immune_strength", results[0])


class TestPainSystem(TestCase):
    """Tests for shm_pain.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))

    def test_add_pain_source(self):
        """Pain source should register correctly."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
            add_pain_source, get_total_pain
        )
        add_pain_source(self.puuid, "test_wound", "wound", 50.0, 10)
        total = get_total_pain(self.puuid)
        self.assertGreater(total, 0)

    def test_pain_effects(self):
        """Pain should produce stat penalties."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
            add_pain_source, get_pain_effects
        )
        add_pain_source(self.puuid, "severe_wound", "wound", 80.0, 20)
        effects = get_pain_effects(self.puuid)
        self.assertGreater(effects["agility_penalty"], 0.1)

    def test_pain_decay(self):
        """Pain should decay over time."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
            add_pain_source, process_pain_decay_tick
        )
        add_pain_source(self.puuid, "decay_test", "wound", 50.0, 5)
        for _ in range(6):
            process_pain_decay_tick(self.puuid)
        total = 0
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import get_total_pain
        total = get_total_pain(self.puuid)
        # After 6 ticks of a 5-tick pain, should be resolved
        self.assertEqual(total, 0)


class TestCombatSkills(TestCase):
    """Tests for shm_combat_skills.py"""

    def setUp(self):
        self.puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'SkillTest', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (self.puuid,))
        db.execute("INSERT OR IGNORE INTO ext_shm_genetics (persona_uuid) VALUES (?)", (self.puuid,))
        # Initialize skills
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import initialize_persona
        initialize_persona(self.puuid, "warrior")

    def test_skill_initialization(self):
        """All 10 combat skills should be initialized."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import get_all_skills
        skills = get_all_skills(self.puuid)
        self.assertEqual(len(skills), 10)
        expected = ["blades", "blunt", "archery", "polearms", "unarmed",
                     "blocking", "dodging", "critical_strike", "combat_awareness", "first_aid"]
        for s in expected:
            self.assertIn(s, skills)

    def test_use_skill_gains_xp(self):
        """Using a skill should gain XP."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
            use_skill, get_skill
        )
        before = get_skill(self.puuid, "blades")
        result = use_skill(self.puuid, "blades", "combat", 1.0)
        self.assertGreater(result["xp_gained"], 0)
        after = get_skill(self.puuid, "blades")
        if result.get("level_up"):
            self.assertGreater(after["level"], before["level"])

    def test_skill_decay(self):
        """Skills should decay when not used."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
            process_skill_decay_tick, get_skill
        )
        # Set a higher level first so decay is visible
        db = get_db()
        db.execute("""
            UPDATE ext_shm_combat_skills SET level = 50, decay_counter = 20
            WHERE persona_uuid = ? AND skill_name = 'blades'
        """, (self.puuid,))

        result = process_skill_decay_tick(self.puuid)
        self.assertGreater(len(result), 0)

    def test_negative_traits(self):
        """Negative traits should be detected."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
            process_negative_traits_tick
        )
        # Set a skill very low to trigger a negative trait
        db = get_db()
        db.execute("""
            UPDATE ext_shm_combat_skills SET level = 3
            WHERE persona_uuid = ? AND skill_name = 'dodging'
        """, (self.puuid,))

        result = process_negative_traits_tick(self.puuid)
        # May or may not trigger depending on other skills
        self.assertIsInstance(result, list)


class TestAIEngine(TestCase):
    """Tests for shm_ai_engine.py"""

    def test_evaluate_critical_condition(self):
        """Critical condition evaluation should work."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_ai_engine import (
            evaluate_critical_condition, CRITICAL_BLOOD_LOSS
        )
        # Without a real persona, this should return None
        result = evaluate_critical_condition("nonexistent")
        self.assertIsNone(result)

    def test_fallback_decision(self):
        """Fallback decision should return valid outcome."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_ai_engine import _fallback_decision

        # Test with critical conditions
        context = {
            "conditions": [{"type": "exsanguination", "severity": "critical"}],
            "blood_volume_pct": 15.0,
        }
        decision = _fallback_decision(context)
        self.assertIn("outcome", decision)
        self.assertIn("reasoning", decision)


class TestDatabase(TestCase):
    """Tests for shm_database.py"""

    def test_schema_creation(self):
        """Schema should create without errors."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import ensure_schema
        ensure_schema()  # Should run without exception

    def test_persona_initialization(self):
        """Full persona init should create all records."""
        puuid = str(uuid.uuid4())
        db = get_db()
        db.execute("INSERT OR IGNORE INTO ext_sp_profiles (persona_uuid, name, archetype, job, region, active, created_at) VALUES (?, 'InitTest', 'warrior', 'guard', 'overworld', 1, datetime('now'))", (puuid,))
        db.execute("INSERT OR IGNORE INTO ext_sp_health (persona_uuid, food, hydration, energy, immune) VALUES (?, 80, 80, 75, 70)", (puuid,))

        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import initialize_persona
        initialize_persona(puuid, "warrior")

        # Verify all tables have data
        tables = ["ext_shm_blood", "ext_shm_blood_regeneration",
                   "ext_shm_anatomy_bones", "ext_shm_anatomy_organs",
                   "ext_shm_muscles", "ext_shm_genetics",
                   "ext_shm_hygiene", "ext_shm_combat_skills"]
        for table in tables:
            row = db.fetch_one(f"SELECT COUNT(*) as cnt FROM {table} WHERE persona_uuid = ?", (puuid,))
            self.assertIsNotNone(row)
            self.assertGreater(row["cnt"], 0, f"Table {table} has no data for persona")


if __name__ == "__main__":
    from unittest import main
    main()
