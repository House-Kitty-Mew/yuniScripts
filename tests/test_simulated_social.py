"""
tests/test_simulated_social.py — Comprehensive unit tests for the
SIMULATED_SOCIAL Auction House extension.

Covers: boredom, exhaustion, memory, activities, relationships,
crisis management, planning, and edge cases.

Run: python3 -m unittest tests.test_simulated_social -v
"""

import os, sys, json, time, math, random, unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager"))
sys.path.insert(0, str(_PROJECT_ROOT))

TEST_DB_DIR = _PROJECT_ROOT / "tests" / "test_data_soc"
TEST_DB_PATH = TEST_DB_DIR / "auctionhouse.db3"


def setup_test_db():
    TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    import AUCTIONHOUSE.ah_database as ah_db
    if ah_db._db_instance is not None:
        ah_db._db_instance.close()
        ah_db._db_instance = None
    if hasattr(ah_db.DatabaseManager, '_local'):
        try:
            if hasattr(ah_db.DatabaseManager._local, 'conn'):
                ah_db.DatabaseManager._local.conn = None
        except AttributeError:
            pass
    ah_db.DB_PATH = TEST_DB_PATH
    ah_db.DB_DIR = TEST_DB_DIR
    from AUCTIONHOUSE.ah_database import initialize_database
    try:
        initialize_database(force=False)
    except Exception:
        pass
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema as sp_ensure
    sp_ensure()
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import ensure_schema as rel_ensure
    try:
        rel_ensure()
    except Exception:
        pass
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import ensure_schema
    ensure_schema()
    from AUCTIONHOUSE.ah_database import get_db
    db = get_db()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for puid, name, arch in [("test-soc-p1", "Tester A", "adventurer"),
                               ("test-soc-p2", "Tester B", "warrior"),
                               ("test-soc-p3", "Tester C", "farmer")]:
        existing = db.fetch_one("SELECT id FROM ext_sp_profiles WHERE persona_uuid = ?", (puid,))
        if not existing:
            db.execute("""
                INSERT INTO ext_sp_profiles
                (persona_uuid, name, archetype, job, region, wealth_tier,
                 personality_traits, active, created_at)
                VALUES (?, ?, ?, 'tester', 'overworld', 'middle', '{}', 1, ?)
            """, (puid, name, arch, now))


def teardown_test_db():
    import AUCTIONHOUSE.ah_database as ah_db
    if ah_db._db_instance is not None:
        ah_db._db_instance.close()
        ah_db._db_instance = None
    try:
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        if TEST_DB_DIR.exists():
            for f in TEST_DB_DIR.iterdir():
                if f.is_file():
                    f.unlink()
            TEST_DB_DIR.rmdir()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 1. BOREDOM SYSTEM TESTS
# ══════════════════════════════════════════════════════════════════════


class TestBoredom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_boredom_decay(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import (
            process_boredom_tick
        )
        result = process_boredom_tick("test-soc-p1", "adventurer")
        self.assertLess(result["boredom"], 100.0)
        self.assertLess(result["decay"], 0)

    def test_boredom_decay_vagabond(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import process_boredom_tick
        r1 = process_boredom_tick("test-soc-p1", "farmer")
        r2 = process_boredom_tick("test-soc-p2", "vagabond")
        self.assertGreater(abs(r2["decay"]), abs(r1["decay"]))

    def test_activity_boredom_recovery(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import apply_activity_boredom
        result = apply_activity_boredom("test-soc-p1", "gift_giving", "adventurer")
        self.assertGreater(result["boredom_change"], 0)
        self.assertGreater(result["new_boredom"], 0)

    def test_get_boredom_status(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import get_boredom_status
        self.assertEqual(get_boredom_status(80), "content")
        self.assertEqual(get_boredom_status(60), "restless")
        self.assertEqual(get_boredom_status(35), "bored")
        self.assertEqual(get_boredom_status(15), "agitated")
        self.assertEqual(get_boredom_status(5), "critical")
        self.assertEqual(get_boredom_status(0), "crisis")

    def test_activity_preference_no_crash(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import get_activity_preference
        act = get_activity_preference("test-soc-p1", "farmer")
        self.assertIsInstance(act, str)
        self.assertGreater(len(act), 0)

    def test_activity_preference_vagabond(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import get_activity_preference
        act = get_activity_preference("test-soc-p1", "vagabond")
        self.assertIsInstance(act, str)


# ══════════════════════════════════════════════════════════════════════
# 2. EXHAUSTION SYSTEM TESTS
# ══════════════════════════════════════════════════════════════════════

class TestExhaustion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_exhaustion_tick_active(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_exhaustion import (
            process_exhaustion_tick
        )
        result = process_exhaustion_tick("test-soc-p1", "adventurer")
        self.assertIn("exhaustion", result)
        self.assertIn("state", result)

    def test_exhaustion_merchant_high(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_exhaustion import (
            process_exhaustion_tick
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
            update_exhaustion
        )
        update_exhaustion("test-soc-p1", 80.0)
        result = process_exhaustion_tick("test-soc-p1", "merchant")
        self.assertIn("state", result)


# ══════════════════════════════════════════════════════════════════════
# 3. MEMORY SYSTEM TESTS
# ══════════════════════════════════════════════════════════════════════

class TestMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_add_short_term_memory(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import add_memory_short
        result = add_memory_short("test-soc-p1", "interaction", "test-soc-p2",
                                   "conversation", "They chatted.", 5)
        self.assertIsNotNone(result)

    def test_add_medium_term_memory(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import add_memory_medium
        result = add_memory_medium("test-soc-p1", "goal", "test-soc-p2",
                                    "trade", "Need to trade with them.",
                                    "market", {"items": ["wheat"]}, 6)
        self.assertIsNotNone(result)

    def test_add_perma_memory(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import add_memory_perma
        result = add_memory_perma("test-soc-p1", "death", "test-soc-p2",
                                   "conflict_defeat",
                                   "Witnessed a tragic death.",
                                   9, {"trauma": -5})
        self.assertIsNotNone(result)

    def test_perma_memory_limit(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import add_memory_perma
        for i in range(25):
            add_memory_perma("test-soc-p1", f"event_{i}", None, None,
                              f"Test event {i}", 8)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import _MAX_PERMA
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        count = db.fetch_one(
            "SELECT COUNT(*) as c FROM ext_soc_memories_perma WHERE persona_uuid = ?",
            ("test-soc-p1",))
        self.assertLessEqual(count["c"], _MAX_PERMA)

    def test_get_recent_memories(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import get_recent_memories
        memories = get_recent_memories("test-soc-p1", include_perma=True)
        self.assertGreaterEqual(len(memories), 0)

    def test_record_interaction_memory(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_memory import record_interaction_memory
        record_interaction_memory("test-soc-p2", "test-soc-p1",
                                   "conversation", "friendly", 5)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_memory import get_memory_context
        ctx = get_memory_context("test-soc-p2", "test-soc-p1")
        self.assertIn("total_memories", ctx)

    def test_empty_memory_context(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_memory import get_memory_context
        ctx = get_memory_context("", "")
        self.assertEqual(ctx["total_memories"], 0)


# ══════════════════════════════════════════════════════════════════════
# 4. ACTIVITY SYSTEM TESTS
# ══════════════════════════════════════════════════════════════════════

class TestActivities(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_process_activity_conversation(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_activities import process_activity
        result = process_activity("test-soc-p1", "conversation",
                                   "test-soc-p2", "adventurer")
        self.assertEqual(result["activity"], "conversation")
        self.assertGreaterEqual(result["boredom_change"], 0)

    def test_process_activity_arguing(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_activities import process_activity
        result = process_activity("test-soc-p1", "arguing",
                                   "test-soc-p2", "warrior")
        self.assertEqual(result["activity"], "arguing")
        self.assertLess(result["boredom_change"], 0)

    def test_process_activity_invalid(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_activities import process_activity
        result = process_activity("test-soc-p1", "invalid_activity")
        self.assertIn("error", result)

    def test_process_activity_empty_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_activities import process_activity
        result = process_activity("", "conversation")
        self.assertIn("error", result)

    def test_valid_activities_set(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_activities import VALID_ACTIVITIES
        self.assertIn("conversation", VALID_ACTIVITIES)
        self.assertIn("arguing", VALID_ACTIVITIES)


# ══════════════════════════════════════════════════════════════════════
# 5. RELATIONSHIP DEPTH TESTS
# ══════════════════════════════════════════════════════════════════════

class TestRelationships(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_upsert_relationship_detail(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
            upsert_relationship_detail
        )
        result = upsert_relationship_detail("test-soc-p1", "test-soc-p2",
                                             "conversation")
        self.assertIn("shared_activity_count", result)
        self.assertGreaterEqual(result["shared_activity_count"], 1)

    def test_relationship_detail_multiple(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
            upsert_relationship_detail
        )
        for _ in range(5):
            upsert_relationship_detail("test-soc-p1", "test-soc-p2",
                                        "eating_together")
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
            get_relationship_detail
        )
        detail = get_relationship_detail("test-soc-p1", "test-soc-p2")
        self.assertGreaterEqual(detail["shared_activity_count"], 5)

    def test_set_married(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
            set_married, get_relationship_detail, add_memory_short
        )
        for _ in range(10):
            add_memory_short("test-soc-p1", "interaction", "test-soc-p2",
                              "conversation", "Building relationship.", 8)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            upsert_relationship
        )
        upsert_relationship("test-soc-p1", "test-soc-p2",
                             strength_delta=50.0, rel_type="romance")
        result = set_married("test-soc-p1", "test-soc-p2")
        self.assertTrue(result)

    def test_relationship_summary(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_relationships import (
            get_relationship_summary
        )
        summary = get_relationship_summary("test-soc-p1")
        self.assertIn("total", summary)
        self.assertIn("friends", summary)

    def test_relationship_decay(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_relationships import (
            process_relationship_decay
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            upsert_relationship
        )
        upsert_relationship("test-soc-p1", "test-soc-p2", strength_delta=30.0)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
            upsert_relationship_detail
        )
        upsert_relationship_detail("test-soc-p1", "test-soc-p2")
        decays = process_relationship_decay("test-soc-p1")
        self.assertIsInstance(decays, list)


# ══════════════════════════════════════════════════════════════════════
# 6. CRISIS MANAGEMENT TESTS
# ══════════════════════════════════════════════════════════════════════

class TestCrisis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_check_crisis_social_activity(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_crisis import check_crisis
        result = check_crisis("test-soc-p1", "conversation", "adventurer")
        self.assertFalse(result["crisis"])

    def test_check_crisis_no_crisis_boredom_high(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_crisis import check_crisis
        result = check_crisis("test-soc-p1", "resting_alone", "farmer")
        self.assertFalse(result["crisis"])

    def test_check_crisis_no_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_crisis import check_crisis
        result = check_crisis("", "resting_alone")
        self.assertFalse(result["crisis"])


# ══════════════════════════════════════════════════════════════════════
# 7. PLANNER TESTS
# ══════════════════════════════════════════════════════════════════════

class TestPlanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_plan_activity(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_planner import plan_activity
        result = plan_activity("test-soc-p1", "adventurer")
        self.assertIn("activity_type", result)
        self.assertIn("reasoning", result)

    def test_plan_activity_no_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_planner import plan_activity
        result = plan_activity("")
        self.assertEqual(result["activity_type"], "resting_alone")

    def test_plan_with_partner(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_planner import plan_activity
        partners = [{"persona_uuid": "test-soc-p2", "archetype": "warrior"},
                     {"persona_uuid": "test-soc-p3", "archetype": "farmer"}]
        result = plan_activity("test-soc-p1", "adventurer", partners)
        self.assertIn("activity_type", result)

    def test_select_partner_none_available(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_planner import plan_activity
        result = plan_activity("test-soc-p1", "farmer", [])
        self.assertIn("activity_type", result)


# ══════════════════════════════════════════════════════════════════════
# 8. EDGE CASES
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_boredom_none_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import process_boredom_tick
        result = process_boredom_tick("")
        self.assertEqual(result["boredom"], 100.0)

    def test_boredom_none_activity(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import apply_activity_boredom
        result = apply_activity_boredom("", "")
        self.assertEqual(result["boredom_change"], 0.0)

    def test_exhaustion_none_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_exhaustion import process_exhaustion_tick
        result = process_exhaustion_tick("")
        self.assertEqual(result["exhaustion"], 0.0)

    def test_marriage_empty_uuids(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import set_married
        result = set_married("", "")
        self.assertFalse(result)

    def test_relationship_summary_empty(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_relationships import get_relationship_summary
        result = get_relationship_summary("")
        self.assertEqual(result, {})

    def test_relationship_decay_nonexistent(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_relationships import process_relationship_decay
        result = process_relationship_decay("")
        self.assertEqual(result, [])

    def test_crisis_suicide_behavior_mod(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_crisis import _SUICIDE_BEHAVIOR_MOD
        self.assertIn("vagabond", _SUICIDE_BEHAVIOR_MOD)
        self.assertGreater(_SUICIDE_BEHAVIOR_MOD["vagabond"], _SUICIDE_BEHAVIOR_MOD["farmer"])

    def test_full_simulation_cycle(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.__init__ import (
            on_simulation_cycle_end
        )
        result = on_simulation_cycle_end()
        self.assertIn("boredom_updates", result)
        self.assertIn("activities_planned", result)
        self.assertIn("marriages", result)

    def test_on_load_registers_hooks(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.__init__ import (
            on_load, EXTENSION_NAME, on_simulation_cycle_end
        )
        mock_registry = MagicMock()
        on_load(mock_registry)
        mock_registry.register.assert_called_once_with(
            "on_simulation_cycle_end", EXTENSION_NAME, on_simulation_cycle_end)

    def test_database_integrity(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
            log_activity, get_or_create_profile
        )
        for i in range(50):
            log_activity("test-soc-p1", "conversation", "test-soc-p2" if i % 2 == 0 else "test-soc-p3", 5.0, 3.0, 1.0)
        profile = get_or_create_profile("test-soc-p1")
        self.assertIn("boredom", profile)
        self.assertIn("social_exhaustion", profile)


if __name__ == "__main__":
    unittest.main()
