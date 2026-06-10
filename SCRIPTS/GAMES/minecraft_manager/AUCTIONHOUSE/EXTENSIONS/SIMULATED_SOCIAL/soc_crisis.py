"""
soc_crisis.py — Crisis management for severe social deprivation.

When boredom reaches 0, personas risk self-harm and suicide:
  - Next action MUST be social or risk check
  - Base 1% suicide chance + 0.10% per tick at 0
  - Behavior modifier (vagabond 1.5x, farmer 0.6x)
  - -2% per close relationship (protective effect)
  - On death: persona deactivated, community mourns
"""

import random, math
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
    get_or_create_profile, update_boredom, add_memory_perma
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_behaviors import (
    get_behavior
)

log = get_logger()

# Behavior suicide modifiers
_SUICIDE_BEHAVIOR_MOD = {
    "adventurer": 0.8, "warrior": 0.7, "mage": 1.3,
    "merchant": 1.1, "builder": 1.2, "miner": 1.0,
    "farmer": 0.6, "vagabond": 1.5,
}

_BASE_SUICIDE_CHANCE = 0.01
_TICK_INCREMENT = 0.001
_PROTECTION_PER_CLOSE = 0.02


def check_crisis(persona_uuid: str, planned_activity: str,
                  archetype: Optional[str] = None,
                  close_relationships: int = 0) -> dict:
    """Check if a persona enters crisis mode.

    Called when boredom = 0 and planned activity isn't social.

    Args:
        persona_uuid: The persona at risk
        planned_activity: What they planned to do
        archetype: Archetype for behavior modifiers
        close_relationships: Count of relationships > 60 strength

    Returns:
        Dict with crisis_result, suicide_attempted, death, reasoning
    """
    if not persona_uuid:
        return {"crisis": False, "reasoning": "no persona"}

    social_activities = {"conversation", "playing", "hanging_out",
                          "eating_together", "drinking", "dancing",
                          "storytelling", "gift_giving", "celebration"}

    if planned_activity in social_activities:
        return {"crisis": False, "suicide_attempted": False,
                "death": False, "reasoning": "social activity planned"}

    profile = get_or_create_profile(persona_uuid)
    boredom = profile.get("boredom", 100.0)
    crisis_ticks = profile.get("consecutive_crisis_ticks", 0)

    if boredom > 0:
        return {"crisis": False, "suicide_attempted": False,
                "death": False, "reasoning": "boredom not at 0"}

    # Increment crisis ticks
    db_updated = False
    crisis_ticks += 1
    try:
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        db.execute("""
            UPDATE ext_soc_profiles
            SET consecutive_crisis_ticks = ?, crisis_flag = 1
            WHERE persona_uuid = ?
        """, (crisis_ticks, persona_uuid))
        db_updated = True
    except Exception:
        pass

    # Calculate suicide probability
    behavior_mod = _SUICIDE_BEHAVIOR_MOD.get(archetype, 1.0)
    protection = _PROTECTION_PER_CLOSE * close_relationships
    chance = (_BASE_SUICIDE_CHANCE + _TICK_INCREMENT * crisis_ticks) * behavior_mod - protection
    chance = max(0.001, min(0.5, chance))  # Cap between 0.1% and 50%

    roll = random.random()
    suicide_attempted = roll < chance

    if not suicide_attempted:
        return {
            "crisis": True,
            "suicide_attempted": False,
            "death": False,
            "chance": chance,
            "roll": roll,
            "crisis_ticks": crisis_ticks,
            "reasoning": f"survived crisis (chance {chance:.4f}, rolled {roll:.4f})"
        }

    # Suicide occurs. Deactivate persona.
    death = _perform_suicide(persona_uuid, archetype, crisis_ticks)

    # Reset crisis ticks on surviving
    if not death and db_updated:
        db.execute("""
            UPDATE ext_soc_profiles
            SET consecutive_crisis_ticks = 0, crisis_flag = 0
            WHERE persona_uuid = ?
        """, (persona_uuid,))

    return {
        "crisis": True,
        "suicide_attempted": True,
        "death": death,
        "chance": chance,
        "roll": roll,
        "crisis_ticks": crisis_ticks,
        "reasoning": f"suicide {'SUCCEEDED' if death else 'attempted but survived'} "
                     f"(chance {chance:.4f}, rolled {roll:.4f})"
    }


def _perform_suicide(persona_uuid: str, archetype: Optional[str],
                      crisis_ticks: int) -> bool:
    """Execute suicide: deactivate persona, create community memories."""
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
            get_active_personas, get_db as sp_get_db
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import (
            deactivate_persona
        )

        # Deactivate the persona
        try:
            deactivate_persona(persona_uuid)
        except Exception:
            pass

        # Create permanent memory for the deceased (if possible)
        add_memory_perma(persona_uuid, "suicide", None, None,
                          f"Succumbed to social isolation after {crisis_ticks} ticks of crisis.",
                          emotional_impact=10)

        # Notify community
        try:
            community = get_active_personas()
            if community:
                for p in community:
                    puid = p.get("persona_uuid", "")
                    if puid and puid != persona_uuid:
                        try:
                            add_memory_perma(
                                puid, "community_death", persona_uuid, None,
                                f"Witnessed the death of a community member from isolation.",
                                emotional_impact=8)
                        except Exception:
                            pass
        except Exception:
            pass

        log.warn("soc_crisis",
                 f"Persona {persona_uuid[:8]} committed suicide "
                 f"(crisis {crisis_ticks} ticks)")
        return True

    except Exception as e:
        log.error("soc_crisis", f"Suicide processing error: {e}")
        return False
