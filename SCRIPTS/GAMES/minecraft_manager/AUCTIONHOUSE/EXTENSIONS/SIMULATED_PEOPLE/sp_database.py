"""
sp_database.py — Database schema & queries for Simulated People extension.

All tables are prefixed with ``ext_sp_`` to namespace them from the
core AH tables.  The schema is created on first load and is safe to
re-create (CREATE TABLE IF NOT EXISTS).

If this extension is removed, the tables remain in the DB but are
never queried — they cause no harm.
"""

from datetime import datetime, timezone
from typing import Optional
import json

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

# ── THREAD-enhanced memory (optional, graceful degradation) ─────────
try:
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_memory_thread import (
        thread_add_memory as _thread_add_memory,
        thread_get_price_memories as _thread_get_price_memories,
        thread_recall_memories as _thread_recall_memories,
        thread_run_reflect as _thread_run_reflect,
        thread_get_persona_stats as _thread_get_persona_stats,
        is_thread_available as _thread_available,
    )
    _HAS_THREAD = True
except ImportError:
    _HAS_THREAD = False
    log.info("sp_memory_thread not available — persona memories use legacy SQL")

# ── Schema ───────────────────────────────────────────────────────────

_PROFILES_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_profiles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    archetype         TEXT NOT NULL,
    job               TEXT NOT NULL,
    region            TEXT NOT NULL DEFAULT 'overworld',
    wealth_tier       TEXT NOT NULL DEFAULT 'working',
    personality_traits TEXT,
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    last_active_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sp_profiles_active ON ext_sp_profiles(active);
CREATE INDEX IF NOT EXISTS idx_sp_profiles_archetype ON ext_sp_profiles(archetype);
CREATE INDEX IF NOT EXISTS idx_sp_profiles_region ON ext_sp_profiles(region);
"""

_FINANCES_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_finances (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL UNIQUE REFERENCES ext_sp_profiles(persona_uuid),
    balance           REAL NOT NULL DEFAULT 0.0,
    lifetime_income   REAL NOT NULL DEFAULT 0.0,
    lifetime_spending REAL NOT NULL DEFAULT 0.0,
    income_per_tick   REAL NOT NULL DEFAULT 0.0,
    savings_goal      REAL NOT NULL DEFAULT 0.0,
    debt              REAL NOT NULL DEFAULT 0.0
);
"""

_NEEDS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_needs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    item_id           TEXT NOT NULL,
    urgency           INTEGER NOT NULL DEFAULT 5,
    max_price         REAL NOT NULL DEFAULT 0.0,
    desired_quantity  INTEGER NOT NULL DEFAULT 1,
    quantity_obtained INTEGER NOT NULL DEFAULT 0,
    reason            TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_needs_persona ON ext_sp_needs(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_needs_item ON ext_sp_needs(item_id);
"""

_MEMORY_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_memory (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    memory_type       TEXT NOT NULL,
    item_id           TEXT,
    price             REAL,
    detail            TEXT,
    emotional_weight  INTEGER NOT NULL DEFAULT 5,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_memory_persona ON ext_sp_memory(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_memory_type ON ext_sp_memory(memory_type);
"""

_LIFE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_life_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    event_type        TEXT NOT NULL,
    description       TEXT,
    financial_impact  REAL NOT NULL DEFAULT 0.0,
    mood_impact       TEXT NOT NULL DEFAULT 'neutral',
    duration_hours    INTEGER NOT NULL DEFAULT 24,
    started_at        TEXT NOT NULL,
    ended_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_sp_life_active ON ext_sp_life_events(ended_at);
"""

_WORLD_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_world_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uuid        TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    description       TEXT,
    region            TEXT NOT NULL DEFAULT 'global',
    severity          TEXT NOT NULL DEFAULT 'moderate',
    financial_multiplier REAL NOT NULL DEFAULT 1.0,
    income_multiplier REAL NOT NULL DEFAULT 1.0,
    started_at        TEXT NOT NULL,
    ended_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_sp_we_active ON ext_sp_world_events(ended_at);
CREATE INDEX IF NOT EXISTS idx_sp_we_region ON ext_sp_world_events(region);
"""

_WORLD_AREAS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_world_areas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    area_uuid         TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    region            TEXT NOT NULL DEFAULT 'overworld',
    biome_type        TEXT NOT NULL DEFAULT 'plains',
    neighbor_ids      TEXT,
    owner_uuid        TEXT,
    resources_json    TEXT,
    is_hotzone        INTEGER NOT NULL DEFAULT 0,
    description       TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_wa_region ON ext_sp_world_areas(region);
CREATE INDEX IF NOT EXISTS idx_sp_wa_owner ON ext_sp_world_areas(owner_uuid);
"""

_PERSONA_SKILLS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_persona_skills (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL UNIQUE REFERENCES ext_sp_profiles(persona_uuid),
    mining            INTEGER NOT NULL DEFAULT 10,
    combat            INTEGER NOT NULL DEFAULT 10,
    farming           INTEGER NOT NULL DEFAULT 10,
    trading           INTEGER NOT NULL DEFAULT 10,
    crafting          INTEGER NOT NULL DEFAULT 10,
    exploration       INTEGER NOT NULL DEFAULT 10,
    leadership        INTEGER NOT NULL DEFAULT 10
);
CREATE INDEX IF NOT EXISTS idx_sp_skills_persona ON ext_sp_persona_skills(persona_uuid);
"""

_PERSONA_LOCATION_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_persona_location (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL UNIQUE REFERENCES ext_sp_profiles(persona_uuid),
    area_uuid         TEXT NOT NULL REFERENCES ext_sp_world_areas(area_uuid),
    moved_last_tick   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sp_loc_area ON ext_sp_persona_location(area_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_loc_persona ON ext_sp_persona_location(persona_uuid);
"""

_TERRITORIES_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_territories (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    territory_uuid    TEXT NOT NULL UNIQUE,
    area_uuid         TEXT NOT NULL REFERENCES ext_sp_world_areas(area_uuid),
    name              TEXT NOT NULL,
    claimed_by        TEXT REFERENCES ext_sp_profiles(persona_uuid),
    defense_rating    INTEGER NOT NULL DEFAULT 1,
    income_per_tick   REAL NOT NULL DEFAULT 0.0,
    war_active        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sp_territory_claim ON ext_sp_territories(claimed_by);
CREATE INDEX IF NOT EXISTS idx_sp_territory_area ON ext_sp_territories(area_uuid);
"""

_WARS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_wars (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    war_uuid          TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    attacker_uuid     TEXT,
    defender_uuid     TEXT,
    territory_uuid    TEXT,
    duration_ticks    INTEGER NOT NULL DEFAULT 5,
    attacker_score    INTEGER NOT NULL DEFAULT 0,
    defender_score    INTEGER NOT NULL DEFAULT 0,
    narrative         TEXT,
    started_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_war_attacker ON ext_sp_wars(attacker_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_war_defender ON ext_sp_wars(defender_uuid);
"""

_PLAYER_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_player_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    player_name       TEXT NOT NULL,
    message_text      TEXT NOT NULL,
    transaction_uuid  TEXT,
    sent_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_msg_player ON ext_sp_player_messages(player_name);
"""

_WEATHER_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_weather (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    area_uuid         TEXT NOT NULL UNIQUE REFERENCES ext_sp_world_areas(area_uuid),
    temperature       REAL NOT NULL DEFAULT 20.0,
    humidity          REAL NOT NULL DEFAULT 50.0,
    cloud_cover       REAL NOT NULL DEFAULT 0.2,
    precipitation_mm  REAL NOT NULL DEFAULT 0.0,
    wind_speed        REAL NOT NULL DEFAULT 2.0,
    wind_direction    REAL NOT NULL DEFAULT 0.0,
    pressure          REAL NOT NULL DEFAULT 1013.0,
    is_raining        INTEGER NOT NULL DEFAULT 0,
    is_snowing        INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_weather_area ON ext_sp_weather(area_uuid);
"""

_HEALTH_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_health (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL UNIQUE REFERENCES ext_sp_profiles(persona_uuid),
    food              INTEGER NOT NULL DEFAULT 80,
    hydration         INTEGER NOT NULL DEFAULT 80,
    energy            INTEGER NOT NULL DEFAULT 75,
    temperature       INTEGER NOT NULL DEFAULT 50,
    waste             INTEGER NOT NULL DEFAULT 0,
    hygiene           INTEGER NOT NULL DEFAULT 70,
    immune            INTEGER NOT NULL DEFAULT 70,
    alive             INTEGER NOT NULL DEFAULT 1,
    cause_of_death    TEXT,
    decay_timer       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sp_health_alive ON ext_sp_health(alive);
CREATE INDEX IF NOT EXISTS idx_sp_health_persona ON ext_sp_health(persona_uuid);
"""

_TRANSACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_transactions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    listing_uuid      TEXT,
    transaction_type  TEXT NOT NULL,
    item_id           TEXT,
    quantity          INTEGER NOT NULL DEFAULT 1,
    price             REAL NOT NULL DEFAULT 0.0,
    reason            TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_tx_persona ON ext_sp_transactions(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_tx_item ON ext_sp_transactions(item_id);
"""


# ── Ecosystem Tables ────────────────────────────────────────────────

_ECOSYSTEM_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_ecosystem (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    area_uuid         TEXT NOT NULL UNIQUE REFERENCES ext_sp_world_areas(area_uuid),
    biomass_grass     REAL NOT NULL DEFAULT 500.0,
    biomass_shrub     REAL NOT NULL DEFAULT 200.0,
    biomass_tree      REAL NOT NULL DEFAULT 800.0,
    biomass_root      REAL NOT NULL DEFAULT 100.0,
    leaf_area_index   REAL NOT NULL DEFAULT 2.0,
    seeds_in_soil     REAL NOT NULL DEFAULT 100.0,
    food_plant_abundance REAL NOT NULL DEFAULT 50.0,
    litter_biomass    REAL NOT NULL DEFAULT 200.0,
    last_growth_tick  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sp_eco_area ON ext_sp_ecosystem(area_uuid);
"""

_ANIMAL_AGENTS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_animal_agents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_uuid       TEXT NOT NULL UNIQUE,
    species           TEXT NOT NULL,
    name              TEXT,
    area_uuid         TEXT NOT NULL REFERENCES ext_sp_world_areas(area_uuid),
    energy            REAL NOT NULL DEFAULT 100.0,
    hydration         REAL NOT NULL DEFAULT 100.0,
    health            REAL NOT NULL DEFAULT 100.0,
    age               REAL NOT NULL DEFAULT 1.0,
    age_days          INTEGER NOT NULL DEFAULT 0,
    is_alive          INTEGER NOT NULL DEFAULT 1,
    is_hibernating    INTEGER NOT NULL DEFAULT 0,
    last_action       TEXT,
    last_action_tick  INTEGER NOT NULL DEFAULT 0,
    mate_cooldown     INTEGER NOT NULL DEFAULT 0,
    hunger_timer      INTEGER NOT NULL DEFAULT 0,
    thirst_timer      INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_animal_area ON ext_sp_animal_agents(area_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_animal_species ON ext_sp_animal_agents(species);
CREATE INDEX IF NOT EXISTS idx_sp_animal_alive ON ext_sp_animal_agents(is_alive);
"""

_ANIMAL_DENSITY_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_animal_density (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    area_uuid         TEXT NOT NULL REFERENCES ext_sp_world_areas(area_uuid),
    species           TEXT NOT NULL,
    density           REAL NOT NULL DEFAULT 0.0,
    UNIQUE(area_uuid, species)
);
CREATE INDEX IF NOT EXISTS idx_sp_density_area ON ext_sp_animal_density(area_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_density_species ON ext_sp_animal_density(species);
"""

_ITEMS_DEF_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_item_defs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id           TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    category          TEXT NOT NULL DEFAULT 'misc',
    weight_kg         REAL NOT NULL DEFAULT 0.1,
    volume_l          REAL NOT NULL DEFAULT 0.1,
    stackable         INTEGER NOT NULL DEFAULT 1,
    max_stack         INTEGER NOT NULL DEFAULT 64,
    perishable        INTEGER NOT NULL DEFAULT 0,
    decay_per_day     REAL NOT NULL DEFAULT 0.0,
    calories_per_unit REAL NOT NULL DEFAULT 0.0,
    hydration_per_unit REAL NOT NULL DEFAULT 0.0,
    toxicity          REAL NOT NULL DEFAULT 0.0,
    material          TEXT,
    crafting_tags     TEXT,
    equip_slot        TEXT,
    armor_value       REAL NOT NULL DEFAULT 0.0,
    insulation_value  REAL NOT NULL DEFAULT 0.0,
    tool_power        REAL NOT NULL DEFAULT 0.0,
    tool_type         TEXT,
    description       TEXT
);
"""

_INVENTORY_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_inventory (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_uuid        TEXT NOT NULL,
    owner_type        TEXT NOT NULL DEFAULT 'persona',
    container         TEXT NOT NULL DEFAULT 'hands',
    item_id           TEXT NOT NULL,
    quantity          REAL NOT NULL DEFAULT 1.0,
    condition_val     REAL NOT NULL DEFAULT 1.0,
    properties_json   TEXT,
    is_equipped       INTEGER NOT NULL DEFAULT 0,
    slot_index        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_inv_owner ON ext_sp_inventory(owner_uuid, owner_type);
CREATE INDEX IF NOT EXISTS idx_sp_inv_container ON ext_sp_inventory(owner_uuid, container);
CREATE INDEX IF NOT EXISTS idx_sp_inv_equipped ON ext_sp_inventory(owner_uuid, is_equipped);
"""

_RESOURCE_NODES_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_resource_nodes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    node_uuid         TEXT NOT NULL UNIQUE,
    area_uuid         TEXT NOT NULL REFERENCES ext_sp_world_areas(area_uuid),
    resource_type     TEXT NOT NULL,
    quantity          REAL NOT NULL DEFAULT 100.0,
    max_quantity      REAL NOT NULL DEFAULT 100.0,
    regrowth_rate     REAL NOT NULL DEFAULT 0.0,
    quality           REAL NOT NULL DEFAULT 1.0,
    access_difficulty REAL NOT NULL DEFAULT 1.0,
    depleted          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_rn_area ON ext_sp_resource_nodes(area_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_rn_type ON ext_sp_resource_nodes(resource_type);
"""

_BUILDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_buildings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    building_uuid     TEXT NOT NULL UNIQUE,
    area_uuid         TEXT NOT NULL REFERENCES ext_sp_world_areas(area_uuid),
    owner_uuid        TEXT REFERENCES ext_sp_profiles(persona_uuid),
    building_type     TEXT NOT NULL,
    name              TEXT,
    health            REAL NOT NULL DEFAULT 100.0,
    max_health        REAL NOT NULL DEFAULT 100.0,
    material          TEXT NOT NULL DEFAULT 'wood',
    room_count        INTEGER NOT NULL DEFAULT 1,
    max_rooms         INTEGER NOT NULL DEFAULT 3,
    is_complete       INTEGER NOT NULL DEFAULT 0,
    build_progress    REAL NOT NULL DEFAULT 0.0,
    defense_rating    REAL NOT NULL DEFAULT 0.0,
    insulation_score  REAL NOT NULL DEFAULT 0.5,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_bldg_area ON ext_sp_buildings(area_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_bldg_owner ON ext_sp_buildings(owner_uuid);
"""

_CRAFTING_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_crafting_queue (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_uuid       TEXT NOT NULL,
    recipe_id         TEXT NOT NULL,
    area_uuid         TEXT NOT NULL,
    progress          REAL NOT NULL DEFAULT 0.0,
    total_required    REAL NOT NULL DEFAULT 1.0,
    status            TEXT NOT NULL DEFAULT 'in_progress',
    output_item_id    TEXT NOT NULL,
    output_quantity   REAL NOT NULL DEFAULT 1.0,
    started_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_cq_worker ON ext_sp_crafting_queue(worker_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_cq_status ON ext_sp_crafting_queue(status);
"""

_WOUNDS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_wounds (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    wound_uuid          TEXT NOT NULL UNIQUE,
    owner_uuid          TEXT NOT NULL,
    body_part           TEXT NOT NULL,
    wound_type          TEXT NOT NULL,
    severity            INTEGER NOT NULL DEFAULT 1,
    bleed_rate          REAL NOT NULL DEFAULT 0.0,
    pain_level          REAL NOT NULL DEFAULT 0.0,
    infection_chance    REAL NOT NULL DEFAULT 0.3,
    infection_progress  REAL NOT NULL DEFAULT 0.0,
    is_infected         INTEGER NOT NULL DEFAULT 0,
    is_bandaged         INTEGER NOT NULL DEFAULT 0,
    is_healed           INTEGER NOT NULL DEFAULT 0,
    created_by          TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_w_owner ON ext_sp_wounds(owner_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_w_healed ON ext_sp_wounds(is_healed);
CREATE INDEX IF NOT EXISTS idx_sp_w_infected ON ext_sp_wounds(is_infected);
"""



_BOARD_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_board (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    board_uuid      TEXT NOT NULL UNIQUE,
    persona_uuid    TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    persona_name    TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    max_price       REAL NOT NULL,
    urgency         INTEGER NOT NULL DEFAULT 5,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT "open",
    fulfilled_by    TEXT,
    fulfilled_at    TEXT,
    created_at      TEXT NOT NULL,
    expires_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sp_board_status ON ext_sp_board(status);
CREATE INDEX IF NOT EXISTS idx_sp_board_persona ON ext_sp_board(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_board_item ON ext_sp_board(item_id);
"""

_GUILDS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_guilds (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_uuid        TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    leader_uuid       TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    renown            REAL NOT NULL DEFAULT 0.0,
    morale            REAL NOT NULL DEFAULT 50.0,
    treasury          REAL NOT NULL DEFAULT 0.0,
    diplomatic_stance TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_guild_leader ON ext_sp_guilds(leader_uuid);
"""

_GUILD_MEMBERS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_guild_members (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_uuid        TEXT NOT NULL REFERENCES ext_sp_guilds(guild_uuid),
    persona_uuid      TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    rank              TEXT NOT NULL DEFAULT 'member',
    joined_at         TEXT NOT NULL,
    UNIQUE(guild_uuid, persona_uuid)
);
CREATE INDEX IF NOT EXISTS idx_sp_gm_persona ON ext_sp_guild_members(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_gm_rank ON ext_sp_guild_members(rank);
"""

_CLAIMS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_claims (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_uuid        TEXT NOT NULL UNIQUE,
    area_uuid         TEXT NOT NULL UNIQUE REFERENCES ext_sp_world_areas(area_uuid),
    owner_uuid        TEXT NOT NULL,
    guild_uuid        TEXT REFERENCES ext_sp_guilds(guild_uuid),
    claim_strength    REAL NOT NULL DEFAULT 100.0,
    claimed_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_claim_area ON ext_sp_claims(area_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_claim_owner ON ext_sp_claims(owner_uuid);
CREATE INDEX IF NOT EXISTS idx_sp_claim_guild ON ext_sp_claims(guild_uuid);
"""


# ── Initialization ───────────────────────────────────────────────────


def post_board_need(persona_uuid, persona_name, item_id, quantity, max_price, urgency, reason=""):
    """Post an urgent need to the player-visible board."""
    import uuid as _uuid
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    open_entries = db.fetch_one("SELECT COUNT(*) as c FROM ext_sp_board WHERE status = 'open'")
    slot = (open_entries["c"] if open_entries else 0) + 1
    board_uuid = str(_uuid.uuid4())
    db.execute("INSERT INTO ext_sp_board (board_uuid, persona_uuid, persona_name, item_id, quantity, max_price, urgency, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
               (board_uuid, persona_uuid, persona_name, item_id, quantity, max_price, urgency, reason, now))
    return {"ok": True, "data": {"slot": slot, "board_uuid": board_uuid}}

def get_open_boards():
    """Get all open board entries sorted by urgency desc."""
    db = get_db()
    return db.fetch_all("SELECT *, id as slot FROM ext_sp_board WHERE status = 'open' ORDER BY urgency DESC, created_at ASC")

def get_board_by_slot(slot):
    """Get a board entry by its visible slot number."""
    all_open = get_open_boards()
    if 0 < slot <= len(all_open):
        return all_open[slot - 1]
    return None

def get_board_by_uuid(board_uuid):
    """Get a board entry by its permanent UUID."""
    db = get_db()
    return db.fetch_one("SELECT * FROM ext_sp_board WHERE board_uuid = ?", (board_uuid,))

def fulfill_board_entry(board_uuid, player_name):
    """Mark a board entry as fulfilled by a player."""
    db = get_db()
    entry = get_board_by_uuid(board_uuid)
    if not entry:
        return {"ok": False, "error": "Board entry not found"}
    if entry["status"] != "open":
        return {"ok": False, "error": f"Board entry is already {entry['status']}"}
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE ext_sp_board SET status = 'fulfilled', fulfilled_by = ?, fulfilled_at = ? WHERE board_uuid = ?",
               (player_name, now, board_uuid))
    return {"ok": True, "data": {"persona_name": entry["persona_name"], "persona_uuid": entry["persona_uuid"], "item_id": entry["item_id"], "quantity": entry["quantity"], "price": entry["max_price"], "reason": entry.get("reason", "")}}

def cancel_stale_board_entries(hours=48):
    """Mark entries older than N hours as expired."""
    db = get_db()
    db.execute("UPDATE ext_sp_board SET status = 'expired' WHERE status = 'open' AND created_at < datetime('now', ?)",
               (f'-{hours} hours',))

def ensure_schema():
    """Create all SP tables if they don't exist.

    Safe to call multiple times.  If the extension is removed, the tables
    stay in the DB but are never queried (no harm).
    """
    db = get_db()
    for sql in [_PROFILES_TABLE, _FINANCES_TABLE, _NEEDS_TABLE,
                _MEMORY_TABLE, _LIFE_EVENTS_TABLE, _WORLD_EVENTS_TABLE,
                _WORLD_AREAS_TABLE, _PERSONA_SKILLS_TABLE,
                _PERSONA_LOCATION_TABLE, _TERRITORIES_TABLE, _WARS_TABLE,
                _PLAYER_MESSAGES_TABLE, _WEATHER_TABLE, _HEALTH_TABLE,
                _TRANSACTIONS_TABLE, _ECOSYSTEM_TABLE, _ANIMAL_AGENTS_TABLE,
                _ANIMAL_DENSITY_TABLE, _ITEMS_DEF_TABLE, _INVENTORY_TABLE,
                _RESOURCE_NODES_TABLE, _BUILDINGS_TABLE, _CRAFTING_QUEUE_TABLE,
                _WOUNDS_TABLE, _GUILDS_TABLE, _GUILD_MEMBERS_TABLE, _CLAIMS_TABLE, _BOARD_TABLE]:
        for stmt in sql.strip().split("\n\n"):
            s = stmt.strip()
            if s:
                # Split into individual SQL statements by semicolon
                for individual_stmt in s.split(";"):
                    individual_stmt = individual_stmt.strip()
                    if individual_stmt and individual_stmt.upper().startswith("CREATE"):
                        try:
                            db.execute(individual_stmt)
                        except Exception as e:
                            log.warn("sp_database", f"Schema stmt failed (may be ok): {e}")
    log.info("sp_database", "Simulated People schema initialized")


def cleanup_database():
    """Remove expired memories, life events, and world events.

    Called periodically to keep the database lean.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"cleanup_database failed: {e}")
        return None
    now = datetime.now(timezone.utc).isoformat()

    # Expired memories (retention days)
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config
    cfg = get_config()
    import datetime as dt
    cutoff = (datetime.now(timezone.utc) - dt.timedelta(days=cfg["memory_retention_days"])).isoformat()
    c = db.execute("DELETE FROM ext_sp_memory WHERE created_at < ?", (cutoff,))
    if c.rowcount > 0:
        log.info("sp_database", f"Cleaned {c.rowcount} expired memories")

    # Ended life events
    c2 = db.execute("DELETE FROM ext_sp_life_events WHERE ended_at IS NOT NULL AND ended_at < ?", (now,))
    if c2.rowcount > 0:
        log.info("sp_database", f"Cleaned {c2.rowcount} ended life events")

    # Ended world events
    c3 = db.execute("DELETE FROM ext_sp_world_events WHERE ended_at IS NOT NULL AND ended_at < ?", (now,))
    if c3.rowcount > 0:
        log.info("sp_database", f"Cleaned {c3.rowcount} ended world events")


# ── Profile Queries ──────────────────────────────────────────────────

def get_active_personas() -> list[dict]:
    """Get all currently active personas with their finances."""
    db = get_db()
    return db.fetch_all("""
        SELECT p.*, f.balance, f.lifetime_income, f.lifetime_spending,
               f.income_per_tick, f.savings_goal, f.debt
        FROM ext_sp_profiles p
        LEFT JOIN ext_sp_finances f ON p.persona_uuid = f.persona_uuid
        WHERE p.active = 1
        ORDER BY p.last_active_at ASC
    """)


def get_inactive_personas() -> list[dict]:
    """Get inactive personas that could be reactivated."""
    db = get_db()
    return db.fetch_all("""
        SELECT p.*, f.balance, f.income_per_tick
        FROM ext_sp_profiles p
        LEFT JOIN ext_sp_finances f ON p.persona_uuid = f.persona_uuid
        WHERE p.active = 0
        ORDER BY RANDOM()
    """)


def get_persona_count() -> dict:
    """Get counts of active/inactive/total personas."""
    db = get_db()
    total = db.fetch_one("SELECT COUNT(*) as c FROM ext_sp_profiles")
    active = db.fetch_one("SELECT COUNT(*) as c FROM ext_sp_profiles WHERE active = 1")
    return {
        "total": total["c"] if total else 0,
        "active": active["c"] if active else 0,
        "inactive": (total["c"] if total else 0) - (active["c"] if active else 0),
    }


def get_persona_by_uuid(uuid_str: str) -> Optional[dict]:
    """Get a single persona with finances by UUID."""
    db = get_db()
    return db.fetch_one("""
        SELECT p.*, f.balance, f.lifetime_income, f.lifetime_spending,
               f.income_per_tick, f.savings_goal, f.debt
        FROM ext_sp_profiles p
        LEFT JOIN ext_sp_finances f ON p.persona_uuid = f.persona_uuid
        WHERE p.persona_uuid = ?
    """, (uuid_str,))


def get_personas_by_region(region: str, active_only: bool = True) -> list[dict]:
    """Get personas in a specific region."""
    db = get_db()
    if active_only:
        return db.fetch_all(
            "SELECT * FROM ext_sp_profiles WHERE region = ? AND active = 1",
            (region,))
    return db.fetch_all(
        "SELECT * FROM ext_sp_profiles WHERE region = ?", (region,))


# ── Finance Queries ──────────────────────────────────────────────────

def update_finance(persona_uuid: str, delta: float, reason: str = "income") -> Optional[float]:
    """Update a persona's balance and return the new balance.

    Args:
        persona_uuid: The persona to update
        delta: Positive = income, negative = expense
        reason: 'income', 'purchase', 'sale', 'windfall', 'expense'

    Returns:
        New balance, or None if persona not found
    """
    db = get_db()
    current = db.fetch_one(
        "SELECT balance, lifetime_income, lifetime_spending FROM ext_sp_finances WHERE persona_uuid = ?",
        (persona_uuid,))
    if not current:
        return None

    new_balance = current["balance"] + delta
    new_lifetime_income = current["lifetime_income"] + (delta if delta > 0 else 0)
    new_lifetime_spending = current["lifetime_spending"] + (abs(delta) if delta < 0 else 0)

    db.execute("""
        UPDATE ext_sp_finances
        SET balance = ?, lifetime_income = ?, lifetime_spending = ?
        WHERE persona_uuid = ?
    """, (new_balance, new_lifetime_income, new_lifetime_spending, persona_uuid))

    return new_balance


# ── Needs Queries ───────────────────────────────────────────────────

def get_needs(persona_uuid: str, only_unfulfilled: bool = True) -> list[dict]:
    """Get all needs for a persona."""
    db = get_db()
    if only_unfulfilled:
        return db.fetch_all(
            "SELECT * FROM ext_sp_needs WHERE persona_uuid = ? AND quantity_obtained < desired_quantity",
            (persona_uuid,))
    return db.fetch_all(
        "SELECT * FROM ext_sp_needs WHERE persona_uuid = ?",
        (persona_uuid,))


def get_all_active_needs() -> list[dict]:
    """Get all unfulfilled needs across all active personas.

    Used to match player listings to persona needs.
    """
    db = get_db()
    return db.fetch_all("""
        SELECT n.*, p.name as persona_name, p.archetype, p.region, p.persona_uuid
        FROM ext_sp_needs n
        JOIN ext_sp_profiles p ON n.persona_uuid = p.persona_uuid
        WHERE p.active = 1 AND n.quantity_obtained < n.desired_quantity
    """)


def count_matching_needs(item_id: str) -> int:
    """Count how many active personas need a specific item.

    Used by the Minescript client to show [needs: N] badges.
    """
    db = get_db()
    row = db.fetch_one("""
        SELECT COUNT(*) as c FROM ext_sp_needs n
        JOIN ext_sp_profiles p ON n.persona_uuid = p.persona_uuid
        WHERE p.active = 1 AND n.item_id = ? AND n.quantity_obtained < n.desired_quantity
    """, (item_id,))
    return row["c"] if row else 0


def add_need(persona_uuid: str, item_id: str, urgency: int = 5,
             max_price: float = 0.0, quantity: int = 1, reason: str = "") -> int:
    """Add a new need for a persona.

    Returns the need ID.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    c = db.execute("""
        INSERT INTO ext_sp_needs (persona_uuid, item_id, urgency, max_price,
                                   desired_quantity, quantity_obtained, reason, created_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
    """, (persona_uuid, item_id, urgency, max_price, quantity, reason, now))
    return c.lastrowid


def fulfill_need(need_id: int, quantity: int = 1):
    """Mark a need as partially or fully fulfilled."""
    db = get_db()
    db.execute("""
        UPDATE ext_sp_needs SET quantity_obtained = quantity_obtained + ? WHERE id = ?
    """, (quantity, need_id))


# ── Memory Queries ──────────────────────────────────────────────────

def add_memory(persona_uuid: str, memory_type: str, item_id: Optional[str] = None,
               price: Optional[float] = None, detail: Optional[str] = None,
               emotional_weight: int = 5):
    """Add a memory entry for a persona.

    When the THREAD system is available, also stores the memory as a
    THREAD graph node with full cognitive features (typed relationships,
    activation-based retrieval, forgetting curve decay).
    """
    # Always store in legacy SQL for backward compatibility
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    import json
    db.execute("""
        INSERT INTO ext_sp_memory (persona_uuid, memory_type, item_id, price,
                                    detail, emotional_weight, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, memory_type, item_id, price,
          json.dumps(detail) if detail else None, emotional_weight, now))

    # Also store in THREAD graph (non-blocking - failures logged and ignored)
    if _HAS_THREAD and _thread_available():
        try:
            _thread_add_memory(
                persona_uuid=persona_uuid,
                memory_type=memory_type,
                item_id=item_id,
                price=price,
                detail=detail,
                emotional_weight=emotional_weight,
            )
        except Exception:
            log.warn("sp_memory", f"THREAD storage failed for {persona_uuid}", exc_info=True)




def get_recent_memories(persona_uuid: str, limit: int = 20) -> list[dict]:
    """Get recent memories for a persona."""
    db = get_db()
    return db.fetch_all("""
        SELECT * FROM ext_sp_memory
        WHERE persona_uuid = ?
        ORDER BY created_at DESC LIMIT ?
    """, (persona_uuid, limit))


def get_price_memories(persona_uuid: str, item_id: str, limit: int = 10) -> list[dict]:
    """Get price-related memories for a specific item.

    When THREAD is available, uses hybrid retrieval (lexical FTS5 + graph
    activation) to find the most relevant price observations. Falls back
    to simple SQL if THREAD is unavailable.
    """
    # Try THREAD-enhanced retrieval first
    if _HAS_THREAD and _thread_available():
        try:
            thread_results = _thread_get_price_memories(persona_uuid, item_id, limit)
            if thread_results is not None and len(thread_results) > 0:
                # Convert enriched THREAD results to legacy format
                legacy = []
                for r in thread_results:
                    legacy.append({
                        'persona_uuid': r.get('persona_uuid', persona_uuid),
                        'memory_type': r.get('memory_type', 'observed_price'),
                        'item_id': r.get('item_id', item_id),
                        'price': r.get('price'),
                        'detail': json.dumps(r.get('detail')) if r.get('detail') else None,
                        'emotional_weight': round(r.get('importance', 0.5) * 10),
                        'created_at': r.get('created_at', ''),
                        '_thread_uuid': r.get('memory_uuid', ''),
                        '_activation_score': r.get('activation_score', 0.0),
                    })
                return legacy
        except Exception:
            log.warn("sp_memory", f"THREAD price recall failed for {persona_uuid}, falling back", exc_info=True)

    # Fallback to legacy SQL
    db = get_db()
    return db.fetch_all("""
        SELECT * FROM ext_sp_memory
        WHERE persona_uuid = ? AND item_id = ? AND memory_type IN ('purchase', 'observed_price')
        ORDER BY created_at DESC LIMIT ?
    """, (persona_uuid, item_id, limit))




# ── Life Event Queries ──────────────────────────────────────────────

def get_active_life_events(persona_uuid: str) -> list[dict]:
    """Get currently active life events for a persona."""
    db = get_db()
    return db.fetch_all("""
        SELECT * FROM ext_sp_life_events
        WHERE persona_uuid = ? AND ended_at IS NULL
    """, (persona_uuid,))


def add_life_event(persona_uuid: str, event_type: str, description: str,
                   financial_impact: float = 0.0, mood_impact: str = "neutral",
                   duration_hours: int = 24):
    """Add a new life event for a persona."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_sp_life_events (persona_uuid, event_type, description,
                                         financial_impact, mood_impact,
                                         duration_hours, started_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, event_type, description, financial_impact,
          mood_impact, duration_hours, now))


def expire_life_events():
    """Mark life events as ended when their duration passes."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        UPDATE ext_sp_life_events SET ended_at = ?
        WHERE ended_at IS NULL
          AND datetime(started_at, '+' || duration_hours || ' hours') < ?
    """, (now, now))


# ── World Event Queries ─────────────────────────────────────────────

def get_active_world_events(region: Optional[str] = None) -> list[dict]:
    """Get active world events, optionally filtered by region."""
    db = get_db()
    if region:
        return db.fetch_all("""
            SELECT * FROM ext_sp_world_events
            WHERE ended_at IS NULL AND (region = 'global' OR region = ?)
        """, (region,))
    return db.fetch_all(
        "SELECT * FROM ext_sp_world_events WHERE ended_at IS NULL")


def add_world_event(name: str, description: str, region: str = "global",
                    severity: str = "moderate",
                    financial_multiplier: float = 1.0,
                    income_multiplier: float = 1.0) -> str:
    """Create a new world event.

    Returns the event UUID.
    """
    import uuid
    db = get_db()
    event_uuid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_sp_world_events (event_uuid, name, description, region,
                                          severity, financial_multiplier,
                                          income_multiplier, started_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_uuid, name, description, region, severity,
          financial_multiplier, income_multiplier, now))
    return event_uuid


def end_world_event(event_uuid: str):
    """End a world event."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE ext_sp_world_events SET ended_at = ? WHERE event_uuid = ?",
               (now, event_uuid))


# ── Transaction Queries ─────────────────────────────────────────────

def record_transaction(persona_uuid: str, listing_uuid: str,
                       tx_type: str, item_id: str, quantity: int,
                       price: float, reason: str = ""):
    """Record a persona's purchase or sale."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_sp_transactions
        (persona_uuid, listing_uuid, transaction_type, item_id, quantity, price, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, listing_uuid, tx_type, item_id, quantity, price, reason, now))

