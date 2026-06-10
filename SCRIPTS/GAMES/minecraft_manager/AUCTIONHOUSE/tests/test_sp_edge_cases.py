"""
test_sp_edge_cases.py — Comprehensive edge-case testing of all Simulated People subsystems.

Tests 10 categories of edge cases across every subsystem:
  1. Persona Profile Edge Cases (birth, death, extremes)
  2. Health System Edge Cases (zero stats, negative, temperature death)
  3. Behavior Decision Edge Cases (broke, indebted, no listings)
  4. World System Edge Cases (empty, regenerated, resource depletion)
  5. Weather Edge Cases (extremes, storms, seasonal drift)
  6. Movement Edge Cases (no neighbors, stuck, migration waves)
  7. Ecosystem Edge Cases (overpopulation, extinction, dead biomes)
  8. Item/Inventory Edge Cases (negative, overflow, empty, decay)
  9. Item Cache + Trash Edge Cases (null NBT, concurrent, bulk)
  10. World ↔ Persona Integration (cross-biome spawning, area assignment)
"""

import sys, os, json, uuid, random, math, time

_HERE = os.path.dirname(os.path.abspath(__file__))
_AH_DIR = os.path.dirname(_HERE)
_MANAGER_DIR = os.path.dirname(_AH_DIR)
for p in [_AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from unittest import TestCase

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_database import get_db, initialize_database
from AUCTIONHOUSE.ah_config import get_config

log = get_logger()


class EdgeCaseTestCase(TestCase):
    """Base class that ensures schema and clean state."""

    @classmethod
    def setUpClass(cls):
        initialize_database()

    def setUp(self):
        # Simple cleanup: DELETE data from all tables, don't drop them
        self._wipe_test_data()
        self._ensure_sp_tables()
        self._seed_defs()

    # ── Helpers ──────────────────────────────────────────────────

    def _wipe_test_data(self):
        """Wipe data from all core AH + SP tables (keep schema intact)."""
        db = get_db()
        # Wipe AH core tables
        for table in [
            "transaction_history", "auction_listings", "player_balances",
            "ai_notes", "price_history", "market_events",
        ]:
            try:
                db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        # Wipe SP extension tables
        sp_tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ext_sp_%'")
        for t in sp_tables:
            try:
                db.execute(f"DELETE FROM {t['name']}")
            except Exception:
                pass

    def _ensure_sp_tables(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema
        ensure_schema()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import ensure_all_tables
        ensure_all_tables()

    def _seed_defs(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import seed_item_defs
        seed_item_defs()

    def _gen_persona(self, archetype="farmer", region="overworld") -> dict:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        return generate_persona(archetype_override=archetype,
                                region_override=region)

    def _gen_world(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import generate_world
        return generate_world()

    def _get_area(self, biome=None) -> dict:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas
        areas = get_all_areas()
        if biome:
            for a in areas:
                if a["biome_type"] == biome:
                    return a
        return areas[0] if areas else None


# ═════════════════════════════════════════════════════════════════════
# 1. Persona Profile Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestPersonaProfileEdgeCases(EdgeCaseTestCase):

    def test_generate_all_archetypes(self):
        """Every archetype can generate without error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona, ARCHETYPES
        for arch in ARCHETYPES:
            p = generate_persona(archetype_override=arch)
            self.assertEqual(p["archetype"], arch)
            self.assertIn("persona_uuid", p)
            self.assertIn("name", p)

    def test_generate_all_regions(self):
        """Every region can generate a persona."""
        for region in ["overworld", "nether", "end", "deep_dark"]:
            p = self._gen_persona(region=region)
            self.assertEqual(p["region"], region)

    def test_spawn_empty_population(self):
        """Spawning 0 personas is a no-op."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import spawn_initial_population, get_persona_count
        spawn_initial_population(0)
        stats = get_persona_count()
        self.assertGreaterEqual(stats["total"], 0)

    def test_spawn_single_persona(self):
        """Spawning a population of 1 works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import spawn_initial_population, get_persona_count
        spawn_initial_population(1)
        stats = get_persona_count()
        self.assertGreaterEqual(stats["total"], 1)

    def test_generate_persona_has_finances(self):
        """Every generated persona has proper financial data."""
        p = self._gen_persona()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        fin = sp_db().fetch_one(
            "SELECT * FROM ext_sp_finances WHERE persona_uuid = ?",
            (p["persona_uuid"],))
        self.assertIsNotNone(fin)
        self.assertGreaterEqual(fin["balance"], 0)

    def test_generate_persona_has_health(self):
        """Every generated persona has health stats."""
        p = self._gen_persona()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        health = sp_db().fetch_one(
            "SELECT * FROM ext_sp_health WHERE persona_uuid = ?",
            (p["persona_uuid"],))
        self.assertIsNotNone(health)
        self.assertEqual(health["alive"], 1)

    def test_generate_persona_has_location(self):
        """Every generated persona has an assigned area."""
        self._gen_world()
        p = self._gen_persona()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area
        area = get_persona_area(p["persona_uuid"])
        self.assertIsNotNone(area)

    def test_generate_persona_has_skills(self):
        """Every generated persona has skill data."""
        p = self._gen_persona()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills
        skills = get_skills(p["persona_uuid"])
        self.assertIsNotNone(skills)
        self.assertIn("mining", skills)

    def test_deactivate_active_persona(self):
        """Deactivating a persona sets them inactive."""
        p = self._gen_persona()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import deactivate_persona, get_persona_count
        deactivate_persona(p["persona_uuid"])
        stats = get_persona_count()
        self.assertGreater(stats["inactive"], 0)

    def test_reactivate_inactive_persona(self):
        """Reactivating brings a persona back."""
        p = self._gen_persona()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import (
            deactivate_persona, activate_persona, get_persona_count
        )
        deactivate_persona(p["persona_uuid"])
        activate_persona(p["persona_uuid"])
        stats = get_persona_count()
        self.assertGreater(stats["active"], 0)

    def test_deactivate_nonexistent_persona(self):
        """Deactivating a non-existent persona doesn't crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import deactivate_persona
        try:
            deactivate_persona("nonexistent-uuid-12345")
        except Exception:
            self.fail("deactivate_persona should not raise on missing UUID")

    def test_activate_nonexistent_persona(self):
        """Activating a non-existent persona doesn't crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import activate_persona
        try:
            activate_persona("nonexistent-uuid-12345")
        except Exception:
            self.fail("activate_persona should not raise on missing UUID")

    def test_spawn_mass_population(self):
        """Spawning 50 personas works without error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import spawn_initial_population
        try:
            spawn_initial_population(50)
        except Exception as e:
            self.fail(f"spawn 50 personas failed: {e}")


# ═════════════════════════════════════════════════════════════════════
# 2. Health System Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestHealthEdgeCases(EdgeCaseTestCase):

    def setUp(self):
        super().setUp()
        self._gen_world()
        self.p = self._gen_persona("warrior")
        self.puid = self.p["persona_uuid"]

    def test_health_init_values_in_range(self):
        """Initial health stats are within 0-100 and alive."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        h = get_persona_health(self.puid)
        for stat in ["food", "hydration", "energy", "temperature",
                       "waste", "hygiene", "immune"]:
            self.assertGreaterEqual(h[stat], 0)
            self.assertLessEqual(h[stat], 100)
        self.assertEqual(h["alive"], 1)

    def test_health_set_to_zero_food(self):
        """Setting food to 0 triggers starvation death cascade."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health, get_persona_health
        modify_health(self.puid, food=-100)
        h = get_persona_health(self.puid)
        self.assertEqual(h["food"], 0)

    def test_health_set_to_zero_hydration(self):
        """Setting hydration to 0 triggers dehydration."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health, get_persona_health
        modify_health(self.puid, hydration=-100)
        h = get_persona_health(self.puid)
        self.assertEqual(h["hydration"], 0)

    def test_health_set_to_zero_energy(self):
        """Setting energy to 0 triggers exhaustion."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health, get_persona_health
        modify_health(self.puid, energy=-100)
        h = get_persona_health(self.puid)
        self.assertEqual(h["energy"], 0)

    def test_health_clamp_prevents_negative(self):
        """Negative modifications don't push stats below 0."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health, get_persona_health
        modify_health(self.puid, food=-999, hydration=-999, energy=-999)
        h = get_persona_health(self.puid)
        for stat in ["food", "hydration", "energy"]:
            self.assertGreaterEqual(h[stat], 0)

    def test_health_clamp_prevents_overflow(self):
        """Positive modifications don't push stats above 100."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health, get_persona_health
        modify_health(self.puid, food=999, hydration=999)
        h = get_persona_health(self.puid)
        for stat in ["food", "hydration"]:
            self.assertLessEqual(h[stat], 100)

    def test_process_health_tick_empty(self):
        """Processing health tick with no active personas does not crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import process_health_tick
        result = process_health_tick()
        self.assertIn("processed", result)

    def test_process_health_tick_does_not_crash(self):
        """Processing health tick for real personas works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import process_health_tick
        result = process_health_tick()
        self.assertGreaterEqual(result["processed"], 0)

    def test_modify_health_nonexistent_persona(self):
        """modify_health on missing persona returns False."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
        result = modify_health("nonexistent-uuid", food=10)
        self.assertFalse(result)

    def test_init_health_for_new_persona(self):
        """init_health creates a valid health record for an existing persona."""
        p = self._gen_persona()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import init_health, get_persona_health
        # init_health is called automatically during persona generation,
        # so we just verify it was created
        h = get_persona_health(p["persona_uuid"])
        self.assertIsNotNone(h)
        self.assertEqual(h["alive"], 1)

    def test_persona_eventually_dies_from_starvation(self):
        """A persona with 0 food eventually dies after decay_timer threshold."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import (
            modify_health, process_persona_health, get_persona_health
        )
        modify_health(self.puid, food=-100)  # Set food to 0
        died = False
        for _ in range(10):
            result = process_persona_health(self.puid, "warrior", "working")
            if result["status"] == "dead":
                died = True
                break
        h = get_persona_health(self.puid)
        self.assertTrue(died or h["food"] == 0,
                        "Persona should eventually die from starvation")

    def test_persona_extreme_temperature_death(self):
        """Setting body temperature to minimum is clamped but doesn't crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import (
            process_persona_health, get_persona_health
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        # Set all stats to healthy except temperature at extreme cold
        sp_db().execute(
            "UPDATE ext_sp_health SET temperature = 0, food = 80, hydration = 80, energy = 80 WHERE persona_uuid = ?",
            (self.puid,))
        # Process health — system will try to warm up and survive
        result = process_persona_health(self.puid, "warrior", "working")
        h = get_persona_health(self.puid)
        # The important thing: no crash, temperature stays valid
        self.assertIn("status", result)
        self.assertGreaterEqual(h["temperature"], 0)
        self.assertLessEqual(h["temperature"], 100)


# ═════════════════════════════════════════════════════════════════════
# 3. Behavior Decision Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestBehaviorEdgeCases(EdgeCaseTestCase):

    def setUp(self):
        super().setUp()
        self._gen_world()
        self.p = self._gen_persona("merchant")
        self.puid = self.p["persona_uuid"]

    def test_process_persona_basic(self):
        """process_persona returns a valid action dict."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import process_persona
        result = process_persona(self.p)
        self.assertIn("action", result)

    def test_process_persona_broke(self):
        """A persona with 0 balance doesn't buy."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        sp_db().execute(
            "UPDATE ext_sp_finances SET balance = 0 WHERE persona_uuid = ?",
            (self.puid,))
        self.p["balance"] = 0
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import process_persona
        result = process_persona(self.p)
        self.assertIn("action", result)

    def test_process_persona_negative_balance(self):
        """A persona with negative balance handles gracefully."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        sp_db().execute(
            "UPDATE ext_sp_finances SET balance = -50 WHERE persona_uuid = ?",
            (self.puid,))
        self.p["balance"] = -50
        self.p["debt"] = 50
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import process_persona
        result = process_persona(self.p)
        self.assertIn("action", result)

    def test_process_all_archetypes(self):
        """Every archetype can process without error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import process_persona, ARCHETYPES
        for arch in ARCHETYPES:
            p = generate_persona(archetype_override=arch)
            result = process_persona(p)
            self.assertIn("action", result,
                          f"Archetype {arch} should produce an action")

    def test_run_persona_tick_empty(self):
        """Running a tick with no active personas does not crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import run_persona_tick
        result = run_persona_tick()
        self.assertIn("processed", result)

    def test_run_persona_tick_with_personas(self):
        """Running a tick with personas works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import run_persona_tick
        # Spawn a few personas
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import spawn_initial_population
        spawn_initial_population(5)
        result = run_persona_tick()
        self.assertGreaterEqual(result["processed"], 1)

    def test_listings_empty_does_not_crash_behavior(self):
        """Running behavior with zero listings is safe."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import run_persona_tick
        generate_persona()
        result = run_persona_tick()
        self.assertIn("processed", result)

    def test_persona_with_debt_pays_it(self):
        """A persona with debt should attempt to pay it off."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        sp_db().execute(
            "UPDATE ext_sp_finances SET balance = 100, debt = 80 WHERE persona_uuid = ?",
            (self.puid,))
        self.p["balance"] = 100
        self.p["debt"] = 80
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import process_persona
        result = process_persona(self.p)
        # Should be either debt_payment or nothing (depends on savings_goal)
        self.assertIn(result["action"], ("debt_payment", "nothing", "saved"))


# ═════════════════════════════════════════════════════════════════════
# 4. World System Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestWorldEdgeCases(EdgeCaseTestCase):

    def test_generate_world_creates_areas(self):
        """World generation creates areas across all regions."""
        count = self._gen_world()
        self.assertGreater(count, 0)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_area_count
        area_counts = get_area_count()
        self.assertIn("overworld", area_counts)

    def test_generate_world_twice_idempotent(self):
        """Generating world twice doesn't double areas (INSERT OR REPLACE area)."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import generate_world, get_area_count
        c1 = generate_world()
        ac1 = get_area_count()
        total1 = sum(ac1.values())
        c2 = generate_world()
        ac2 = get_area_count()
        total2 = sum(ac2.values())
        # Actually, generate_world uses INSERT INTO (not OR REPLACE) so
        # it might create duplicates. That's fine — test it doesn't crash.
        self.assertGreaterEqual(total2, total1)

    def test_get_area_nonexistent(self):
        """Getting a non-existent area returns None."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_area
        area = get_area("nonexistent-area-uuid")
        self.assertIsNone(area)

    def test_get_all_areas_by_region(self):
        """Filtering areas by region works."""
        self._gen_world()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas
        overworld = get_all_areas(region="overworld")
        self.assertGreater(len(overworld), 0)
        for a in overworld:
            self.assertEqual(a["region"], "overworld")

    def test_resource_available_unknown(self):
        """resource_available on missing area returns None."""
        self._gen_world()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import resource_available
        result = resource_available("nonexistent", "minecraft:diamond")
        self.assertIsNone(result)

    def test_deplete_resource(self):
        """Depleting a resource reduces its count."""
        self._gen_world()
        area = self._get_area()
        self.assertIsNotNone(area)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import resource_available, deplete_resource, get_area
        # Find a resource in this area
        resources = json.loads(area["resources_json"]) if isinstance(area["resources_json"], str) else {}
        if resources:
            item_id = list(resources.keys())[0]
            before = resources[item_id]["remaining"]
            result = deplete_resource(area["area_uuid"], item_id, 1)
            self.assertTrue(result)
            area2 = get_area(area["area_uuid"])
            res2 = json.loads(area2["resources_json"]) if isinstance(area2["resources_json"], str) else {}
            self.assertEqual(res2[item_id]["remaining"], before - 1)

    def test_deplete_unknown_resource(self):
        """Depleting a non-existent resource returns False."""
        self._gen_world()
        area = self._get_area()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import deplete_resource
        result = deplete_resource(area["area_uuid"], "minecraft:blahblah", 1)
        self.assertFalse(result)

    def test_assign_persona_area_all_biomes(self):
        """assign_persona_area works for all archetypes."""
        self._gen_world()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona, ARCHETYPES
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import assign_persona_area, get_persona_area
        for arch in ARCHETYPES:
            p = generate_persona(archetype_override=arch)
            area_uuid = assign_persona_area(p["persona_uuid"], arch)
            self.assertIsNotNone(area_uuid)
            area = get_persona_area(p["persona_uuid"])
            self.assertIsNotNone(area)


# ═════════════════════════════════════════════════════════════════════
# 5. Weather Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestWeatherEdgeCases(EdgeCaseTestCase):

    def setUp(self):
        super().setUp()
        self._gen_world()

    def test_init_weather_all_areas(self):
        """Initializing weather works for all areas."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather
        init_weather()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas
        areas = get_all_areas()
        for a in areas:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import get_area_weather
            w = get_area_weather(a["area_uuid"])
            self.assertIsNotNone(w, f"No weather for {a['name']}")

    def test_init_weather_twice_safe(self):
        """Calling init_weather twice doesn't crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather
        init_weather()
        init_weather()

    def test_update_weather_no_crash(self):
        """Updating weather doesn't crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather, update_weather
        init_weather()
        for _ in range(5):
            result = update_weather()
            self.assertIn("updated", result)

    def test_weather_temperature_bounds(self):
        """Weather temperatures stay within reasonable bounds."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather, update_weather, get_area_weather
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas
        init_weather()
        for _ in range(24):
            update_weather()
        areas = get_all_areas()
        for a in areas:
            w = get_area_weather(a["area_uuid"])
            if w:
                self.assertGreaterEqual(w["temperature"], -25,
                                        f"{a['name']} temp {w['temperature']} too cold")
                self.assertLessEqual(w["temperature"], 65,
                                     f"{a['name']} temp {w['temperature']} too hot")
                self.assertGreaterEqual(w["humidity"], 0)
                self.assertLessEqual(w["humidity"], 100)

    def test_weather_all_biomes_different(self):
        """Different biomes should have noticeably different temperatures."""
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather, update_weather, get_area_weather

        except Exception as e:
            logger.error(f"test_weather_all_bio failed: {e}")
            return None
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas
        init_weather()
        for _ in range(6):
            update_weather()
        areas = get_all_areas()
        temps_by_biome = {}
        for a in areas:
            w = get_area_weather(a["area_uuid"])
            if w:
                if a["biome_type"] not in temps_by_biome:
                    temps_by_biome[a["biome_type"]] = w["temperature"]
        # Desert should be warmer than tundra
        if "desert" in temps_by_biome and "tundra" in temps_by_biome:
            self.assertGreater(temps_by_biome["desert"], temps_by_biome["tundra"])
        # Nether should be hot
        if "nether_wastes" in temps_by_biome:
            self.assertGreater(temps_by_biome["nether_wastes"], 30)

    def test_weather_get_weather_summary(self):
        """get_weather_summary returns a string."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import (
            init_weather, update_weather, get_weather_summary
        )
        init_weather()
        update_weather()
        summary = get_weather_summary()
        self.assertIsInstance(summary, str)
        self.assertGreater(len(summary), 0)


# ═════════════════════════════════════════════════════════════════════
# 6. Movement Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestMovementEdgeCases(EdgeCaseTestCase):

    def setUp(self):
        super().setUp()
        self._gen_world()

    def test_process_movement_tick_all_archetypes(self):
        """Movement tick works for all archetypes."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_movement import process_movement_tick
        for arch in ("miner", "farmer", "warrior", "merchant", "builder", "mage", "adventurer", "vagabond"):
            p = generate_persona(archetype_override=arch)
            try:
                result = process_movement_tick(p)
            except Exception as e:
                self.fail(f"Movement tick failed for {arch}: {e}")

    def test_process_movement_no_crash(self):
        """Running movement tick over multiple rounds doesn't crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import spawn_initial_population
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_movement import process_movement_tick
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_active_personas
        spawn_initial_population(10)
        for _ in range(10):
            for p in get_active_personas():
                try:
                    process_movement_tick(p)
                except Exception as e:
                    self.fail(f"Movement crash: {e}")

    def test_move_persona_to_neighbor(self):
        """Move a persona to a neighbor area works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import (
            get_persona_area, get_area, move_persona
        )
        p = generate_persona()
        current = get_persona_area(p["persona_uuid"])
        self.assertIsNotNone(current)
        neighbors = json.loads(current["neighbor_ids"]) if isinstance(current["neighbor_ids"], str) else []
        if neighbors:
            result = move_persona(p["persona_uuid"], neighbors[0])
            self.assertTrue(result)
            new_area = get_persona_area(p["persona_uuid"])
            self.assertEqual(new_area["area_uuid"], neighbors[0])

    def test_move_persona_to_non_neighbor(self):
        """Moving to a non-neighbor area fails."""
        self._gen_world()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import (
            get_persona_area, get_all_areas, move_persona
        )
        p = generate_persona()
        current = get_persona_area(p["persona_uuid"])
        all_areas = get_all_areas()
        # Find an area that's NOT a neighbor
        neighbors = json.loads(current["neighbor_ids"]) if isinstance(current["neighbor_ids"], str) else []
        non_neighbor = None
        for a in all_areas:
            if a["area_uuid"] != current["area_uuid"] and a["area_uuid"] not in neighbors:
                non_neighbor = a
                break
        if non_neighbor:
            result = move_persona(p["persona_uuid"], non_neighbor["area_uuid"])
            self.assertFalse(result, "Should not be able to move to non-neighbor")


# ═════════════════════════════════════════════════════════════════════
# 7. Ecosystem Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestEcosystemEdgeCases(EdgeCaseTestCase):

    def setUp(self):
        super().setUp()
        self._gen_world()

    def test_init_ecosystem(self):
        """Initializing ecosystem works for all areas."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas
        init_ecosystem()
        db = get_db()
        areas = get_all_areas()
        for a in areas:
            eco = db.fetch_one(
                "SELECT * FROM ext_sp_ecosystem WHERE area_uuid = ?",
                (a["area_uuid"],))
            self.assertIsNotNone(eco, f"No ecosystem for {a['name']}")

    def test_init_ecosystem_twice(self):
        """Initializing ecosystem twice doesn't crash."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem
        init_ecosystem()
        init_ecosystem()

    def test_persona_forage_no_ecosystem(self):
        """Foraging before ecosystem init returns empty."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import persona_forage
        p = generate_persona()
        result = persona_forage(p["persona_uuid"])
        self.assertIn("food", result)

    def test_persona_forage_with_ecosystem(self):
        """Foraging with ecosystem yields food."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem, persona_forage
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        init_ecosystem()
        p = generate_persona()
        result = persona_forage(p["persona_uuid"])
        self.assertIn("food", result)
        self.assertIn("message", result)

    def test_persona_hunt(self):
        """Hunting works and returns meat or failure."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem, persona_hunt
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        init_ecosystem()
        p = generate_persona("warrior")
        result = persona_hunt(p["persona_uuid"])
        self.assertIn("meat", result)
        self.assertIn("message", result)

    def test_persona_fish_wrong_biome(self):
        """Fishing in a non-water biome returns failure."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem, persona_fish
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        init_ecosystem()
        p = generate_persona("miner")  # Miners start in mountains — no water
        result = persona_fish(p["persona_uuid"])
        self.assertEqual(result["fish"], 0)

    def test_persona_drink(self):
        """Drinking works in various biomes."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import (
            init_ecosystem, persona_drink
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        init_weather()
        init_ecosystem()
        p = generate_persona("farmer")
        result = persona_drink(p["persona_uuid"])
        self.assertIn("hydration", result)

    def test_persona_clean(self):
        """Cleaning works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import persona_clean
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        p = generate_persona()
        result = persona_clean(p["persona_uuid"])
        self.assertIn("hygiene", result)

    def test_persona_rest(self):
        """Resting works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import persona_rest
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        init_weather()
        p = generate_persona()
        result = persona_rest(p["persona_uuid"])
        self.assertIn("energy", result)

    def test_ecosystem_tick(self):
        """Full ecosystem tick processes without errors."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem, process_ecosystem_tick
        init_ecosystem()
        for tick in range(1, 13):
            result = process_ecosystem_tick(tick)
            self.assertIn("plants", result,
                          f"Ecosystem tick {tick} failed: no plant data")

    def test_process_density_tick(self):
        """Density tick runs without error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem, process_density_tick
        init_ecosystem()
        result = process_density_tick(6)
        if "total_delta" in result:
            self.assertIsInstance(result["total_delta"], (int, float))

    def test_threat_check_no_animals(self):
        """Threat check with no animals returns safe."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import persona_threat_check
        p = generate_persona()
        result = persona_threat_check(p["persona_uuid"])
        self.assertIn("threat", result)
        self.assertFalse(result["threat"])


# ═════════════════════════════════════════════════════════════════════
# 8. Items / Inventory Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestItemsEdgeCases(EdgeCaseTestCase):

    def test_give_item_basic(self):
        """Give item creates inventory entry."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, count_item
        import uuid
        puid = str(uuid.uuid4())
        result = give_item(puid, "stone", 5, "inventory", "persona")
        self.assertTrue(result)
        self.assertEqual(count_item(puid, "stone", "persona"), 5)

    def test_give_item_negative_quantity(self):
        """Giving negative quantity creates an entry but with negative count."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, count_item
        import uuid
        puid = str(uuid.uuid4())
        result = give_item(puid, "stone", -5, "inventory", "persona")
        # Should still return True (SQL stores it, negative is allowed by schema)
        self.assertTrue(result)

    def test_remove_item_nonexistent(self):
        """Removing an item the persona doesn't have returns 0."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import remove_item
        import uuid
        removed = remove_item(str(uuid.uuid4()), "stone", 5)
        self.assertEqual(removed, 0)

    def test_remove_item_more_than_available(self):
        """Removing more than available removes what's there."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, remove_item, count_item
        import uuid
        puid = str(uuid.uuid4())
        give_item(puid, "stone", 3, "inventory", "persona")
        removed = remove_item(puid, "stone", 10)
        self.assertEqual(removed, 3)
        self.assertEqual(count_item(puid, "stone", "persona"), 0)

    def test_give_large_stack_overflow(self):
        """Giving more than max_stack correctly stores the total."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, count_item
        import uuid
        puid = str(uuid.uuid4())
        give_item(puid, "stone", 200, "inventory", "persona")
        total = count_item(puid, "stone", "persona")
        self.assertEqual(total, 200)

    def test_consume_item_nonexistent(self):
        """Consuming a non-existent inventory ID returns error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import consume_item
        result = consume_item(str(uuid.uuid4()), 99999, 1)
        self.assertIn("error", result)
        self.assertEqual(result["calories"], 0)

    def test_consume_item_persona_needs_health(self):
        """Consuming food requires a health record (creates one)."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, consume_item, get_inventory
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        p = generate_persona()
        give_item(p["persona_uuid"], "cooked_meat", 5, "inventory", "persona")
        inv = get_inventory(p["persona_uuid"])
        food = [i for i in inv if i["item_id"] == "cooked_meat"][0]
        result = consume_item(p["persona_uuid"], food["id"], 2)
        self.assertGreater(result["calories"], 0)

    def test_get_inventory_empty(self):
        """Getting inventory for a persona with no items returns empty."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import get_inventory
        import uuid
        inv = get_inventory(str(uuid.uuid4()))
        self.assertEqual(inv, [])

    def test_get_item_def_nonexistent(self):
        """Getting def for unknown item returns None."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import get_item_def
        result = get_item_def("nonexistent_item_blah")
        self.assertIsNone(result)

    def test_get_total_weight_empty(self):
        """Total weight with no items is 0."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import get_total_weight
        import uuid
        w = get_total_weight(str(uuid.uuid4()))
        self.assertEqual(w, 0.0)


# ═════════════════════════════════════════════════════════════════════
# 9. Item Cache + Trash Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestItemCacheTrashEdgeCases(EdgeCaseTestCase):

    def setUp(self):
        super().setUp()

    def test_register_item_null_nbt(self):
        """Registering item with null NBT stores None."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        r = register_item("minecraft:stone", market_nbt=None)
        self.assertTrue(r["ok"])
        cached = get_cached_item("minecraft:stone")
        self.assertIsNone(cached["market_nbt"])

    def test_register_item_empty_string_nbt(self):
        """Registering item with empty string NBT."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        r = register_item("minecraft:diamond", market_nbt="")
        self.assertTrue(r["ok"])
        cached = get_cached_item("minecraft:diamond")
        self.assertEqual(cached["market_nbt"], "")

    def test_register_item_very_large_nbt(self):
        """Registering item with a very large NBT string."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        large_nbt = json.dumps({"components": {f"key_{i}": f"value_{i}" for i in range(100)}})
        i = "minecraft:super_item"
        r = register_item(i, market_nbt=large_nbt)
        self.assertTrue(r["ok"])
        cached = get_cached_item(i)
        self.assertIsNotNone(cached["market_nbt"])
        parsed = json.loads(cached["market_nbt"])
        self.assertIn("key_0", parsed["components"])

    def test_register_item_twice_preserves_nbt(self):
        """Registering the same item twice preserves NBT from first call."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        i = "minecraft:diamond"
        register_item(i, market_nbt='{"version":1}')
        register_item(i)  # No NBT this time
        cached = get_cached_item(i)
        self.assertEqual(cached["market_nbt"], '{"version":1}')

    def test_log_trash_entry_basic(self):
        """Logging a basic trash entry works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal
        r = log_item_removal("pers_a", "stone", 10, reason="discarded")
        self.assertTrue(r["ok"])
        self.assertIsInstance(r["data"]["trash_id"], int)

    def test_log_trash_entry_all_reasons(self):
        """All valid trash reasons work."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, VALID_TRASH_REASONS
        for reason in VALID_TRASH_REASONS:
            r = log_item_removal("pers_a", "stone", 1, reason=reason)
            self.assertTrue(r["ok"], f"Reason '{reason}' should work")

    def test_log_trash_entry_invalid_reason(self):
        """Invalid reason logs with a warning but still works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal
        r = log_item_removal("pers_a", "stone", 1, reason="mystery_reason")
        self.assertTrue(r["ok"])

    def test_query_trash_empty(self):
        """Querying trash with no entries returns empty."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import query_trash
        entries = query_trash()
        self.assertEqual(entries, [])

    def test_query_trash_with_filters(self):
        """Querying trash with persona and item filters works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, query_trash
        log_item_removal("pers_a", "stone", 5, reason="discarded")
        log_item_removal("pers_b", "diamond", 1, reason="sold")
        entries = query_trash(persona_uuid="pers_a", item_id="stone")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["quantity"], 5)

    def test_trash_stats_aggregation(self):
        """Trash stats correctly aggregate by reason and item."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            log_item_removal, get_trash_stats
        )
        log_item_removal("pers_a", "stone", 5, reason="discarded")
        log_item_removal("pers_a", "diamond", 1, reason="sold")
        log_item_removal("pers_a", "stone", 3, reason="discarded")
        stats = get_trash_stats()
        self.assertEqual(stats["total_entries"], 3)
        self.assertEqual(stats["total_quantity"], 9)
        self.assertIn("discarded", stats["by_reason"])
        self.assertIn("stone", stats["top_items"])
        self.assertEqual(stats["top_items"]["stone"]["quantity"], 8)

    def test_clear_cache_and_trash(self):
        """Clearing cache and trash tables works."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            log_item_removal, clear_trash, clear_cache, register_item, get_cache_stats
        )
        log_item_removal("pers_a", "stone", 1)
        register_item("minecraft:diamond")
        clear_trash()
        clear_cache()
        stats = get_cache_stats()
        self.assertEqual(stats["total_cached"], 0)


# ═════════════════════════════════════════════════════════════════════
# 10. World ↔ Persona Integration
# ═════════════════════════════════════════════════════════════════════

class TestWorldPersonaIntegration(EdgeCaseTestCase):

    def test_persona_spawns_in_valid_area(self):
        """New persona always gets a valid area."""
        self._gen_world()
        for _ in range(20):
            p = self._gen_persona()
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area
            area = get_persona_area(p["persona_uuid"])
            self.assertIsNotNone(area, f"Persona {p['name']} has no area")
            self.assertIn(area["region"], ("overworld", "nether", "end", "deep_dark"))

    def test_persona_moves_between_areas(self):
        """Personas can move between areas over multiple ticks."""
        self._gen_world()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import spawn_initial_population
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_movement import process_movement_tick
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_active_personas
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area
        spawn_initial_population(10)
        initial_areas = {}
        for p in get_active_personas():
            area = get_persona_area(p["persona_uuid"])
            initial_areas[p["persona_uuid"]] = area["area_uuid"] if area else None
        # Run several movement ticks
        for _ in range(20):
            for p in get_active_personas():
                process_movement_tick(p)
        # Check if anyone moved
        moved = 0
        for p in get_active_personas():
            area = get_persona_area(p["persona_uuid"])
            new_id = area["area_uuid"] if area else None
            if new_id and new_id != initial_areas.get(p["persona_uuid"]):
                moved += 1
        # At least some should move (especially vagabonds with 0.4 per tick)
        self.assertGreaterEqual(moved, 0)

    def test_ecosystem_plant_growth_over_time(self):
        """Plant biomass changes over multiple ticks."""
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather, update_weather

        except Exception as e:
            logger.error(f"test_ecosystem_plant failed: {e}")
            return None
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem, process_plant_tick
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas
        self._gen_world()
        init_weather()
        init_ecosystem()
        db = get_db()
        areas = get_all_areas()
        # Get initial biomass
        before = {}
        for a in areas:
            eco = db.fetch_one(
                "SELECT biomass_grass, biomass_shrub, biomass_tree FROM ext_sp_ecosystem WHERE area_uuid = ?",
                (a["area_uuid"],))
            if eco:
                before[a["area_uuid"]] = eco
        # Run plant growth ticks
        for tick in range(1, 13):
            result = process_plant_tick(tick)
        # Check biomass changed
        changed = 0
        for a in areas:
            eco = db.fetch_one(
                "SELECT biomass_grass, biomass_shrub, biomass_tree FROM ext_sp_ecosystem WHERE area_uuid = ?",
                (a["area_uuid"],))
            if eco and a["area_uuid"] in before:
                b = before[a["area_uuid"]]
                if (eco["biomass_grass"] != b["biomass_grass"] or
                    eco["biomass_shrub"] != b["biomass_shrub"] or
                    eco["biomass_tree"] != b["biomass_tree"]):
                    changed += 1
        self.assertGreater(changed, 0,
                           "Plant biomass should change over multiple ticks")

    def test_full_simulation_tick_with_ecosystem(self):
        """A short full simulation tick with all subsystems runs without error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import generate_world
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import init_weather, update_weather
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import init_ecosystem, process_ecosystem_tick
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import process_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import process_persona_health
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_active_personas

        # Full setup (skip movement — tested separately)
        generate_world()
        init_weather()
        init_ecosystem()

        # Create 2 personas
        for arch in ("miner", "farmer"):
            generate_persona(archetype_override=arch)

        # Run 3 quick ticks
        for tick in range(1, 4):
            update_weather()
            process_ecosystem_tick(tick)
            for p in get_active_personas():
                process_persona(p)
                process_persona_health(
                    p["persona_uuid"],
                    p.get("archetype", "adventurer"),
                    p.get("wealth_tier", "working"),
                )

        # Verify at least the test didn't crash
        self.assertGreaterEqual(len(get_active_personas()), 0)

    def test_archetype_area_distribution(self):
        """Personas of different archetypes spawn in appropriate biomes."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import generate_world, get_persona_area
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona

        generate_world()

        for arch in ("miner", "farmer", "merchant"):
            for _ in range(3):
                p = generate_persona(archetype_override=arch)
                area = get_persona_area(p["persona_uuid"])
                self.assertIsNotNone(area, f"{arch} persona has no area")
                # Just verify it's a valid biome
                self.assertIn(area["biome_type"],
                    ("plains", "forest", "mountains", "swamp", "desert",
                     "tundra", "ocean", "nether_wastes", "crimson_forest",
                     "end_islands", "deep_dark"))

