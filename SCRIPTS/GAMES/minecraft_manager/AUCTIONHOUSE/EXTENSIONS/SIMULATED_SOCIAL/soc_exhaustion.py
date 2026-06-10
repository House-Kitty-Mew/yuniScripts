"""
soc_exhaustion.py — Social exhaustion and resting system.

Tracks social exhaustion (0-100) and manages rest states:
  - Active: normal operation
  - Resting: reduced exhaustion recovery, slight boredom increase
  - Sleeping: major exhaustion recovery, boredom decrease
  - Burnout: forced 3-tick rest period
"""

import random
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
    get_or_create_profile, update_exhaustion, update_resting_state, update_boredom,
    get_recent_memories
)

log = get_logger()

_ARCH_EXHAUSTION_MULT = {
    "adventurer": 1.2, "merchant": 2.0, "builder": 0.7,
    "miner": 1.0, "farmer": 0.6, "warrior": 0.8,
    "mage": 1.5, "vagabond": 0.5,
}

_RECOVERY_RATES = {
    "active": -0.2,    # Slow natural recovery
    "resting": -1.0,   # Moderate recovery
    "sleeping": -3.0,  # Fast recovery
    "burnout": -4.0,   # Burnout recovery
}


def process_exhaustion_tick(persona_uuid: str, archetype: Optional[str] = None) -> dict:
    """Process one tick of exhaustion for a persona.

    Manages rest state transitions and exhaustion recovery.
    """
    if not persona_uuid:
        return {"exhaustion": 0.0, "state": "active"}

    profile = get_or_create_profile(persona_uuid)
    exhaustion = profile.get("social_exhaustion", 0.0)
    state = profile.get("resting_state", "active")
    rest_ticks = profile.get("rest_ticks", 0)
    burnout = profile.get("burnout_ticks", 0)

    # Burnout recovery
    if burnout > 0:
        update_resting_state(persona_uuid, "burnout", burnout - 1)
        new_exhaustion = update_exhaustion(persona_uuid, _RECOVERY_RATES["burnout"])
        return {"exhaustion": new_exhaustion, "state": "burnout", "rest_ticks": burnout - 1}

    # Rest state progression
    if exhaustion >= 100:
        # Enter burnout
        update_resting_state(persona_uuid, "burnout", 3)
        log.info("soc_exhaustion", f"{persona_uuid[:8]} entered burnout")
        return {"exhaustion": 100.0, "state": "burnout", "rest_ticks": 3}

    # Determine if persona should rest
    can_rest_activities = get_recent_memories(persona_uuid, 1)
    exhaustion_high = exhaustion >= 70

    if state == "active" and exhaustion_high:
        # Start resting
        state = "resting"
        rest_ticks = 0
        update_resting_state(persona_uuid, "resting", 0)
    elif state == "resting":
        rest_ticks += 1
        if rest_ticks >= 8:  # Convert to sleep after 8 rest ticks
            state = "sleeping"
            update_resting_state(persona_uuid, "sleeping", rest_ticks)
        else:
            update_resting_state(persona_uuid, "resting", rest_ticks)
    elif state == "sleeping":
        rest_ticks += 1
        if rest_ticks >= 12:  # Wake after 12 sleep ticks
            state = "active"
            rest_ticks = 0
        update_resting_state(persona_uuid, "sleeping" if state == "sleeping" else "active", rest_ticks)

    # Apply recovery
    recovery = _RECOVERY_RATES.get(state, -0.2)
    new_exhaustion = update_exhaustion(persona_uuid, recovery)

    # Boredom changes during rest/sleep
    if state in ("resting", "sleeping"):
        boredom_delta = -1.0 if state == "sleeping" else 0.5
        update_boredom(persona_uuid, boredom_delta)

    return {"exhaustion": new_exhaustion, "state": state, "rest_ticks": rest_ticks}

