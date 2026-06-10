"""
tests/test_simulated_relationships.py — Comprehensive unit tests for
the SIMULATED_RELATIONSHIPS Auction House extension.

Uses unittest (no external dependencies).

Run: python3 -m unittest tests.test_simulated_relationships -v
"""

import os, sys, json, time, math, random, unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# ── Project setup ────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager"))
sys.path.insert(0, str(_PROJECT_ROOT))

TEST_DB_DIR = _PROJECT_ROOT / "tests" / "test_data_rel"
TEST_DB_PATH = TEST_DB_DIR / "auctionhouse.db3"

# Persona test fixtures (module-level for reuse)
FARMER = {
    "persona_uuid": "test-farmer-001",
    "name": "Greta Greenfield", "archetype": "farmer",
    "job": "wheat_farmer", "region": "overworld", "wealth_tier": "middle", "active": 1,
    "skills": {"mining": 15, "combat": 12, "farming": 65, "trading": 30,
               "crafting": 20, "exploration": 18, "leadership": 22},
    "traits": {"aggression": 2, "generosity": 7, "curiosity": 4,
               "sociability": 5, "cautiousness": 6, "territoriality": 6,
               "honor": 6, "impulsiveness": 2},
    "health": {"food": 75, "hydration": 80, "energy": 70, "temperature": 50,
               "waste": 10, "hygiene": 60, "immune": 70},
}
WARRIOR = {
    "persona_uuid": "test-warrior-002",
    "name": "Bjorn Ironfist", "archetype": "warrior",
    "job": "mercenary", "region": "overworld", "wealth_tier": "working", "active": 1,
    "skills": {"mining": 20, "combat": 72, "farming": 10, "trading": 15,
               "crafting": 18, "exploration": 35, "leadership": 45},
    "traits": {"aggression": 8, "generosity": 5, "curiosity": 5,
               "sociability": 5, "cautiousness": 4, "territoriality": 7,
               "honor": 8, "impulsiveness": 6},
    "health": {"food": 85, "hydration": 70, "energy": 85, "temperature": 55,
               "waste": 5, "hygiene": 40, "immune": 80},
}
MERCHANT = {
    "persona_uuid": "test-merchant-003",
    "name": "Silas Coinworth", "archetype": "merchant",
    "job": "trader", "region": "overworld", "wealth_tier": "wealthy", "active": 1,
    "skills": {"mining": 8, "combat": 10, "farming": 8, "trading": 75,
               "crafting": 25, "exploration": 15, "leadership": 35},
    "traits": {"aggression": 2, "generosity": 3, "curiosity": 6,
               "sociability": 8, "cautiousness": 7, "territoriality": 3,
               "honor": 4, "impulsiveness": 2},
    "health": {"food": 80, "hydration": 85, "energy": 75, "temperature": 52,
               "waste": 5, "hygiene": 80, "immune": 75},
}
AREA_PLAINS = {"name": "Golden Fields", "type": "plains", "region": "overworld"}
AREA_MOUNTAINS = {"name": "Stonejaw Peaks", "type": "mountains", "region": "overworld"}


def setup_test_db():
    """Initialize test database with required schemas and a test persona."""
    TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    import AUCTIONHOUSE.ah_database as ah_db
    # Force the singleton to reconnect to the new DB
    if ah_db._db_instance is not None:
        ah_db._db_instance.close()
        ah_db._db_instance = None
    ah_db.DB_PATH = TEST_DB_PATH
    ah_db.DB_DIR = TEST_DB_DIR
    from AUCTIONHOUSE.ah_database import initialize_database
    try:
        initialize_database(force=False)
    except Exception:
        pass
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema as sp_ensure
    sp_ensure()
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import ensure_schema
    ensure_schema()
    # Seed a test persona for FK constraints
    from AUCTIONHOUSE.ah_database import get_db
    db = get_db()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for puid, name, arch in [("test-rel-persona", "Test Persona", "adventurer"),
                               ("test-skill-persona", "Skill Tester", "farmer"),
                               ("skill-p1", "Skill One", "farmer"),
                               ("drm-persona", "Diminishing", "warrior"),
                               ("summ-p", "Summary", "adventurer"),
                               ("edge-p", "Edge", "miner"),
                               ("zd-a", "ZDelta A", "farmer"),
                               ("zd-b", "ZDelta B", "merchant")]:
        existing = db.fetch_one("SELECT id FROM ext_sp_profiles WHERE persona_uuid = ?", (puid,))
        if not existing:
            db.execute("""
                INSERT INTO ext_sp_profiles
                (persona_uuid, name, archetype, job, region, wealth_tier, 
                 personality_traits, active, created_at)
                VALUES (?, ?, ?, 'tester', 'test', 'middle', '{}', 1, ?)
            """, (puid, name, arch, now))


def teardown_test_db():
    """Clean up test database."""
    import AUCTIONHOUSE.ah_database as ah_db
    # Reset singleton BEFORE deleting so no dangling reference
    if ah_db._db_instance is not None:
        ah_db._db_instance.close()
        ah_db._db_instance = None
    # Reset internal connection too
    if hasattr(ah_db.DatabaseManager, '_local'):
        try:
            if hasattr(ah_db.DatabaseManager._local, 'conn'):
                ah_db.DatabaseManager._local.conn = None
        except AttributeError:
            pass
    try:
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        take = TEST_DB_DIR
        if take.exists():
            for f in take.iterdir():
                if f.is_file():
                    f.unlink()
            take.rmdir()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 1. DATABASE TESTS
# ══════════════════════════════════════════════════════════════════════

class TestDatabase(unittest.TestCase):
    """Test schema creation and CRUD operations."""

    @classmethod
    def setUpClass(cls):
        setup_test_db()
        from AUCTIONHOUSE.ah_database import get_db
        cls.db = get_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_schema_creates_tables(self):
        tables = self.db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ext_rel_%'")
        table_names = {t["name"] for t in tables}
        expected = {"ext_rel_relationships", "ext_rel_situationships",
                     "ext_rel_interactions", "ext_rel_contention_events",
                     "ext_rel_skill_history"}
        self.assertEqual(expected - table_names, set())

    def test_upsert_relationship_creates(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import upsert_relationship
        rel = upsert_relationship("persona-a", "persona-b",
                                   rel_type="friendship", strength_delta=15.0)
        self.assertIsNotNone(rel)
        self.assertIn(rel["relationship_type"], ("friendship", "neutral"))

    def test_upsert_relationship_updates(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            upsert_relationship, get_relationship
        )
        upsert_relationship("a", "b", strength_delta=20.0)
        upsert_relationship("a", "b", strength_delta=-10.0)
        rel = get_relationship("a", "b")
        self.assertIsNotNone(rel)

    def test_list_relationships(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            upsert_relationship, list_relationships
        )
        upsert_relationship("alpha", "beta")
        upsert_relationship("alpha", "gamma")
        rels = list_relationships("alpha")
        self.assertEqual(len(rels), 2)

    def test_get_relationship_direction(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            upsert_relationship, get_relationship_direction
        )
        upsert_relationship("p", "q", rel_type="rivalry", strength_delta=-20.0)
        typ = get_relationship_direction("p", "q")
        self.assertEqual(typ, "rivalry")


# ══════════════════════════════════════════════════════════════════════
# 2. SITUATIONSHIP TESTS
# ══════════════════════════════════════════════════════════════════════

class TestSituationships(unittest.TestCase):
    """Test temporary relationship modifiers."""

    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_create_situationship(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            create_situationship, get_relationship
        )
        sit = create_situationship("a", "b", "temporary_alliance",
                                    duration_hours=24, strength_bonus=20.0)
        self.assertEqual(sit["type"], "temporary_alliance")
        rel = get_relationship("a", "b")
        self.assertIsNotNone(rel)

    def test_get_active_situationships(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            create_situationship, get_active_situationships
        )
        create_situationship("x", "y", "quest_partners", duration_hours=48)
        active = get_active_situationships("x")
        self.assertGreaterEqual(len(active), 1)

    def test_expire_stale_situationships(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            get_active_situationships, expire_stale_situationships
        )
        from AUCTIONHOUSE.ah_database import get_db
        from datetime import datetime, timezone, timedelta
        db = get_db()
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        db.execute("""
            INSERT INTO ext_rel_situationships
            (persona_uuid_a, persona_uuid_b, situationship_type,
             strength_bonus, trust_bonus, intimacy_bonus,
             started_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("exp_a", "exp_b", "test", 10.0, 5.0, 0.0,
              past.isoformat(), past.isoformat()))
        n = expire_stale_situationships()
        self.assertGreaterEqual(n, 1)


# ══════════════════════════════════════════════════════════════════════
# 3. BEHAVIOR TESTS
# ══════════════════════════════════════════════════════════════════════

class TestBehaviors(unittest.TestCase):
    """Test behavior archetypes and compatibility."""

    def test_get_behavior(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import get_behavior
        warrior = get_behavior("warrior")
        self.assertEqual(warrior["traits"]["aggression"], 8)
        self.assertEqual(warrior["conflict_threshold"], 30)

    def test_get_behavior_fallback(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import get_behavior
        default = get_behavior("nonexistent")
        self.assertEqual(default["name"], "Adventurer")

    def test_get_compatibility(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import get_compatibility
        self.assertEqual(get_compatibility("farmer", "farmer"), 70)
        self.assertEqual(get_compatibility("warrior", "mage"), 30)

    def test_compute_interaction_bias(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import compute_interaction_bias
        bias = compute_interaction_bias(FARMER, WARRIOR)
        self.assertGreaterEqual(bias, 0.05)
        self.assertLessEqual(bias, 0.95)

    def test_determine_contention_outcome(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            determine_contention_outcome
        )
        result = determine_contention_outcome([FARMER, MERCHANT], "plains")
        self.assertIn(result["outcome"], ("peaceful_coexistence", "social_gathering",
                                           "negotiation", "conflict", "avoidance"))

    def test_contention_outcome_territorial_resources(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            determine_contention_outcome
        )
        result = determine_contention_outcome([WARRIOR, FARMER], "mountains",
                                               has_resources=True)
        self.assertIn("outcome", result)

    def test_generate_narrative(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            generate_contention_narrative
        )
        n = generate_contention_narrative([FARMER, WARRIOR], "conflict",
                                           "Stonejaw Peaks", "overworld")
        self.assertIsInstance(n, str)
        self.assertGreater(len(n), 20)


# ══════════════════════════════════════════════════════════════════════
# 4. SKILL SYSTEM TESTS
# ══════════════════════════════════════════════════════════════════════

class TestSkills(unittest.TestCase):
    """Test evolving skill system."""

    def test_apply_skill_change(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import apply_skill_change
        setup_test_db()
        result = apply_skill_change("test-skill-persona", "farming", 5.0,
                                     "practice", archetype="farmer")
        self.assertGreater(result["delta_applied"], 0)
        teardown_test_db()

    def test_skill_change_archetype_multiplier(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import apply_skill_change
        setup_test_db()
        farmer_result = apply_skill_change("skill-p1", "farming", 5.0,
                                            "practice", archetype="farmer")
        warrior_result = apply_skill_change("skill-p1", "combat", 5.0,
                                             "practice", archetype="warrior")
        self.assertGreater(farmer_result["delta_applied"], 0)
        self.assertGreater(warrior_result["delta_applied"], 0)
        teardown_test_db()

    def test_diminishing_returns(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            apply_skill_change
        )
        setup_test_db()
        for _ in range(30):
            apply_skill_change("drm-persona", "combat", 10.0, "training",
                                archetype="warrior")
        result = apply_skill_change("drm-persona", "combat", 10.0, "final",
                                     archetype="warrior")
        self.assertLessEqual(result["new_value"], 100)
        teardown_test_db()

    def test_get_skill_summary(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import get_skill_summary
        setup_test_db()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import save_skills
        save_skills("summ-p", {"mining": 50, "combat": 60, "farming": 70,
                                "trading": 40, "crafting": 30, "exploration": 20,
                                "leadership": 10})
        summary = get_skill_summary("summ-p")
        self.assertIn("skills", summary)
        self.assertIn("average", summary)
        teardown_test_db()


# ══════════════════════════════════════════════════════════════════════
# 5. AI RESOLVER TESTS
# ══════════════════════════════════════════════════════════════════════

class TestAIResolver(unittest.TestCase):
    """Test the AI thinking-mode resolver."""

    def test_resolver_creates_reasoning(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=True)
        result = resolver.resolve([FARMER, WARRIOR], AREA_PLAINS)
        self.assertGreaterEqual(len(result["reasoning"]), 5)

    def test_resolver_determines_outcome(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        result = resolver.resolve([FARMER, WARRIOR], AREA_PLAINS)
        self.assertIn(result["outcome"], ("conflict", "negotiation",
                                           "social_gathering", "peaceful_coexistence",
                                           "avoidance"))
        self.assertGreaterEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 1.0)

    def test_resolver_persona_effects(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        result = resolver.resolve([FARMER, WARRIOR], AREA_MOUNTAINS,
                                   has_resources=True)
        self.assertEqual(len(result["persona_effects"]), 2)
        for e in result["persona_effects"]:
            self.assertIn("health_delta", e)
            self.assertIn("wealth_delta", e)

    def test_resolver_relationship_changes(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        result = resolver.resolve([FARMER, WARRIOR], AREA_PLAINS)
        self.assertGreaterEqual(len(result["relationship_changes"]), 1)

    def test_resolver_skill_changes(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        result = resolver.resolve([FARMER, WARRIOR], AREA_MOUNTAINS,
                                   has_resources=True)
        self.assertGreaterEqual(len(result["skill_changes"]), 2)

    def test_narrative_generated(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=True)
        result = resolver.resolve([FARMER, WARRIOR], AREA_PLAINS)
        self.assertIsInstance(result["narrative"], str)
        self.assertGreater(len(result["narrative"]), 10)

    def test_three_persona_resolution(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        result = resolver.resolve([FARMER, WARRIOR, MERCHANT], AREA_MOUNTAINS,
                                   has_resources=True)
        self.assertEqual(len(result["persona_effects"]), 3)

    def test_empty_persona_list(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        result = resolver.resolve([], {"name": "Empty", "type": "void", "region": "void"})
        self.assertIn("outcome", result)


# ══════════════════════════════════════════════════════════════════════
# 6. CONTENTION SYSTEM TESTS
# ══════════════════════════════════════════════════════════════════════

class TestContention(unittest.TestCase):
    """Test the cell contention resolution system."""

    def test_interaction_logging(self):
        setup_test_db()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            log_interaction, get_relationship, get_recent_interactions
        )
        log_interaction("log-a", "log-b", "conflict",
                         outcome={"winner": "log-a"},
                         location_area="TestArea",
                         location_region="test",
                         relationship_delta=-15.0,
                         narrative="Test conflict.")
        rel = get_relationship("log-a", "log-b")
        self.assertIsNotNone(rel)
        self.assertLess(rel["strength"], 55)
        recent = get_recent_interactions("log-a")
        self.assertGreaterEqual(len(recent), 1)
        teardown_test_db()

    def test_contention_event_logging(self):
        setup_test_db()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            log_contention_event
        )
        res = log_contention_event("TestField", ["p1", "p2"], "social_gathering",
                                    resolution="social_gathering",
                                    narrative="They shared stories.")
        self.assertEqual(res["area"], "TestField")
        self.assertEqual(res["resolution"], "social_gathering")
        teardown_test_db()

    def test_skill_history_recording(self):
        setup_test_db()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            record_skill_change
        )
        rec = record_skill_change("skill-p", "combat", 50.0, 55.0,
                                   "combat_experience", "opponent-uuid")
        self.assertEqual(rec["delta"], 5.0)
        self.assertEqual(rec["reason"], "combat_experience")
        teardown_test_db()


# ══════════════════════════════════════════════════════════════════════
# 7. PLUGIN REGISTRATION TESTS
# ══════════════════════════════════════════════════════════════════════

class TestPluginRegistration(unittest.TestCase):
    """Test the extension's plugin registration."""

    def test_on_load_registers_hooks(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.__init__ import (
            on_load, EXTENSION_NAME, on_simulation_cycle_end
        )
        mock_registry = MagicMock()
        on_load(mock_registry)
        mock_registry.register.assert_called_once_with(
            "on_simulation_cycle_end", EXTENSION_NAME, on_simulation_cycle_end)


# ══════════════════════════════════════════════════════════════════════
# 8. EDGE CASES
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def test_skill_clamped_minimum(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            apply_skill_change
        )
        setup_test_db()
        result = apply_skill_change("edge-p", "mining", -999.0,
                                     "catastrophe", archetype="miner")
        self.assertGreaterEqual(result["new_value"], 1.0)
        teardown_test_db()

    def test_bias_clamping(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            compute_interaction_bias
        )
        extreme = {"archetype": "warrior",
                    "traits": {"aggression": 10, "honor": 1,
                                "sociability": 1, "curiosity": 1}}
        bias = compute_interaction_bias(extreme, extreme, relationship_strength=100.0)
        self.assertGreaterEqual(bias, 0.05)
        self.assertLessEqual(bias, 0.95)

    def test_database_cleanup(self):
        """Multiple operations don't corrupt the database."""
        setup_test_db()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            log_interaction, get_recent_interactions
        )
        for i in range(10):
            log_interaction("p-a", f"p-b-{i}", "trade",
                             relationship_delta=random.uniform(-5, 10))
        recent = get_recent_interactions("p-a", limit=100)
        self.assertEqual(len(recent), 10)
        teardown_test_db()

    def test_simulate_encounter_missing_persona(self):
        """Simulating with missing persona raises error."""
        setup_test_db()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_contention import (
            simulate_encounter
        )
        with self.assertRaises(ValueError):
            simulate_encounter("invalid-1", "invalid-2", use_thinking_mode=False)
        teardown_test_db()

    def test_zero_delta_interaction(self):
        """Interactions with zero delta still logged."""
        setup_test_db()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            log_interaction, get_recent_interactions
        )
        log_interaction("zd-a", "zd-b", "conversation", relationship_delta=0.0)
        recent = get_recent_interactions("zd-a")
        self.assertGreaterEqual(len(recent), 1)
        teardown_test_db()


# ══════════════════════════════════════════════════════════════════════
# 10. EDGE CASE VALIDATION TESTS
# ══════════════════════════════════════════════════════════════════════

class TestAIResponseValidation(unittest.TestCase):
    "Test AI response validation and retry logic."
    
    def test_outcome_schema_validation_valid(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver, VALID_OUTCOMES
        )
        for outcome in VALID_OUTCOMES:
            if outcome == "solitary":
                continue
            resolver = ContentionResolver(use_thinking_mode=True)
            result = resolver.resolve([FARMER], AREA_PLAINS)
            self.assertIn(result["outcome"], VALID_OUTCOMES)
            self.assertIn("narrative", result)
            self.assertGreaterEqual(result["confidence"], 0.0)
            self.assertLessEqual(result["confidence"], 1.0)

    def test_outcome_schema_invalid_fixed(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        result = resolver._validate_outcome_schema({
            "outcome": "INVALID",
            "confidence": 1.5,
            "narrative": "",
        })
        self.assertEqual(result["outcome"], "peaceful_coexistence")
        self.assertEqual(result["confidence"], 1.0)
        self.assertTrue(result["narrative"])

    def test_outcome_schema_nan_confidence(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        import math
        resolver = ContentionResolver()
        result = resolver._validate_outcome_schema({
            "outcome": "conflict",
            "confidence": float("nan"),
            "narrative": "Test.",
            "persona_effects": [],
            "relationship_changes": [],
            "skill_changes": [],
        })
        self.assertEqual(result["confidence"], 0.5)

    def test_outcome_schema_not_a_dict(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver()
        result = resolver._validate_outcome_schema("not_a_dict")
        self.assertEqual(result["outcome"], "peaceful_coexistence")
        self.assertEqual(result["confidence"], 0.5)

    def test_reformat_retry_message_inline(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            VALID_OUTCOMES
        )
        self.assertIn("conflict", VALID_OUTCOMES)
        self.assertIn("negotiation", VALID_OUTCOMES)


class TestPersonaValidation(unittest.TestCase):
    "Test persona input validation."

    def test_empty_persona_list_resolve_full(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=True)
        result = resolver.resolve([], {"name": "Empty", "type": "void", "region": "void"})
        self.assertEqual(result["outcome"], "solitary")

    def test_persona_missing_all_fields(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        result = resolver.resolve([{"nonsense": True}], {})
        self.assertIn("outcome", result)
        self.assertIn("narrative", result)

    def test_persona_nan_skills(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        import math
        resolver = ContentionResolver(use_thinking_mode=False)
        bad = dict(FARMER)
        bad["skills"] = {"combat": float("nan"), "farming": float("inf")}
        result = resolver.resolve([bad, WARRIOR], AREA_MOUNTAINS, has_resources=True)
        self.assertIn("outcome", result)

    def test_persona_negative_skills(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        bad = dict(FARMER)
        bad["skills"] = {"combat": -999, "farming": -1}
        bad["traits"] = {"aggression": -5, "honor": 20}
        result = resolver.resolve([bad, WARRIOR], AREA_PLAINS)
        self.assertIn("outcome", result)

    def test_arena_not_a_dict(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=True)
        result = resolver.resolve([FARMER, WARRIOR], "not_a_dict")
        self.assertIn("outcome", result)
        self.assertIn("narrative", result)

    def test_personas_not_a_list(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver()
        result = resolver.resolve("not_a_list", AREA_PLAINS)
        self.assertEqual(result["outcome"], "peaceful_coexistence")

    def test_twenty_personas_doesnt_crash(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_ai_resolver import (
            ContentionResolver
        )
        resolver = ContentionResolver(use_thinking_mode=False)
        many = [dict(FARMER) for _ in range(25)]
        result = resolver.resolve(many, AREA_PLAINS)
        self.assertIn("outcome", result)


class TestSkillEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()
    
    @classmethod
    def tearDownClass(cls):
        teardown_test_db()
    "Test skill system edge cases."

    def test_skill_nan_base_delta(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            apply_skill_change
        )
        import math
        result = apply_skill_change("edge-p", "mining", float("nan"),
                                     "nan_test", archetype="miner")
        self.assertEqual(result["delta_applied"], 0.0)

    def test_skill_inf_base_delta(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            apply_skill_change
        )
        import math
        result = apply_skill_change("edge-p", "mining", float("inf"),
                                     "inf_test", archetype="miner")
        self.assertEqual(result["delta_applied"], 0.0)

    def test_skill_str_base_delta(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            apply_skill_change
        )
        result = apply_skill_change("edge-p", "mining", "not_a_number",
                                     "str_test", archetype="miner")
        self.assertEqual(result["delta_applied"], 0.0)

    def test_skill_none_reason(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            apply_skill_change
        )
        result = apply_skill_change("edge-p", "mining", 5.0, None)
        self.assertGreaterEqual(result["new_value"], result["old_value"])

    def test_skill_summary_empty_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            get_skill_summary
        )
        result = get_skill_summary("")
        self.assertEqual(result["average"], 0.0)

    def test_skill_summary_none_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            get_skill_summary
        )
        result = get_skill_summary("nonexistent-uuid-nobody")
        self.assertEqual(result["total"], 0)

    def test_skill_decay_no_skills(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
            process_skill_decay
        )
        # Decay requires a valid persona in DB; test with one that exists
        existing = {"mining": 5.0, "combat": 5.0}
        result = process_skill_decay("edge-p", existing, "miner")
        self.assertIsInstance(result, dict)


class TestBehaviorEdgeCases(unittest.TestCase):
    "Test behavior system edge cases."

    def test_get_behavior_none(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            get_behavior
        )
        b = get_behavior(None)
        self.assertEqual(b["name"], "Adventurer")

    def test_get_behavior_empty(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            get_behavior
        )
        b = get_behavior("")
        self.assertEqual(b["name"], "Adventurer")

    def test_compute_bias_none_personas(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            compute_interaction_bias
        )
        import math
        bias = compute_interaction_bias(None, None, relationship_strength=float("nan"))
        self.assertEqual(bias, 0.5)

    def test_compute_bias_nan_strength(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            compute_interaction_bias
        )
        import math
        bias = compute_interaction_bias(FARMER, WARRIOR,
                                        relationship_strength=float("nan"))
        self.assertGreaterEqual(bias, 0.05)
        self.assertLessEqual(bias, 0.95)

    def test_narrative_none_outcome(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            generate_contention_narrative
        )
        n = generate_contention_narrative([FARMER, WARRIOR], None)
        self.assertIsInstance(n, str)
        self.assertGreater(len(n), 10)

    def test_narrative_empty_personas(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            generate_contention_narrative
        )
        n = generate_contention_narrative([], "test", "Somewhere", "overworld")
        self.assertIsInstance(n, str)

    def test_outcome_empty_personas(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            determine_contention_outcome
        )
        result = determine_contention_outcome([], "plains")
        self.assertEqual(result["outcome"], "solitary")

    def test_outcome_none_area(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
            determine_contention_outcome
        )
        result = determine_contention_outcome([FARMER, WARRIOR], None)
        self.assertIn("outcome", result)


class TestEdgeCaseIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()
    
    @classmethod
    def tearDownClass(cls):
        teardown_test_db()
    "Integration tests with extreme edge cases."

    def test_full_cycle_all_edge_cases(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.__init__ import (
            on_simulation_cycle_end
        )
        result = on_simulation_cycle_end()
        self.assertIsInstance(result, dict)
        self.assertIn("contentions_processed", result)
        self.assertIn("skills_decayed", result)

    def test_contention_with_nan_max_events(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_contention import (
            process_all_contentions
        )
        import math
        result = process_all_contentions(use_thinking_mode=False, max_events=float("nan"))
        self.assertIsInstance(result, list)

    def test_contention_with_str_max_events(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_contention import (
            process_all_contentions
        )
        result = process_all_contentions(use_thinking_mode=False, max_events="abc")
        self.assertIsInstance(result, list)

    def test_contention_with_invalid_dict(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_contention import (
            resolve_contention
        )
        result = resolve_contention({}, use_thinking_mode=False)
        self.assertEqual(result["outcome"], "skipped")

    def test_simulate_encounter_none_area(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_contention import (
            simulate_encounter
        )
        setup_test_db()
        with self.assertRaises(ValueError):
            simulate_encounter("", "invalid-2")
        teardown_test_db()


class TestDatabaseEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()
    
    @classmethod
    def tearDownClass(cls):
        teardown_test_db()
    "Test database edge cases."

    def test_get_relationship_nonexistent(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            get_relationship
        )
        rel = get_relationship("does-not-exist", "also-does-not-exist")
        self.assertIsNone(rel)

    def test_get_relationship_empty_uuid(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            get_relationship
        )
        rel = get_relationship("", "")
        self.assertIsNone(rel)

    def test_list_relationships_nonexistent(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            list_relationships
        )
        rels = list_relationships("does-not-exist")
        self.assertEqual(len(rels), 0)

    def test_upsert_extreme_values(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            upsert_relationship, get_relationship
        )
        upsert_relationship("extreme-a", "extreme-b", strength_delta=9999.0)
        rel = get_relationship("extreme-a", "extreme-b")
        self.assertLessEqual(rel["strength"], 100.0)
        upsert_relationship("extreme-a", "extreme-b", strength_delta=-9999.0)
        rel = get_relationship("extreme-a", "extreme-b")
        self.assertGreaterEqual(rel["strength"], 0.0)

    def test_multiple_interactions_db_integrity(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            log_interaction, get_recent_interactions
        )
        for i in range(1000):
            log_interaction(f"stress-a-{i}", f"stress-b-{i}", "trade",
                             relationship_delta=random.uniform(-5, 10))
        recent = get_recent_interactions("stress-a-0", limit=100)
        self.assertEqual(len(recent), 1)
        recent_all = get_recent_interactions("stress-a-500", limit=100)
        self.assertEqual(len(recent_all), 1)


if __name__ == "__main__":
    unittest.main()
