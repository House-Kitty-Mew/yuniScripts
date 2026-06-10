"""
SIMULATED_RELATIONSHIPS — Rich relationships, situationships, and cell contention.

Extends the SIMULATED_PEOPLE plugin with:
  - Rich persona-to-persona relationships (friendship, rivalry, romance, etc.)
  - Temporary situationships (time-limited relationship modifiers)
  - Complex behavior patterns derived from persona stats
  - Cell contention events when multiple personas occupy the same area
  - AI-powered thinking-mode resolution with full outcome application
  - Evolving skills that change through experience and decay over time

Hooks into:
  - on_simulation_cycle_end: Process cell contentions and decay
"""

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()
EXTENSION_NAME = "SIMULATED_RELATIONSHIPS"


def on_load(registry):
    """Register this extension's hooks with the AH plugin registry.

    Args:
        registry: The _HookRegistry instance
    """
    registry.register("on_simulation_cycle_end", EXTENSION_NAME,
                       on_simulation_cycle_end)

    # Initialize database schema
    from .rel_database import ensure_schema
    ensure_schema()

    log.info("rel_relationships",
             f"Extension '{EXTENSION_NAME}' loaded — "
             f"relationships, situationships, contention, evolving skills active")


def on_simulation_cycle_end(**kwargs):
    """Called at the end of each simulation cycle.

    Processes:
      1. Cell contention events (multi-persona encounters)
      2. Natural skill decay for all personas

    Args:
        **kwargs: Passed by the simulation scheduler (may include
                  announce_fn, rcon_func, etc.)

    Returns:
        Dict with summary of what was processed
    """
    from .rel_contention import process_all_contentions
    from .rel_skills import process_skill_decay
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
        get_active_personas
    )
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills

    # ── Read shared state from SIMULATED_SOCIAL ────────────────
    from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state as _get_state
    _shared = _get_state()
    _soc_interactions = _shared.get("social_interactions", [])
    if _soc_interactions:
        log.info("rel_relationships", f"Read {len(_soc_interactions)} social interactions from shared state")

    result = {"contentions_processed": 0, "skills_decayed": 0}

    # 1. Process cell contentions
    try:
        contentions = process_all_contentions(use_thinking_mode=True, max_events=5)
        result["contentions_processed"] = len(contentions)
        if contentions:
            outcomes = [c.get("outcome", "?") for c in contentions]
            log.info("rel_relationships",
                     f"Processed {len(contentions)} contention(s): {outcomes}")
    except Exception as e:
        log.error("rel_relationships", f"Contention processing error: {e}")
        import traceback
        log.error("rel_relationships", traceback.format_exc())

    # 2. Natural skill decay for active personas
    try:
        active = get_active_personas() or []
        decay_count = 0
        for p in active:
            arch = p.get("archetype", "adventurer")
            skills = get_skills(p["persona_uuid"])
            if skills:
                changes = process_skill_decay(p["persona_uuid"], skills, arch)
                decay_count += len(changes)
        result["skills_decayed"] = decay_count
        if decay_count > 0:
            log.info("rel_relationships", f"Applied {decay_count} skill decay(s)")
    except Exception as e:
        log.error("rel_relationships", f"Skill decay error: {e}")

    # ── Write relationship summary to shared state for CHAT ─────
    try:
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state as _get_state2
        _shared = _get_state2()
        # Peek at recent relationship changes for CHAT integration
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import get_recent_interactions
        _recent = get_recent_interactions("", limit=10)
        if _recent:
            _shared.set("recent_relationship_interactions", _recent, "SIMULATED_RELATIONSHIPS")
    except Exception:
        pass

    return result
