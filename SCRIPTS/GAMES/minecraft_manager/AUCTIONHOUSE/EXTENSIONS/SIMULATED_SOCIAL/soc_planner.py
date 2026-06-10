"""
soc_planner.py — Activity planner for personas.

Determines what each persona should do each tick based on:
  - Boredom level (crisis/urgent/desired/optional)
  - Social exhaustion (too tired = passive activities)
  - Available company (personas in same area)
  - Existing relationships (prefer close partners)
  - Archetype preferences
  - Crisis management (suicide check if boredom=0 and non-social planned)
"""

import random
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
    get_or_create_profile, get_recent_memories
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import (
    get_activity_preference, get_boredom_status, _ACTIVITY_BOREDOM_RECOVERY
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_crisis import check_crisis

log = get_logger()


def plan_activity(persona_uuid: str, archetype: Optional[str] = None,
                  available_partners: Optional[list[dict]] = None,
                  use_thinking_mode: bool = True) -> dict:
    """Plan the next social activity for a persona.

    Args:
        persona_uuid: The persona to plan for
        archetype: Archetype for behavior modifiers
        available_partners: List of persona dicts in the same area
        use_thinking_mode: Whether to use extended reasoning

    Returns:
        Dict with activity_type, partner_uuid, reasoning
    """
    if not persona_uuid:
        return {"activity_type": "resting_alone", "reasoning": "no persona"}

    profile = get_or_create_profile(persona_uuid)
    boredom = profile.get("boredom", 100.0)
    exhaustion = profile.get("social_exhaustion", 0.0)
    status = get_boredom_status(boredom)

    # Burnout check
    if profile.get("burnout_ticks", 0) > 0:
        return {"activity_type": "resting_alone", "partner_uuid": None,
                "reasoning": "burnout rest"}

    # Select partner if available
    partner = _select_partner(persona_uuid, available_partners or []) if \
              available_partners else None

    # Pick activity based on boredom level
    base_activity = get_activity_preference(persona_uuid, archetype)

    # Crisis mode — force social activity or check suicide
    if boredom <= 0:
        social_activities = {"conversation", "playing", "hanging_out",
                              "eating_together", "drinking", "dancing",
                              "storytelling", "gift_giving", "celebration"}

        if base_activity not in social_activities or not partner:
            # Try to find a social activity
            social_options = [a for a in social_activities
                              if a in _ACTIVITY_BOREDOM_RECOVERY]
            if social_options and partner:
                base_activity = random.choice(social_options)
            elif partner:
                base_activity = "conversation"
            else:
                # No partner available — suicide check
                crisis = check_crisis(persona_uuid, base_activity,
                                       archetype, 0)
                if crisis.get("death"):
                    return {"activity_type": "suicide", "partner_uuid": None,
                            "reasoning": crisis.get("reasoning", "suicide")}
                if crisis.get("crisis"):
                    # Forced to wait — can't do anything without company
                    return {"activity_type": "waiting", "partner_uuid": None,
                            "reasoning": crisis.get("reasoning",
                                                     "waiting for social contact")}

    # Adjust activity based on exhaustion
    if exhaustion > 70 and base_activity not in ("resting_alone",
                                                   "resting_company"):
        base_activity = random.choice(["hanging_out", "conversation"]
                                       if partner else ["resting_alone"])
    elif exhaustion > 50 and base_activity in ("dancing", "celebration"):
        base_activity = random.choice(["eating_together", "hanging_out",
                                        "conversation"])

    return {
        "activity_type": base_activity,
        "partner_uuid": partner.get("persona_uuid") if partner else None,
        "reasoning": f"boredom={boredom:.0f}, exhaustion={exhaustion:.0f}, "
                     f"status={status}, partner={'yes' if partner else 'no'}",
    }


def _select_partner(persona_uuid: str,
                     available: list[dict]) -> Optional[dict]:
    """Select the best partner from available personas.

    Prefers close relationships, then friends, then anyone available.
    """
    if not persona_uuid or not available:
        return None

    # Filter out self
    others = [p for p in available
              if p.get("persona_uuid") != persona_uuid]
    if not others:
        return None

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
        get_relationship_direction
    )

    # Score each candidate
    scored = []
    for p in others:
        puid = p.get("persona_uuid", "")
        rel = get_relationship_direction(persona_uuid, puid)
        score = 0

        if rel == "romance":
            score = 100
        elif rel == "friendship":
            score = 50
        elif rel == "rivalry":
            score = 20
        elif rel == "enmity":
            score = 5
        else:
            score = 25  # Neutral — still social

        # Add random variance
        score += random.randint(-10, 10)
        scored.append((p, max(1, score)))

    # Pick weighted random
    if not scored:
        return None
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0]

