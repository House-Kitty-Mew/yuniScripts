"""
soc_boredom.py — Boredom system for the social simulation.

Tracks boredom (0-100) for each persona:
  - Decays over time based on archetype
  - Recovered through social activities
  - Drives social planning and crisis management
"""

import random, math
from typing import Optional, Any
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
    get_or_create_profile, update_boredom, update_exhaustion
)

log = get_logger()

# Archetype boredom decay multipliers
_ARCHETYPE_BOREDOM_MULT = {
    "adventurer": 1.5, "merchant": 1.0, "builder": 0.7,
    "miner": 1.2, "farmer": 0.5, "warrior": 0.8,
    "mage": 1.0, "vagabond": 2.0,
}

_ARCHETYPE_BOREDOM_REC_MULT = {
    "adventurer": 0.8, "merchant": 1.0, "builder": 1.3,
    "miner": 0.9, "farmer": 1.5, "warrior": 0.7,
    "mage": 0.8, "vagabond": 1.1,
}

_ACTIVITY_BOREDOM_RECOVERY = {
    "conversation": 8, "playing": 15, "hanging_out": 10,
    "eating_together": 12, "drinking": 18, "dancing": 20,
    "storytelling": 14, "gift_giving": 25, "celebration": 22,
    "arguing": -5, "resting_alone": 0, "resting_company": 2,
}

_ACTIVITY_EXHAUSTION_COST = {
    "conversation": 3, "playing": 5, "hanging_out": 2,
    "eating_together": 4, "drinking": 6, "dancing": 8,
    "storytelling": 4, "gift_giving": 2, "celebration": 10,
    "arguing": 8, "resting_alone": 0, "resting_company": -2,
}

_ARCH_EXHAUSTION_MULT = {
    "adventurer": 1.2, "merchant": 2.0, "builder": 0.7,
    "miner": 1.0, "farmer": 0.6, "warrior": 0.8,
    "mage": 1.5, "vagabond": 0.5,
}


def process_boredom_tick(persona_uuid: str, archetype: Optional[str] = None) -> dict:
    """Process one tick of boredom for a persona.

    Decays boredom based on archetype. Returns the result.
    """
    if not persona_uuid:
        return {"boredom": 100.0, "decay": 0.0}

    mult = _ARCHETYPE_BOREDOM_MULT.get(archetype, 1.0)
    decay = -random.uniform(0.5, 2.0) * mult

    profile = get_or_create_profile(persona_uuid)
    if profile.get("resting_state") in ("sleeping", "resting"):
        decay *= 0.3  # Slower decay while resting

    new_val = update_boredom(persona_uuid, decay)
    return {"boredom": new_val, "decay": decay, "archetype": archetype}


def apply_activity_boredom(persona_uuid: str, activity_type: str,
                            archetype: Optional[str] = None) -> dict:
    """Apply boredom recovery and exhaustion from a social activity.

    Returns dict with boredom_change, exhaustion_change.
    """
    if not persona_uuid or not activity_type:
        return {"boredom_change": 0.0, "exhaustion_change": 0.0}

    rec_mult = _ARCHETYPE_BOREDOM_REC_MULT.get(archetype, 1.0)
    exh_mult = _ARCH_EXHAUSTION_MULT.get(archetype, 1.0)

    base_recovery = _ACTIVITY_BOREDOM_RECOVERY.get(activity_type, 5)
    base_exhaustion = _ACTIVITY_EXHAUSTION_COST.get(activity_type, 3)

    boredom_change = base_recovery * rec_mult
    exhaustion_change = base_exhaustion * exh_mult

    new_boredom = update_boredom(persona_uuid, boredom_change)
    new_exhaustion = update_exhaustion(persona_uuid, exhaustion_change)

    return {
        "boredom_change": boredom_change,
        "exhaustion_change": exhaustion_change,
        "new_boredom": new_boredom,
        "new_exhaustion": new_exhaustion,
    }


def get_activity_preference(persona_uuid: str, archetype: Optional[str] = None) -> str:
    """Recommend an activity type based on persona state and characteristics.

    Deterministic fallback when AI mode is off.
    """
    profile = get_or_create_profile(persona_uuid)
    boredom = profile.get("boredom", 100.0)
    exhaustion = profile.get("social_exhaustion", 0.0)

    # Crisis mode
    if boredom <= 10.0:
        return _pick_critical_activity(archetype, exhaustion)
    # Urgent
    if boredom <= 25.0:
        return _pick_urgent_activity(archetype, exhaustion)
    # Desired
    if boredom <= 50.0:
        return _pick_leisure_activity(archetype, exhaustion)
    # Optional
    return _pick_passive_activity(archetype)


def _pick_critical_activity(archetype: Optional[str], exhaustion: float) -> str:
    """When social is critical, pick the most effective available activity."""
    if exhaustion > 80:
        return "conversation"
    active = archetype in ("adventurer", "warrior", "vagabond")
    return "hanging_out" if active or exhaustion > 50 else "eating_together"


def _pick_urgent_activity(archetype: Optional[str], exhaustion: float) -> str:
    if exhaustion > 60:
        return "conversation"
    if exhaustion > 40:
        return "hanging_out"
    return random.choice(["eating_together", "playing", "storytelling"])


def _pick_leisure_activity(archetype: Optional[str], exhaustion: float) -> str:
    options = ["conversation", "hanging_out", "eating_together"]
    if exhaustion < 50:
        options.extend(["playing", "storytelling"])
    if exhaustion < 30:
        options.append("dancing")
    return random.choice(options)


def _pick_passive_activity(archetype: Optional[str]) -> str:
    options = ["resting_alone", "resting_company"]
    if archetype in ("warrior", "vagabond"):
        options.append("playing")
    return random.choice(options)


def get_boredom_status(boredom: float) -> str:
    if boredom >= 75:
        return "content"
    if boredom >= 50:
        return "restless"
    if boredom >= 25:
        return "bored"
    if boredom >= 10:
        return "agitated"
    if boredom > 0:
        return "critical"
    return "crisis"

