"""
soc_memory.py — Three-tier memory system.

Manages short-term, medium-term, and permanent memories for personas.
- Short-term: context clues, FIFO at 50, auto-pruned
- Medium-term: goals/plans, pruned by importance+age at 100
- Permanent: life-changing events, 20 max, never pruned
"""

from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_SOCIAL.soc_database import (
    add_memory_short, add_memory_medium, add_memory_perma,
    _boost_memory_priority, get_recent_memories
)

log = get_logger()

# Events worthy of permanent memory
_PERMA_WORTHY_EVENTS = frozenset({
    "death", "marriage", "divorce", "conflict_victory", "conflict_defeat",
    "suicide", "community_death", "skill_mastery", "major_trade",
    "betrayal", "reconciliation", "first_meeting", "child_birth",
})


def record_interaction_memory(persona_uuid: str, target_uuid: str,
                               activity_type: str,
                               outcome: str = "neutral",
                               emotional_weight: int = 3,
                               location: Optional[str] = None) -> None:
    """Record an interaction across appropriate memory tiers.

    Determines which memory tier to use based on emotional weight and
    activity type.
    """
    if not persona_uuid or not target_uuid:
        return

    importance = max(1, min(10, emotional_weight))
    context = f"{activity_type} with {target_uuid[:8]}: {outcome}"
    detailed = (f"Had a {outcome} {activity_type} interaction "
                f"with {target_uuid[:8]}. "
                f"Location: {location or 'unknown'}.")

    # Always record in short-term
    add_memory_short(persona_uuid, "interaction", target_uuid,
                      activity_type, context, importance)

    # Medium-term for moderate importance events
    if importance >= 4:
        add_memory_medium(persona_uuid, "interaction", target_uuid,
                           activity_type, detailed, location,
                           {"outcome": outcome}, importance)

    # Permanent for life-changing events
    if activity_type in _PERMA_WORTHY_EVENTS and importance >= 7:
        add_memory_perma(persona_uuid, activity_type, target_uuid,
                          activity_type,
                          (f"A life-changing {activity_type} with "
                           f"{target_uuid[:8]}: {detailed}"),
                          emotional_impact=emotional_weight)


def get_memory_context(persona_uuid: str, target_uuid: str) -> dict:
    """Get combined memory context about a target persona.

    Returns summary of what this persona remembers about the target.
    """
    if not persona_uuid or not target_uuid:
        return {"total_memories": 0, "relationship_trend": "neutral",
                "recent_interactions": []}

    memories = get_recent_memories(persona_uuid, limit=5, include_perma=True)
    relevant = [m for m in memories if m.get("target_uuid") == target_uuid]

    # Determine relationship trend from memories
    pos_count = sum(1 for m in relevant
                    if m.get("memory_type") in
                    ("friendship", "romance", "celebration"))
    neg_count = sum(1 for m in relevant
                    if m.get("memory_type") in
                    ("conflict", "betrayal", "enmity"))
    trend = "positive" if pos_count > neg_count else \
            "negative" if neg_count > pos_count else "neutral"

    return {
        "total_memories": len(relevant),
        "relationship_trend": trend,
        "recent_interactions": relevant[:3],
    }


def prune_medium_memories(persona_uuid: Optional[str] = None) -> int:
    """Force prune medium-term memories.

    Args:
        persona_uuid: If specified, prune only for this persona.
                      If None, prune globally.

    Returns:
        Number of pruned memories
    """
    from AUCTIONHOUSE.ah_database import get_db
    db = get_db()
    pruned = 0

    if persona_uuid:
        count = db.fetch_one(
            "SELECT COUNT(*) as c FROM ext_soc_memories_medium "
            "WHERE persona_uuid = ?", (persona_uuid,))
        if count and count["c"] > 100:
            excess = count["c"] - 95
            db.execute("""
                DELETE FROM ext_soc_memories_medium
                WHERE id IN (
                    SELECT id FROM ext_soc_memories_medium
                    WHERE persona_uuid = ?
                    AND importance < 4
                    AND created_at < datetime('now', '-48 hours')
                    ORDER BY created_at ASC
                    LIMIT ?
                )
            """, (persona_uuid, excess))
            pruned = excess
    else:
        # Global prune — all personas
        db.execute("""
            DELETE FROM ext_soc_memories_medium
            WHERE id IN (
                SELECT id FROM ext_soc_memories_medium
                WHERE importance < 4
                AND created_at < datetime('now', '-48 hours')
            )
        """)
        pruned = db._get_conn().total_changes if hasattr(db, '_get_conn') else 0

    return pruned

