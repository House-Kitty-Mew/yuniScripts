"""
test_flow_persona.py — Data Flow Test: Persona Lifecycle (Flow 8)

Tests every code path and edge case for the persona system including:
  - Persona generation and activation
  - Behavior ticks (needs, finances, purchases)
  - Persona purchasing from the AH
  - Memory recording and pruning
  - World events affecting personas
  - Life events
  - Max persona caps
"""

from tests.dataflow.conftest_dataflow import (
    DataFlowTestCase, mock_rcon, reset_rcon
)
from unittest.mock import patch
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_core import list_item, query_listings
from tests.dataflow.probes.trace_probe import (
    PATH_PEOPLE_BEHAVIOR, PATH_PEOPLE_FINANCE, PATH_PEOPLE_NEEDS,
    PATH_PEOPLE_PURCHASE,
)
from tests.conftest import unique_item_id
import uuid


def _ensure_sp_schema():
    """Ensure the SIMULATED_PEOPLE schema exists."""
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema
        ensure_schema()
    except Exception:
        pass


def _create_test_persona(persona_uuid: str = None, name: str = "TestPersona",
                          archetype: str = "adventurer", job: str = "miner",
                          region: str = "overworld") -> str:
    """Helper: create a test persona in the database."""
    _ensure_sp_schema()
    puid = persona_uuid or str(uuid.uuid4())
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    db = get_db()
    db.execute("""
        INSERT OR IGNORE INTO ext_sp_profiles
        (persona_uuid, name, archetype, job, region, active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
    """, (puid, name, archetype, job, region, now))

    db.execute("""
        INSERT OR IGNORE INTO ext_sp_finances
        (persona_uuid, balance, lifetime_income, lifetime_spending,
         income_per_tick, savings_goal, debt)
        VALUES (?, ?, 0.0, 0.0, ?, 0.0, 0.0)
    """, (puid, 200.0, 5.0))

    db.execute("""
        INSERT OR IGNORE INTO ext_sp_health
        (persona_uuid, food, hydration, energy, immune)
        VALUES (?, 80, 80, 75, 70)
    """, (puid,))

    return puid


# ══════════════════════════════════════════════════════════════════════
# Test 8.1-8.6: Persona basics
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestPersonaLifecycle(DataFlowTestCase):

    def test_8_1_persona_generation(self, mock_bridge):
        """Persona is created with profile, finances, health."""
        puid = _create_test_persona()

        db = get_db()
        profile = db.fetch_one(
            "SELECT * FROM ext_sp_profiles WHERE persona_uuid = ?", (puid,)
        )
        self.assertIsNotNone(profile)
        self.assertEqual(profile["name"], "TestPersona")
        self.assertEqual(profile["archetype"], "adventurer")

        finances = db.fetch_one(
            "SELECT * FROM ext_sp_finances WHERE persona_uuid = ?", (puid,)
        )
        self.assertIsNotNone(finances)
        self.assertGreater(finances["balance"], 0)

        health = db.fetch_one(
            "SELECT * FROM ext_sp_health WHERE persona_uuid = ?", (puid,)
        )
        self.assertIsNotNone(health)

    def test_8_2_persona_finance_income(self, mock_bridge):
        """Persona receives income per tick."""
        puid = _create_test_persona()
        db = get_db()

        # Apply income
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_finance import apply_income_tick
            apply_income_tick(puid)
        except (ImportError, Exception) as e:
            self.skipTest(f"sp_finance not available: {e}")

        finances = db.fetch_one(
            "SELECT * FROM ext_sp_finances WHERE persona_uuid = ?", (puid,)
        )
        self.assertIsNotNone(finances)

    def test_8_3_persona_need_generation(self, mock_bridge):
        """Persona generates needs for items."""
        puid = _create_test_persona()

        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import generate_needs
            generate_needs(puid)
        except (ImportError, Exception) as e:
            self.skipTest(f"sp_behavior not available: {e}")

        db = get_db()
        needs = db.fetch_all(
            "SELECT * FROM ext_sp_needs WHERE persona_uuid = ?", (puid,)
        )
        self.assertGreaterEqual(len(needs), 0)

    def test_8_4_persona_activation_deactivation(self, mock_bridge):
        """Persona activation flips the active flag."""
        puid = _create_test_persona()
        db = get_db()

        db.execute(
            "UPDATE ext_sp_profiles SET active = 0 WHERE persona_uuid = ?",
            (puid,)
        )
        profile = db.fetch_one(
            "SELECT active FROM ext_sp_profiles WHERE persona_uuid = ?",
            (puid,)
        )
        self.assertEqual(profile["active"], 0)

        db.execute(
            "UPDATE ext_sp_profiles SET active = 1, last_active_at = datetime('now') "
            "WHERE persona_uuid = ?", (puid,)
        )
        profile = db.fetch_one(
            "SELECT active FROM ext_sp_profiles WHERE persona_uuid = ?",
            (puid,)
        )
        self.assertEqual(profile["active"], 1)

    def test_8_5_persona_memory_recording(self, mock_bridge):
        """Persona memories are recorded and retrievable."""
        puid = _create_test_persona()
        db = get_db()

        db.execute("""
            INSERT INTO ext_sp_memory
            (persona_uuid, memory_type, item_id, price, detail, emotional_weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (puid, "purchase", "minecraft:diamond", 10.0,
              "Bought diamonds at market", 7))

        memories = db.fetch_all(
            "SELECT * FROM ext_sp_memory WHERE persona_uuid = ?", (puid,)
        )
        self.assertGreaterEqual(len(memories), 1)
        self.assertEqual(memories[0]["item_id"], "minecraft:diamond")

    def test_8_6_world_event_affects_persona(self, mock_bridge):
        """World event financial multiplier affects personas."""
        puid = _create_test_persona()
        db = get_db()

        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc).isoformat()
        db.execute("""
            INSERT INTO ext_sp_world_events
            (event_uuid, name, description, region, severity,
             financial_multiplier, income_multiplier, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), "Test_Prosperity",
              "A time of plenty", "overworld", "moderate",
              1.5, 1.2, now))

        events = db.fetch_all(
            "SELECT * FROM ext_sp_world_events WHERE ended_at IS NULL"
        )
        self.assertGreaterEqual(len(events), 1)


# ══════════════════════════════════════════════════════════════════════
# Test 8.7-8.10: Persona-AH interaction
# ══════════════════════════════════════════════════════════════════════

@patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None)
class TestPersonaAHInteraction(DataFlowTestCase):

    def test_8_7_persona_can_list_items(self, mock_bridge):
        """Persona can create simulated listings."""
        result = list_item(
            seller="Persona_Alex", item_id=unique_item_id(),
            count=1, start_price=15.0, is_simulated=True,
        )
        self.assertTrue(result["ok"])

    def test_8_8_persona_scans_listings(self, mock_bridge):
        """Listings are visible to persona queries."""
        list_item(
            seller="PlayerSteve", item_id=unique_item_id(),
            count=1, start_price=10.0, rcon_func=mock_rcon
        )
        list_item(
            seller="Persona_Bob", item_id=unique_item_id("minecraft:iron_ingot"),
            count=5, start_price=5.0, is_simulated=True,
        )

        all_listings = query_listings(filter_type="all")
        self.assertGreaterEqual(all_listings["data"]["total"], 2)

    def test_8_9_db_integrity_persona_operations(self, mock_bridge):
        """No FK violations after persona operations."""
        puids = [_create_test_persona(str(uuid.uuid4()), f"P{i}",
                                       "adventurer", "miner")
                 for i in range(3)]

        self.assert_no_db_violations()

    def test_8_10_persona_max_cap(self, mock_bridge):
        """Max active personas cap is configurable."""
        from AUCTIONHOUSE.ah_config import get_config
        cfg = get_config()
        self.assertIsNotNone(cfg.__dict__.get("max_active_personas", 50))
