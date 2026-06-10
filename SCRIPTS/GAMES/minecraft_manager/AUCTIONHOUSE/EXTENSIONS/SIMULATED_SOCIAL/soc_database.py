"""
soc_database.py — Database schema for the SIMULATED_SOCIAL extension.

Adds tables for:
  - ext_soc_profiles          — Boredom, exhaustion, crisis state
  - ext_soc_memories_short    — Short-term memory (FIFO, 50 max)
  - ext_soc_memories_medium   — Medium-term memory (100 max, pruned by importance)
  - ext_soc_memories_perma    — Permanent memory (20 max, never pruned)
  - ext_soc_activity_log      — Social activity log
  - ext_soc_relationship_details — Deep relationship tracking + marriage
"""

from typing import Optional, Any
from datetime import datetime, timezone
import json

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

_SCHEMA_SQL = """

CREATE TABLE IF NOT EXISTS ext_soc_profiles (
    persona_uuid            TEXT PRIMARY KEY REFERENCES ext_sp_profiles(persona_uuid),
    boredom                 REAL NOT NULL DEFAULT 100.0 CHECK(boredom >= 0 AND boredom <= 100),
    social_exhaustion       REAL NOT NULL DEFAULT 0.0 CHECK(social_exhaustion >= 0 AND social_exhaustion <= 100),
    resting_state           TEXT NOT NULL DEFAULT 'active',
    rest_ticks              INTEGER NOT NULL DEFAULT 0,
    consecutive_crisis_ticks INTEGER NOT NULL DEFAULT 0,
    last_activity_at        TEXT,
    crisis_flag             INTEGER NOT NULL DEFAULT 0,
    burnout_ticks           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ext_soc_memories_short (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid    TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    memory_type     TEXT NOT NULL,
    target_uuid     TEXT,
    activity_type   TEXT,
    brief_context   TEXT NOT NULL,
    importance      INTEGER NOT NULL DEFAULT 3 CHECK(importance >= 1 AND importance <= 10),
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_soc_mem_short_p ON ext_soc_memories_short(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_soc_mem_short_t ON ext_soc_memories_short(target_uuid);

CREATE TABLE IF NOT EXISTS ext_soc_memories_medium (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid    TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    memory_type     TEXT NOT NULL,
    target_uuid     TEXT,
    activity_type   TEXT,
    detailed_context TEXT,
    location        TEXT,
    goals           TEXT,
    importance      INTEGER NOT NULL DEFAULT 3 CHECK(importance >= 1 AND importance <= 10),
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_soc_mem_med_p ON ext_soc_memories_medium(persona_uuid);

CREATE TABLE IF NOT EXISTS ext_soc_memories_perma (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid    TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    memory_type     TEXT NOT NULL,
    target_uuid     TEXT,
    activity_type   TEXT,
    full_narrative  TEXT,
    emotional_impact INTEGER NOT NULL DEFAULT 5 CHECK(emotional_impact >= 1 AND emotional_impact <= 10),
    skill_effects   TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_soc_mem_perm_p ON ext_soc_memories_perma(persona_uuid);

CREATE TABLE IF NOT EXISTS ext_soc_activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid    TEXT NOT NULL REFERENCES ext_sp_profiles(persona_uuid),
    activity_type   TEXT NOT NULL,
    partner_uuid    TEXT,
    boredom_change  REAL NOT NULL,
    exhaustion_change REAL NOT NULL,
    relationship_delta REAL DEFAULT 0,
    skill_deltas    TEXT,
    narrative       TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_soc_act_p ON ext_soc_activity_log(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_soc_act_time ON ext_soc_activity_log(created_at);

CREATE TABLE IF NOT EXISTS ext_soc_relationship_details (
    persona_uuid_a        TEXT NOT NULL,
    persona_uuid_b        TEXT NOT NULL,
    relationship_type     TEXT NOT NULL DEFAULT 'acquaintance',
    shared_activity_count INTEGER NOT NULL DEFAULT 0,
    last_activity_type    TEXT,
    last_activity_at      TEXT,
    marriage_flag         INTEGER NOT NULL DEFAULT 0,
    married_at            TEXT,
    shared_memories       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (persona_uuid_a, persona_uuid_b)
);

"""


def ensure_schema():
    """Create all social extension tables."""
    db = get_db()
    for stmt in _SCHEMA_SQL.split(";"):
        s = stmt.strip()
        if s:
            try:
                db.execute(s)
            except Exception as e:
                log.warn("soc_database", f"Schema stmt skipped: {e}")
    log.info("soc_database", "SIMULATED_SOCIAL schema ensured")


# ── Social Profile CRUD ──────────────────────────────────────────

def get_or_create_profile(persona_uuid: str, archetype: Optional[str] = None) -> dict:
    """Get or create a social profile for a persona."""
    if not persona_uuid or not isinstance(persona_uuid, str):
        return {"boredom": 100.0, "social_exhaustion": 0.0}

    db = get_db()
    profile = db.fetch_one(
        "SELECT * FROM ext_soc_profiles WHERE persona_uuid = ?",
        (persona_uuid,))
    if profile:
        return dict(profile)

    db.execute("""
        INSERT INTO ext_soc_profiles (persona_uuid, boredom, social_exhaustion)
        VALUES (?, 100.0, 0.0)
    """, (persona_uuid,))
    return {"persona_uuid": persona_uuid, "boredom": 100.0,
            "social_exhaustion": 0.0}


def update_boredom(persona_uuid: str, delta: float) -> float:
    """Update a persona's boredom level. Returns new value."""
    if not persona_uuid:
        return 100.0
    delta = _sanitize_float(delta)
    db = get_db()
    profile = get_or_create_profile(persona_uuid)
    new_val = max(0.0, min(100.0, profile["boredom"] + delta))
    db.execute("UPDATE ext_soc_profiles SET boredom = ? WHERE persona_uuid = ?",
               (new_val, persona_uuid))
    return new_val


def update_exhaustion(persona_uuid: str, delta: float) -> float:
    """Update social exhaustion. Returns new value."""
    if not persona_uuid:
        return 0.0
    delta = _sanitize_float(delta)
    db = get_db()
    profile = get_or_create_profile(persona_uuid)
    new_val = max(0.0, min(100.0, profile["social_exhaustion"] + delta))
    db.execute(
        "UPDATE ext_soc_profiles SET social_exhaustion = ? WHERE persona_uuid = ?",
        (new_val, persona_uuid))
    return new_val


def update_resting_state(persona_uuid: str, state: str, ticks: int):
    if not persona_uuid:
        return
    db = get_db()
    db.execute("""
        UPDATE ext_soc_profiles
        SET resting_state = ?, rest_ticks = ?
        WHERE persona_uuid = ?
    """, (state[:16], max(0, ticks), persona_uuid))


def _sanitize_float(v: Any) -> float:
    if v is None or isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        import math
        if math.isnan(v) or math.isinf(v):
            return 0.0
        return float(v)
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


# ── Memory CRUD ──────────────────────────────────────────────────

_MAX_SHORT = 50
_MAX_MEDIUM = 100
_MAX_PERMA = 20


def add_memory_short(persona_uuid: str, memory_type: str,
                      target_uuid: Optional[str] = None,
                      activity_type: Optional[str] = None,
                      context: str = "",
                      importance: int = 3) -> Optional[dict]:
    """Add a short-term memory. Auto-prunes if > _MAX_SHORT."""
    if not persona_uuid or not memory_type:
        return None
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_soc_memories_short
        (persona_uuid, memory_type, target_uuid, activity_type,
         brief_context, importance, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, memory_type[:32], target_uuid,
          activity_type[:32] if activity_type else None,
          context[:256], max(1, min(10, importance)), now))

    # Prune
    count = db.fetch_one(
        "SELECT COUNT(*) as c FROM ext_soc_memories_short WHERE persona_uuid = ?",
        (persona_uuid,))
    if count and count["c"] > _MAX_SHORT:
        excess = count["c"] - _MAX_SHORT
        db.execute("""
            DELETE FROM ext_soc_memories_short
            WHERE id IN (
                SELECT id FROM ext_soc_memories_short
                WHERE persona_uuid = ?
                ORDER BY importance ASC, created_at ASC
                LIMIT ?
            )
        """, (persona_uuid, excess))

    # Prioritize: boost importance for partners/rivals
    if target_uuid:
        _boost_memory_priority(persona_uuid, target_uuid)

    return {"persona_uuid": persona_uuid, "type": memory_type,
            "importance": importance}


def add_memory_medium(persona_uuid: str, memory_type: str,
                       target_uuid: Optional[str] = None,
                       activity_type: Optional[str] = None,
                       context: str = "", location: Optional[str] = None,
                       goals: Optional[dict] = None,
                       importance: int = 3) -> Optional[dict]:
    """Add a medium-term memory. Auto-prunes low-importance old ones if > _MAX_MEDIUM."""
    if not persona_uuid or not memory_type:
        return None
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_soc_memories_medium
        (persona_uuid, memory_type, target_uuid, activity_type,
         detailed_context, location, goals, importance, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, memory_type[:32], target_uuid,
          activity_type[:32] if activity_type else None,
          context[:1024], location[:64] if location else None,
          json.dumps(goals) if goals else None,
          max(1, min(10, importance)), now))

    count = db.fetch_one(
        "SELECT COUNT(*) as c FROM ext_soc_memories_medium WHERE persona_uuid = ?",
        (persona_uuid,))
    if count and count["c"] > _MAX_MEDIUM:
        excess = count["c"] - _MAX_MEDIUM + 5
        db.execute("""
            DELETE FROM ext_soc_memories_medium
            WHERE id IN (
                SELECT id FROM ext_soc_memories_medium
                WHERE persona_uuid = ?
                ORDER BY importance ASC, created_at ASC
                LIMIT ?
            )
        """, (persona_uuid, excess))

    if target_uuid:
        _boost_memory_priority(persona_uuid, target_uuid)

    return {"persona_uuid": persona_uuid, "type": memory_type}


def add_memory_perma(persona_uuid: str, memory_type: str,
                      target_uuid: Optional[str] = None,
                      activity_type: Optional[str] = None,
                      narrative: str = "",
                      emotional_impact: int = 5,
                      skill_effects: Optional[dict] = None) -> Optional[dict]:
    """Add a permanent memory. Max 20 — silently fails if full."""
    if not persona_uuid or not memory_type:
        return None
    db = get_db()
    count = db.fetch_one(
        "SELECT COUNT(*) as c FROM ext_soc_memories_perma WHERE persona_uuid = ?",
        (persona_uuid,))
    if count and count["c"] >= _MAX_PERMA:
        log.info("soc_memory",
                 f"Perma memory pool full for {persona_uuid[:8]}")
        return None

    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_soc_memories_perma
        (persona_uuid, memory_type, target_uuid, activity_type,
         full_narrative, emotional_impact, skill_effects, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, memory_type[:32], target_uuid,
          activity_type[:32] if activity_type else None,
          narrative[:2048],
          max(1, min(10, emotional_impact)),
          json.dumps(skill_effects) if skill_effects else None,
          now))

    log.info("soc_memory",
             f"Perma memory for {persona_uuid[:8]}: {memory_type} "
             f"(impact {emotional_impact}) — {count['c'] + 1}/{_MAX_PERMA}")
    return {"persona_uuid": persona_uuid, "type": memory_type,
            "emotional_impact": emotional_impact}


def _boost_memory_priority(persona_uuid: str, target_uuid: str):
    """Boost importance of memories involving important relationships."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
        get_relationship_direction
    )
    try:
        rel = get_relationship_direction(persona_uuid, target_uuid)
        if rel in ("friendship", "romance", "rivalry", "enmity"):
            db = get_db()
            db.execute("""
                UPDATE ext_soc_memories_short
                SET importance = MIN(10, importance + 2)
                WHERE persona_uuid = ? AND target_uuid = ?
            """, (persona_uuid, target_uuid))
    except Exception:
        pass


def get_recent_memories(persona_uuid: str, limit: int = 10,
                         include_perma: bool = False) -> list[dict]:
    """Get most recent memories across all tiers."""
    if not persona_uuid:
        return []
    db = get_db()
    results = db.fetch_all("""
        SELECT 'short' as tier, id, memory_type, target_uuid,
               activity_type, brief_context as content, importance, created_at
        FROM ext_soc_memories_short WHERE persona_uuid = ?
        UNION ALL
        SELECT 'medium', id, memory_type, target_uuid, activity_type,
               detailed_context, importance, created_at
        FROM ext_soc_memories_medium WHERE persona_uuid = ?
    """, (persona_uuid, persona_uuid))
    if include_perma:
        perma = db.fetch_all("""
            SELECT 'perma' as tier, id, memory_type, target_uuid,
                   activity_type, full_narrative as content,
                   emotional_impact as importance, created_at
            FROM ext_soc_memories_perma WHERE persona_uuid = ?
        """, (persona_uuid,))
        results.extend(perma)
    results.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return results[:limit]


# ── Activity Log CRUD ────────────────────────────────────────────

def log_activity(persona_uuid: str, activity_type: str,
                 partner_uuid: Optional[str] = None,
                 boredom_change: float = 0.0,
                 exhaustion_change: float = 0.0,
                 relationship_delta: float = 0.0,
                 skill_deltas: Optional[dict] = None,
                 narrative: Optional[str] = None) -> Optional[dict]:
    """Log a social activity and its effects."""
    if not persona_uuid:
        return None
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_soc_activity_log
        (persona_uuid, activity_type, partner_uuid,
         boredom_change, exhaustion_change, relationship_delta,
         skill_deltas, narrative, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, activity_type[:32], partner_uuid,
          _sanitize_float(boredom_change),
          _sanitize_float(exhaustion_change),
          _sanitize_float(relationship_delta),
          json.dumps(skill_deltas) if skill_deltas else None,
          narrative[:512] if narrative else None, now))
    return {"persona_uuid": persona_uuid, "activity": activity_type}


# ── Relationship Detail CRUD ─────────────────────────────────────

def get_relationship_detail(persona_a: str, persona_b: str) -> Optional[dict]:
    if not persona_a or not persona_b:
        return None
    a, b = sorted([persona_a, persona_b])
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_soc_relationship_details "
        "WHERE persona_uuid_a = ? AND persona_uuid_b = ?",
        (a, b))


def upsert_relationship_detail(persona_a: str, persona_b: str,
                                activity_type: Optional[str] = None,
                                delta_type: Optional[str] = None) -> dict:
    if not persona_a or not persona_b:
        return {}

    a, b = sorted([persona_a, persona_b])
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    existing = get_relationship_detail(a, b)

    if existing:
        shared = existing["shared_activity_count"] + 1
        db.execute("""
            UPDATE ext_soc_relationship_details
            SET shared_activity_count = ?,
                last_activity_type = ?, last_activity_at = ?
            WHERE persona_uuid_a = ? AND persona_uuid_b = ?
        """, (shared, activity_type, now, a, b))

        # Update relationship type based on shared count + rel strength
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            get_relationship_direction
        )
        rel = get_relationship_direction(a, b) or "neutral"
        new_type = _compute_relationship_subtype(rel, shared)
        if new_type != existing["relationship_type"]:
            db.execute("""
                UPDATE ext_soc_relationship_details
                SET relationship_type = ?
                WHERE persona_uuid_a = ? AND persona_uuid_b = ?
            """, (new_type, a, b))
    else:
        shared = 1
        db.execute("""
            INSERT INTO ext_soc_relationship_details
            (persona_uuid_a, persona_uuid_b, relationship_type,
             shared_activity_count, last_activity_type, last_activity_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (a, b, "acquaintance", shared, activity_type, now))

    return get_relationship_detail(a, b) or {}


def _compute_relationship_subtype(rel_type: str, shared_count: int) -> str:
    """Compute relationship subtype based on direction and shared activity count."""
    if rel_type in ("rivalry", "enmity"):
        if shared_count > 10:
            return "rival" if rel_type == "rivalry" else "enemy"

    if shared_count >= 20:
        return "best_friend"
    elif shared_count >= 10:
        return "close_friend"
    elif shared_count >= 5:
        return "friend"
    return "acquaintance"


# ── Marriage CRUD ────────────────────────────────────────────────
def set_married(persona_a: str, persona_b: str) -> bool:
    """Perform a marriage ceremony between two personas.
    Returns True if married, False if already married."""
    a, b = sorted([persona_a, persona_b])
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    detail = get_relationship_detail(a, b)
    if detail and detail.get("marriage_flag"):
        return False  # Already married
    if detail:
        db.execute("""
            UPDATE ext_soc_relationship_details
            SET relationship_type = 'married',
                marriage_flag = 1, married_at = ?
            WHERE persona_uuid_a = ? AND persona_uuid_b = ?
        """, (now, a, b))
    else:
        upsert_relationship_detail(a, b)
        db.execute("""
            UPDATE ext_soc_relationship_details
            SET relationship_type = 'married',
                marriage_flag = 1, married_at = ?
            WHERE persona_uuid_a = ? AND persona_uuid_b = ?
        """, (now, a, b))

    # Create permanent memory for both personas
    for puid in (a, b):
        add_memory_perma(puid, "marriage", a if puid == b else b,
                          "celebration",
                          f"Married {b if puid == a else a} in a ceremony.",
                          emotional_impact=9)
    return True


def check_divorce(persona_a: str, persona_b: str) -> bool:
    """Check if a marriage should end. Returns True if divorced."""
    detail = get_relationship_detail(persona_a, persona_b)
    if not detail or not detail.get("marriage_flag"):
        return False

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
        get_relationship
    )
    rel = get_relationship(persona_a, persona_b)
    if rel and rel.get("strength", 0) < 30:
        db = get_db()
        db.execute("""
            UPDATE ext_soc_relationship_details
            SET relationship_type = 'divorced',
                marriage_flag = 0
            WHERE persona_uuid_a = ? AND persona_uuid_b = ?
        """, (detail["persona_uuid_a"], detail["persona_uuid_b"]))
        for puid in (persona_a, persona_b):
            add_memory_perma(puid, "divorce",
                              persona_a if puid == persona_b else persona_b,
                              "arguing",
                              f"Divorced from {persona_b if puid == persona_a else persona_a}.",
                              emotional_impact=7)
        return True
    return False

ensure_schema()

