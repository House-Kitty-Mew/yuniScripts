"""
shm_combat_skills.py — Combat skills evolution, proficiency, and negative traits.

Tracks 10 combat skills that improve through use and decay from neglect.
Proficiency titles unlock at level thresholds. Negative traits develop
from poor skill balance or over-reliance on specific skills.
"""

import random, math
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Skill Definitions
# ══════════════════════════════════════════════════════════════════════

SKILL_NAMES = [
    "blades", "blunt", "archery", "polearms", "unarmed",
    "blocking", "dodging", "critical_strike", "combat_awareness", "first_aid",
]

PROFICIENCY_TITLES = [
    (0, "Unskilled"),
    (1, "Novice"),
    (11, "Apprentice"),
    (26, "Journeyman"),
    (41, "Expert"),
    (56, "Master"),
    (71, "Grandmaster"),
    (86, "Legendary"),
]

# Synergy pairs: skills that boost each other
SYNERGY_PAIRS = [
    ("blades", "critical_strike", 0.05),
    ("blades", "blocking", 0.03),
    ("blunt", "critical_strike", 0.03),
    ("archery", "combat_awareness", 0.05),
    ("polearms", "blocking", 0.04),
    ("unarmed", "dodging", 0.06),
    ("dodging", "combat_awareness", 0.04),
    ("blocking", "combat_awareness", 0.03),
    ("first_aid", "combat_awareness", 0.02),
    ("critical_strike", "combat_awareness", 0.04),
]

# Negative trait definitions
NEGATIVE_TRAITS_DEFS = {
    "sluggish_reflexes": {
        "name": "Sluggish Reflexes",
        "cause": "low dodging (< 15)",
        "effect": "-10% dodge chance",
        "severity_per_trigger": 0.1,
        "check_condition": lambda skills: skills.get("dodging", 0) < 15,
    },
    "clumsy_strikes": {
        "name": "Clumsy Strikes",
        "cause": "low all weapon skills (< 10 average)",
        "effect": "+10% fumble chance",
        "severity_per_trigger": 0.1,
        "check_condition": lambda skills: (
            sum(skills.get(s, 0) for s in ["blades", "blunt", "archery", "polearms", "unarmed"]) / 5
        ) < 10,
    },
    "shield_dependent": {
        "name": "Shield Dependent",
        "cause": "blocking much higher than dodging (2x+)",
        "effect": "-20% dodge when unshielded",
        "severity_per_trigger": 0.15,
        "check_condition": lambda skills: (
            skills.get("blocking", 0) > skills.get("dodging", 0) * 2
        ),
    },
    "reckless_swing": {
        "name": "Reckless Swing",
        "cause": "low critical_strike + high aggression",
        "effect": "+5% self-damage chance on attacks",
        "severity_per_trigger": 0.12,
        "check_condition": lambda skills: skills.get("critical_strike", 0) < 20,
    },
    "panic_parry": {
        "name": "Panic Parry",
        "cause": "stress from repeated combat losses",
        "effect": "-15% parry effectiveness",
        "severity_per_trigger": 0.1,
        "check_condition": lambda skills: (
            skills.get("blocking", 0) > skills.get("combat_awareness", 0) * 1.5
        ),
    },
    "tunnel_vision": {
        "name": "Tunnel Vision",
        "cause": "low combat_awareness (< 10)",
        "effect": "-20% perception in combat",
        "severity_per_trigger": 0.15,
        "check_condition": lambda skills: skills.get("combat_awareness", 0) < 10,
    },
    "weak_grip": {
        "name": "Weak Grip",
        "cause": "prolonged low activity in weapon skills",
        "effect": "Chance to drop weapon on hit",
        "severity_per_trigger": 0.08,
        "check_condition": lambda skills: (
            sum(skills.get(s, 0) for s in ["blades", "blunt", "polearms"]) / 3
        ) < 5,
    },
    "slow_healer": {
        "name": "Slow Healer",
        "cause": "poor first_aid practice",
        "effect": "Injuries heal 25% slower",
        "severity_per_trigger": 0.1,
        "check_condition": lambda skills: skills.get("first_aid", 0) < 5,
    },
    "predictable_pattern": {
        "name": "Predictable Pattern",
        "cause": "only one weapon skill developed",
        "effect": "+10% chance opponent predicts moves",
        "severity_per_trigger": 0.12,
        "check_condition": lambda skills: (
            max(skills.get(s, 0) for s in ["blades", "blunt", "archery", "polearms", "unarmed"]) > 0 and
            sum(skills.get(s, 0) for s in ["blades", "blunt", "archery", "polearms", "unarmed"]) / 5 <
            max(skills.get(s, 0) for s in ["blades", "blunt", "archery", "polearms", "unarmed"]) * 0.3
        ),
    },
    "combat_fatigue": {
        "name": "Combat Fatigue",
        "cause": "prolonged combat without rest (low energy + recent combat)",
        "effect": "Energy drains 30% faster",
        "severity_per_trigger": 0.15,
        "check_condition": lambda skills: False,  # Checked externally with health data
    },
}


def get_skill(persona_uuid: str, skill_name: str) -> Optional[dict]:
    """Get a specific combat skill for a persona."""
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_shm_combat_skills WHERE persona_uuid = ? AND skill_name = ?",
        (persona_uuid, skill_name))


def get_all_skills(persona_uuid: str) -> dict[str, dict]:
    """Get all combat skills for a persona as a dict."""
    db = get_db()
    skills = db.fetch_all(
        "SELECT * FROM ext_shm_combat_skills WHERE persona_uuid = ?",
        (persona_uuid,))
    return {s["skill_name"]: s for s in skills}


def get_skill_level(persona_uuid: str, skill_name: str) -> float:
    """Get a single skill's level (default 0 if not found)."""
    skill = get_skill(persona_uuid, skill_name)
    return skill["level"] if skill else 0.0


def get_proficiency_title(level: float) -> str:
    """Get proficiency title for a skill level."""
    for threshold, title in reversed(PROFICIENCY_TITLES):
        if level >= threshold:
            return title
    return "Unskilled"


def use_skill(persona_uuid: str, skill_name: str,
              context: str = "practice", intensity: float = 1.0) -> dict:
    """Use a combat skill, potentially gaining XP.

    Args:
        persona_uuid: The persona
        skill_name: Which skill to use
        context: 'combat', 'training', 'practice', 'hunting'
        intensity: 0.0-2.0 how hard the activity was

    Returns skill usage result.
    """
    if skill_name not in SKILL_NAMES:
        return {"error": f"unknown skill: {skill_name}"}

    db = get_db()
    skill = get_skill(persona_uuid, skill_name)
    if not skill:
        return {"error": f"skill not initialized for persona: {skill_name}"}

    # Context XP multipliers
    context_mult = {
        "combat": 2.0,
        "training": 1.5,
        "practice": 0.8,
        "hunting": 1.5,
        "sparring": 1.2,
    }
    xp_mult = context_mult.get(context, 1.0)

    # Genetics-based growth rate
    genetics = db.fetch_one(
        "SELECT muscle_growth_rate FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    growth_rate = genetics["muscle_growth_rate"] if genetics else 1.0

    # Synergy bonuses from related skills
    synergy_bonus = 0.0
    for skill_a, skill_b, bonus in SYNERGY_PAIRS:
        if skill_a == skill_name:
            partner = get_skill_level(persona_uuid, skill_b)
            synergy_bonus += partner * bonus * 0.01
        elif skill_b == skill_name:
            partner = get_skill_level(persona_uuid, skill_a)
            synergy_bonus += partner * bonus * 0.01

    # Soft cap: gains halved above 50
    soft_cap_penalty = 1.0
    if skill["level"] > 50:
        soft_cap_penalty = max(0.1, 1.0 - (skill["level"] - 50) * 0.02)

    # Calculate XP gain
    base_xp = random.uniform(0.5, 2.0)
    xp_gain = (base_xp * xp_mult * growth_rate * intensity *
               (1.0 + synergy_bonus) * soft_cap_penalty)

    new_xp = skill["xp_current"] + xp_gain
    xp_needed = skill["xp_to_next"]
    level_up = False
    new_level = skill["level"]

    if new_xp >= xp_needed:
        new_level = min(100, skill["level"] + 1)
        new_xp = new_xp - xp_needed
        new_xp_needed = 10.0 + (new_level * 1.5)
        level_up = True
        new_title = get_proficiency_title(new_level)
    else:
        new_xp_needed = skill["xp_to_next"]
        new_title = skill["proficiency_title"]

    db.execute("""
        UPDATE ext_shm_combat_skills SET
            level = ?,
            proficiency_title = ?,
            xp_current = ?,
            xp_to_next = ?,
            times_used = times_used + 1,
            last_used_tick = ?,
            decay_counter = 0
        WHERE persona_uuid = ? AND skill_name = ?
    """, (new_level, new_title, new_xp, new_xp_needed,
          0, persona_uuid, skill_name))

    result = {
        "skill": skill_name,
        "xp_gained": round(xp_gain, 2),
        "level": new_level,
        "title": new_title,
        "level_up": level_up,
        "synergy_bonus": round(synergy_bonus, 3),
    }

    if level_up:
        log.info("shm_combat_skills",
                 f"Skill up: {persona_uuid[:8]} - {skill_name} -> {new_level} ({new_title})")

    return result


def process_skill_decay_tick(persona_uuid: str) -> list[dict]:
    """Process skill decay for all combat skills.

    Skills decay when not used for extended periods.
    Higher-level skills decay faster (use it or lose it).
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"process_skill_decay_ failed: {e}")
        return []
    skills = get_all_skills(persona_uuid)
    if not skills:
        return []

    results = []
    for skill_name, skill in skills.items():
        # Increment decay counter
        new_counter = skill["decay_counter"] + 1

        # Decay after 5 ticks of no use, accelerate after 10+
        if new_counter >= 5:
            decay_rate = 0.1  # Base decay
            if new_counter > 10:
                decay_rate = 0.3
            if new_counter > 20:
                decay_rate = 0.5

            # High skills decay faster
            level_factor = 1.0 + (skill["level"] / 100)
            decay_amount = decay_rate * level_factor * random.uniform(0.5, 1.5)

            new_level = max(1, skill["level"] - decay_amount)
            new_title = get_proficiency_title(new_level)

            db.execute("""
                UPDATE ext_shm_combat_skills SET
                    level = ?,
                    proficiency_title = ?,
                    decay_counter = ?
                WHERE persona_uuid = ? AND skill_name = ?
            """, (new_level, new_title, new_counter,
                  persona_uuid, skill_name))

            if int(new_level) < int(skill["level"]):
                results.append({
                    "skill": skill_name,
                    "level_before": round(skill["level"], 1),
                    "level_after": round(new_level, 1),
                    "decay_amount": round(decay_amount, 2),
                })
        else:
            db.execute("""
                UPDATE ext_shm_combat_skills SET decay_counter = ?
                WHERE persona_uuid = ? AND skill_name = ?
            """, (new_counter, persona_uuid, skill_name))

    return results


def process_negative_traits_tick(persona_uuid: str) -> list[dict]:
    """Check and update negative traits based on current skill levels.

    Returns list of trait changes.
    """
    db = get_db()
    skills = get_all_skills(persona_uuid)
    if not skills:
        return []

    # Build simple level dict for checking conditions
    skill_levels = {name: s["level"] for name, s in skills.items()}

    # Check combat fatigue separately (needs health data)
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
        if health:
            NEGATIVE_TRAITS_DEFS["combat_fatigue"]["check_condition"] = (
                lambda s: health["energy"] < 25
            )
    except Exception:
        pass

    results = []
    for trait_id, trait_def in NEGATIVE_TRAITS_DEFS.items():
        condition_met = trait_def["check_condition"](skill_levels)

        existing = db.fetch_one("""
            SELECT * FROM ext_shm_negative_traits
            WHERE persona_uuid = ? AND trait_name = ? AND is_active = 1
        """, (persona_uuid, trait_id))

        if condition_met and not existing:
            # New negative trait
            severity = trait_def["severity_per_trigger"]
            db.execute("""
                INSERT INTO ext_shm_negative_traits
                (persona_uuid, trait_name, severity, cause, is_active, acquired_tick)
                VALUES (?, ?, ?, ?, 1, 0)
            """, (persona_uuid, trait_id, severity,
                  trait_def["cause"]))

            log.info("shm_combat_skills",
                     f"Negative trait acquired: {persona_uuid[:8]} - {trait_id} (severity={severity})")

            results.append({
                "trait": trait_id,
                "name": trait_def["name"],
                "status": "acquired",
                "severity": severity,
            })

        elif not condition_met and existing:
            # Trait has been overcome
            db.execute("""
                UPDATE ext_shm_negative_traits SET is_active = 0
                WHERE persona_uuid = ? AND trait_name = ?
            """, (persona_uuid, trait_id))

            log.info("shm_combat_skills",
                     f"Negative trait overcome: {persona_uuid[:8]} - {trait_id}")

            results.append({
                "trait": trait_id,
                "name": trait_def["name"],
                "status": "overcome",
            })

        elif condition_met and existing:
            # Existing trait may intensify
            new_severity = min(3.0, existing["severity"] + trait_def["severity_per_trigger"] * 0.1)
            db.execute("""
                UPDATE ext_shm_negative_traits SET severity = ?
                WHERE id = ?
            """, (new_severity, existing["id"]))

    return results


def get_combat_skill_modifiers(persona_uuid: str) -> dict:
    """Get all combat modifiers from skills and negative traits.

    Returns dict with hit_chance, damage, dodge, block, perception modifiers.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"get_combat_skill_mod failed: {e}")
        return {}
    skills = get_all_skills(persona_uuid)
    negative_traits = db.fetch_all("""
        SELECT * FROM ext_shm_negative_traits
        WHERE persona_uuid = ? AND is_active = 1
    """, (persona_uuid,))

    # Base modifiers from skill levels
    def _skill_mod(skill_name):
        level = skills.get(skill_name, {}).get("level", 0) if skills else 0
        return level * 0.35  # 35% at level 100

    modifiers = {
        "hit_chance_bonus": 0.0,
        "damage_bonus": 0.0,
        "dodge_bonus": 0.0,
        "block_bonus": 0.0,
        "perception_bonus": 0.0,
        "critical_chance_bonus": 0.0,
    }

    if skills:
        avg_weapon = (skills.get("blades", {}).get("level", 0) +
                     skills.get("blunt", {}).get("level", 0) +
                     skills.get("archery", {}).get("level", 0) +
                     skills.get("polearms", {}).get("level", 0) +
                     skills.get("unarmed", {}).get("level", 0)) / 5

        modifiers["hit_chance_bonus"] = avg_weapon * 0.35
        modifiers["damage_bonus"] = avg_weapon * 0.3
        modifiers["dodge_bonus"] = skills.get("dodging", {}).get("level", 0) * 0.4
        modifiers["block_bonus"] = skills.get("blocking", {}).get("level", 0) * 0.4
        modifiers["perception_bonus"] = skills.get("combat_awareness", {}).get("level", 0) * 0.5
        modifiers["critical_chance_bonus"] = skills.get("critical_strike", {}).get("level", 0) * 0.3

    # Apply negative trait penalties
    for trait in negative_traits:
        trait_id = trait["trait_name"]
        sev = trait["severity"]
        if trait_id == "sluggish_reflexes":
            modifiers["dodge_bonus"] -= 10 * sev
        elif trait_id == "clumsy_strikes":
            modifiers["hit_chance_bonus"] -= 10 * sev
        elif trait_id == "shield_dependent":
            modifiers["dodge_bonus"] -= 20 * sev
        elif trait_id == "reckless_swing":
            modifiers["hit_chance_bonus"] -= 5 * sev
        elif trait_id == "panic_parry":
            modifiers["block_bonus"] -= 15 * sev
        elif trait_id == "tunnel_vision":
            modifiers["perception_bonus"] -= 20 * sev
        elif trait_id == "predictable_pattern":
            modifiers["hit_chance_bonus"] -= 10 * sev

    return modifiers

