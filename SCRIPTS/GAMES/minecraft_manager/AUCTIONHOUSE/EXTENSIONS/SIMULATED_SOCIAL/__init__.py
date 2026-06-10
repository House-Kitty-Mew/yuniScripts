"""
SIMULATED_SOCIAL — Social simulation engine for personas.

Adds boredom, social exhaustion, three-tier memory, crisis management,
deep relationships, marriage, and social activity planning.

Hooks into on_simulation_cycle_end to process social updates.
"""

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()
EXTENSION_NAME = "SIMULATED_SOCIAL"


def on_load(registry):
    """Register hooks with the AH plugin registry."""
    registry.register("on_simulation_cycle_end", EXTENSION_NAME,
                       on_simulation_cycle_end)

    from .soc_database import ensure_schema
    ensure_schema()

    log.info("soc_social",
             f"Extension '{EXTENSION_NAME}' loaded — "
             f"boredom, exhaustion, 3-tier memory, crisis management active")


def on_simulation_cycle_end(**kwargs):
    """Process social updates for all active personas.

    Each tick:
      1. Process boredom decay
      2. Process exhaustion recovery/rest
      3. Check crisis mode for personas at 0 boredom
      4. Plan and execute social activities
      5. Process relationship decay
      6. Check marriage/divorce potential
      7. Prune medium-term memories
    """
    from .soc_boredom import process_boredom_tick
    from .soc_exhaustion import process_exhaustion_tick
    from .soc_planner import plan_activity
    from .soc_activities import process_activity
    from .soc_relationships import (
        process_relationship_decay, check_marriage_potential,
        check_divorce_potential
    )
    from .soc_memory import prune_medium_memories
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
        get_active_personas
    )

    # ── Phase 2C: Read shared state from SIMULATED_PEOPLE ────────────
    from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state as _get_state
    _shared = _get_state()
    _active_from_state = _shared.get("active_personas", [])
    _weather = _shared.get("weather", {})
    _world_events = _shared.get("world_events", {})
    if _active_from_state:
        active_personas = _active_from_state

    result = {
        "boredom_updates": 0,
        "exhaustion_updates": 0,
        "activities_planned": 0,
        "marriages": 0,
        "divorces": 0,
        "memories_pruned": 0,
    }

    try:
        active = get_active_personas()
        if not active:
            return result

        for persona in active:
            puid = persona.get("persona_uuid", "")
            arch = persona.get("archetype", "adventurer")
            if not puid:
                continue

            # 1. Boredom
            process_boredom_tick(puid, arch)
            result["boredom_updates"] += 1

            # 2. Exhaustion
            process_exhaustion_tick(puid, arch)
            result["exhaustion_updates"] += 1

            # 3. Plan activity
            plan = plan_activity(puid, arch, [])
            result["activities_planned"] += 1

            # 4. Execute if not resting/crisis-only
            if plan["activity_type"] not in ("suicide", "waiting",
                                              "resting_alone"):
                process_activity(puid, plan["activity_type"],
                                  plan.get("partner_uuid"), arch)

            # 5. Relationship decay
            process_relationship_decay(puid)

            # 6. Marriage check (small chance per persona)
            try:
                partners = [p for p in (active or [])
                            if p.get("persona_uuid") != puid]
                for partner in partners[:3]:
                    m = check_marriage_potential(puid, partner.get("persona_uuid", ""))
                    if m.get("married"):
                        result["marriages"] += 1
                    d = check_divorce_potential(puid, partner.get("persona_uuid", ""))
                    if d.get("divorced"):
                        result["divorces"] += 1
            except Exception:
                pass

        # 7. Memory pruning
        pruned = prune_medium_memories()
        result["memories_pruned"] = pruned

    except Exception as e:
        log.error("soc_social", f"Simulation cycle error: {e}")

    return result
