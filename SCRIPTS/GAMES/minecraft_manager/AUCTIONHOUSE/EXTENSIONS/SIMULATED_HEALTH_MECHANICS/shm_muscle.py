"""
shm_muscle.py — Muscle & strength system for persona health.

Tracks 12 major muscle groups with strength, protein level, fatigue,
injury state, and atrophy. Simulates protein burn during activity,
strength gain through training, and weakness during healing.
"""

import random, math
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import (
    get_db, ALL_MUSCLES
)

log = get_logger()


def get_muscles(persona_uuid: str) -> list[dict]:
    """Get all muscle data for a persona."""
    db = get_db()
    return db.fetch_all(
        "SELECT * FROM ext_shm_muscles WHERE persona_uuid = ?",
        (persona_uuid,))


def get_muscle(persona_uuid: str, muscle_group: str) -> Optional[dict]:
    """Get a specific muscle group."""
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_shm_muscles WHERE persona_uuid = ? AND muscle_group = ?",
        (persona_uuid, muscle_group))


def get_total_strength(persona_uuid: str) -> float:
    """Get average strength across all muscle groups (0-100)."""
    muscles = get_muscles(persona_uuid)
    if not muscles:
        return 30.0
    return sum(m["strength"] for m in muscles) / len(muscles)


def use_muscles(persona_uuid: str, activity_type: str,
                intensity: float = 1.0) -> dict:
    """Simulate muscle use from activity.

    Different activities work different muscle groups:
    - mining: back, shoulders, forearms, quadriceps
    - combat: chest, back, shoulders, biceps, triceps, quadriceps
    - running: quadriceps, hamstrings, glutes, calves
    - swimming: chest, back, shoulders, triceps
    - farming: back, shoulders, quadriceps, abdominals
    - crafting: forearms, biceps

    Args:
        persona_uuid: The persona
        activity_type: Type of activity
        intensity: 0.0-2.0 how hard

    Returns dict with per-muscle results.
    """
    db = get_db()
    activity_muscles = {
        "mining": ["back", "shoulders", "forearms", "quadriceps"],
        "combat": ["chest", "back", "shoulders", "biceps", "triceps", "quadriceps"],
        "running": ["quadriceps", "hamstrings", "glutes", "calves"],
        "swimming": ["chest", "back", "shoulders", "triceps"],
        "farming": ["back", "shoulders", "quadriceps", "abdominals"],
        "crafting": ["forearms", "biceps", "neck"],
        "walking": ["quadriceps", "hamstrings", "calves"],
        "lifting": ["chest", "back", "shoulders", "biceps", "quadriceps"],
    }

    muscle_names = activity_muscles.get(activity_type, ["chest", "back", "shoulders"])
    results = []

    for muscle_name in muscle_names:
        muscle = get_muscle(persona_uuid, muscle_name)
        if not muscle:
            continue

        # Genetics-based muscle growth rate
        genetics = db.fetch_one(
            "SELECT muscle_growth_rate, metabolic_rate FROM ext_shm_genetics WHERE persona_uuid = ?",
            (persona_uuid,))
        growth_rate = genetics["muscle_growth_rate"] if genetics else 1.0
        metabolic_rate = genetics["metabolic_rate"] if genetics else 1.0

        # Fatigue accumulation
        fatigue_gain = intensity * random.uniform(5, 15) * metabolic_rate
        new_fatigue = min(100, muscle["fatigue"] + fatigue_gain)

        # Protein burn
        protein_burn = intensity * random.uniform(2, 6) * metabolic_rate
        new_protein = max(0, muscle["protein_level"] - protein_burn)

        # Strength gain (hypertrophy) if not exhausted
        strength_gain = 0
        if new_fatigue < 80 and muscle["strength"] < muscle["max_strength"]:
            strength_gain = (intensity * 0.5 * growth_rate *
                             random.uniform(0.3, 1.0))
            new_strength = min(muscle["max_strength"],
                               muscle["strength"] + strength_gain)
        else:
            new_strength = muscle["strength"]

        # Reset atrophy counter
        db.execute("""
            UPDATE ext_shm_muscles SET
                strength = ?,
                fatigue = ?,
                protein_level = ?,
                atrophy_days = 0
            WHERE persona_uuid = ? AND muscle_group = ?
        """, (new_strength, new_fatigue, new_protein,
              persona_uuid, muscle_name))

        results.append({
            "muscle": muscle_name,
            "strength_gain": round(strength_gain, 2),
            "new_strength": round(new_strength, 1),
            "new_fatigue": round(new_fatigue, 1),
            "protein_burn": round(protein_burn, 1),
            "new_protein": round(new_protein, 1),
        })

    return {"activity": activity_type, "muscles": results}


def process_muscle_recovery_tick(persona_uuid: str) -> dict:
    """Process muscle recovery (fatigue reduction, protein restoration).

    Recovery requires:
    - Rest (low energy drain → faster recovery)
    - Food intake (protein synthesis)
    - Time

    Returns recovery results.
    """
    db = get_db()
    muscles = get_muscles(persona_uuid)
    if not muscles:
        return {"error": "no muscles"}

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
    except Exception:
        health = None

    rest_factor = (health["energy"] / 100) if health else 0.5
    food_factor = (health["food"] / 100) if health else 0.5

    genetics = db.fetch_one(
        "SELECT healing_factor, metabolic_rate FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    healing_factor = genetics["healing_factor"] if genetics else 1.0

    results = []
    for muscle in muscles:
        # Fatigue recovery
        if muscle["fatigue"] > 0:
            fatigue_recovery = (5 + 10 * rest_factor * healing_factor *
                                random.uniform(0.5, 1.5))
            new_fatigue = max(0, muscle["fatigue"] - fatigue_recovery)
        else:
            new_fatigue = 0

        # Protein restoration (from food)
        if muscle["protein_level"] < 100:
            protein_synthesis = (2 + 5 * food_factor * healing_factor *
                                 random.uniform(0.3, 1.0))
            new_protein = min(100, muscle["protein_level"] + protein_synthesis)
        else:
            new_protein = muscle["protein_level"]

        # Atrophy: if no use and protein is low, lose strength
        new_strength = muscle["strength"]
        new_atrophy_days = muscle["atrophy_days"]
        if muscle["fatigue"] < 5 and muscle["protein_level"] < 30:
            new_atrophy_days = muscle["atrophy_days"] + 1
            if new_atrophy_days > 3:
                atrophy_loss = 0.5 * (new_atrophy_days - 3)
                new_strength = max(1, muscle["strength"] - atrophy_loss)
        else:
            new_atrophy_days = 0

        db.execute("""
            UPDATE ext_shm_muscles SET
                fatigue = ?,
                protein_level = ?,
                strength = ?,
                atrophy_days = ?
            WHERE persona_uuid = ? AND muscle_group = ?
        """, (new_fatigue, new_protein, new_strength, new_atrophy_days,
              persona_uuid, muscle["muscle_group"]))

        results.append({
            "muscle": muscle["muscle_group"],
            "fatigue_recovery": round(muscle["fatigue"] - new_fatigue, 1),
            "new_fatigue": round(new_fatigue, 1),
            "protein_synthesis": round(new_protein - muscle["protein_level"], 1),
            "new_protein": round(new_protein, 1),
            "strength_change": round(new_strength - muscle["strength"], 2),
            "atrophy_days": new_atrophy_days,
        })

    return {"muscles": results}


def injure_muscle(persona_uuid: str, muscle_group: str,
                   damage: float = 0.3) -> dict:
    """Injure a muscle group (strain, tear).

    Args:
        persona_uuid: The persona
        muscle_group: Which muscle
        damage: 0.0-1.0 injury severity

    Returns injury result.
    """
    db = get_db()
    muscle = get_muscle(persona_uuid, muscle_group)
    if not muscle:
        return {"error": f"muscle not found: {muscle_group}"}

    penalty = damage * 50  # 0-50% penalty
    new_penalty = min(100, muscle["injury_penalty"] + penalty)

    db.execute("""
        UPDATE ext_shm_muscles SET
            is_injured = 1,
            injury_penalty = ?
        WHERE persona_uuid = ? AND muscle_group = ?
    """, (new_penalty, persona_uuid, muscle_group))

    # Register pain
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import add_pain_source
        add_pain_source(persona_uuid, f"strain_{muscle_group}",
                        "muscle_strain", penalty * 2, duration_ticks=24)
    except Exception:
        pass

    log.info("shm_muscle",
             f"Muscle injured: {persona_uuid[:8]} - {muscle_group} ({new_penalty:.0f}% penalty)")

    return {
        "muscle": muscle_group,
        "injury_penalty": round(new_penalty, 1),
        "effective_strength": round(muscle["strength"] * (1 - new_penalty / 100), 1),
    }


def heal_muscle_tick(persona_uuid: str) -> list[dict]:
    """Process healing of injured muscles.

    Healing muscles requires protein and rest.
    Weakness during healing reduces strength in affected area.
    """
    db = get_db()
    muscles = get_muscles(persona_uuid)
    genetics = db.fetch_one(
        "SELECT healing_factor FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    healing_factor = genetics["healing_factor"] if genetics else 1.0

    results = []
    for muscle in muscles:
        if not muscle["is_injured"]:
            continue

        # Healing requires protein
        protein_available = muscle["protein_level"]
        heal_amount = (2.0 * healing_factor * (protein_available / 100) *
                       random.uniform(0.5, 1.5))

        new_penalty = max(0, muscle["injury_penalty"] - heal_amount)
        protein_used = heal_amount * 0.5
        new_protein = max(0, muscle["protein_level"] - protein_used)

        is_healed = new_penalty <= 0

        db.execute("""
            UPDATE ext_shm_muscles SET
                injury_penalty = ?,
                protein_level = ?,
                is_injured = ?
            WHERE persona_uuid = ? AND muscle_group = ?
        """, (new_penalty, new_protein, 0 if is_healed else 1,
              persona_uuid, muscle["muscle_group"]))

        if is_healed:
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
                    remove_pain_source
                )
                remove_pain_source(persona_uuid, f"strain_{muscle['muscle_group']}")
            except Exception:
                pass

        results.append({
            "muscle": muscle["muscle_group"],
            "heal_amount": round(heal_amount, 1),
            "new_penalty": round(new_penalty, 1),
            "protein_used": round(protein_used, 1),
            "is_healed": is_healed,
        })

    return results


def get_muscle_combat_modifier(persona_uuid: str, combat_action: str) -> float:
    """Get strength multiplier for a combat action based on relevant muscles.

    Args:
        persona_uuid: The persona
        combat_action: 'melee', 'ranged', 'block', 'dodge', 'grapple'

    Returns multiplier (1.0 = normal, higher = stronger).
    """
    muscles = get_muscles(persona_uuid)
    if not muscles:
        return 1.0

    muscle_map = {m["muscle_group"]: m for m in muscles}

    relevant = {
        "melee": ["chest", "shoulders", "biceps", "triceps", "back"],
        "ranged": ["shoulders", "triceps", "forearms", "back"],
        "block": ["chest", "shoulders", "biceps", "forearms"],
        "dodge": ["quadriceps", "hamstrings", "calves", "abdominals"],
        "grapple": ["chest", "back", "biceps", "forearms", "abdominals"],
    }

    relevant_groups = relevant.get(combat_action, ["chest", "back"])

    total = 0
    count = 0
    for group in relevant_groups:
        if group in muscle_map:
            m = muscle_map[group]
            effective_strength = m["strength"] * (1 - m["injury_penalty"] / 100)
            fatigue_penalty = m["fatigue"] / 100
            total += effective_strength * (1 - fatigue_penalty * 0.5)
            count += 1

    avg_strength = total / count if count > 0 else 30
    return round(avg_strength / 50, 2)  # Normalize: 50 = 1.0
