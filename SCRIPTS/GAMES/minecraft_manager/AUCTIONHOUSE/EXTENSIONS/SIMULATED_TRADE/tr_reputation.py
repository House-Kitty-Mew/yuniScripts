"""
tr_reputation.py — Reputation & law enforcement for SIMULATED_TRADE extension.

Tracks and manages:
  - Reputation score (-1000 to +1000)
  - Notoriety (0-1000, hidden)
  - Bounties for crimes
  - Guard patrols and arrests
  - Jail system
"""

import json, math, random
from datetime import datetime, timezone
from typing import Any, Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import fire_hook
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    ensure_reputation, get_reputation,
    update_notoriety, update_bounty, update_reputation_score,
    set_jail_ticks, is_persona_jailed,
    get_all_wanted_personas, get_db,
)

log = get_logger()


def init_persona_reputation(persona_uuid: str) -> dict:
    """Initialize reputation tracking for a new persona.

    Called when a persona is first activated.

    Returns:
        {"ok": True, "reputation": dict}
    """
    success = ensure_reputation(persona_uuid)
    if not success:
        return {"ok": False, "error": "Failed to initialize reputation"}

    rep = get_reputation(persona_uuid)
    if not rep:
        return {"ok": False, "error": "Reputation not found after init"}

    # Update state registry
    state = get_state()
    rep_map = dict(state.get("persona_reputations", {}))
    rep_map[persona_uuid] = rep
    state.set("persona_reputations", rep_map, "SIMULATED_TRADE")

    log.info("tr_reputation", f"Initialized reputation for {persona_uuid[:8]}")
    return {"ok": True, "reputation": rep}


def get_persona_reputation(persona_uuid: str) -> dict:
    """Get full reputation info for a persona.

    Returns:
        Dict with reputation, notoriety, bounty, jail status, etc.
    """
    ensure_reputation(persona_uuid)
    rep = get_reputation(persona_uuid)
    if not rep:
        return {"reputation_score": 0, "bounty": 0.0, "notoriety": 0,
                "is_wanted": False, "in_jail": False}

    return {
        "reputation_score": rep.get("reputation_score", 0),
        "notoriety": rep.get("notoriety", 0),
        "bounty": rep.get("bounty", 0.0),
        "is_wanted": bool(rep.get("is_wanted", 0)),
        "in_jail": rep.get("jail_ticks_remaining", 0) > 0,
        "jail_ticks_remaining": rep.get("jail_ticks_remaining", 0),
        "arrest_count": rep.get("arrest_count", 0),
    }


def apply_crime_consequences(persona_uuid: str, crime_type: str,
                              severity: float = 1.0) -> dict:
    """Apply reputation consequences for a crime.

    Args:
        persona_uuid: The criminal
        crime_type: 'theft', 'banditry', 'murder'
        severity: Multiplier for the severity (0.0-1.0)

    Returns:
        Dict with notoriety and bounty changes.
    """
    ensure_reputation(persona_uuid)

    CRIME_NOTORIETY = {
        "theft": get_config("reputation", "notoriety_per_theft", 35),
        "banditry": get_config("reputation", "notoriety_per_banditry_base", 100),
        "murder": 200,
    }

    notoriety_gain = int(CRIME_NOTORIETY.get(crime_type, 50) * severity)
    rep_loss = -notoriety_gain // 2

    update_notoriety(persona_uuid, notoriety_gain)
    update_reputation_score(persona_uuid, rep_loss)

    # Bounty proportional to crime
    bounty_mult = {
        "theft": 2.0,
        "banditry": get_config("banditry", "bounty_ratio", 0.5) * 100,
        "murder": 500.0,
    }
    bounty_add = bounty_mult.get(crime_type, 50.0) * severity
    update_bounty(persona_uuid, bounty_add)

    log.info("tr_reputation",
             f"{crime_type} by {persona_uuid[:8]}: +{notoriety_gain} notoriety, "
             f"{rep_loss} rep, +{bounty_add:.0f} bounty")

    return {
        "notoriety_gained": notoriety_gain,
        "reputation_lost": rep_loss,
        "bounty_added": round(bounty_add, 2),
    }


def clear_bounty(persona_uuid: str) -> dict:
    """Pay off a bounty to clear wanted status.

    Requires payment of 1.5x the bounty amount.

    Returns:
        {"ok": True, "cost": float} or {"ok": False, "error": str}
    """
    try:
        rep = get_reputation(persona_uuid)

    except Exception as e:
        log.error(f"clear_bounty failed: {e}")
        return {}
    if not rep:
        return {"ok": False, "error": "No reputation record found"}

    bounty = rep.get("bounty", 0.0)
    if bounty <= 0:
        return {"ok": False, "error": "No bounty to clear"}

    cost = bounty * get_config("reputation", "bounty_clear_multiplier", 1.5)

    # Check if persona has enough gold
    state = get_state()
    finances = state.get("persona_finances", {})
    balance = finances.get(persona_uuid, {}).get("balance", 0.0)

    if balance < cost:
        return {"ok": False, "error": f"Need {cost:.0f}g to clear bounty (have {balance:.0f}g)"}

    # Deduct gold
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import _remove_gold_from_persona
    _remove_gold_from_persona(persona_uuid, cost)

    # Clear bounty and notoriety
    update_bounty(persona_uuid, -bounty)
    update_notoriety(persona_uuid, -get_reputation(persona_uuid)["notoriety"])

    # Reset wanted status
    db = get_db()
    db.execute("""
        UPDATE ext_tr_reputation
        SET is_wanted = 0
        WHERE persona_uuid = ?
    """, (persona_uuid,))
    db.conn.commit()

    log.info("tr_reputation",
             f"{persona_uuid[:8]} paid {cost:.0f}g to clear bounty of {bounty:.0f}g")

    return {"ok": True, "cost": cost}


def process_reputation_tick() -> dict:
    """Process reputation decay and jail time for one tick.

    - Decay notoriety (1% per tick)
    - Decay bounty (0.1% per tick)
    - Reduce jail time
    - Update wanted status

    Returns:
        Stats dict with decay counts.
    """
    stats = {
        "notoriety_decayed": 0,
        "bounty_decayed": 0,
        "jail_ticks_reduced": 0,
        "released_from_jail": 0,
    }

    db = get_db()

    # Decay notoriety by 1%
    notoriety_decay = get_config("reputation", "notoriety_decay_rate", 0.01)
    cursor = db.execute("""
        UPDATE ext_tr_reputation
        SET notoriety = MAX(0, CAST(notoriety * (1.0 - ?) AS INTEGER))
        WHERE notoriety > 0
    """, (notoriety_decay,))
    stats["notoriety_decayed"] = cursor.rowcount or 0

    # Decay bounty by 0.1%
    bounty_decay = get_config("reputation", "bounty_decay_rate", 0.001)
    cursor = db.execute("""
        UPDATE ext_tr_reputation
        SET bounty = MAX(0.0, bounty * (1.0 - ?))
        WHERE bounty > 0.0
    """, (bounty_decay,))
    stats["bounty_decayed"] = cursor.rowcount or 0

    # Reduce jail time
    cursor = db.execute("""
        UPDATE ext_tr_reputation
        SET jail_ticks_remaining = MAX(0, jail_ticks_remaining - 1)
        WHERE jail_ticks_remaining > 0
    """)
    stats["jail_ticks_reduced"] = cursor.rowcount or 0

    # Release from jail
    cursor = db.execute("""
        UPDATE ext_tr_reputation
        SET is_wanted = 0
        WHERE jail_ticks_remaining <= 0 AND is_wanted = 1 AND bounty <= 0
    """)
    stats["released_from_jail"] = cursor.rowcount or 0

    db.conn.commit()

    # Update state registry
    state = get_state()
    rep_map = {}
    for row in db.execute("SELECT * FROM ext_tr_reputation").fetchall():
        rep_map[row["persona_uuid"]] = dict(row)
    state.set("persona_reputations", rep_map, "SIMULATED_TRADE")

    return stats


def get_price_modifier(persona_uuid: str) -> float:
    """Get the price modifier from reputation for trade calculations.

    Positive reputation = better prices (up to 5% discount)
    Negative reputation = worse prices (up to 20% markup)

    Returns:
        Multiplier (e.g., 0.95 = 5% discount, 1.2 = 20% markup).
    """
    try:
        rep = get_reputation(persona_uuid)

    except Exception as e:
        log.error(f"get_price_modifier failed: {e}")
        return 0.0
    if not rep:
        return 1.0

    rep_score = rep.get("reputation_score", 0)

    if rep_score > 0:
        # Positive: up to 5% discount
        discount = min(0.05, rep_score / 20000.0)
        return 1.0 - discount
    elif rep_score < 0:
        # Negative: up to 20% markup
        markup = min(0.20, abs(rep_score) / 5000.0)
        return 1.0 + markup
    else:
        return 1.0


def is_guard_hostile(persona_uuid: str) -> bool:
    """Check if guards are hostile to this persona.

    Returns True if reputation < -200 and bounty > 0.
    """
    rep = get_reputation(persona_uuid)
    if not rep:
        return False

    host_threshold = get_config("reputation", "guard_hostile_reputation", -200)
    return (
        rep.get("reputation_score", 0) < host_threshold
        and rep.get("bounty", 0) > 0
    )

