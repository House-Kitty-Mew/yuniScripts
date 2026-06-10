"""
tr_database.py — Database schema & queries for SIMULATED_TRADE extension.

All tables are prefixed with ``ext_tr_`` to namespace from core AH tables.
Schema is created on first load. Provides CRUD methods for all trade entities.
"""

import json, os, sqlite3, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from AUCTIONHOUSE.ah_database import DB_DIR, get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

# ── Schema SQL ───────────────────────────────────────────────────────

_SCHEMA_SQL = """

-- 3.1 ext_tr_trades — Completed trade records
CREATE TABLE IF NOT EXISTS ext_tr_trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_uuid          TEXT NOT NULL UNIQUE,
    seller_uuid         TEXT NOT NULL,
    buyer_uuid          TEXT NOT NULL,
    trade_type          TEXT NOT NULL DEFAULT 'currency',
    items_offered       TEXT NOT NULL DEFAULT '{}',
    items_received      TEXT NOT NULL DEFAULT '{}',
    gold_amount         REAL NOT NULL DEFAULT 0.0,
    barter_skill_used   REAL DEFAULT 0.0,
    location_area       TEXT NOT NULL DEFAULT 'unknown',
    distance_modifier   REAL DEFAULT 1.0,
    relationship_delta  REAL DEFAULT 0.0,
    traded_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tr_trades_seller ON ext_tr_trades(seller_uuid);
CREATE INDEX IF NOT EXISTS idx_tr_trades_buyer ON ext_tr_trades(buyer_uuid);
CREATE INDEX IF NOT EXISTS idx_tr_trades_time ON ext_tr_trades(traded_at);

-- 3.2 ext_tr_routes — Trade routes / roads
CREATE TABLE IF NOT EXISTS ext_tr_routes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    route_uuid          TEXT NOT NULL UNIQUE,
    owner_uuid          TEXT NOT NULL,
    from_claim_uuid     TEXT NOT NULL,
    to_claim_uuid       TEXT NOT NULL,
    road_segments       TEXT NOT NULL DEFAULT '[]',
    distance            REAL NOT NULL DEFAULT 0.0,
    level               INTEGER NOT NULL DEFAULT 1,
    trade_volume        REAL NOT NULL DEFAULT 0.0,
    last_maintained     TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    bandit_activity     REAL NOT NULL DEFAULT 0.0,
    built_at            TEXT NOT NULL,
    guard_combat_power  REAL NOT NULL DEFAULT 5.0
);

CREATE INDEX IF NOT EXISTS idx_tr_routes_owner ON ext_tr_routes(owner_uuid);
CREATE INDEX IF NOT EXISTS idx_tr_routes_active ON ext_tr_routes(is_active);
CREATE INDEX IF NOT EXISTS idx_tr_routes_from ON ext_tr_routes(from_claim_uuid);

-- 3.3 ext_tr_pending_trades — Active trade offers
CREATE TABLE IF NOT EXISTS ext_tr_pending_trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_uuid          TEXT NOT NULL UNIQUE,
    initiator_uuid      TEXT NOT NULL,
    target_uuid         TEXT,
    offer_type          TEXT NOT NULL DEFAULT 'trade',
    offered_items       TEXT NOT NULL DEFAULT '{}',
    requested_items     TEXT NOT NULL DEFAULT '{}',
    gold_requested      REAL DEFAULT 0.0,
    gold_offered        REAL DEFAULT 0.0,
    expires_at          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',
    location_area       TEXT NOT NULL DEFAULT 'unknown',
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tr_pending_initiator ON ext_tr_pending_trades(initiator_uuid);
CREATE INDEX IF NOT EXISTS idx_tr_pending_target ON ext_tr_pending_trades(target_uuid);
CREATE INDEX IF NOT EXISTS idx_tr_pending_status ON ext_tr_pending_trades(status);

-- 3.4 ext_tr_banditry — Banditry incidents
CREATE TABLE IF NOT EXISTS ext_tr_banditry (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_uuid       TEXT NOT NULL UNIQUE,
    attacker_uuid       TEXT NOT NULL,
    target_route_uuid   TEXT,
    combat_power        REAL NOT NULL DEFAULT 0.0,
    defense_power       REAL NOT NULL DEFAULT 0.0,
    outcome             TEXT NOT NULL DEFAULT 'defeat',
    loot_value          REAL NOT NULL DEFAULT 0.0,
    items_stolen        TEXT NOT NULL DEFAULT '{}',
    casualties          TEXT NOT NULL DEFAULT '{}',
    notoriety_gained    INTEGER NOT NULL DEFAULT 0,
    bounty_placed       REAL NOT NULL DEFAULT 0.0,
    occurred_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tr_banditry_attacker ON ext_tr_banditry(attacker_uuid);
CREATE INDEX IF NOT EXISTS idx_tr_banditry_route ON ext_tr_banditry(target_route_uuid);

-- 3.5 ext_tr_resource_state — Per-region resource tracking
CREATE TABLE IF NOT EXISTS ext_tr_resource_state (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    region_id           TEXT NOT NULL,
    resource_id         TEXT NOT NULL,
    base_abundance      REAL NOT NULL DEFAULT 1.0,
    current_abundance   REAL NOT NULL DEFAULT 1.0,
    depletion_mult      REAL NOT NULL DEFAULT 1.0,
    depleted_at         TEXT,
    regeneration_start  TEXT,
    regeneration_rate   REAL NOT NULL DEFAULT 0.001,
    event_active        INTEGER NOT NULL DEFAULT 0,
    estimated_recovery_ticks INTEGER DEFAULT 0,
    UNIQUE(region_id, resource_id)
);

-- 3.6 ext_tr_reputation — Law enforcement state
CREATE TABLE IF NOT EXISTS ext_tr_reputation (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid            TEXT NOT NULL UNIQUE,
    notoriety               INTEGER NOT NULL DEFAULT 0,
    bounty                  REAL NOT NULL DEFAULT 0.0,
    reputation_score        INTEGER NOT NULL DEFAULT 0,
    arrest_count            INTEGER NOT NULL DEFAULT 0,
    jail_ticks_remaining    INTEGER NOT NULL DEFAULT 0,
    is_wanted               INTEGER NOT NULL DEFAULT 0,
    last_crime_at           TEXT,
    disguise_active         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tr_rep_wanted ON ext_tr_reputation(is_wanted);
CREATE INDEX IF NOT EXISTS idx_tr_rep_notoriety ON ext_tr_reputation(notoriety);

-- 3.7 ext_tr_caravans — Active caravans on routes
CREATE TABLE IF NOT EXISTS ext_tr_caravans (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    caravan_uuid        TEXT NOT NULL UNIQUE,
    route_uuid          TEXT NOT NULL,
    owner_uuid          TEXT NOT NULL,
    cargo_json          TEXT NOT NULL DEFAULT '{}',
    gold_carried        REAL NOT NULL DEFAULT 0.0,
    guard_count         INTEGER NOT NULL DEFAULT 1,
    guard_combat_power  REAL NOT NULL DEFAULT 5.0,
    current_segment     INTEGER NOT NULL DEFAULT 0,
    total_segments      INTEGER NOT NULL DEFAULT 1,
    speed               REAL NOT NULL DEFAULT 1.0,
    status              TEXT NOT NULL DEFAULT 'traveling'
);

CREATE INDEX IF NOT EXISTS idx_tr_caravans_route ON ext_tr_caravans(route_uuid);
CREATE INDEX IF NOT EXISTS idx_tr_caravans_owner ON ext_tr_caravans(owner_uuid);
CREATE INDEX IF NOT EXISTS idx_tr_caravans_status ON ext_tr_caravans(status);

-- 3.8 ext_tr_world_events — Active world events
CREATE TABLE IF NOT EXISTS ext_tr_world_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uuid          TEXT NOT NULL UNIQUE,
    event_type          TEXT NOT NULL,
    event_name          TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    affected_region     TEXT NOT NULL,
    affected_resources  TEXT NOT NULL DEFAULT '[]',
    severity            REAL NOT NULL DEFAULT 0.5,
    duration_ticks      INTEGER NOT NULL DEFAULT 100,
    ticks_remaining     INTEGER NOT NULL DEFAULT 100,
    price_multiplier    REAL NOT NULL DEFAULT 1.5,
    started_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tr_events_region ON ext_tr_world_events(affected_region);
CREATE INDEX IF NOT EXISTS idx_tr_events_type ON ext_tr_world_events(event_type);

-- 3.9 ext_tr_trade_cooldowns — Per-persona trade cooldowns
CREATE TABLE IF NOT EXISTS ext_tr_trade_cooldowns (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid        TEXT NOT NULL,
    cooldown_type       TEXT NOT NULL,
    ticks_remaining     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(persona_uuid, cooldown_type)
);

-- 3.10 ext_tr_route_trade_log — Trade volume tracking per route
CREATE TABLE IF NOT EXISTS ext_tr_route_trade_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    route_uuid          TEXT NOT NULL,
    trade_uuid          TEXT NOT NULL,
    volume              REAL NOT NULL DEFAULT 0.0,
    recorded_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tr_route_log ON ext_tr_route_trade_log(route_uuid);
"""


# ── Database initialization ──────────────────────────────────────────

def init_database() -> bool:
    """Create all ext_tr_ tables if they don't exist.

    Executes each SQL statement individually since the database
    manager wraps sqlite3 and auto-commits.

    Returns:
        True on success, False on failure.
    """
    try:
        db = get_db()
        # Split schema into individual statements and execute each
        statements = [s.strip() for s in _SCHEMA_SQL.split(';') if s.strip()]
        for stmt in statements:
            if stmt:
                try:
                    db.execute(stmt)
                except Exception as stmt_e:
                    log.warn("tr_database", f"Schema statement warning: {stmt_e}")
        log.info("tr_database", "SIMULATED_TRADE schema initialized (10 tables)")
        return True
    except Exception as e:
        log.error("tr_database", f"Schema init failed: {e}")
        return False


# ── Trades ───────────────────────────────────────────────────────────

def record_trade(seller_uuid: str, buyer_uuid: str, trade_type: str,
                 items_offered: dict, items_received: dict,
                 gold_amount: float, location_area: str,
                 distance_modifier: float = 1.0,
                 relationship_delta: float = 0.0,
                 barter_skill_used: float = 0.0) -> Optional[str]:
    """Record a completed trade transaction.

    Returns:
        trade_uuid on success, None on failure.
    """
    try:
        trade_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        db.execute("""
            INSERT INTO ext_tr_trades
                (trade_uuid, seller_uuid, buyer_uuid, trade_type,
                 items_offered, items_received, gold_amount,
                 barter_skill_used, location_area, distance_modifier,
                 relationship_delta, traded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade_uuid, seller_uuid, buyer_uuid, trade_type,
              json.dumps(items_offered), json.dumps(items_received),
              gold_amount, barter_skill_used, location_area,
              distance_modifier, relationship_delta, now))
        db.conn.commit()
        log.info("tr_database", f"Trade {trade_uuid} recorded: {seller_uuid} -> {buyer_uuid}")
        return trade_uuid
    except Exception as e:
        log.error("tr_database", f"Failed to record trade: {e}")
        return None


def get_trade_history(persona_uuid: str, limit: int = 50) -> list[dict]:
    """Get trade history for a persona (as buyer or seller)."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_trades
        WHERE seller_uuid = ? OR buyer_uuid = ?
        ORDER BY traded_at DESC LIMIT ?
    """, (persona_uuid, persona_uuid, limit))
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


# ── Pending Trades ───────────────────────────────────────────────────

def create_pending_offer(initiator_uuid: str, target_uuid: Optional[str],
                         offer_type: str, offered_items: dict,
                         requested_items: dict, gold_requested: float,
                         gold_offered: float, expiry_ticks: int,
                         location_area: str = "unknown") -> Optional[str]:
    """Create a pending trade offer.

    Returns:
        offer_uuid on success, None on failure.
    """
    try:
        offer_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        exp = datetime.now(timezone.utc).isoformat()  # expiry managed by tick system
        db = get_db()
        db.execute("""
            INSERT INTO ext_tr_pending_trades
                (offer_uuid, initiator_uuid, target_uuid, offer_type,
                 offered_items, requested_items, gold_requested, gold_offered,
                 expires_at, status, location_area, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
        """, (offer_uuid, initiator_uuid, target_uuid, offer_type,
              json.dumps(offered_items), json.dumps(requested_items),
              gold_requested, gold_offered, exp, location_area, now))
        db.conn.commit()
        return offer_uuid
    except Exception as e:
        log.error("tr_database", f"Failed to create offer: {e}")
        return None


def accept_offer(offer_uuid: str) -> bool:
    """Mark a pending offer as accepted.

    Returns:
        True if accepted, False if already claimed.
    """
    db = get_db()
    cursor = db.execute("""
        UPDATE ext_tr_pending_trades
        SET status = 'accepted'
        WHERE offer_uuid = ? AND status = 'open'
    """, (offer_uuid,))
    db.conn.commit()
    return cursor.rowcount > 0


def decline_offer(offer_uuid: str) -> bool:
    """Mark a pending offer as declined."""
    db = get_db()
    cursor = db.execute("""
        UPDATE ext_tr_pending_trades
        SET status = 'declined'
        WHERE offer_uuid = ? AND status = 'open'
    """, (offer_uuid,))
    db.conn.commit()
    return cursor.rowcount > 0


def get_open_offers_for_persona(persona_uuid: str) -> list[dict]:
    """Get all open offers targeting or initiated by a persona."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_pending_trades
        WHERE (target_uuid = ? OR target_uuid IS NULL)
          AND status = 'open'
          AND initiator_uuid != ?
        ORDER BY created_at DESC
    """, (persona_uuid, persona_uuid))
    return [dict(row) for row in cursor.fetchall()]


def count_open_offers_by_initiator(initiator_uuid: str) -> int:
    """Count how many open offers a specific persona has initiated."""
    db = get_db()
    cursor = db.execute("""
        SELECT COUNT(*) as cnt FROM ext_tr_pending_trades
        WHERE initiator_uuid = ? AND status = 'open'
    """, (initiator_uuid,))
    row = cursor.fetchone()
    return row["cnt"] if row else 0


def expire_old_offers(max_age_ticks: int) -> int:
    """Expire offers older than max_age_ticks.

    Returns:
        Number of expired offers.
    """
    # In a real system, expires_at would be set properly.
    # Here we use a simple count query to find stale offers.
    db = get_db()
    cursor = db.execute("""
        UPDATE ext_tr_pending_trades
        SET status = 'expired'
        WHERE status = 'open'
          AND datetime(created_at) < datetime('now', ?)
    """, (f'-{max_age_ticks} seconds',))
    db.conn.commit()
    return cursor.rowcount


# ── Routes ───────────────────────────────────────────────────────────

def create_route(owner_uuid: str, from_claim_uuid: str, to_claim_uuid: str,
                 road_segments: list, distance: float) -> Optional[str]:
    """Create a new trade route.

    Returns:
        route_uuid on success, None on failure.
    """
    try:
        route_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        db.execute("""
            INSERT INTO ext_tr_routes
                (route_uuid, owner_uuid, from_claim_uuid, to_claim_uuid,
                 road_segments, distance, level, last_maintained,
                 is_active, bandit_activity, built_at, guard_combat_power)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, 1, 0.0, ?, 5.0)
        """, (route_uuid, owner_uuid, from_claim_uuid, to_claim_uuid,
              json.dumps(road_segments), distance, now, now))
        db.conn.commit()
        return route_uuid
    except Exception as e:
        log.error("tr_database", f"Failed to create route: {e}")
        return None


def upgrade_route(route_uuid: str) -> bool:
    """Increase route level by 1 (max 5)."""
    db = get_db()
    cursor = db.execute("""
        UPDATE ext_tr_routes
        SET level = MIN(level + 1, 5),
            last_maintained = ?
        WHERE route_uuid = ? AND level < 5
    """, (datetime.now(timezone.utc).isoformat(), route_uuid))
    db.conn.commit()
    return cursor.rowcount > 0


def maintain_route(route_uuid: str) -> bool:
    """Reset maintenance timer on a route."""
    db = get_db()
    cursor = db.execute("""
        UPDATE ext_tr_routes
        SET last_maintained = ?
        WHERE route_uuid = ?
    """, (datetime.now(timezone.utc).isoformat(), route_uuid))
    db.conn.commit()
    return cursor.rowcount > 0


def degrade_route(route_uuid: str) -> bool:
    """Decrease route level by 1 (min 1)."""
    db = get_db()
    cursor = db.execute("""
        UPDATE ext_tr_routes
        SET level = MAX(level - 1, 1)
        WHERE route_uuid = ? AND level > 1
    """, (route_uuid,))
    db.conn.commit()
    return cursor.rowcount > 0


def get_routes_for_persona(persona_uuid: str) -> list[dict]:
    """Get all routes owned by a persona."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_routes
        WHERE owner_uuid = ? AND is_active = 1
        ORDER BY built_at DESC
    """, (persona_uuid,))
    return [dict(row) for row in cursor.fetchall()]


def get_routes_between_claims(claim_a: str, claim_b: str) -> list[dict]:
    """Get all routes connecting two claims."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_routes
        WHERE ((from_claim_uuid = ? AND to_claim_uuid = ?)
            OR (from_claim_uuid = ? AND to_claim_uuid = ?))
          AND is_active = 1
    """, (claim_a, claim_b, claim_b, claim_a))
    return [dict(row) for row in cursor.fetchall()]


def log_route_trade(route_uuid: str, trade_uuid: str, volume: float) -> bool:
    """Log trade volume on a route."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        db.execute("""
            INSERT INTO ext_tr_route_trade_log
                (route_uuid, trade_uuid, volume, recorded_at)
            VALUES (?, ?, ?, ?)
        """, (route_uuid, trade_uuid, volume, now))
        # Update accumulated volume
        db.execute("""
            UPDATE ext_tr_routes
            SET trade_volume = trade_volume + ?
            WHERE route_uuid = ?
        """, (volume, route_uuid))
        db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to log route trade: {e}")
        return False


# ── Banditry ─────────────────────────────────────────────────────────

def record_banditry(attacker_uuid: str, target_route_uuid: Optional[str],
                    combat_power: float, defense_power: float,
                    outcome: str, loot_value: float,
                    items_stolen: dict, casualties: dict,
                    notoriety_gained: int, bounty_placed: float) -> Optional[str]:
    """Record a banditry incident.

    Returns:
        incident_uuid on success, None on failure.
    """
    try:
        incident_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        db.execute("""
            INSERT INTO ext_tr_banditry
                (incident_uuid, attacker_uuid, target_route_uuid,
                 combat_power, defense_power, outcome, loot_value,
                 items_stolen, casualties, notoriety_gained,
                 bounty_placed, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (incident_uuid, attacker_uuid, target_route_uuid,
              combat_power, defense_power, outcome, loot_value,
              json.dumps(items_stolen), json.dumps(casualties),
              notoriety_gained, bounty_placed, now))
        db.conn.commit()
        return incident_uuid
    except Exception as e:
        log.error("tr_database", f"Failed to record banditry: {e}")
        return None


def get_banditry_history(attacker_uuid: str, limit: int = 20) -> list[dict]:
    """Get banditry incidents for a persona."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_banditry
        WHERE attacker_uuid = ?
        ORDER BY occurred_at DESC LIMIT ?
    """, (attacker_uuid, limit))
    return [dict(row) for row in cursor.fetchall()]


# ── Resource State ───────────────────────────────────────────────────

def ensure_resource_state(region_id: str, resource_id: str) -> bool:
    """Ensure a resource state row exists for a region+resource pair."""
    try:
        db = get_db()
        cursor = db.execute("""
            SELECT id FROM ext_tr_resource_state
            WHERE region_id = ? AND resource_id = ?
        """, (region_id, resource_id))
        if cursor.fetchone() is None:
            db.execute("""
                INSERT INTO ext_tr_resource_state
                    (region_id, resource_id, base_abundance, current_abundance,
                     depletion_mult, regeneration_rate)
                VALUES (?, ?, 1.0, 1.0, 1.0, 0.001)
            """, (region_id, resource_id))
            db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to ensure resource state: {e}")
        return False


def get_resource_state(region_id: str, resource_id: str) -> Optional[dict]:
    """Get resource state for a specific region+resource."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_resource_state
        WHERE region_id = ? AND resource_id = ?
    """, (region_id, resource_id))
    row = cursor.fetchone()
    return dict(row) if row else None


def update_resource_abundance(region_id: str, resource_id: str,
                              new_abundance: float) -> bool:
    """Update the current abundance level for a resource."""
    try:
        db = get_db()
        depletion_mult = 1.0
        if new_abundance < 0.5:
            depletion_mult = 1.0 + (0.5 - new_abundance) * 2.0
        db.execute("""
            UPDATE ext_tr_resource_state
            SET current_abundance = ?,
                depletion_mult = ?
            WHERE region_id = ? AND resource_id = ?
        """, (new_abundance, depletion_mult, region_id, resource_id))
        db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to update resource: {e}")
        return False


# ── Reputation ───────────────────────────────────────────────────────

def ensure_reputation(persona_uuid: str) -> bool:
    """Ensure a reputation record exists for a persona."""
    try:
        db = get_db()
        cursor = db.execute("""
            SELECT id FROM ext_tr_reputation WHERE persona_uuid = ?
        """, (persona_uuid,))
        if cursor.fetchone() is None:
            db.execute("""
                INSERT INTO ext_tr_reputation
                    (persona_uuid, notoriety, bounty, reputation_score,
                     arrest_count, jail_ticks_remaining, is_wanted,
                     disguise_active)
                VALUES (?, 0, 0.0, 0, 0, 0, 0, 0)
            """, (persona_uuid,))
            db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to ensure reputation: {e}")
        return False


def get_reputation(persona_uuid: str) -> Optional[dict]:
    """Get reputation record for a persona."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_reputation WHERE persona_uuid = ?
    """, (persona_uuid,))
    row = cursor.fetchone()
    return dict(row) if row else None


def update_notoriety(persona_uuid: str, delta: int) -> bool:
    """Add or subtract notoriety (clamped to 0-1000)."""
    try:
        db = get_db()
        db.execute("""
            UPDATE ext_tr_reputation
            SET notoriety = MAX(0, MIN(1000, notoriety + ?)),
                last_crime_at = CASE WHEN ? > 0 THEN ? ELSE last_crime_at END,
                is_wanted = CASE WHEN notoriety + ? > 50 THEN 1 ELSE is_wanted END
            WHERE persona_uuid = ?
        """, (delta, delta, datetime.now(timezone.utc).isoformat(), delta, persona_uuid))
        db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to update notoriety: {e}")
        return False


def update_bounty(persona_uuid: str, delta: float) -> bool:
    """Add or subtract bounty (min 0)."""
    try:
        db = get_db()
        db.execute("""
            UPDATE ext_tr_reputation
            SET bounty = MAX(0.0, bounty + ?)
            WHERE persona_uuid = ?
        """, (delta, persona_uuid))
        db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to update bounty: {e}")
        return False


def update_reputation_score(persona_uuid: str, delta: int) -> bool:
    """Add or subtract reputation (clamped to -1000 to +1000)."""
    try:
        db = get_db()
        db.execute("""
            UPDATE ext_tr_reputation
            SET reputation_score = MAX(-1000, MIN(1000, reputation_score + ?))
            WHERE persona_uuid = ?
        """, (delta, persona_uuid))
        db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to update reputation: {e}")
        return False


def set_jail_ticks(persona_uuid: str, ticks: int) -> bool:
    """Set jail time remaining for a persona."""
    try:
        db = get_db()
        db.execute("""
            UPDATE ext_tr_reputation
            SET jail_ticks_remaining = ?
            WHERE persona_uuid = ?
        """, (ticks, persona_uuid))
        db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to set jail ticks: {e}")
        return False


def is_persona_jailed(persona_uuid: str) -> bool:
    """Check if a persona is currently in jail."""
    rep = get_reputation(persona_uuid)
    if rep and rep.get("jail_ticks_remaining", 0) > 0:
        return True
    return False


# ── Caravans ─────────────────────────────────────────────────────────

def create_caravan(route_uuid: str, owner_uuid: str, cargo: dict,
                   gold_carried: float, guard_count: int,
                   guard_combat_power: float, total_segments: int) -> Optional[str]:
    """Create a new caravan on a trade route.

    Returns:
        caravan_uuid on success, None on failure.
    """
    try:
        caravan_uuid = str(uuid.uuid4())
        db = get_db()
        db.execute("""
            INSERT INTO ext_tr_caravans
                (caravan_uuid, route_uuid, owner_uuid, cargo_json,
                 gold_carried, guard_count, guard_combat_power,
                 current_segment, total_segments, speed, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 1.0, 'traveling')
        """, (caravan_uuid, route_uuid, owner_uuid, json.dumps(cargo),
              gold_carried, guard_count, guard_combat_power, total_segments))
        db.conn.commit()
        log.info("tr_database", f"Caravan {caravan_uuid} created on route {route_uuid}")
        return caravan_uuid
    except Exception as e:
        log.error("tr_database", f"Failed to create caravan: {e}")
        return None


def advance_caravan(caravan_uuid: str, speed: float) -> bool:
    """Advance a caravan along its route by speed segments.

    Returns:
        True if still traveling, False if arrived/destroyed.
    """
    try:
        db = get_db()
        cursor = db.execute("""
            UPDATE ext_tr_caravans
            SET current_segment = MIN(current_segment + ?, total_segments),
                status = CASE
                    WHEN current_segment + ? >= total_segments THEN 'arrived'
                    ELSE 'traveling'
                END
            WHERE caravan_uuid = ? AND status = 'traveling'
        """, (speed, speed, caravan_uuid))
        db.conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        log.error("tr_database", f"Failed to advance caravan: {e}")
        return False


def get_active_caravans_on_route(route_uuid: str) -> list[dict]:
    """Get all traveling caravans on a specific route."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_caravans
        WHERE route_uuid = ? AND status = 'traveling'
    """, (route_uuid,))
    return [dict(row) for row in cursor.fetchall()]


# ── World Events ─────────────────────────────────────────────────────

def create_world_event(event_type: str, event_name: str, description: str,
                       affected_region: str, affected_resources: list,
                       severity: float, duration_ticks: int,
                       price_multiplier: float) -> Optional[str]:
    """Create a new world event.

    Returns:
        event_uuid on success, None on failure.
    """
    try:
        event_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        db.execute("""
            INSERT INTO ext_tr_world_events
                (event_uuid, event_type, event_name, description,
                 affected_region, affected_resources, severity,
                 duration_ticks, ticks_remaining, price_multiplier, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (event_uuid, event_type, event_name, description,
              affected_region, json.dumps(affected_resources),
              severity, duration_ticks, duration_ticks,
              price_multiplier, now))
        db.conn.commit()
        return event_uuid
    except Exception as e:
        log.error("tr_database", f"Failed to create world event: {e}")
        return None


def tick_world_events() -> int:
    """Reduce ticks_remaining on all active events.

    Returns:
        Number of events that expired this tick.
    """
    try:
        db = get_db()
        # Decrement ticks
        db.execute("""
            UPDATE ext_tr_world_events
            SET ticks_remaining = ticks_remaining - 1
            WHERE ticks_remaining > 0
        """)
        # Count expired
        cursor = db.execute("""
            SELECT COUNT(*) as cnt FROM ext_tr_world_events
            WHERE ticks_remaining <= 0
        """)
        expired = cursor.fetchone()["cnt"]
        # Remove expired
        db.execute("""
            DELETE FROM ext_tr_world_events WHERE ticks_remaining <= 0
        """)
        db.conn.commit()
        return expired or 0
    except Exception as e:
        log.error("tr_database", f"Failed to tick world events: {e}")
        return 0


def get_active_world_events() -> list[dict]:
    """Get all active world events."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_world_events
        WHERE ticks_remaining > 0
        ORDER BY started_at DESC
    """)
    return [dict(row) for row in cursor.fetchall()]


# ── Cooldowns ────────────────────────────────────────────────────────

def check_cooldown(persona_uuid: str, cooldown_type: str) -> bool:
    """Check if a persona is on cooldown for an action type.

    Returns:
        True if on cooldown, False if action is allowed.
    """
    db = get_db()
    cursor = db.execute("""
        SELECT ticks_remaining FROM ext_tr_trade_cooldowns
        WHERE persona_uuid = ? AND cooldown_type = ? AND ticks_remaining > 0
    """, (persona_uuid, cooldown_type))
    return cursor.fetchone() is not None


def set_cooldown(persona_uuid: str, cooldown_type: str, ticks: int) -> bool:
    """Set a cooldown for a persona on an action type."""
    try:
        db = get_db()
        db.execute("""
            INSERT INTO ext_tr_trade_cooldowns
                (persona_uuid, cooldown_type, ticks_remaining)
            VALUES (?, ?, ?)
            ON CONFLICT(persona_uuid, cooldown_type)
            DO UPDATE SET ticks_remaining = MAX(ticks_remaining, ?)
        """, (persona_uuid, cooldown_type, ticks, ticks))
        db.conn.commit()
        return True
    except Exception as e:
        log.error("tr_database", f"Failed to set cooldown: {e}")
        return False


def tick_cooldowns() -> int:
    """Decrement all cooldowns by 1 tick.

    Returns:
        Number of cooldowns that expired.
    """
    try:
        db = get_db()
        db.execute("""
            UPDATE ext_tr_trade_cooldowns
            SET ticks_remaining = MAX(0, ticks_remaining - 1)
            WHERE ticks_remaining > 0
        """)
        cursor = db.execute("""
            DELETE FROM ext_tr_trade_cooldowns WHERE ticks_remaining <= 0
        """)
        db.conn.commit()
        return cursor.rowcount or 0
    except Exception as e:
        log.error("tr_database", f"Failed to tick cooldowns: {e}")
        return 0


# ── Cleanup / Full state ─────────────────────────────────────────────

def get_all_wanted_personas() -> list[dict]:
    """Get all personas that are currently wanted by guards."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_reputation
        WHERE is_wanted = 1 AND bounty > 0
        ORDER BY bounty DESC
    """)
    return [dict(row) for row in cursor.fetchall()]


def get_all_resource_states() -> list[dict]:
    """Get all resource states across all regions."""
    db = get_db()
    cursor = db.execute("""
        SELECT * FROM ext_tr_resource_state
        ORDER BY region_id, resource_id
    """)
    return [dict(row) for row in cursor.fetchall()]


def get_summary_stats() -> dict:
    """Get summary statistics for the trade extension.

    Returns:
        Dict with counts of various entities.
    """
    db = get_db()
    stats = {}
    for table, label in [
        ("ext_tr_trades", "total_trades"),
        ("ext_tr_routes", "active_routes"),
        ("ext_tr_pending_trades", "open_offers"),
        ("ext_tr_banditry", "banditry_incidents"),
        ("ext_tr_caravans", "active_caravans"),
        ("ext_tr_world_events", "active_events"),
    ]:
        cursor = db.execute(f"SELECT COUNT(*) as cnt FROM {table}")
        row = cursor.fetchone()
        stats[label] = row["cnt"] if row else 0
    return stats
