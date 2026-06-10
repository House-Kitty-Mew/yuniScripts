"""
rel_database.py — Database schema for the SIMULATED_RELATIONSHIPS extension.

Extends the SP plugin with tables for:
  - ext_rel_relationships — Rich persona-to-persona relationships
  - ext_rel_situationships — Temporary relationship modifiers
  - ext_rel_interactions — Tracked interaction history
  - ext_rel_contention_events — Cell/movement contention resolution log
  - ext_rel_skill_history — Evolving skill change log

All tables use the ext_rel_ prefix to namespace from core AH (no prefix)
and SP tables (ext_sp_).
"""

from typing import Optional
import json
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()


# ── Schema ───────────────────────────────────────────────────────────

_SCHEMA_SQL = """

-- Core relationships between two personas
CREATE TABLE IF NOT EXISTS ext_rel_relationships (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid_a      TEXT NOT NULL,
    persona_uuid_b      TEXT NOT NULL,
    relationship_type   TEXT NOT NULL DEFAULT 'neutral',
    --   neutral, friendship, rivalry, romance, mentorship, enmity, alliance
    strength            REAL NOT NULL DEFAULT 50.0,
    -- 0.0 = nonexistent, 50.0 = neutral, 100.0 = maximum
    intimacy            REAL NOT NULL DEFAULT 0.0,
    -- 0-100: how close/personal the relationship is
    trust               REAL NOT NULL DEFAULT 50.0,
    -- 0 = total distrust, 50 = neutral, 100 = absolute trust
    dominance           REAL NOT NULL DEFAULT 0.0,
    -- -100 (persona_a dominated by b) to +100 (a dominates b)
    last_interaction_at TEXT,
    interaction_count   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_pair ON ext_rel_relationships(persona_uuid_a, persona_uuid_b);
CREATE INDEX IF NOT EXISTS idx_rel_type ON ext_rel_relationships(relationship_type);

-- Temporary situationships (time-limited relationship modifiers)
CREATE TABLE IF NOT EXISTS ext_rel_situationships (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid_a      TEXT NOT NULL,
    persona_uuid_b      TEXT NOT NULL,
    situationship_type  TEXT NOT NULL,
    --   temporary_alliance, feud, quest_partners, trade_partners, rivals
    strength_bonus      REAL NOT NULL DEFAULT 10.0,
    trust_bonus         REAL NOT NULL DEFAULT 0.0,
    intimacy_bonus      REAL NOT NULL DEFAULT 0.0,
    details             TEXT,  -- JSON: reason, trigger_event, etc.
    started_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_sit_pair ON ext_rel_situationships(persona_uuid_a, persona_uuid_b);

-- Interaction history
CREATE TABLE IF NOT EXISTS ext_rel_interactions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid_a      TEXT NOT NULL,
    persona_uuid_b      TEXT NOT NULL,
    interaction_type    TEXT NOT NULL,
    --   trade, conflict, conversation, collaboration, combat, gift, betrayal
    outcome             TEXT,  -- JSON: result data specific to interaction type
    location_area       TEXT,
    location_region     TEXT,
    skill_changes       TEXT,  -- JSON: {skill_name: delta}
    relationship_delta  REAL NOT NULL DEFAULT 0.0,
    narrative           TEXT,  -- Brief AI narrative of what happened
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_int_a ON ext_rel_interactions(persona_uuid_a);
CREATE INDEX IF NOT EXISTS idx_rel_int_b ON ext_rel_interactions(persona_uuid_b);
CREATE INDEX IF NOT EXISTS idx_rel_int_time ON ext_rel_interactions(created_at);

-- Cell/area contention events (multi-persona encounters)
CREATE TABLE IF NOT EXISTS ext_rel_contention_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id             TEXT NOT NULL,
    involved_personas   TEXT NOT NULL,  -- JSON array of persona UUIDs
    contention_type     TEXT NOT NULL,
    --   space_dispute, resource_conflict, social_gathering, accidental_meeting
    ai_reasoning        TEXT,  -- Full thinking-mode reasoning trace
    resolution          TEXT NOT NULL,
    --   peaceful, conflict, avoided, alliance_formed, trade_occurred
    outcome_data        TEXT,  -- JSON: detailed outcome (skill changes, injuries, relationship changes)
    narrative           TEXT,  -- Human-readable narrative of what happened
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_cont_area ON ext_rel_contention_events(area_id);

-- Evolving skill change history
CREATE TABLE IF NOT EXISTS ext_rel_skill_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid        TEXT NOT NULL,
    skill_name          TEXT NOT NULL,
    old_value           REAL NOT NULL,
    new_value           REAL NOT NULL,
    delta               REAL NOT NULL,
    reason              TEXT NOT NULL,
    --   practice, decay, combat_experience, trade_experience, event_outcome
    related_entity_uuid TEXT,  -- UUID of the other persona involved (if any)
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_skill_persona ON ext_rel_skill_history(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_rel_skill_name ON ext_rel_skill_history(skill_name);

"""


def ensure_schema():
    """Create all relationship extension tables if they don't exist."""
    db = get_db()
    for statement in _SCHEMA_SQL.split(";"):
        stmt = statement.strip()
        if stmt:
            try:
                db.execute(stmt)
            except Exception as e:
                log.warn("rel_database", f"Schema statement failed (may be benign): {e}")
    log.info("rel_database", "SIMULATED_RELATIONSHIPS schema ensured")


# ── Relationship CRUD ───────────────────────────────────────────────

def get_relationship(persona_a: str, persona_b: str) -> Optional[dict]:
    """Get the relationship between two personas, or None."""
    db = get_db()
    return db.fetch_one("""
        SELECT * FROM ext_rel_relationships
        WHERE (persona_uuid_a = ? AND persona_uuid_b = ?)
           OR (persona_uuid_a = ? AND persona_uuid_b = ?)
    """, (persona_a, persona_b, persona_b, persona_a))


def list_relationships(persona_uuid: str) -> list[dict]:
    """List all relationships for a persona."""
    db = get_db()
    return db.fetch_all("""
        SELECT * FROM ext_rel_relationships
        WHERE persona_uuid_a = ? OR persona_uuid_b = ?
        ORDER BY strength DESC
    """, (persona_uuid, persona_uuid))


def get_relationship_direction(persona: str, other: str) -> Optional[str]:
    """Get the relationship type FROM persona TO other (could be asymmetric)."""
    rel = get_relationship(persona, other)
    if rel is None:
        return None
    # The relationship is stored without direction; return the type
    return rel["relationship_type"]


def upsert_relationship(persona_a: str, persona_b: str,
                         rel_type: str = "neutral",
                         strength_delta: float = 0.0,
                         intimacy_delta: float = 0.0,
                         trust_delta: float = 0.0,
                         dominance_delta: float = 0.0) -> dict:
    """Create or update a relationship between two personas.

    Args:
        persona_a, persona_b: The two personas
        rel_type: Relationship type
        strength_delta: Change to relationship strength (-100 to +100)
        intimacy_delta: Change to intimacy level
        trust_delta: Change to trust level
        dominance_delta: Change to dominance (affects persona_a relative to b)

    Returns:
        The updated relationship dict
    """
    try:
        from datetime import datetime, timezone

    except Exception as e:
        log.error(f"upsert_relationship failed: {e}")
        return {}
    now = datetime.now(timezone.utc).isoformat()

    # Canonicalize ordering (smaller UUID first for consistent lookup)
    a, b = sorted([persona_a, persona_b])
    db = get_db()

    existing = get_relationship(persona_a, persona_b)
    if existing:
        new_strength = max(0.0, min(100.0, existing["strength"] + strength_delta))
        new_intimacy = max(0.0, min(100.0, existing["intimacy"] + intimacy_delta))
        new_trust = max(0.0, min(100.0, existing["trust"] + trust_delta))
        new_dominance = max(-100.0, min(100.0, existing["dominance"] + dominance_delta))

        # Type transitions based on strength thresholds
        if rel_type == "neutral":
            if new_strength >= 70:
                rel_type = "friendship"
            elif new_strength <= 20 and new_strength < existing["strength"]:
                rel_type = "rivalry"

        db.execute("""
            UPDATE ext_rel_relationships
            SET relationship_type = ?,
                strength = ?, intimacy = ?, trust = ?, dominance = ?,
                interaction_count = interaction_count + 1,
                last_interaction_at = ?, updated_at = ?
            WHERE id = ?
        """, (rel_type, new_strength, new_intimacy, new_trust, new_dominance,
              now, now, existing["id"]))
    else:
        new_strength = max(0.0, min(100.0, 50.0 + strength_delta))
        new_intimacy = max(0.0, min(100.0, intimacy_delta))
        new_trust = max(0.0, min(100.0, 50.0 + trust_delta))
        db.execute("""
            INSERT INTO ext_rel_relationships
            (persona_uuid_a, persona_uuid_b, relationship_type,
             strength, intimacy, trust, dominance,
             last_interaction_at, interaction_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (a, b, rel_type, new_strength, new_intimacy, new_trust,
              dominance_delta, now, now, now))

    return get_relationship(persona_a, persona_b)


# ── Situationship CRUD ──────────────────────────────────────────────

def create_situationship(persona_a: str, persona_b: str,
                          sit_type: str,
                          duration_hours: int = 24,
                          strength_bonus: float = 10.0,
                          trust_bonus: float = 0.0,
                          intimacy_bonus: float = 0.0,
                          details: Optional[dict] = None) -> dict:
    """Create a temporary situationship between two personas.

    Args:
        persona_a, persona_b: The two personas
        sit_type: Type of situationship
        duration_hours: How long it lasts
        strength_bonus: Bonus to relationship strength
        details: JSON-serializable dict with reason, trigger, etc.

    Returns:
        The situationship record
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=duration_hours)
    db = get_db()

    db.execute("""
        INSERT INTO ext_rel_situationships
        (persona_uuid_a, persona_uuid_b, situationship_type,
         strength_bonus, trust_bonus, intimacy_bonus, details,
         started_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_a, persona_b, sit_type,
          strength_bonus, trust_bonus, intimacy_bonus,
          json.dumps(details) if details else None,
          now.isoformat(), expires.isoformat()))

    # Also boost the core relationship
    upsert_relationship(persona_a, persona_b, rel_type="neutral",
                         strength_delta=strength_bonus * 0.5,
                         trust_delta=trust_bonus,
                         intimacy_delta=intimacy_bonus)

    return {"persona_a": persona_a, "persona_b": persona_b,
            "type": sit_type, "expires_at": expires.isoformat()}


def get_active_situationships(persona_uuid: str) -> list[dict]:
    """Get all active situationships involving this persona."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    return db.fetch_all("""
        SELECT * FROM ext_rel_situationships
        WHERE (persona_uuid_a = ? OR persona_uuid_b = ?)
          AND expires_at > ?
        ORDER BY expires_at ASC
    """, (persona_uuid, persona_uuid, now))


def expire_stale_situationships():
    """Remove all expired situationships and revert their bonuses."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    stale = db.fetch_all(
        "SELECT * FROM ext_rel_situationships WHERE expires_at <= ?",
        (now,))
    for s in stale:
        # Decay the temporary bonuses from the core relationship
        upsert_relationship(s["persona_uuid_a"], s["persona_uuid_b"],
                             strength_delta=-s["strength_bonus"] * 0.3,
                             trust_delta=-s["trust_bonus"] * 0.3,
                             intimacy_delta=-s["intimacy_bonus"] * 0.3)
        log.info("rel_relationships",
                 f"Expired situationship {s['situationship_type']} "
                 f"between {s['persona_uuid_a'][:8]} and {s['persona_uuid_b'][:8]}")
    db.execute("DELETE FROM ext_rel_situationships WHERE expires_at <= ?", (now,))
    return len(stale)


# ── Interaction Logging ─────────────────────────────────────────────

def log_interaction(persona_a: str, persona_b: str,
                     interaction_type: str,
                     outcome: Optional[dict] = None,
                     location_area: Optional[str] = None,
                     location_region: Optional[str] = None,
                     skill_changes: Optional[dict] = None,
                     relationship_delta: float = 0.0,
                     narrative: Optional[str] = None) -> dict:
    """Record an interaction between two personas and update their relationship.

    Args:
        persona_a, persona_b: The personas involved
        interaction_type: Type of interaction
        outcome: Result data
        location_area, location_region: Where it happened
        skill_changes: Dict of skill name → delta
        relationship_delta: Change to relationship strength
        narrative: AI-generated narrative

    Returns:
        The interaction record
    """
    from datetime import datetime, timezone
    import json
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()

    db.execute("""
        INSERT INTO ext_rel_interactions
        (persona_uuid_a, persona_uuid_b, interaction_type,
         outcome, location_area, location_region,
         skill_changes, relationship_delta, narrative, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_a, persona_b, interaction_type,
          json.dumps(outcome) if outcome else None,
          location_area, location_region,
          json.dumps(skill_changes) if skill_changes else None,
          relationship_delta, narrative, now))

    # Update relationship
    upsert_relationship(persona_a, persona_b,
                         strength_delta=relationship_delta,
                         intimacy_delta=abs(relationship_delta) * 0.1)

    return {"persona_a": persona_a, "persona_b": persona_b,
            "type": interaction_type, "delta": relationship_delta}


def get_recent_interactions(persona_uuid: str, limit: int = 20) -> list[dict]:
    """Get the most recent interactions for a persona."""
    db = get_db()
    return db.fetch_all("""
        SELECT * FROM ext_rel_interactions
        WHERE persona_uuid_a = ? OR persona_uuid_b = ?
        ORDER BY created_at DESC LIMIT ?
    """, (persona_uuid, persona_uuid, limit))


# ── Contention Event Logging ────────────────────────────────────────

def log_contention_event(area_id: str,
                          personas: list[str],
                          contention_type: str,
                          ai_reasoning: Optional[str] = None,
                          resolution: str = "peaceful",
                          outcome_data: Optional[dict] = None,
                          narrative: Optional[str] = None) -> dict:
    """Record a cell/area contention event.

    Args:
        area_id: The area where the contention occurred
        personas: List of persona UUIDs involved
        contention_type: Type of contention (space_dispute, resource_conflict, etc.)
        ai_reasoning: Full thinking-mode trace
        resolution: How it was resolved
        outcome_data: Detailed outcome JSON
        narrative: Human-readable narrative

    Returns:
        The contention event record
    """
    from datetime import datetime, timezone
    import json
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()

    db.execute("""
        INSERT INTO ext_rel_contention_events
        (area_id, involved_personas, contention_type,
         ai_reasoning, resolution, outcome_data, narrative, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (area_id, json.dumps(personas), contention_type,
          ai_reasoning, resolution,
          json.dumps(outcome_data) if outcome_data else None,
          narrative, now))

    return {"area": area_id, "personas": personas,
            "resolution": resolution, "narrative": narrative}


# ── Skill History ───────────────────────────────────────────────────

def record_skill_change(persona_uuid: str, skill_name: str,
                         old_value: float, new_value: float,
                         reason: str,
                         related_entity: Optional[str] = None) -> dict:
    """Record a skill change for a persona.

    Args:
        persona_uuid: The persona whose skill changed
        skill_name: Which skill
        old_value, new_value: Before/after
        reason: Why it changed (practice, decay, event_outcome, etc.)
        related_entity: UUID of another persona involved (if any)

    Returns:
        The skill history record
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()

    db.execute("""
        INSERT INTO ext_rel_skill_history
        (persona_uuid, skill_name, old_value, new_value, delta,
         reason, related_entity_uuid, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, skill_name, old_value, new_value,
          new_value - old_value, reason, related_entity, now))

    return {"persona_uuid": persona_uuid, "skill": skill_name,
            "delta": new_value - old_value, "reason": reason}



