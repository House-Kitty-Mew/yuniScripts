"""
test_shm_integration.py — 3-Round Full Integration Test.

Each round tests the complete SHM system end-to-end with simulated
personas, running through:
  - Round 1 (Foundation): Initialization, blood, anatomy, genetics
  - Round 2 (Systems Active): Disease, immune, hygiene, muscle, pain
  - Round 3 (Combat Extension): Combat skills, negative traits, AI decisions

After all 3 rounds, validates every subsystem produced correct output.
"""

import sys, os, json, uuid, random, time, logging

log = logging.getLogger(__name__)
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHM_DIR = os.path.dirname(_HERE)
_EXT_DIR = os.path.dirname(_SHM_DIR)
_AH_DIR = os.path.dirname(_EXT_DIR)
_MANAGER_DIR = os.path.dirname(_AH_DIR)
for p in [_SHM_DIR, _EXT_DIR, _AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from AUCTIONHOUSE.ah_database import get_db, initialize_database

# ── SP Schema (replicated here to avoid engine.config_loader dep) ────

SP_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ext_sp_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    archetype TEXT NOT NULL,
    job TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'overworld',
    wealth_tier TEXT NOT NULL DEFAULT 'working',
    personality_traits TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_active_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sp_profiles_active ON ext_sp_profiles(active);
CREATE INDEX IF NOT EXISTS idx_sp_profiles_archetype ON ext_sp_profiles(archetype);

CREATE TABLE IF NOT EXISTS ext_sp_finances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL UNIQUE,
    balance REAL NOT NULL DEFAULT 0.0,
    lifetime_income REAL NOT NULL DEFAULT 0.0,
    lifetime_spending REAL NOT NULL DEFAULT 0.0,
    income_per_tick REAL NOT NULL DEFAULT 0.0,
    savings_goal REAL NOT NULL DEFAULT 0.0,
    debt REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS ext_sp_needs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL,
    item_id TEXT NOT NULL,
    urgency INTEGER NOT NULL DEFAULT 5,
    max_price REAL NOT NULL DEFAULT 0.0,
    desired_quantity INTEGER NOT NULL DEFAULT 1,
    quantity_obtained INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_needs_persona ON ext_sp_needs(persona_uuid);

CREATE TABLE IF NOT EXISTS ext_sp_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    item_id TEXT,
    price REAL,
    detail TEXT,
    emotional_weight INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_memory_persona ON ext_sp_memory(persona_uuid);

CREATE TABLE IF NOT EXISTS ext_sp_life_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT,
    financial_impact REAL NOT NULL DEFAULT 0.0,
    mood_impact TEXT NOT NULL DEFAULT 'neutral',
    duration_hours INTEGER NOT NULL DEFAULT 24,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS ext_sp_world_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    region TEXT NOT NULL DEFAULT 'global',
    severity TEXT NOT NULL DEFAULT 'moderate',
    financial_multiplier REAL NOT NULL DEFAULT 1.0,
    income_multiplier REAL NOT NULL DEFAULT 1.0,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS ext_sp_world_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area_uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'overworld',
    biome_type TEXT NOT NULL DEFAULT 'plains',
    neighbor_ids TEXT,
    owner_uuid TEXT,
    resources_json TEXT,
    is_hotzone INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_wa_region ON ext_sp_world_areas(region);

CREATE TABLE IF NOT EXISTS ext_sp_persona_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL UNIQUE,
    mining INTEGER NOT NULL DEFAULT 10,
    combat INTEGER NOT NULL DEFAULT 10,
    farming INTEGER NOT NULL DEFAULT 10,
    trading INTEGER NOT NULL DEFAULT 10,
    crafting INTEGER NOT NULL DEFAULT 10,
    exploration INTEGER NOT NULL DEFAULT 10,
    leadership INTEGER NOT NULL DEFAULT 10
);

CREATE TABLE IF NOT EXISTS ext_sp_persona_location (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL UNIQUE,
    area_uuid TEXT NOT NULL,
    moved_last_tick INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sp_loc_area ON ext_sp_persona_location(area_uuid);

CREATE TABLE IF NOT EXISTS ext_sp_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL UNIQUE,
    food INTEGER NOT NULL DEFAULT 80,
    hydration INTEGER NOT NULL DEFAULT 80,
    energy INTEGER NOT NULL DEFAULT 75,
    temperature INTEGER NOT NULL DEFAULT 50,
    waste INTEGER NOT NULL DEFAULT 0,
    hygiene INTEGER NOT NULL DEFAULT 70,
    immune INTEGER NOT NULL DEFAULT 70,
    alive INTEGER NOT NULL DEFAULT 1,
    cause_of_death TEXT,
    decay_timer INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ext_sp_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid TEXT NOT NULL,
    listing_uuid TEXT,
    transaction_type TEXT NOT NULL,
    item_id TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    price REAL NOT NULL DEFAULT 0.0,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ext_sp_wounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wound_uuid TEXT NOT NULL UNIQUE,
    owner_uuid TEXT NOT NULL,
    body_part TEXT NOT NULL,
    wound_type TEXT NOT NULL,
    severity INTEGER NOT NULL DEFAULT 1,
    bleed_rate REAL NOT NULL DEFAULT 0.0,
    pain_level REAL NOT NULL DEFAULT 0.0,
    infection_chance REAL NOT NULL DEFAULT 0.3,
    infection_progress REAL NOT NULL DEFAULT 0.0,
    is_infected INTEGER NOT NULL DEFAULT 0,
    is_bandaged INTEGER NOT NULL DEFAULT 0,
    is_healed INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_w_owner ON ext_sp_wounds(owner_uuid);

CREATE TABLE IF NOT EXISTS ext_sp_board (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board_uuid TEXT NOT NULL UNIQUE,
    persona_uuid TEXT NOT NULL,
    persona_name TEXT NOT NULL,
    item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    max_price REAL NOT NULL,
    urgency INTEGER NOT NULL DEFAULT 5,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    fulfilled_by TEXT,
    fulfilled_at TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
"""

# Already defined in SP schema
SP_WEATHER_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_weather (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area_uuid TEXT NOT NULL UNIQUE,
    temperature REAL NOT NULL DEFAULT 20.0,
    humidity REAL NOT NULL DEFAULT 50.0,
    cloud_cover REAL NOT NULL DEFAULT 0.2,
    precipitation_mm REAL NOT NULL DEFAULT 0.0,
    wind_speed REAL NOT NULL DEFAULT 2.0,
    wind_direction REAL NOT NULL DEFAULT 0.0,
    pressure REAL NOT NULL DEFAULT 1013.0,
    is_raining INTEGER NOT NULL DEFAULT 0,
    is_snowing INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_weather_area ON ext_sp_weather(area_uuid);
"""


def _init_sp_schema():
    """Initialize SP tables directly (bypass engine.config_loader dependency)."""
    db = get_db()
    for stmt in SP_SCHEMA_SQL.strip().split(";"):
        s = stmt.strip()
        if s and s.upper().startswith("CREATE"):
            try:
                db.execute(s)
            except Exception as e:
                print(f"  WARN SP schema: {e}")
    for stmt in SP_WEATHER_TABLE.strip().split(";"):
        s = stmt.strip()
        if s and s.upper().startswith("CREATE"):
            try:
                db.execute(s)
            except Exception:
                pass


def _init_shm_schema():
    """Initialize SHM tables."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import ensure_schema
    ensure_schema()


def _full_reset():
    """Clean wipe of all relevant tables."""
    initialize_database()
    _init_sp_schema()
    _init_shm_schema()
    db = get_db()
    tables = [
        "ext_shm_blood", "ext_shm_blood_regeneration",
        "ext_shm_anatomy_bones", "ext_shm_anatomy_organs",
        "ext_shm_muscles", "ext_shm_genetics",
        "ext_shm_diseases", "ext_shm_disease_spread",
        "ext_shm_hygiene", "ext_shm_immune_response",
        "ext_shm_pain", "ext_shm_combat_skills",
        "ext_shm_negative_traits", "ext_shm_healing_log",
        "ext_shm_ai_decisions",
        "ext_sp_profiles", "ext_sp_finances", "ext_sp_health",
        "ext_sp_persona_skills", "ext_sp_memory", "ext_sp_needs",
        "ext_sp_persona_location", "ext_sp_world_areas",
        "ext_sp_wounds", "ext_sp_transactions",
        "ext_sp_life_events", "ext_sp_weather",
    ]
    for t in tables:
        try:
            db.execute(f"DELETE FROM {t}")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# Test helpers
# ══════════════════════════════════════════════════════════════════════

_TEST_LOG: list[str] = []


def _log(msg: str):
    _TEST_LOG.append(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")
    print(msg)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _create_test_persona(puuid: str, name: str, archetype: str,
                          region: str = "overworld") -> bool:
    """Create a minimal test persona with full SHM initialization."""
    db = get_db()
    now = _now()
    try:
        # Determine wealth tier
        wealth_map = {
            "warrior": "middle", "mage": "middle", "merchant": "wealthy",
            "farmer": "working", "miner": "middle", "builder": "working",
            "adventurer": "working", "vagabond": "poor",
        }
        wealth = wealth_map.get(archetype, "working")

        db.execute("""
            INSERT OR IGNORE INTO ext_sp_profiles
            (persona_uuid, name, archetype, job, region, wealth_tier,
             personality_traits, active, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?, '{}', 1, ?, ?)
        """, (puuid, name, archetype, f"{archetype}_job", region, wealth, now, now))

        db.execute("""
            INSERT OR IGNORE INTO ext_sp_health
            (persona_uuid, food, hydration, energy, temperature,
             waste, hygiene, immune, alive)
            VALUES (?, ?, ?, ?, 50, 0, ?, ?, 1)
        """, (puuid, random.randint(60, 90), random.randint(60, 90),
              random.randint(50, 80),
              random.randint(50, 80), random.randint(50, 80)))

        db.execute("""
            INSERT OR IGNORE INTO ext_sp_finances
            (persona_uuid, balance, lifetime_income, lifetime_spending,
             income_per_tick, savings_goal, debt)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (puuid, random.uniform(50, 500), 100, 0,
              random.uniform(1, 10), random.uniform(100, 500)))

        # Initialize SHM data
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import (
            initialize_persona
        )
        initialize_persona(puuid, archetype)
        return True
    except Exception as e:
        _log(f"  ERROR creating persona {name}: {e}")
        import traceback
        traceback.print_exc()
        return False


def _verify_table_has_data(puuid: str, table: str, min_rows: int = 1) -> bool:
    """Check a table has at least min_rows for a persona."""
    db = get_db()
    try:
        row = db.fetch_one(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE persona_uuid = ?",
            (puuid,))
        return row and row["cnt"] >= min_rows
    except Exception:
        return False


def _verify_blood_values(puuid: str) -> list[str]:
    """Verify blood system values are in valid ranges."""
    try:
        errors = []

    except Exception as e:
        log.error(f"_verify_blood_values failed: {e}")
        return []
    db = get_db()
    blood = db.fetch_one("SELECT * FROM ext_shm_blood WHERE persona_uuid = ?", (puuid,))
    if not blood:
        return ["Blood record missing"]

    if blood["blood_volume_ml"] < 1000 or blood["blood_volume_ml"] > 7000:
        errors.append(f"Blood volume {blood['blood_volume_ml']} out of range")
    if blood["oxygen_saturation"] < 50 or blood["oxygen_saturation"] > 100:
        errors.append(f"O2 sat {blood['oxygen_saturation']} out of range")
    if blood["platelets"] < 0 or blood["platelets"] > 100:
        errors.append(f"Platelets {blood['platelets']} out of range")

    return errors


def _verify_bone_count(puuid: str) -> list[str]:
    """Verify exactly 206 bones."""
    errors = []
    db = get_db()
    count = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM ext_shm_anatomy_bones WHERE persona_uuid = ?",
        (puuid,))
    if not count or count["cnt"] != 206:
        errors.append(f"Bone count {count['cnt'] if count else 0} != 206")
    return errors


def _verify_organ_count(puuid: str) -> list[str]:
    """Verify 13 organs."""
    errors = []
    db = get_db()
    count = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM ext_shm_anatomy_organs WHERE persona_uuid = ?",
        (puuid,))
    if not count or count["cnt"] != 13:
        errors.append(f"Organ count {count['cnt'] if count else 0} != 13")
    return errors


def _verify_muscle_count(puuid: str) -> list[str]:
    """Verify 12 muscle groups."""
    errors = []
    db = get_db()
    count = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM ext_shm_muscles WHERE persona_uuid = ?",
        (puuid,))
    if not count or count["cnt"] != 12:
        errors.append(f"Muscle count {count['cnt'] if count else 0} != 12")
    return errors


def _verify_skill_count(puuid: str) -> list[str]:
    """Verify 10 combat skills."""
    errors = []
    db = get_db()
    count = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM ext_shm_combat_skills WHERE persona_uuid = ?",
        (puuid,))
    if not count or count["cnt"] != 10:
        errors.append(f"Combat skill count {count['cnt'] if count else 0} != 10")
    return errors


def _verify_genetics(puuid: str) -> list[str]:
    """Verify genetics are within valid range."""
    errors = []
    db = get_db()
    genetics = db.fetch_one(
        "SELECT * FROM ext_shm_genetics WHERE persona_uuid = ?", (puuid,))
    if not genetics:
        return ["Genetics record missing"]

    for key in ["metabolic_rate", "immune_potency", "pain_tolerance",
                 "healing_factor", "disease_resistance", "muscle_growth_rate",
                 "toxin_resistance", "blood_efficiency", "organ_vitality",
                 "nerve_density"]:
        val = genetics.get(key, 0)
        if val < 0.3 or val > 2.0:
            errors.append(f"Genetics {key}={val} out of range [0.3, 2.0]")

    return errors


# ══════════════════════════════════════════════════════════════════════
# Round 1: Foundation - Initialization & Basic Systems
# ══════════════════════════════════════════════════════════════════════

def round1_foundation() -> dict:
    """Round 1: Initialize personas and verify basic systems.

    Tests:
      - Schema creation
      - Persona SHM initialization (blood, 206 bones, 13 organs, 12 muscles)
      - Genetics generation
      - Hygiene initialization
      - Combat skill initialization
      - Nutrient absorption
    """
    try:
        _log("\n=== ROUND 1: FOUNDATION ===")

    except Exception as e:
        log.error(f"round1_foundation failed: {e}")
        return {}
    _full_reset()

    # Create 3 personas of different archetypes
    personas = [
        ("uuid-warrior-001", "Hilda Ironheart", "warrior"),
        ("uuid-mage-002", "Merlin Frostweaver", "mage"),
        ("uuid-vagabond-003", "Rex Driftwood", "vagabond"),
    ]

    round_errors = []
    for puuid, name, archetype in personas:
        _log(f"  Creating {name} ({archetype})...")
        ok = _create_test_persona(puuid, name, archetype)
        if not ok:
            round_errors.append(f"Failed to create {name}")
            continue

        # Verify all basic systems
        errors = []
        errors.extend(_verify_blood_values(puuid))
        errors.extend(_verify_bone_count(puuid))
        errors.extend(_verify_organ_count(puuid))
        errors.extend(_verify_muscle_count(puuid))
        errors.extend(_verify_skill_count(puuid))
        errors.extend(_verify_genetics(puuid))

        # Verify hygiene
        db = get_db()
        hygiene = db.fetch_one(
            "SELECT * FROM ext_shm_hygiene WHERE persona_uuid = ?", (puuid,))
        if not hygiene:
            errors.append("Hygiene record missing")
        else:
            for key in ["personal_cleanliness", "oral_hygiene",
                         "clothing_cleanliness", "wound_care",
                         "environment_cleanliness"]:
                if hygiene[key] < 0 or hygiene[key] > 100:
                    errors.append(f"Hygiene {key}={hygiene[key]} out of range")

        # Verify blood regeneration
        regen = db.fetch_one(
            "SELECT * FROM ext_shm_blood_regeneration WHERE persona_uuid = ?", (puuid,))
        if not regen:
            errors.append("Blood regeneration record missing")

        if errors:
            round_errors.extend([f"{name}: {e}" for e in errors])
            _log(f"    FAILED: {len(errors)} errors")
        else:
            _log(f"    PASSED: all systems verified")

    # Test nutrient absorption on warrior
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
        process_nutrient_absorption
    )
    nutrient_result = process_nutrient_absorption(
        "uuid-warrior-001", 50.0, "meat")
    if "error" in nutrient_result:
        round_errors.append(f"Nutrient absorption failed: {nutrient_result['error']}")
    else:
        _log(f"    Nutrient absorption: iron_gain={nutrient_result.get('iron_gain', 0)}")

    return {
        "round": 1,
        "name": "Foundation",
        "personas_created": len(personas) - len([e for e in round_errors if "Failed" in e]),
        "errors": round_errors,
        "passed": len(round_errors) == 0,
    }


# ══════════════════════════════════════════════════════════════════════
# Round 2: Systems Active - Disease, Immune, Hygiene, Muscle, Pain
# ══════════════════════════════════════════════════════════════════════

def round2_systems_active() -> dict:
    """Round 2: Activate all dynamic systems.

    Tests:
      - Disease infection & progression
      - Immune response initiation
      - Hygiene decay from activities
      - Muscle use, fatigue, and recovery
      - Pain generation from wounds/fractures
      - Anatomy fracture & healing
      - Blood loss from wounds
      - Disease spread between personas
    """
    try:
        _log("\n=== ROUND 2: SYSTEMS ACTIVE ===")

    except Exception as e:
        log.error(f"round2_systems_activ failed: {e}")
        return {}
    _full_reset()

    # Create test personas
    _create_test_persona("uuid-round2-p1", "TestSubject Alpha", "warrior")
    _create_test_persona("uuid-round2-p2", "TestSubject Beta", "farmer")
    _create_test_persona("uuid-round2-p3", "TestSubject Gamma", "merchant")

    # Put all in same area for disease spread
    db = get_db()
    area_uuid = "test-area-round2"
    now = _now()
    db.execute("""
        INSERT OR IGNORE INTO ext_sp_world_areas
        (area_uuid, name, region, biome_type, created_at)
        VALUES (?, 'test_area', 'overworld', 'plains', ?)
    """, (area_uuid, now))
    for puuid in ["uuid-round2-p1", "uuid-round2-p2", "uuid-round2-p3"]:
        db.execute("""
            INSERT OR IGNORE INTO ext_sp_persona_location
            (persona_uuid, area_uuid) VALUES (?, ?)
        """, (puuid, area_uuid))

    round_errors = []

    # ── 2a. Test disease infection ──────────────────────────────────
    _log("  2a. Testing disease infection...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_disease import (
        expose_to_disease, process_disease_tick, has_disease,
        try_spread_disease
    )

    # Infect warrior with wound infection
    result = expose_to_disease("uuid-round2-p1", "wound_infection",
                                "combat_wound", force_infection=True)
    if not result.get("infected"):
        round_errors.append("Force infection failed")
    else:
        _log(f"    Infected: incubation={result.get('incubation_ticks')} ticks")

    # Run disease ticks to progress
    reached_acute = False
    for _ in range(20):
        ticks = process_disease_tick("uuid-round2-p1")
        for t in ticks:
            if t.get("stage") in ("acute", "immune_battle"):
                reached_acute = True

    if not reached_acute:
        round_errors.append("Disease did not reach acute/immune_battle stage")
    else:
        _log("    Disease reached acute/immune_battle stage")

    # ── 2b. Test immune response ────────────────────────────────────
    _log("  2b. Testing immune response...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_immune import (
        start_immune_response, process_immune_ticks, get_immune_status
    )

    # Check if disease already triggered an immune response (it should have)
    immune = db.fetch_all(
        "SELECT * FROM ext_shm_immune_response WHERE persona_uuid = 'uuid-round2-p1' AND is_active = 1")
    _log(f"    Existing immune responses: {len(immune)}")
    if not immune:
        start_immune_response("uuid-round2-p1", "disease",
                               "wound_infection", 50.0, 0.5)

    # Run immune ticks on any active responses
    immune_ticks = process_immune_ticks("uuid-round2-p1")
    if immune_ticks:
        _log(f"    Immune battle ticks: {len(immune_ticks)} processed")
        for it in immune_ticks:
            _log(f"      - target={it.get('target','?')}, "
                 f"strength={it.get('immune_strength',0):.1f}, "
                 f"inflammation={it.get('inflammation',0):.1f}")

    # ── 2c. Test hygiene decay ──────────────────────────────────────
    _log("  2c. Testing hygiene decay...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_hygiene import (
        process_hygiene_decay_tick, get_hygiene
    )

    hygiene_before = get_hygiene("uuid-round2-p1")
    for _ in range(5):
        process_hygiene_decay_tick("uuid-round2-p1", "combat")
    hygiene_after = get_hygiene("uuid-round2-p1")
    if hygiene_after["personal_cleanliness"] >= hygiene_before["personal_cleanliness"]:
        round_errors.append("Hygiene did not decay")
    else:
        _log(f"    Hygiene decayed: {hygiene_before['personal_cleanliness']:.0f} -> {hygiene_after['personal_cleanliness']:.0f}")

    # ── 2d. Test muscle system ──────────────────────────────────────
    _log("  2d. Testing muscle system...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import (
        use_muscles, process_muscle_recovery_tick, injure_muscle,
        get_muscle
    )

    # Use muscles to build fatigue
    for _ in range(3):
        use_muscles("uuid-round2-p1", "combat", 1.5)

    # Verify fatigue accumulated
    chest = get_muscle("uuid-round2-p1", "chest")
    if chest and chest["fatigue"] > 0:
        _log(f"    Muscle fatigue: {chest['fatigue']:.1f}")

    # Test muscle injury
    injury = injure_muscle("uuid-round2-p1", "biceps", 0.6)
    if "error" in injury:
        round_errors.append(f"Muscle injury failed: {injury['error']}")
    else:
        _log(f"    Muscle injured: penalty={injury.get('injury_penalty', 0):.1f}")

    # ── 2e. Test anatomy fractures & pain ────────────────────────────
    _log("  2e. Testing anatomy & pain...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import (
        fracture_bone, get_fractured_bones, fracture_from_impact
    )
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
        add_pain_source, get_total_pain, get_pain_effects,
        process_pain_decay_tick, process_healing_tick
    )

    # Fracture a bone
    fracture = fracture_bone("uuid-round2-p1", "humerus_left", 1.8)
    if "error" in fracture:
        round_errors.append(f"Fracture failed: {fracture['error']}")
    else:
        _log(f"    Bone fracture: {fracture['bone']}, pain={fracture.get('pain_level', 0):.1f}")

    # Check total pain
    total_pain = get_total_pain("uuid-round2-p1")
    if total_pain <= 0:
        round_errors.append("No pain registered from fracture")
    else:
        _log(f"    Total pain: {total_pain:.1f}")

    # Check pain effects
    effects = get_pain_effects("uuid-round2-p1")
    if effects.get("agility_penalty", 0) > 0:
        _log(f"    Pain effects: agility={effects['agility_penalty']}, strength={effects['strength_penalty']}")

    # Test healing
    healing = process_healing_tick("uuid-round2-p1")

    # ── 2f. Test disease spread ──────────────────────────────────────
    _log("  2f. Testing disease spread...")
    spreads = try_spread_disease("uuid-round2-p1")
    if spreads:
        for s in spreads:
            _log(f"    Disease spread: {s.get('disease')} -> {s.get('to')}")
    else:
        _log("    No disease spread this tick (probabilistic)")

    # ── 2g. Test blood loss ─────────────────────────────────────────
    _log("  2g. Testing blood loss...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
        cause_bleeding, process_bleeding_tick, get_blood_stats,
        process_blood_regeneration_tick
    )

    blood_before = get_blood_stats("uuid-round2-p1")
    cause_bleeding("uuid-round2-p1", 300.0, "torso")
    bleeding = process_bleeding_tick("uuid-round2-p1")
    blood_after = get_blood_stats("uuid-round2-p1")
    if blood_after["blood_volume_ml"] >= blood_before["blood_volume_ml"]:
        round_errors.append("Blood loss did not reduce volume")
    else:
        _log(f"    Blood loss: {blood_before['blood_volume_ml']:.0f}ml -> {blood_after['blood_volume_ml']:.0f}ml")

    return {
        "round": 2,
        "name": "Systems Active",
        "errors": round_errors,
        "passed": len(round_errors) == 0,
    }


# ══════════════════════════════════════════════════════════════════════
# Round 3: Combat Extension - Skills, Traits, AI Decisions
# ══════════════════════════════════════════════════════════════════════

def round3_combat_extension() -> dict:
    """Round 3: Combat skills evolution, negative traits, and AI decisions.

    Tests:
      - All 10 combat skills functional
      - XP gains and leveling up
      - Skill decay from inactivity
      - Negative trait detection
      - Combat modifier calculation
      - Critical condition evaluation
      - AI outcome decision
      - Hook integration (on_listing_created, on_purchase)
    """
    try:
        _log("\n=== ROUND 3: COMBAT EXTENSION ===")

    except Exception as e:
        log.error(f"round3_combat_extens failed: {e}")
        return {}
    _full_reset()

    # Create diverse personas
    _create_test_persona("uuid-round3-war", "CombatMaster", "warrior")
    _create_test_persona("uuid-round3-miner", "RockBreaker", "miner")
    _create_test_persona("uuid-round3-merchant", "GoldFingers", "merchant")

    round_errors = []

    # ── 3a. Test skill XP and leveling ───────────────────────────────
    _log("  3a. Testing skill XP gains...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
        use_skill, get_skill, get_all_skills, process_skill_decay_tick,
        process_negative_traits_tick, get_combat_skill_modifiers
    )

    # Use all skills for the warrior
    skills_used = ["blades", "blunt", "archery", "polearms", "unarmed",
                    "blocking", "dodging", "critical_strike", "combat_awareness",
                    "first_aid"]
    level_ups = 0
    for skill in skills_used:
        for _ in range(15):  # Use each skill 15 times
            result = use_skill("uuid-round3-war", skill, "combat", 2.0)
            if result.get("level_up"):
                level_ups += 1

    _log(f"    Skill level ups: {level_ups}")

    if level_ups == 0:
        round_errors.append("No skill level ups achieved")

    # Verify all skills have been used
    all_skills = get_all_skills("uuid-round3-war")
    for skill in skills_used:
        if skill not in all_skills:
            round_errors.append(f"Skill {skill} missing")
            continue
        if all_skills[skill]["times_used"] == 0:
            round_errors.append(f"Skill {skill} has 0 uses")

    # ── 3b. Test skill decay ─────────────────────────────────────────
    _log("  3b. Testing skill decay...")
    db = get_db()
    db.execute("""
        UPDATE ext_shm_combat_skills SET decay_counter = 15
        WHERE persona_uuid = 'uuid-round3-merchant'
    """)

    decay_results = process_skill_decay_tick("uuid-round3-merchant")
    decay_occurred = any(
        r.get("level_after", 0) < r.get("level_before", 0)
        for r in decay_results
    )
    _log(f"    Skill decay occurred: {decay_occurred} ({len(decay_results)} skills affected)")

    # ── 3c. Test negative traits ─────────────────────────────────────
    _log("  3c. Testing negative traits...")
    db.execute("""
        UPDATE ext_shm_combat_skills SET level = 2
        WHERE persona_uuid = 'uuid-round3-merchant' AND skill_name = 'combat_awareness'
    """)

    trait_results = process_negative_traits_tick("uuid-round3-merchant")
    traits_found = any(t["status"] == "acquired" for t in trait_results)
    _log(f"    Negative traits acquired: {traits_found}")

    # ── 3d. Test combat modifiers ────────────────────────────────────
    _log("  3d. Testing combat modifiers...")
    war_mods = get_combat_skill_modifiers("uuid-round3-war")
    mer_mods = get_combat_skill_modifiers("uuid-round3-merchant")

    if war_mods.get("hit_chance_bonus", 0) > 0 or war_mods.get("damage_bonus", 0) > 0:
        _log(f"    Warrior: hit={war_mods['hit_chance_bonus']:.1f}%, "
             f"dmg={war_mods['damage_bonus']:.1f}%, "
             f"dodge={war_mods['dodge_bonus']:.1f}%")

    # ── 3e. Test AI critical decisions ───────────────────────────────
    _log("  3e. Testing AI engine...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_ai_engine import (
        evaluate_critical_condition, decide_critical_outcome,
        _fallback_decision, apply_outcome
    )

    # Create a critical condition manually
    db.execute("""
        UPDATE ext_shm_blood SET
            blood_volume_ml = CAST(max_blood_volume AS REAL) * 0.2,
            oxygen_saturation = 35,
            blood_toxicity = 85
        WHERE persona_uuid = 'uuid-round3-war'
    """)

    # Check evaluation
    critical = evaluate_critical_condition("uuid-round3-war")
    if critical:
        _log(f"    Critical conditions detected: {len(critical['conditions'])}")
        for c in critical["conditions"]:
            _log(f"      - {c['type']}: {c['severity']}")

    # Test AI decision
    decision = decide_critical_outcome("uuid-round3-war")
    _log(f"    AI decision: {decision.get('outcome')} - {decision.get('reasoning', '')[:80]}")

    if decision.get("outcome") not in ("survive", "survive_with_consequences", "death", "stable"):
        round_errors.append(f"Unexpected AI outcome: {decision.get('outcome')}")

    # Test apply outcome
    applied = apply_outcome("uuid-round3-war", decision)
    if not applied.get("applied"):
        round_errors.append(f"Outcome application failed: {applied.get('error')}")

    # ── 3f. Test hook integration ────────────────────────────────────
    _log("  3f. Testing hook integration...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS import (
        _on_listing_created, _on_purchase
    )

    listing_result = _on_listing_created(
        listing={"item_id": "minecraft:golden_apple"})
    _log(f"    Listing hook (medicine): {listing_result}")

    purchase_result = _on_purchase(
        transaction={"buyer": "CombatMaster", "item_id": "minecraft:golden_apple",
                     "quantity": 1})
    _log(f"    Purchase hook: {purchase_result}")

    # ── 3g. Test full healing cycle ──────────────────────────────────
    _log("  3g. Testing full healing cycle...")
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
        process_healing_tick, get_total_pain, add_pain_source
    )
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import fracture_bone
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import injure_muscle

    add_pain_source("uuid-round3-miner", "work_injury", "muscle_strain", 30.0, 5)
    fracture_bone("uuid-round3-miner", "radius_left", 1.0)
    injure_muscle("uuid-round3-miner", "back", 0.4)

    for _ in range(10):
        process_healing_tick("uuid-round3-miner")

    remaining_pain = get_total_pain("uuid-round3-miner")
    _log(f"    Pain after healing: {remaining_pain:.1f}")

    return {
        "round": 3,
        "name": "Combat Extension",
        "errors": round_errors,
        "level_ups": level_ups,
        "ai_outcome": decision.get("outcome"),
        "traits_found": traits_found,
        "remaining_pain": round(remaining_pain, 1),
        "passed": len(round_errors) == 0,
    }


# ══════════════════════════════════════════════════════════════════════
# Main Integration Runner
# ══════════════════════════════════════════════════════════════════════

def run_all_rounds() -> dict:
    """Execute all 3 integration test rounds.

    Returns combined results with pass/fail for each round.
    """
    overall_start = time.time()
    results = []

    # Round 1
    r1 = round1_foundation()
    results.append(r1)

    # Round 2
    r2 = round2_systems_active()
    results.append(r2)

    # Round 3
    r3 = round3_combat_extension()
    results.append(r3)

    total_errors = sum(len(r.get("errors", [])) for r in results)
    total_passed = sum(1 for r in results if r.get("passed"))
    total_time = time.time() - overall_start

    summary = {
        "rounds_completed": len(results),
        "rounds_passed": total_passed,
        "total_errors": total_errors,
        "total_time_seconds": round(total_time, 2),
        "rounds": results,
        "overall_passed": total_passed == 3,
    }

    return summary


if __name__ == "__main__":
    summary = run_all_rounds()

    print("\n" + "=" * 60)
    print("INTEGRATION TEST RESULTS")
    print("=" * 60)

    for r in summary["rounds"]:
        status = "✅ PASSED" if r["passed"] else "❌ FAILED"
        print(f"\nRound {r['round']} - {r['name']}: {status}")
        if r.get("errors"):
            for e in r["errors"]:
                print(f"  └─ Error: {e}")
        for key, val in r.items():
            if key not in ("round", "name", "errors", "passed"):
                print(f"  ├─ {key}: {val}")

    print(f"\n{'='*60}")
    overall = "✅ ALL ROUNDS PASSED" if summary["overall_passed"] else "❌ SOME ROUNDS FAILED"
    print(f"Overall: {overall}")
    print(f"Time: {summary['total_time_seconds']}s")
    print(f"Rounds: {summary['rounds_passed']}/{summary['rounds_completed']} passed")
    print(f"Total errors: {summary['total_errors']}")
    print(f"{'='*60}")

