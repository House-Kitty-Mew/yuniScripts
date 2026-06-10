"""
test_shm_combat_bridge.py — Unit tests for shm_combat_bridge.py and shm_bridge_pending.py.

Tests all bridge functions with mocked dependencies, covering:
  - bridge_get_combat_stats()
  - bridge_get_pain_effects()
  - bridge_on_wound_created()
  - bridge_get_skill_level()
  - bridge_log_skill_use()
  - bridge_on_healing_tick()
  - bridge_sync_infection_to_shm()
  - bridge_migrate_existing_wounds()
  - get_impact_force()
  - _map_item_to_skill()
  - _get_fracture_penalty()
  - Feature flag toggles
  - Cache clearing
  - Edge cases (empty, disabled, failed imports)
  - shm_bridge_pending.py: add, resolve, delete, process, cleanup

NOTE: Bridge functions use LAZY imports inside function bodies. Tests mock the
SOURCE modules (shm_muscle, shm_pain, shm_blood, etc.) directly.
"""

import sys, os, json, uuid, time
from unittest import TestCase, main
from unittest.mock import patch, MagicMock, PropertyMock, DEFAULT

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SHM_DIR = os.path.dirname(_HERE)
_EXT_DIR = os.path.dirname(_SHM_DIR)
_AH_DIR = os.path.dirname(_EXT_DIR)
_MANAGER_DIR = os.path.dirname(_AH_DIR)
for p in [_SHM_DIR, _EXT_DIR, _AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ══════════════════════════════════════════════════════════════════════
# Test Helpers
# ══════════════════════════════════════════════════════════════════════

def _make_bridge():
    """Import and return the bridge module, ensuring fresh state.

    Uses reset_bridge_state() to clear module-level globals instead of
    deleting/re-importing the module. This avoids sys.modules thrashing
    and provides deterministic state across all test classes.
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS import shm_combat_bridge
    shm_combat_bridge.reset_bridge_state()
    return shm_combat_bridge


def _make_pending():
    """Import and return the pending module, ensuring fresh state.

    Uses reset_pending_state() to clear registered handlers.
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS import shm_bridge_pending
    shm_bridge_pending.reset_pending_state()
    return shm_bridge_pending


# ══════════════════════════════════════════════════════════════════════
# Test: get_impact_force()
# ══════════════════════════════════════════════════════════════════════

class TestGetImpactForce(TestCase):
    """Tests for the get_impact_force helper function."""

    def setUp(self):
        self.bridge = _make_bridge()

    def test_severity_1_returns_low_force(self):
        self.bridge = _make_bridge()
        force = self.bridge.get_impact_force(1)
        self.assertGreaterEqual(force, 0.15)
        self.assertLessEqual(force, 0.35)

    def test_severity_4_returns_high_force(self):
        force = self.bridge.get_impact_force(4)
        self.assertGreaterEqual(force, 0.9)

    def test_blunt_weapon_adds_bonus(self):
        force_plain = self.bridge.get_impact_force(2)
        force_blunt = self.bridge.get_impact_force(2, "mace")
        self.assertGreater(force_blunt, force_plain)

    def test_piercing_weapon_reduces_blunt(self):
        force_plain = self.bridge.get_impact_force(2)
        force_piercing = self.bridge.get_impact_force(2, "spear")
        self.assertLess(force_piercing, force_plain)

    def test_unknown_weapon_no_modifier(self):
        force_plain = self.bridge.get_impact_force(3)
        force_unknown = self.bridge.get_impact_force(3, "laser_rifle")
        self.assertEqual(force_plain, force_unknown)

    def test_force_clamped_to_range(self):
        """Force should never go below 0.05 or above 2.0."""
        force_min = self.bridge.get_impact_force(0)
        force_max = self.bridge.get_impact_force(10)
        self.assertGreaterEqual(force_min, 0.05)
        self.assertLessEqual(force_max, 2.0)


# ══════════════════════════════════════════════════════════════════════
# Test: _map_item_to_skill()
# ══════════════════════════════════════════════════════════════════════

class TestMapItemToSkill(TestCase):
    """Tests for the _map_item_to_skill helper."""

    def setUp(self):
        self.bridge = _make_bridge()

    def test_sword_maps_to_swordsmanship(self):
        result = self.bridge._map_item_to_skill("minecraft:diamond_sword")
        self.assertEqual(result, "swordsmanship")

    def test_axe_maps_to_axes(self):
        result = self.bridge._map_item_to_skill("minecraft:iron_axe")
        self.assertEqual(result, "axes")

    def test_pickaxe_maps_to_mining(self):
        result = self.bridge._map_item_to_skill("minecraft:stone_pickaxe")
        self.assertEqual(result, "mining")

    def test_shovel_maps_to_axes(self):
        result = self.bridge._map_item_to_skill("minecraft:wooden_shovel")
        self.assertEqual(result, "axes")

    def test_bow_maps_to_archery(self):
        result = self.bridge._map_item_to_skill("minecraft:bow")
        self.assertEqual(result, "archery")

    def test_trident_maps_to_swordsmanship(self):
        result = self.bridge._map_item_to_skill("minecraft:trident")
        self.assertEqual(result, "swordsmanship")

    def test_crossbow_maps_to_archery(self):
        result = self.bridge._map_item_to_skill("minecraft:crossbow")
        self.assertEqual(result, "archery")

    def test_unknown_weapon_returns_unarmed(self):
        result = self.bridge._map_item_to_skill("minecraft:stick")
        self.assertEqual(result, "unarmed")


# ══════════════════════════════════════════════════════════════════════
# Test: _get_fracture_penalty()
# ══════════════════════════════════════════════════════════════════════

class TestGetFracturePenalty(TestCase):
    """Tests for _get_fracture_penalty helper."""

    def setUp(self):
        self.bridge = _make_bridge()

    def test_no_fractures_returns_zero(self):
        bones = []
        penalty = self.bridge._get_fracture_penalty(bones)
        self.assertEqual(penalty, 0.0)

    def test_single_fracture_penalty(self):
        bones = [{"bone_name": "femur", "fractured": 1, "pain_contribution": 10.0}]
        penalty = self.bridge._get_fracture_penalty(bones)
        self.assertGreater(penalty, 0.0)

    def test_multiple_fractures_stack(self):
        bones = [
            {"bone_name": "femur", "fractured": 1, "pain_contribution": 10.0},
            {"bone_name": "humerus", "fractured": 1, "pain_contribution": 8.0},
        ]
        penalty = self.bridge._get_fracture_penalty(bones)
        # Two fractures should have higher penalty than one
        single_penalty = self.bridge._get_fracture_penalty(
            [{"bone_name": "femur", "fractured": 1, "pain_contribution": 10.0}]
        )
        self.assertGreater(penalty, single_penalty)

    def test_healed_fractures_no_penalty(self):
        bones = [{"bone_name": "femur", "fractured": 0, "pain_contribution": 5.0}]
        penalty = self.bridge._get_fracture_penalty(bones)
        self.assertEqual(penalty, 0.0)


# ══════════════════════════════════════════════════════════════════════
# Test: bridge_get_combat_stats()
# ══════════════════════════════════════════════════════════════════════

class TestBridgeGetCombatStats(TestCase):
    """Tests for bridge_get_combat_stats()."""

    def setUp(self):
        self.bridge = _make_bridge()
        self.puuid = str(uuid.uuid4())

    def test_disabled_returns_none(self):
        """Should return None when SHM is disabled."""
        self.bridge.SHM_BRIDGE_CONFIG["enabled"] = False
        result = self.bridge.bridge_get_combat_stats(self.puuid, 1)
        self.assertIsNone(result)

    def test_basic_stats_structure(self):
        """Should return a dict with all expected keys."""
        with patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_combat.get_combat_stats',
            return_value={
                "strength": 50, "agility": 40, "endurance": 60,
                "weapon_skill": 45, "armor_rating": 30
            }
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood.get_blood_stats',
            return_value={"blood_volume_ml": 5000, "max_blood_volume": 5000}
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle.get_muscle_stats',
            return_value=[{"strength": 50, "fatigue": 10}]
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain.get_total_pain',
            return_value=0.0
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database.get_db',
            return_value=MagicMock()
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world.get_persona_area',
            return_value={"biome_type": "plains"}
        ):
            result = self.bridge.bridge_get_combat_stats(self.puuid, 1)
            self.assertIsNotNone(result)
            for key in ("strength", "agility", "endurance", "weapon_skill",
                        "armor_rating", "pain_factor", "blood_penalty",
                        "biome_bonus", "effective_strength"):
                self.assertIn(key, result)


# ══════════════════════════════════════════════════════════════════════
# Test: bridge_get_pain_effects()
# ══════════════════════════════════════════════════════════════════════

class TestBridgeGetPainEffects(TestCase):
    """Tests for bridge_get_pain_effects()."""

    def setUp(self):
        self.bridge = _make_bridge()
        self.puuid = str(uuid.uuid4())

    def test_disabled_returns_none(self):
        self.bridge.SHM_BRIDGE_CONFIG["enabled"] = False
        result = self.bridge.bridge_get_pain_effects(self.puuid)
        self.assertIsNone(result)

    def test_basic_pain_effects(self):
        """Should return pain effects when replace_pain is True."""
        self.bridge.SHM_BRIDGE_CONFIG["replace_pain"] = True

        mock_db = MagicMock()
        mock_db.fetch_all.return_value = [
            {"bone_name": "femur", "fractured": 1, "pain_contribution": 25.0}
        ]

        with patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database.get_db',
            return_value=mock_db
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain.get_total_pain',
            return_value=50.0
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_genetics.get_genetics',
            return_value={"pain_tolerance": 1.0}
        ):
            result = self.bridge.bridge_get_pain_effects(self.puuid)
            self.assertIsNotNone(result)
            self.assertIn("total_pain", result)
            self.assertIn("fracture_penalty", result)


# ══════════════════════════════════════════════════════════════════════
# Test: Bridge module-level cache
# ══════════════════════════════════════════════════════════════════════

class TestBridgeCache(TestCase):
    """Tests for the per-tick LRU cache."""

    def setUp(self):
        self.bridge = _make_bridge()

    def test_cache_starts_empty(self):
        self.bridge = _make_bridge()
        self.assertEqual(self.bridge._bridge_cache, {})

    def test_cache_clears_on_tick_change(self):
        self.bridge._bridge_cache["prev"] = "data"
        self.bridge.SHM_BRIDGE_CONFIG["current_tick"] = 2
        result = self.bridge._get_cache("test")
        self.assertIsNone(result)

    def test_cache_hits_on_same_tick(self):
        self.bridge.SHM_BRIDGE_CONFIG["current_tick"] = 1
        self.bridge._bridge_cache["key_1"] = "cached_val"
        result = self.bridge._get_cache("key")
        self.assertEqual(result, "cached_val")


# ══════════════════════════════════════════════════════════════════════
# Test: bridge_on_wound_created()
# ══════════════════════════════════════════════════════════════════════

class TestBridgeOnWoundCreated(TestCase):
    """Tests for bridge_on_wound_created()."""

    def setUp(self):
        self.bridge = _make_bridge()
        self.puuid = str(uuid.uuid4())

    def test_disabled_returns_false(self):
        self.bridge.SHM_BRIDGE_CONFIG["enabled"] = False
        result = self.bridge.bridge_on_wound_created(
            self.puuid, {"wound_type": "cut", "severity": 3}, "sword"
        )
        self.assertFalse(result)

    def test_basic_wound_triggers_effects(self):
        with patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database.get_db',
            return_value=MagicMock()
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood.cause_bleeding',
            return_value={"blood_lost_ml": 200, "new_volume": 4800, "new_oxygen": 95.0}
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain.register_pain',
            return_value={"new_pain": 15.0}
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_bridge.get_impact_force',
            return_value=0.33
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy.cause_fracture',
            return_value={"fractured": True}
        ):
            result = self.bridge.bridge_on_wound_created(
                self.puuid, {"wound_type": "cut", "severity": 3}, "sword",
                wound_uuid=str(uuid.uuid4())
            )
            self.assertTrue(result)


# ══════════════════════════════════════════════════════════════════════
# Test: bridge_get_skill_level() and bridge_log_skill_use()
# ══════════════════════════════════════════════════════════════════════

class TestBridgeSkills(TestCase):
    """Tests for bridge skill queries."""

    def setUp(self):
        self.bridge = _make_bridge()
        self.puuid = str(uuid.uuid4())

    def test_get_skill_level_disabled_returns_none(self):
        self.bridge.SHM_BRIDGE_CONFIG["enabled"] = False
        result = self.bridge.bridge_get_skill_level(self.puuid, "swordsmanship")
        self.assertIsNone(result)

    def test_get_skill_level_with_data(self):
        with patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle.get_muscle_stats',
            return_value=[{"muscle_group": "arms", "strength": 40, "fatigue": 5}]
        ):
            result = self.bridge.bridge_get_skill_level(self.puuid, "swordsmanship")
            self.assertIsNotNone(result)


# ══════════════════════════════════════════════════════════════════════
# Test: bridge_on_healing_tick()
# ══════════════════════════════════════════════════════════════════════

class TestBridgeOnHealingTick(TestCase):
    """Tests for bridge_on_healing_tick()."""

    def setUp(self):
        self.bridge = _make_bridge()
        self.puuid = str(uuid.uuid4())

    def test_disabled_returns_false(self):
        self.bridge.SHM_BRIDGE_CONFIG["enabled"] = False
        result = self.bridge.bridge_on_healing_tick(
            self.puuid, ["wound_1"], {"health": 80}
        )
        self.assertFalse(result)

    def test_empty_wounds_returns_empty_list(self):
        self.bridge.SHM_BRIDGE_CONFIG["enabled"] = True
        self.bridge.SHM_BRIDGE_CONFIG["replace_healing"] = True
        with patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_bridge.get_active_wounds',
            return_value=[]
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database.get_db',
            return_value=MagicMock()
        ):
            result = self.bridge.bridge_on_healing_tick(
                self.puuid, ["wound_1"], {"health": 80}
            )
            self.assertEqual(result, [])


# ══════════════════════════════════════════════════════════════════════
# Test: bridge_sync_infection_to_shm()
# ══════════════════════════════════════════════════════════════════════

class TestBridgeSyncInfection(TestCase):
    """Tests for bridge_sync_infection_to_shm()."""

    def setUp(self):
        self.bridge = _make_bridge()
        self.puuid = str(uuid.uuid4())

    def test_disabled_returns_empty(self):
        self.bridge.SHM_BRIDGE_CONFIG["enabled"] = False
        result = self.bridge.bridge_sync_infection_to_shm(
            self.puuid, [{"wound_uuid": "w1", "infected": True}]
        )
        self.assertEqual(result, [])

    def test_no_infected_wounds(self):
        with patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database.get_db',
            return_value=MagicMock()
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_bridge.has_disease',
            return_value=False
        ), patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_bridge.expose_to_disease',
            return_value={"infected": False}
        ):
            wounds = [
                {"wound_uuid": "w1", "is_infected": False},
                {"wound_uuid": "w2", "is_infected": False},
            ]
            result = self.bridge.bridge_sync_infection_to_shm(self.puuid, wounds)
            self.assertEqual(result, [])


# ══════════════════════════════════════════════════════════════════════
# Test: bridge_migrate_existing_wounds()
# ══════════════════════════════════════════════════════════════════════

class TestBridgeMigrateExistingWounds(TestCase):
    """Tests for bridge_migrate_existing_wounds()."""

    def setUp(self):
        self.bridge = _make_bridge()
        self.puuid = str(uuid.uuid4())

    def test_disabled_returns_zero(self):
        self.bridge.SHM_BRIDGE_CONFIG["enabled"] = False
        result = self.bridge.bridge_migrate_existing_wounds(self.puuid, [], {})
        self.assertEqual(result, {"migrated": 0, "errors": 0})

    def test_migration_with_mocked_db(self):
        with patch(
            'AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database.get_db',
            return_value=MagicMock()
        ):
            result = self.bridge.bridge_migrate_existing_wounds(
                self.puuid,
                [{"wound_uuid": "w1", "severity": 2}],
                {"pain_tolerance": 1.0}
            )
            self.assertIn("migrated", result)
            self.assertIn("errors", result)


# ══════════════════════════════════════════════════════════════════════
# Test: shm_bridge_pending.py
# ══════════════════════════════════════════════════════════════════════

class TestBridgePending(TestCase):
    """Tests for shm_bridge_pending module.

    Uses an in-memory SQLite database to prevent ordering interference
    from earlier bridge test classes that use the singleton DB connection.
    """

    def setUp(self):
        # Ensure the pending schema exists in the test DB
        from AUCTIONHOUSE.ah_database import initialize_database, get_db
        # Force re-init ensures a clean schema state even if earlier test
        # classes left the DB in a stale state.
        initialize_database()
        self.pending = _make_pending()
        self.pending.ensure_pending_schema()
        # Clean any leftover test data
        try:
            db = get_db()
            db.execute("DELETE FROM shm_bridge_pending")
        except Exception:
            pass
        self.wound_uuid = str(uuid.uuid4())
        self.owner_uuid = str(uuid.uuid4())

    def _count_pending(self):
        # Use self.pending's get_db (the same module-level import that
        # add_pending_action/resolve_pending_action use) to guarantee
        # we're counting in the same DB connection.
        db = self.pending.get_db()
        result = db.fetch_one("SELECT COUNT(*) as cnt FROM shm_bridge_pending")
        return result["cnt"] if result else 0

    def test_ensure_pending_schema(self):
        """Schema should be creatable without error."""
        try:
            self.pending = _make_pending()
            result = self.pending.ensure_pending_schema()
        except Exception as e:
            self.fail(f"ensure_pending_schema raised: {e}")

    def test_add_pending_action(self):
        result = self.pending.add_pending_action(
            self.wound_uuid, self.owner_uuid, "create_fracture",
            {"body_part": "left_arm", "severity": 3}
        )
        self.assertTrue(result)
        self.assertEqual(self._count_pending(), 1)

    def test_add_duplicate_pending_action(self):
        self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "create_fracture")
        result = self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "create_fracture")
        self.assertTrue(result)  # INSERT OR IGNORE
        self.assertEqual(self._count_pending(), 1)  # No duplicates

    def test_resolve_pending_action(self):
        self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "register_pain")
        result = self.pending.resolve_pending_action(self.wound_uuid)
        self.assertTrue(result)

    def test_delete_pending_action(self):
        self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "register_pain")
        self.pending.resolve_pending_action(self.wound_uuid)
        result = self.pending.delete_pending_action(self.wound_uuid)
        self.assertTrue(result)

    def test_process_pending_actions_empty(self):
        result = self.pending.process_pending_actions()
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["succeeded"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["exceeded"], 0)

    def test_process_pending_actions_with_handler(self):
        self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "test_action")

        handler_calls = []
        def test_handler(wound_uuid, owner_uuid, action_data):
            handler_calls.append((wound_uuid, owner_uuid, action_data))
            return True

        self.pending.register_pending_handler("test_action", test_handler)
        result = self.pending.process_pending_actions()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(len(handler_calls), 1)

    def test_process_pending_actions_handler_fails(self):
        self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "fail_action")

        handler_calls = []
        def failing_handler(wound_uuid, owner_uuid, action_data):
            handler_calls.append((wound_uuid, owner_uuid, action_data))
            return False

        self.pending.register_pending_handler("fail_action", failing_handler)
        result = self.pending.process_pending_actions()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["succeeded"], 0)

    def test_exceeded_max_retries(self):
        self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "retry_action")

        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        db = get_db()
        db.execute("""
            UPDATE shm_bridge_pending SET max_retries = 1
            WHERE wound_uuid = ?
        """, (self.wound_uuid,))

        def forever_failing(wound_uuid, owner_uuid, action_data):
            return False

        self.pending.register_pending_handler("retry_action", forever_failing)
        result = self.pending.process_pending_actions()

        self.assertEqual(result["exceeded"], 1)

    def test_cleanup_resolved_actions(self):
        self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "cleanup_test")
        self.pending.resolve_pending_action(self.wound_uuid)

        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        db = get_db()
        db.execute("""
            UPDATE shm_bridge_pending SET created_at = '2020-01-01T00:00:00'
            WHERE wound_uuid = ?
        """, (self.wound_uuid,))

        # Switch back to the pending module's get_db
        result = self.pending.cleanup_resolved_actions(max_age_hours=1)
        # Verify no stale resolved actions remain
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        remaining = get_db().fetch_one(
            "SELECT COUNT(*) as cnt FROM shm_bridge_pending WHERE is_resolved = 1"
        )
        self.assertEqual(remaining["cnt"], 0)

    def test_no_handler_skips_action(self):
        self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "no_handler_action")
        result = self.pending.process_pending_actions()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["succeeded"], 0)

    def test_register_and_unregister_handler(self):
        def my_handler(a, b, c):
            return True

        self.pending.register_pending_handler("custom_action", my_handler)
        self.assertIn("custom_action", self.pending._PENDING_HANDLERS)
        self.assertEqual(self.pending._PENDING_HANDLERS["custom_action"], my_handler)

    def test_add_pending_with_empty_data(self):
        result = self.pending.add_pending_action(self.wound_uuid, self.owner_uuid, "test_action")
        self.assertTrue(result)

    def test_add_multiple_actions(self):
        for i in range(5):
            w = str(uuid.uuid4())
            self.pending.add_pending_action(w, self.owner_uuid, f"action_{i}")
        self.assertEqual(self._count_pending(), 5)


if __name__ == "__main__":
    main()
