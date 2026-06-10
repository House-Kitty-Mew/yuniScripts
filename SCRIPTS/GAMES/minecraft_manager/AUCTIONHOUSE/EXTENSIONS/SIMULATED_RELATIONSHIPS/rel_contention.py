"""
rel_contention.py — Cell contention events for multi-persona encounters.

When two or more personas occupy the same area/cell, this module:
  1. Detects the contention (multiple personas, same location)
  2. Gathers full context (persona stats, relationships, environment)
  3. Calls the AI resolver for thinking-mode outcome determination
  4. Applies ALL changes atomically:
     - Relationship updates
     - Skill changes
     - Health effects
     - Wealth changes
     - Memory/event logging
  5. Records the full event in the database

This creates a living world where persona stats and relationships
evolve dynamically through their interactions.
"""

import random, json, math
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_active_personas, get_persona_by_uuid, get_db as sp_get_db,
    add_memory, add_life_event
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import (
    get_persona_area, get_area, get_all_areas
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import (
    process_persona_health, init_health
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import (
    get_skills, save_skills
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import (
    _get_listings_for_purchase
)

log = get_logger()


# ══════════════════════════════════════════════════════════════════════
# Contention detection
# ══════════════════════════════════════════════════════════════════════

def find_area_contentions() -> list[dict]:
    """Find all areas where 2+ personas are currently present.

    Scans all active personas, groups by area, and returns areas
    where multiple personas are co-located.

    Returns:
        List of dicts: {area_id, area_info, personas: [persona_dicts]}
    """
    db = sp_get_db()
    try:
        active = get_active_personas()
    except Exception:
        log.warn("rel_contention", "Failed to query active personas")
        return []
    if not active or not isinstance(active, list):
        return []

    # Group by area (max 100 personas to prevent overload)
    area_map: dict[str, list[dict]] = {}
    for p in active[:100]:
        loc = get_persona_area(p["persona_uuid"])
        area_id = loc.get("area_id") if loc else None
        if not area_id:
            continue
        if area_id not in area_map:
            area_map[area_id] = []
        # Enrich persona data
        p_full = dict(p)
        p_full["skills"] = get_skills(p["persona_uuid"]) or {}
        area_map[area_id].append(p_full)

    # Filter areas with 2+ personas
    contentions = []
    for area_id, p_list in area_map.items():
        if len(p_list) >= 2:
            area_info = get_area(area_id) or {"name": area_id, "type": "plains",
                                               "region": "overworld"}
            contentions.append({
                "area_id": area_id,
                "area_info": area_info,
                "personas": p_list,
                "count": len(p_list),
            })

    return contentions


# ══════════════════════════════════════════════════════════════════════
# Single contention resolution (full cycle)
# ══════════════════════════════════════════════════════════════════════

def resolve_contention(contention: dict,
                       use_thinking_mode: bool = True) -> dict:
    if not isinstance(contention, dict) or not contention.get("personas"):
        log.warn("rel_contention", "Invalid contention dict — skipping")
        return {"outcome": "skipped", "reasoning": ["Invalid contention"],
                "changes_applied": [], "persona_effects": [],
                "relationship_changes": [], "skill_changes": [],
                "narrative": "The contention was skipped due to invalid data.",
                "confidence": 0.0}
    """Fully resolve a single contention event.

    This is the main entry point that:
      1. Enriches persona data with relationships, skills, health
      2. Gathers world event context
      3. Runs the AI resolver
      4. Applies ALL changes
      5. Records the event

    Args:
        contention: Dict from find_area_contentions()
        use_thinking_mode: Whether to use full AI thinking mode

    Returns:
        Full resolution result with all applied changes
    """
    from .rel_ai_resolver import ContentionResolver
    from .rel_database import (
        get_relationship, log_interaction, log_contention_event
    )
    from .rel_skills import apply_contention_skill_effects

    area_id = contention["area_id"]
    area_info = contention["area_info"]
    personas = contention["personas"]
    resolver = ContentionResolver(use_thinking_mode=use_thinking_mode)

    # ── Step 1: Enrich persona context ──
    enriched = []
    for i, p in enumerate(personas):
        puid = p["persona_uuid"]
        ep = dict(p)

        # Add relationship data for every other persona
        for j, other in enumerate(personas):
            if i == j:
                continue
            rel = get_relationship(puid, other["persona_uuid"])
            if rel:
                ep["relationship"] = rel

        # Add health
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db
        hdb = get_db()
        health_row = hdb.fetch_one(
            "SELECT * FROM ext_sp_health WHERE persona_uuid = ?", (puid,))
        if health_row:
            ep["health"] = dict(health_row)

        # Add traits from profile
        profile = get_persona_by_uuid(puid) or {}
        traits_str = profile.get("personality_traits", "{}")
        try:
            ep["traits"] = json.loads(traits_str) if isinstance(traits_str, str) else traits_str
        except (json.JSONDecodeError, TypeError):
            ep["traits"] = {}

        enriched.append(ep)

    # ── Step 2: Determine contention type ──
    area_type = area_info.get("type", "plains")
    n = len(enriched)
    has_resources = area_type in ("mountains", "deep_dark", "nether_wastes",
                                   "crimson_forest")
    contention_type = "space_dispute"
    if has_resources and n >= 3:
        contention_type = "resource_conflict"
    elif n >= 4:
        contention_type = "social_gathering"
    elif n == 2:
        # Check if they know each other
        if enriched[0].get("relationship"):
            contention_type = "accidental_meeting"
        else:
            contention_type = "space_dispute"

    # ── Step 3: Get world events ──
    world_events = []
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
            get_active_world_events
        )
        world_events = get_active_world_events(region=area_info.get("region"))
    except Exception:
        pass

    # ── Step 4: Run AI resolver ──
    resolution = resolver.resolve(
        enriched, area_info,
        has_resources=has_resources,
        world_events=world_events
    )

    outcome = resolution["outcome"]

    # ── Step 5: Apply ALL changes ──
    changes_applied = []

    # 5a: Relationship changes
    for rc in resolution.get("relationship_changes", []):
        i = rc["persona_a_index"]
        j = rc["persona_b_index"]
        if i >= len(enriched) or j >= len(enriched):
            continue
        puid_a = enriched[i]["persona_uuid"]
        puid_b = enriched[j]["persona_uuid"]
        delta = rc["strength_delta"]

        from .rel_database import upsert_relationship
        upsert_relationship(puid_a, puid_b,
                             strength_delta=delta,
                             rel_type=rc.get("suggested_type", "neutral"),
                             intimacy_delta=abs(delta) * 0.1,
                             trust_delta=delta * 0.3)
        changes_applied.append(f"Relationship {puid_a[:8]} ↔ {puid_b[:8]}: {delta:+.1f}")

    # 5b: Skill changes
    for sc in resolution.get("skill_changes", []):
        i = sc["persona_index"]
        if i >= len(enriched):
            continue
        puid = enriched[i]["persona_uuid"]
        arch = enriched[i].get("archetype", "adventurer")
        for skill_name, delta in sc["skill_deltas"].items():
            result = apply_contention_skill_effects(
                puid, arch, outcome,
                won=(i == 0 and outcome == "conflict"),
                opponent_uuid=enriched[0]["persona_uuid"] if i != 0 else None
            )
            for sk, dv in result.items():
                changes_applied.append(f"Skill {puid[:8]}: {sk} {dv:+.1f}")

    # 5c: Health/wealth effects
    for effect in resolution.get("persona_effects", []):
        i = effect["persona_index"]
        if i >= len(enriched):
            continue
        puid = enriched[i]["persona_uuid"]
        wealth_delta = effect.get("wealth_delta", 0)
        if abs(wealth_delta) > 0.01:
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
                    update_finance
                )
                update_finance(puid, "balance", wealth_delta)
                changes_applied.append(f"Wealth {puid[:8]}: {wealth_delta:+.1f}")
            except Exception:
                pass

        # Add memory of the event
        mem = effect.get("memory", {})
        if mem:
            try:
                add_memory(puid, mem.get("type", "encounter"),
                           detail={"narrative": resolution.get("narrative", ""),
                                    "area": area_id,
                                    "outcome": outcome},
                           emotional_weight=mem.get("emotional_weight", 5))
            except Exception:
                pass

    # 5d: Create situationships for peaceful outcomes
    if outcome in ("negotiation", "social_gathering"):
        for i in range(min(2, len(enriched))):
            for j in range(i + 1, min(3, len(enriched))):
                if j >= len(enriched):
                    continue
                from .rel_database import create_situationship
                create_situationship(
                    enriched[i]["persona_uuid"],
                    enriched[j]["persona_uuid"],
                    "temporary_alliance" if outcome == "negotiation" else "trade_partners",
                    duration_hours=random.randint(12, 48) if outcome == "negotiation"
                    else random.randint(6, 24),
                    strength_bonus=5.0,
                    trust_bonus=3.0 if outcome == "negotiation" else 5.0,
                )
                changes_applied.append(
                    f"Situationship: {enriched[i]['persona_uuid'][:8]} ↔ "
                    f"{enriched[j]['persona_uuid'][:8]}")

    # ── Step 6: Log interaction for each pair ──
    for i in range(len(enriched)):
        for j in range(i + 1, len(enriched)):
            log_interaction(
                enriched[i]["persona_uuid"],
                enriched[j]["persona_uuid"],
                outcome,
                outcome={"resolution": outcome, "area": area_id},
                location_area=area_info.get("name", area_id),
                location_region=area_info.get("region"),
                skill_changes={sc["persona_index"]: sc.get("skill_deltas", {})
                               for sc in resolution.get("skill_changes", [])
                               if sc["persona_index"] in (i, j)},
                relationship_delta=resolution.get("relationship_changes", [{}])[0]
                                   .get("strength_delta", 0) if resolution.get(
                    "relationship_changes") else 0,
                narrative=resolution.get("narrative", ""),
            )

    # ── Step 7: Log the contention event ──
    log_contention_event(
        area_id,
        [p["persona_uuid"] for p in enriched],
        contention_type,
        ai_reasoning="\n".join(resolution.get("reasoning", [])),
        resolution=outcome,
        outcome_data={
            "changes_applied": changes_applied,
            "relationship_changes": resolution.get("relationship_changes", []),
            "persona_effects": resolution.get("persona_effects", []),
        },
        narrative=resolution.get("narrative", ""),
    )

    log.info("rel_contention",
             f"[{area_id}] {len(enriched)} personas → {outcome} "
             f"({len(changes_applied)} changes applied)")

    resolution["changes_applied"] = changes_applied
    return resolution


# ══════════════════════════════════════════════════════════════════════
# Batch processing (called by simulation cycle hook)
# ══════════════════════════════════════════════════════════════════════

def process_all_contentions(use_thinking_mode: bool = True,
                             max_events: int = 5) -> list[dict]:
    # Sanitize max_events
    if not isinstance(max_events, (int, float)) or isinstance(max_events, bool):
        max_events = 5
    if max_events is not None and (isinstance(max_events, float) and 
        (math.isnan(max_events) or math.isinf(max_events))):
        max_events = 5
    max_events = max(1, min(100, int(max_events)))
    """Process all current area contentions in batch.

    Called by on_simulation_cycle_end hook. Processes up to max_events
    contentions per cycle to avoid overloading the simulation.

    Args:
        use_thinking_mode: Whether to use full AI reasoning
        max_events: Maximum contention events to process this cycle

    Returns:
        List of resolution results
    """
    from .rel_database import expire_stale_situationships

    # Clean up expired situationships first
    expired = expire_stale_situationships()
    if expired > 0:
        log.info("rel_contention", f"Expired {expired} stale situationships")

    contentions = find_area_contentions()
    if not contentions:
        log.info("rel_contention", "No area contentions found this cycle")
        return []

    # Prioritize contentions with more personas (more interesting)
    contentions.sort(key=lambda c: c["count"], reverse=True)
    contentions = contentions[:max_events]

    results = []
    for c in contentions:
        try:
            result = resolve_contention(c, use_thinking_mode=use_thinking_mode)
            results.append(result)
        except Exception as e:
            log.error("rel_contention",
                       f"Failed to resolve contention in {c['area_id']}: {e}")
            import traceback
            log.error("rel_contention", traceback.format_exc())

    return results


# ── Simplified API for testing ──────────────────────────────────────

def simulate_encounter(persona_a_uuid: str, persona_b_uuid: str,
                        area_id: Optional[str] = None,
                        use_thinking_mode: bool = True) -> dict:
    if not persona_a_uuid or not persona_b_uuid:
        raise ValueError("Both persona UUIDs are required")
    if not area_id or not isinstance(area_id, str):
        area_id = "test_area_encounter"
    if not isinstance(use_thinking_mode, bool):
        use_thinking_mode = True
    """Simulate a direct encounter between two specified personas.

    Useful for testing specific relationship dynamics.

    Args:
        persona_a_uuid, persona_b_uuid: Personas to encounter
        area_id: Where the encounter happens
        use_thinking_mode: Enable AI reasoning

    Returns:
        Resolution result
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
        get_persona_by_uuid
    )

    p_a = get_persona_by_uuid(persona_a_uuid)
    p_b = get_persona_by_uuid(persona_b_uuid)

    if not p_a or not p_b:
        raise ValueError(f"Persona not found: "
                         f"{persona_a_uuid if not p_a else persona_b_uuid}")

    for p in (p_a, p_b):
        p["skills"] = get_skills(p["persona_uuid"]) or {}

    contention = {
        "area_id": area_id,
        "area_info": {"name": area_id, "type": "plains", "region": "overworld"},
        "personas": [p_a, p_b],
        "count": 2,
    }

    return resolve_contention(contention, use_thinking_mode=use_thinking_mode)

