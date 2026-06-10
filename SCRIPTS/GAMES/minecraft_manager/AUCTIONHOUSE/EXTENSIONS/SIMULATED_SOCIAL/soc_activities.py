"""
soc_activities.py — Social activity definitions and outcome processing.

Defines 12 activity types with their effects on boredom, exhaustion,
skills, and relationships.  Processes activity outcomes atomically.
"""

import random
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
    log_activity, upsert_relationship_detail, get_or_create_profile,
    add_memory_short, add_memory_medium, add_memory_perma
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_boredom import (
    apply_activity_boredom
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_skills import (
    apply_skill_change
)

log = get_logger()

SKILL_EFFECTS = {
    "conversation": {"trading": 0.5},
    "playing": {"crafting": 1.0, "combat": 1.0},
    "hanging_out": {},
    "eating_together": {"farming": 0.5},
    "drinking": {},
    "dancing": {"exploration": 1.0},
    "storytelling": {"exploration": 1.0, "leadership": 1.0},
    "gift_giving": {"trading": 1.0},
    "celebration": {"leadership": 2.0},
    "arguing": {"combat": 1.0, "leadership": 1.0},
}

RELATIONSHIP_DELTAS = {
    "conversation": 1.0, "playing": 3.0, "hanging_out": 2.0,
    "eating_together": 2.0, "drinking": 3.0, "dancing": 4.0,
    "storytelling": 3.0, "gift_giving": 5.0, "celebration": 4.0,
    "arguing": -8.0,
}

VALID_ACTIVITIES = frozenset({
    "conversation", "playing", "hanging_out", "eating_together",
    "drinking", "dancing", "storytelling", "gift_giving",
    "celebration", "arguing", "resting_alone", "resting_company",
})


def process_activity(persona_uuid: str, activity_type: str,
                     partner_uuid: Optional[str] = None,
                     archetype: Optional[str] = None,
                     relationship_strength: float = 50.0) -> dict:
    """Process the full outcome of a social activity.

    Applies boredom recovery, exhaustion, skill changes, relationship changes,
    and memory updates atomically.

    Args:
        persona_uuid: The persona performing the activity
        activity_type: Type of activity
        partner_uuid: The other persona (if applicable)
        archetype: Persona's archetype for modifiers
        relationship_strength: Current relationship strength with partner

    Returns:
        Dict with all effects applied
    """
    if not persona_uuid or not activity_type:
        return {"error": "missing required fields"}

    if activity_type not in VALID_ACTIVITIES:
        return {"error": f"invalid activity: {activity_type}"}

    # 1. Apply boredom and exhaustion changes
    boredom_result = apply_activity_boredom(persona_uuid, activity_type, archetype)

    # 2. Relationship change
    base_rel_delta = RELATIONSHIP_DELTAS.get(activity_type, 0.0)
    if base_rel_delta < 0:
        # Negative interactions are amplified by low relationship
        rel_delta = base_rel_delta * (2.0 - relationship_strength / 100.0)
    else:
        rel_delta = base_rel_delta * (0.5 + relationship_strength / 200.0)

    if partner_uuid:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
            upsert_relationship
        )
        upsert_relationship(persona_uuid, partner_uuid,
                             strength_delta=rel_delta,
                             intimacy_delta=abs(rel_delta) * 0.1,
                             trust_delta=rel_delta * 0.3)
        upsert_relationship_detail(persona_uuid, partner_uuid,
                                    activity_type)

    # 3. Skill changes
    skill_deltas = SKILL_EFFECTS.get(activity_type, {})
    applied_skills = {}
    for skill_name, delta in skill_deltas.items():
        try:
            result = apply_skill_change(persona_uuid, skill_name, delta,
                                         f"social_{activity_type}",
                                         archetype=archetype)
            applied_skills[skill_name] = result.get("delta_applied", 0)
        except Exception as e:
            log.warn("soc_activities", f"Skill change failed: {e}")

    # 4. Memory recording
    narrative = _generate_activity_narrative(activity_type, partner_uuid)
    add_memory_short(persona_uuid, activity_type, partner_uuid,
                      activity_type,
                      f"Engaged in {activity_type}.",
                      importance=max(1, int(abs(rel_delta))))

    # 5. Log the activity
    log_activity(persona_uuid, activity_type, partner_uuid,
                 boredom_result.get("boredom_change", 0),
                 boredom_result.get("exhaustion_change", 0),
                 rel_delta, applied_skills, narrative)

    return {
        "activity": activity_type,
        "partner": partner_uuid,
        "boredom_change": boredom_result.get("boredom_change", 0),
        "exhaustion_change": boredom_result.get("exhaustion_change", 0),
        "relationship_delta": rel_delta,
        "skill_deltas": applied_skills,
        "narrative": narrative,
    }


def _generate_activity_narrative(activity_type: str,
                                  partner_uuid: Optional[str]) -> str:
    """Generate a simple narrative for an activity."""
    partner = partner_uuid[:8] if partner_uuid else "themselves"
    narratives = {
        "conversation": f"Had a chat with {partner}.",
        "playing": f"Played games with {partner}.",
        "hanging_out": f"Spent time with {partner}.",
        "eating_together": f"Shared a meal with {partner}.",
        "drinking": f"Drank with {partner}.",
        "dancing": f"Danced with {partner}.",
        "storytelling": f"Shared stories with {partner}.",
        "gift_giving": f"Gave a gift to {partner}.",
        "celebration": f"Celebrated with {partner}.",
        "arguing": f"Argued with {partner}.",
        "resting_alone": "Rested quietly.",
        "resting_company": f"Rested in the company of {partner}.",
    }
    return narratives.get(activity_type, "Did something.")
