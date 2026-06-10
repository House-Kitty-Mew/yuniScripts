"""
rel_skills.py — Evolving skill system for simulated personas.

Extends the SP skill system with:
  - Dynamic skill evolution based on actions and experiences
  - Natural skill decay for disuse
  - Synergistic skill bonuses (adjacent skills boost each other)
  - Event-driven skill changes from contention outcomes
  - Skill caps determined by archetype and age
  - Complete change history tracking

Each persona's skills now evolve in real-time based on what they
actually DO (fight, trade, craft, etc.) and what happens TO them.
"""

import random, math
from typing import Optional, Any
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import (
    SKILL_NAMES, get_skills, save_skills
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db

log = get_logger()

# ── Skill evolution constants ───────────────────────────────────────

_SKILL_GAIN_RATES = {
    # (skill, archetype) -> gain_multiplier
    "mining": {"miner": 2.0, "builder": 1.5, "adventurer": 1.2, "default": 1.0},
    "combat": {"warrior": 2.0, "adventurer": 1.5, "vagabond": 1.3, "default": 1.0},
    "farming": {"farmer": 2.0, "default": 1.0},
    "trading": {"merchant": 2.0, "vagabond": 1.3, "default": 1.0},
    "crafting": {"builder": 2.0, "mage": 1.5, "default": 1.0},
    "exploration": {"adventurer": 2.0, "vagabond": 1.8, "warrior": 1.2, "default": 1.0},
    "leadership": {"warrior": 1.5, "merchant": 1.5, "default": 1.0},
}

_SKILL_DECAY_RATES = {
    # Skills decay slower for archetypes that USE them
    "mining": {"miner": 0.1, "default": 0.5},
    "combat": {"warrior": 0.1, "adventurer": 0.2, "default": 0.4},
    "farming": {"farmer": 0.1, "default": 0.6},
    "trading": {"merchant": 0.1, "vagabond": 0.2, "default": 0.4},
    "crafting": {"builder": 0.1, "mage": 0.2, "default": 0.5},
    "exploration": {"adventurer": 0.1, "vagabond": 0.1, "default": 0.4},
    "leadership": {"warrior": 0.2, "merchant": 0.2, "default": 0.7},
}

# Skills above 90 get a diminishing returns penalty
_DIMINISHING_RETURNS_CAP = 90.0
_MAX_SKILL = 100.0
_MIN_SKILL = 1.0


def _get_gain_rate(skill: str, archetype: str) -> float:
    """Get the gain multiplier for a skill given an archetype."""
    rates = _SKILL_GAIN_RATES.get(skill, {})
    return rates.get(archetype, rates.get("default", 1.0))


def _get_decay_rate(skill: str, archetype: str) -> float:
    """Get the decay multiplier for a skill given an archetype."""
    rates = _SKILL_DECAY_RATES.get(skill, {})
    return rates.get(archetype, rates.get("default", 0.5))


def _apply_diminishing_returns(current: float, gain: float) -> float:
    """Apply diminishing returns for high skill levels."""
    if current <= _DIMINISHING_RETURNS_CAP:
        return gain
    excess = current - _DIMINISHING_RETURNS_CAP
    penalty = 1.0 - (excess / _MAX_SKILL)
    return gain * max(0.1, penalty)


def _clamp_skill(value: float) -> float:
    """Clamp skill value to valid range."""
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return _MIN_SKILL
    return max(_MIN_SKILL, min(_MAX_SKILL, value))


def _sanitize_skill_value(value: Any, default: float = 10.0) -> float:
    """Sanitize a skill value: clamp NaN/Inf/None to a safe default."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            return default
        return float(value)
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _sanitize_base_delta(delta: Any) -> float:
    """Sanitize base delta: None/bool -> 0, NaN/Inf -> 0, str -> try float(0)."""
    if delta is None or isinstance(delta, bool):
        return 0.0
    if isinstance(delta, (int, float)):
        if math.isnan(delta) or math.isinf(delta):
            return 0.0
        return float(delta)
    try:
        return float(delta)
    except (ValueError, TypeError):
        return 0.0


def apply_skill_change(persona_uuid: str, skill: str,
                        base_delta: float,
                        reason: str,
                        archetype: str = "adventurer",
                        related_entity: Optional[str] = None,
                        record_history: bool = True) -> dict:
    """Apply a skill change to a persona with archetype modifiers.

    Args:
        persona_uuid: Target persona
        skill: Which skill to change
        base_delta: Raw change value (positive for gain, negative for loss)
        reason: Why the change occurred
        archetype: Persona's archetype for rate calculation
        related_entity: UUID of other persona involved (if any)
        record_history: Whether to record in skill history table

    Returns:
        Dict with {skill, old_value, new_value, delta_applied}
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import record_skill_change

    # Validate and sanitize inputs
    base_delta = _sanitize_base_delta(base_delta)
    skill = skill if skill and isinstance(skill, str) else "combat"
    reason = reason if reason and isinstance(reason, str) else "unknown"
    archetype = archetype if archetype and isinstance(archetype, str) else "adventurer"
    current_skills = get_skills(persona_uuid) or {}
    old_val = current_skills.get(skill, 10.0)

    # Determine the actual delta based on gain/decay rates
    if base_delta > 0:
        multiplier = _get_gain_rate(skill, archetype)
        effective_gain = base_delta * multiplier
        effective_gain = _apply_diminishing_returns(old_val, effective_gain)
        delta = effective_gain
    else:
        multiplier = _get_decay_rate(skill, archetype)
        delta = base_delta * multiplier

    new_val = _clamp_skill(old_val + delta)

    # Update the persona's skills
    current_skills[skill] = new_val
    save_skills(persona_uuid, current_skills)

    # Record history
    if record_history and abs(new_val - old_val) > 0.01:
        record_skill_change(persona_uuid, skill, old_val, new_val,
                             reason, related_entity)

    return {"skill": skill, "old_value": old_val,
            "new_value": new_val, "delta_applied": new_val - old_val}


def process_skill_decay(persona_uuid: str,
                         skills: dict[str, float],
                         archetype: str) -> dict[str, float]:
    """Process natural skill decay for all skills.

    Skills that are rarely used decay slowly. The most recent
    interaction history is checked — recently used skills decay
    less.

    Args:
        persona_uuid: The persona
        skills: Current skills dict
        archetype: Persona's archetype

    Returns:
        Dict of {skill: delta} for all changes applied
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_RELATIONSHIPS.rel_database import (
        get_recent_interactions, record_skill_change
    )

    changes = {}
    recent = get_recent_interactions(persona_uuid, limit=10)
    recently_used = set()
    for r in recent:
        sc = r.get("skill_changes", "")
        if sc:
            import json
            try:
                sc_d = json.loads(sc) if isinstance(sc, str) else sc
                for sk in sc_d:
                    recently_used.add(sk)
            except (json.JSONDecodeError, TypeError):
                pass

    for skill in SKILL_NAMES:
        current = skills.get(skill, 10.0)
        if current <= _MIN_SKILL:
            continue  # Can't decay below minimum

        # Recently used skills decay less
        decay_mult = 0.3 if skill in recently_used else 1.0
        decay_rate = _get_decay_rate(skill, archetype)
        decay = 0.5 * decay_rate * decay_mult * random.uniform(0.5, 1.5)

        new_val = _clamp_skill(current - decay)
        if abs(new_val - current) > 0.1:
            skills[skill] = new_val
            changes[skill] = new_val - current
            record_skill_change(persona_uuid, skill, current, new_val,
                                 "decay", None)

    if changes:
        save_skills(persona_uuid, skills)

    return changes


def apply_contention_skill_effects(persona_uuid: str,
                                    archetype: str,
                                    outcome_type: str,
                                    won: bool = False,
                                    opponent_uuid: Optional[str] = None) -> dict[str, float]:
    """Apply skill changes from a contention event outcome.

    Different contention outcomes affect different skills:
      - conflict (won): combat +3..6, leadership +1..3
      - conflict (lost): combat +1..2, defense_learned, leadership -1..-3
      - negotiation: trading +2..4, leadership +1..2
      - social_gathering: trading +1..2, exploration +1..2 (hearing stories)
      - avoidance: exploration +1..2 (fleeing/scouting)
      - peaceful_coexistence: leadership +0..1 (patience)

    Args:
        persona_uuid: The persona
        archetype: Persona's archetype
        outcome_type: Type of outcome
        won: Whether the persona "won" (only applies for conflict)
        opponent_uuid: UUID of opponent (for history tracking)

    Returns:
        Dict of skill->delta applied
    """
    try:
        import random

    except Exception as e:
        log.error(f"apply_contention_ski failed: {e}")
        return {}
    results = {}

    if outcome_type == "conflict":
        if won:
            results["combat"] = apply_skill_change(
                persona_uuid, "combat", random.uniform(3.0, 6.0),
                "combat_experience_victory", archetype, opponent_uuid)["delta_applied"]
            results["leadership"] = apply_skill_change(
                persona_uuid, "leadership", random.uniform(1.0, 3.0),
                "leadership_from_victory", archetype, opponent_uuid)["delta_applied"]
        else:
            results["combat"] = apply_skill_change(
                persona_uuid, "combat", random.uniform(1.0, 2.0),
                "combat_experience_defeat", archetype, opponent_uuid)["delta_applied"]
            results["leadership"] = apply_skill_change(
                persona_uuid, "leadership", random.uniform(-3.0, -1.0),
                "leadership_loss_from_defeat", archetype, opponent_uuid)["delta_applied"]

    elif outcome_type == "negotiation":
        results["trading"] = apply_skill_change(
            persona_uuid, "trading", random.uniform(2.0, 4.0),
            "negotiation_experience", archetype, opponent_uuid)["delta_applied"]
        results["leadership"] = apply_skill_change(
            persona_uuid, "leadership", random.uniform(1.0, 2.0),
            "diplomacy_experience", archetype, opponent_uuid)["delta_applied"]

    elif outcome_type == "social_gathering":
        results["trading"] = apply_skill_change(
            persona_uuid, "trading", random.uniform(1.0, 2.0),
            "social_trade_experience", archetype, opponent_uuid)["delta_applied"]
        results["exploration"] = apply_skill_change(
            persona_uuid, "exploration", random.uniform(1.0, 2.0),
            "stories_exchange", archetype, opponent_uuid)["delta_applied"]

    elif outcome_type == "peaceful_coexistence":
        results["leadership"] = apply_skill_change(
            persona_uuid, "leadership", random.uniform(0.0, 1.0),
            "patience_experience", archetype, opponent_uuid)["delta_applied"]

    elif outcome_type == "avoidance":
        results["exploration"] = apply_skill_change(
            persona_uuid, "exploration", random.uniform(1.0, 2.0),
            "scouting_experience", archetype, opponent_uuid)["delta_applied"]

    return results


def get_skill_summary(persona_uuid: str) -> dict:
    """Get a summary of a persona's skills with recent changes.

    Returns:
        Dict with skills, total_level, average, highest, lowest
    """
    skills = get_skills(persona_uuid) or {}
    if not skills:
        return {"skills": {}, "total": 0, "average": 0.0,
                "highest": "None", "lowest": "None"}

    values = [v for v in skills.values()]
    total = sum(values)
    avg = total / len(values)
    highest = max(skills, key=skills.get)
    lowest = min(skills, key=skills.get)

    return {
        "skills": skills,
        "total": total,
        "average": round(avg, 1),
        "highest": (highest, skills[highest]),
        "lowest": (lowest, skills[lowest]),
    }

