"""
soc_relationships.py — Deep relationship tracking.

Extends SIMULATED_RELATIONSHIPS with:
  - Detailed relationship subtypes (friend, close_friend, best_friend, etc.)
  - Marriage and divorce tracking
  - Time-based relationship decay
  - Shared activity counting
  - Relationship memory effects
"""

import random, math
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
    get_relationship_detail, upsert_relationship_detail, set_married,
    check_divorce, add_memory_perma, add_memory_medium
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
    get_relationship, upsert_relationship, list_relationships,
    get_relationship_direction
)

log = get_logger()


def process_relationship_decay(persona_uuid: str) -> list[dict]:
    """Process time-based decay for all relationships of a persona.

    Returns list of (target, delta) for relationships that decayed.
    """
    if not persona_uuid:
        return []

    decays = []
    relationships = list_relationships(persona_uuid)
    for rel in relationships:
        other = (rel["persona_uuid_a"] if rel["persona_uuid_b"] == persona_uuid
                 else rel["persona_uuid_b"])
        detail = get_relationship_detail(persona_uuid, other)

        if rel["strength"] <= 0:
            continue

        # Base decay
        decay = 0.5

        # Close relationships decay slower
        if detail:
            st = detail.get("relationship_type", "")
            if st in ("married", "best_friend"):
                decay *= 0.1
            elif st in ("close_friend", "partner"):
                decay *= 0.3
            elif st in ("friend", "romantic_interest"):
                decay *= 0.5

        # Marriage protects from decay
        if detail and detail.get("marriage_flag"):
            decay *= 0.1

        # Apply decay
        upsert_relationship(persona_uuid, other, strength_delta=-decay)
        decays.append({"target": other, "decay": decay})

    return decays


def check_marriage_potential(persona_a: str, persona_b: str) -> dict:
    """Check if two personas should get married.

    Criteria:
      - Relationship strength > 85
      - Compatible archetypes
      - Shared history (> 5 interactions)
      - Small random chance per tick
    """
    if not persona_a or not persona_b:
        return {"potential": False}

    rel = get_relationship(persona_a, persona_b)
    if not rel or rel.get("strength", 0) < 85:
        return {"potential": False}

    detail = get_relationship_detail(persona_a, persona_b)
    if detail and detail.get("marriage_flag"):
        return {"potential": False, "already_married": True}

    if detail and detail.get("shared_activity_count", 0) < 5:
        return {"potential": False, "reason": "not enough shared history"}

    # Random chance
    if random.random() > 0.05:
        return {"potential": False, "reason": "not yet ready"}

    success = set_married(persona_a, persona_b)
    return {"potential": True, "married": success}


def check_divorce_potential(persona_a: str, persona_b: str) -> dict:
    """Check if a married couple should divorce.

    Triggered when relationship strength drops below 30.
    """
    if not persona_a or not persona_b:
        return {"divorced": False}

    detail = get_relationship_detail(persona_a, persona_b)
    if not detail or not detail.get("marriage_flag"):
        return {"divorced": False}

    divorced = check_divorce(persona_a, persona_b)
    return {"divorced": divorced}


def get_relationship_summary(persona_uuid: str) -> dict:
    """Get a comprehensive summary of a persona's social relationships.

    Returns counts of friends, close friends, enemies, romantic partners, etc.
    """
    if not persona_uuid:
        return {}

    relationships = list_relationships(persona_uuid)
    summary = {
        "total": len(relationships),
        "friends": 0,
        "close_friends": 0,
        "best_friends": 0,
        "rivals": 0,
        "enemies": 0,
        "romantic_interests": 0,
        "married": False,
        "partner_name": None,
    }

    for rel in relationships:
        other = (rel["persona_uuid_a"] if rel["persona_uuid_b"] == persona_uuid
                 else rel["persona_uuid_b"])
        detail = get_relationship_detail(persona_uuid, other)
        st = detail.get("relationship_type", "") if detail else ""
        rtype = rel.get("relationship_type", "")

        if st == "married":
            summary["married"] = True
            summary["partner_name"] = other
        if rtype in ("rivalry",) or st == "rival":
            summary["rivals"] += 1
        if rtype == "enmity" or st == "enemy":
            summary["enemies"] += 1
        if st == "romantic_interest":
            summary["romantic_interests"] += 1
        if st == "close_friend":
            summary["close_friends"] += 1
        if st == "best_friend":
            summary["best_friends"] += 1
        if st == "friend":
            summary["friends"] += 1

    return summary

