"""
ah_market_events.py — Market Event system.

Manages the lifecycle of market events:
  - start_event()     — Create and activate a new market event
  - end_event()       — Manually end an active event
  - get_active_events() — Get all currently active events
  - check_event_progress() — Check if any events have met their hidden goals
  - get_event_history() — Get past events
  - can_start_event() — Check if a new event of a given rarity is allowed (cooldown)

Events have 4 rarity tiers (Small, Medium, Rare, Major) with staggered
frequency intervals and cooldowns.  The AI decides when to trigger events,
but the system enforces minimum cooldowns to prevent spam.
"""

import uuid, json, math
from datetime import datetime, timezone, timedelta
from typing import Optional

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_config import get_config

log = get_logger()
cfg = get_config

VALID_EVENT_TYPES = frozenset({
    "seasonal", "shortage", "surplus", "discovery", "disaster", "festival", "raid"
})

VALID_RARITY_TIERS = frozenset({"small", "medium", "rare", "major"})


# ──────────────────────────────────────────────────────────────────────
# Cooldown & frequency checks
# ──────────────────────────────────────────────────────────────────────

def _get_cooldown_hours(rarity_tier: str) -> int:
    """Return the minimum cooldown (in hours) for a given rarity tier.

    These ensure events don't fire too close together.
    """
    config = cfg()
    cooldowns = {
        "small":  6,                                 # 6 hours
        "medium": 24,                                # 1 day
        "rare":   72,                                # 3 days
        "major":  config.event_major_cooldown_days * 24,  # 30 days
    }
    return cooldowns.get(rarity_tier, 24)


def _get_interval_hours(rarity_tier: str) -> int:
    """Return the target interval between events of this rarity."""
    config = cfg()
    intervals = {
        "small":  config.event_small_interval_hours,    # 3 hours
        "medium": config.event_medium_interval_hours,   # 48 hours
        "rare":   config.event_rare_interval_hours,     # 336 hours (2 weeks)
        "major":  config.event_major_interval_hours,    # 2160 hours (3 months)
    }
    return intervals.get(rarity_tier, 24)


def can_start_event(rarity_tier: str) -> dict:
    """Check whether a new event of the given rarity is allowed.

    Checks:
      1. Max active overlap (don't exceed config.event_max_active_overlap)
      2. Same-rarity cooldown (no two events of same tier too close)
      3. Major event special: must have build-up notes, 30d cooldown

    Returns:
        {"ok": True} or {"ok": False, "reason": "..."}
    """
    db = get_db()
    now = datetime.now(timezone.utc)

    config = cfg()

    # 1. Max active overlap
    active_count = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM market_events WHERE is_active = 1")
    if active_count and active_count["cnt"] >= config.event_max_active_overlap:
        return {"ok": False, "reason": f"Max active events ({config.event_max_active_overlap}) reached"}

    # 2. Count already-active events of this rarity
    same_tier_active = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM market_events WHERE rarity_tier = ? AND is_active = 1",
        (rarity_tier,)
    )
    if same_tier_active and same_tier_active["cnt"] > 0:
        return {"ok": False, "reason": f"An event of rarity '{rarity_tier}' is already active"}

    # 3. Same-rarity cooldown
    cooldown_hours = _get_cooldown_hours(rarity_tier)
    cooldown_cutoff = (now - timedelta(hours=cooldown_hours)).isoformat()

    recent_same = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM market_events WHERE rarity_tier = ? AND started_at > ?",
        (rarity_tier, cooldown_cutoff)
    )
    if recent_same and recent_same["cnt"] > 0:
        return {"ok": False, "reason": f"Cooldown for '{rarity_tier}' events ({cooldown_hours}h) not yet elapsed"}

    # 4. Major event special checks
    if rarity_tier == "major":
        from AUCTIONHOUSE.ah_helper_db import get_event_build_up_notes
        build_up = get_event_build_up_notes("major", min_days=7)
        if not build_up:
            return {"ok": False, "reason": "Major events need ≥1 week of build-up notes in AI helper DB first"}
        # Check 30-day cooldown since last major
        major_cutoff = (now - timedelta(days=config.event_major_cooldown_days)).isoformat()
        last_major = db.fetch_one(
            "SELECT COUNT(*) as cnt FROM market_events WHERE rarity_tier = 'major' AND started_at > ?",
            (major_cutoff,)
        )
        if last_major and last_major["cnt"] > 0:
            return {"ok": False, "reason": f"Major event cooldown ({config.event_major_cooldown_days}d) not yet elapsed"}

    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────
# Start event
# ──────────────────────────────────────────────────────────────────────

def start_event(event_name: str, event_title: str, event_flavor: str,
                event_type: str, rarity_tier: str,
                affected_items: list,
                price_multiplier: float = 1.5,
                demand_boost: float = 1.0,
                duration_seconds: Optional[int] = None,
                goal_count: Optional[int] = None,
                trigger_condition: Optional[dict] = None,
                created_by: str = "AI") -> dict:
    """Start a new market event.

    Args:
        event_name: Machine-readable identifier (e.g. "extreme_winter")
        event_title: Player-facing title (e.g. "❄ Extreme Winter!")
        event_flavor: Flavor text broadcast to players
        event_type: One of VALID_EVENT_TYPES
        rarity_tier: One of VALID_RARITY_TIERS
        affected_items: List of item IDs affected (e.g. ["minecraft:coal"])
        price_multiplier: Multiply simulated prices by this factor
        demand_boost: Increase demand by this factor
        duration_seconds: Duration in seconds (auto-calculated from tier if None)
        goal_count: Hidden goal for event resolution
        trigger_condition: JSON dict for trigger conditions
        created_by: 'AI' or 'admin'

    Returns:
        {"ok": True, "data": {"event_uuid": ..., "event_name": ...}}
        or {"ok": False, "error": "..."}
    """
    if event_type not in VALID_EVENT_TYPES:
        return {"ok": False, "error": f"Invalid event type '{event_type}'. Valid: {', '.join(sorted(VALID_EVENT_TYPES))}"}

    if rarity_tier not in VALID_RARITY_TIERS:
        return {"ok": False, "error": f"Invalid rarity tier '{rarity_tier}'. Valid: {', '.join(sorted(VALID_RARITY_TIERS))}"}

    # Check cooldowns
    check = can_start_event(rarity_tier)
    if not check["ok"]:
        return check

    # Auto-calculate duration if not provided
    if duration_seconds is None:
        duration_map = {"small": 3600, "medium": 28800, "rare": 86400, "major": 259200}
        duration_seconds = duration_map.get(rarity_tier, 86400)

    # Auto-calculate goal if not provided
    if goal_count is None:
        db = get_db()
        total_stock = 0
        for item_id in affected_items:
            row = db.fetch_one(
                "SELECT current_stock FROM simulated_inventory WHERE item_id = ?",
                (item_id,)
            )
            if row:
                total_stock += row["current_stock"]
        goal_count = max(10, int(total_stock * 0.3)) if total_stock > 0 else 100

    # Apply price multiplier to simulated inventory
    db = get_db()
    for item_id in affected_items:
        db.execute("""
            UPDATE simulated_inventory
            SET current_price = ROUND(base_price * ?, 2),
                trend_direction = 1,
                trend_strength = ?
            WHERE item_id = ?
        """, (price_multiplier, min(demand_boost, 1.0), item_id))

    now = datetime.now(timezone.utc).isoformat()
    event_uuid = str(uuid.uuid4())

    db.execute("""
        INSERT INTO market_events
        (event_uuid, event_name, event_title, event_flavor, event_type,
         rarity_tier, affected_items, price_multiplier, demand_boost,
         duration_seconds, trigger_condition, goal_count, current_count,
         is_active, started_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
    """, (
        event_uuid, event_name, event_title, event_flavor, event_type,
        rarity_tier, json.dumps(affected_items), price_multiplier, demand_boost,
        duration_seconds,
        json.dumps(trigger_condition) if trigger_condition else None,
        goal_count, now, created_by
    ))

    log.info("market_events",
             f"Event started: '{event_title}' ({rarity_tier}) — affects {affected_items}, "
             f"multiplier={price_multiplier}x, goal={goal_count}",
             {"event_uuid": event_uuid})

    return {"ok": True, "data": {
        "event_uuid": event_uuid,
        "event_name": event_name,
        "event_title": event_title,
        "event_type": event_type,
        "rarity_tier": rarity_tier,
        "affected_items": affected_items,
        "price_multiplier": price_multiplier,
        "goal_count": goal_count,
        "duration_seconds": duration_seconds,
    }}


# ──────────────────────────────────────────────────────────────────────
# End event
# ──────────────────────────────────────────────────────────────────────

def end_event(event_uuid: str, reason: str = "manual") -> dict:
    """End an active market event and normalize prices.

    Args:
        event_uuid: UUID of the event to end
        reason: Why the event ended ('goal_reached', 'expired', 'manual')

    Returns:
        {"ok": True, "data": {"event_name": ..., "affected_items": [...]}}
        or {"ok": False, "error": "..."}
    """
    db = get_db()
    event = db.fetch_one(
        "SELECT * FROM market_events WHERE event_uuid = ?", (event_uuid,))
    if not event:
        return {"ok": False, "error": "Event not found"}
    if not event["is_active"]:
        return {"ok": False, "error": "Event is already ended"}

    now = datetime.now(timezone.utc).isoformat()

    # Normalize prices for affected items
    try:
        affected = json.loads(event["affected_items"])
    except (json.JSONDecodeError, TypeError):
        affected = []

    for item_id in affected:
        db.execute("""
            UPDATE simulated_inventory
            SET current_price = base_price,
                trend_direction = 0,
                trend_strength = 0.0
            WHERE item_id = ?
        """, (item_id,))

    db.execute(
        "UPDATE market_events SET is_active = 0, ended_at = ? WHERE event_uuid = ?",
        (now, event_uuid)
    )

    log.info("market_events",
             f"Event ended: '{event['event_title']}' — reason: {reason}",
             {"event_uuid": event_uuid})

    return {"ok": True, "data": {
        "event_name": event["event_name"],
        "event_title": event["event_title"],
        "affected_items": affected,
        "reason": reason,
    }}


# ──────────────────────────────────────────────────────────────────────
# Queries
# ──────────────────────────────────────────────────────────────────────

def get_active_events() -> list[dict]:
    """Get all currently active market events."""
    db = get_db()
    return db.fetch_all(
        "SELECT * FROM market_events WHERE is_active = 1 ORDER BY started_at DESC"
    )


def get_event_history(limit: int = 20) -> list[dict]:
    """Get past (ended) events.

    Args:
        limit: Max events to return

    Returns:
        List of event dicts
    """
    db = get_db()
    return db.fetch_all(
        "SELECT * FROM market_events ORDER BY started_at DESC LIMIT ?",
        (limit,)
    )


def check_event_progress() -> list[dict]:
    """Check if any active events have met their hidden goal or expired.

    Returns:
        List of events that just completed (for announcement)
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    completed = []

    active = db.fetch_all(
        "SELECT * FROM market_events WHERE is_active = 1")

    for event in active:
        # Check expiration
        started = datetime.fromisoformat(event["started_at"])
        duration = timedelta(seconds=event["duration_seconds"])
        if datetime.now(timezone.utc) > started + duration:
            # Event expired
            end_event(event["event_uuid"], reason="expired")
            completed.append({
                "event_uuid": event["event_uuid"],
                "event_name": event["event_name"],
                "event_title": event["event_title"],
                "reason": "expired",
                "goal_reached": False,
            })
            continue

        # Check hidden goal
        if event["goal_count"] and (event["current_count"] or 0) >= event["goal_count"]:
            end_event(event["event_uuid"], reason="goal_reached")
            completed.append({
                "event_uuid": event["event_uuid"],
                "event_name": event["event_name"],
                "event_title": event["event_title"],
                "reason": "goal_reached",
                "goal_reached": True,
                "goal_count": event["goal_count"],
            })

    return completed


def get_event_summary_for_prompt() -> str:
    """Format active events for inclusion in the AI prompt.

    Returns:
        Formatted string describing active events and recent ones.
    """
    active = get_active_events()
    if not active:
        return "  No active market events."

    lines = []
    for e in active:
        try:
            affected = json.loads(e["affected_items"])
        except (json.JSONDecodeError, TypeError):
            affected = [e["affected_items"]]

        progress = f"{e['current_count'] or 0}/{e['goal_count'] or '?'}" if e["goal_count"] else "N/A"
        lines.append(
            f"  [{e['rarity_tier']:6s}] {e['event_title']} — "
            f"affects: {', '.join(affected)} | "
            f"multiplier: {e['price_multiplier']}x | "
            f"progress: {progress}"
        )

    return "\n".join(lines)


def get_price_multiplier_for_item(item_id: str) -> float:
    """Get the current price multiplier for an item from active events.

    If multiple events affect the same item, multipliers stack multiplicatively.

    Args:
        item_id: e.g. "minecraft:coal"

    Returns:
        Combined multiplier (1.0 = no active events)
    """
    db = get_db()
    events = db.fetch_all(
        "SELECT price_multiplier, affected_items FROM market_events WHERE is_active = 1"
    )

    multiplier = 1.0
    for e in events:
        try:
            affected = json.loads(e["affected_items"])
        except (json.JSONDecodeError, TypeError):
            affected = [e["affected_items"]]
        if item_id in affected:
            multiplier *= e["price_multiplier"]

    return multiplier
