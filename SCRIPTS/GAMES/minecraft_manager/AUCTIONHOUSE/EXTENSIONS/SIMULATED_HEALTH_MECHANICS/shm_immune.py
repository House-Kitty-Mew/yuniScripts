"""
shm_immune.py — Immune system & autoimmune response simulation.

The immune system battles pathogens tick-by-tick, with results
determined by genetics, current health, hygiene, and random factors.

Autoimmune conditions can develop when the immune system attacks
healthy tissue — triggered by severe infections or genetic predisposition.
"""

import random, math
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db

log = get_logger()


def start_immune_response(persona_uuid: str, target_type: str,
                          target_id: str, pathogen_count: float,
                          virulence: float) -> dict:
    """Start a new immune response against a pathogen.

    Args:
        persona_uuid: The persona
        target_type: 'disease', 'wound', 'bacteria'
        target_id: The specific disease/wound ID
        pathogen_count: Initial pathogen load
        virulence: How aggressive the pathogen is

    Returns immune response data.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Check if already fighting this target
    existing = db.fetch_one("""
        SELECT id FROM ext_shm_immune_response
        WHERE persona_uuid = ? AND target_id = ? AND is_active = 1
    """, (persona_uuid, target_id))
    if existing:
        return {"error": "immune response already active",
                "existing_id": existing["id"]}

    # Genetics
    genetics = db.fetch_one(
        "SELECT immune_potency FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    immune_potency = genetics["immune_potency"] if genetics else 1.0

    # Base immune strength from health
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
    except Exception:
        health = None

    food_factor = (health["food"] / 100) if health else 0.5
    energy_factor = (health["energy"] / 100) if health else 0.5
    immune_stat = (health["immune"] / 100) if health else 0.5

    base_strength = (immune_stat * 50 + food_factor * 25 + energy_factor * 25)
    base_strength *= immune_potency
    base_strength = max(10, min(100, base_strength))

    # WBC production rate
    wbc_rate = 1.0 + (immune_potency * 0.5) + (food_factor * 0.3)

    db.execute("""
        INSERT OR REPLACE INTO ext_shm_immune_response
        (persona_uuid, target_type, target_id, immune_strength,
         inflammation, fever, wbc_production, is_active)
        VALUES (?, ?, ?, ?, 5.0, 0.0, ?, 1)
    """, (persona_uuid, target_type, target_id,
          base_strength, wbc_rate))

    return {
        "immune_response_started": True,
        "initial_strength": round(base_strength, 1),
        "wbc_production_rate": round(wbc_rate, 2),
        "target": target_id,
    }


def process_immune_ticks(persona_uuid: str) -> list[dict]:
    """Process all active immune responses for a persona.

    Returns list of battle results.
    """
    db = get_db()
    responses = db.fetch_all("""
        SELECT * FROM ext_shm_immune_response
        WHERE persona_uuid = ? AND is_active = 1
    """, (persona_uuid,))

    if not responses:
        return []

    results = []
    for response in responses:
        result = _process_single_immune_response(persona_uuid, response, db)
        results.append(result)

    return results


def _process_single_immune_response(persona_uuid: str,
                                    response: dict, db) -> dict:
    """Process a single immune response tick."""
    # Immune strength decays slightly per tick (energy cost)
    energy_cost = response["immune_strength"] * 0.02
    new_strength = max(0, response["immune_strength"] - energy_cost)

    # WBC production replenishes immune strength
    wbc_replenish = response["wbc_production"] * random.uniform(0.3, 0.8)
    new_strength = min(100, new_strength + wbc_replenish)

    # Inflammation decreases if no active threat
    new_inflammation = max(0, response["inflammation"] - random.uniform(0.5, 2.0))

    # Fever regulation
    new_fever = response["fever"]
    if response["fever"] > 0:
        if response["inflammation"] > 20:
            # Fever persists while inflamed
            new_fever = min(42, response["fever"] + random.uniform(0, 0.3))
        else:
            # Fever receding
            new_fever = max(0, response["fever"] - random.uniform(0.2, 0.5))

    # Autoimmune check: if immune system is strong but can't find pathogen
    # It may start attacking healthy tissue
    autoimmune_check = False
    if (new_strength > 60 and response["inflammation"] > 30 and
            response["battle_tick"] > 10 and random.random() < 0.02):
        autoimmune_check = True

    db.execute("""
        UPDATE ext_shm_immune_response SET
            immune_strength = ?,
            inflammation = ?,
            fever = ?,
            battle_tick = battle_tick + 1,
            is_autoimmune = CASE WHEN ? THEN 1 ELSE is_autoimmune END
        WHERE id = ?
    """, (new_strength, new_inflammation, new_fever,
          autoimmune_check, response["id"]))

    result = {
        "target": response["target_id"],
        "immune_strength": round(new_strength, 1),
        "inflammation": round(new_inflammation, 1),
        "fever": round(new_fever, 1),
        "battle_tick": response["battle_tick"] + 1,
        "autoimmune": autoimmune_check,
    }

    # Autoimmune event
    if autoimmune_check:
        _trigger_autoimmune_event(persona_uuid, response, db)

    return result


def _trigger_autoimmune_event(persona_uuid: str, response: dict, db):
    """Trigger an autoimmune reaction - immune system attacks healthy tissue."""
    import random

    # Pick a random organ to attack
    organs = ["brain", "heart", "liver", "kidney_left", "kidney_right",
              "stomach", "intestines", "spleen", "pancreas", "lung_left", "lung_right"]
    target_organ = random.choice(organs)

    damage = random.uniform(5, 20)

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import damage_organ
        damage_organ(persona_uuid, target_organ, damage, "autoimmune")
    except Exception:
        pass

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import add_life_event
        add_life_event(persona_uuid, "autoimmune",
                       f"Immune system attacking {target_organ}!",
                       financial_impact=0, mood_impact="stressed",
                       duration_hours=72)
    except Exception:
        pass

    log.warn("shm_immune",
             f"Autoimmune event: {persona_uuid[:8]} - immune attacking {target_organ} ({damage:.0f} dmg)")

    return {"organ": target_organ, "damage": damage}


def get_immune_status(persona_uuid: str) -> dict:
    """Get a summary of immune system status."""
    db = get_db()
    responses = db.fetch_all("""
        SELECT * FROM ext_shm_immune_response
        WHERE persona_uuid = ? AND is_active = 1
    """, (persona_uuid,))

    active_battles = len(responses)
    total_strength = sum(r["immune_strength"] for r in responses) if responses else 0
    total_inflammation = sum(r["inflammation"] for r in responses) if responses else 0

    genetics = db.fetch_one(
        "SELECT immune_potency FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    immune_potency = genetics["immune_potency"] if genetics else 1.0

    # Overall immune health
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
    except Exception:
        health = None
    immune_stat = (health["immune"] / 100) if health else 0.5

    overall = ((immune_stat * 50) + (immune_potency * 25) +
               (total_strength / max(1, active_battles) if active_battles > 0 else 50)) / 2
    overall = min(100, max(0, overall))

    return {
        "active_battles": active_battles,
        "total_immune_strength": round(total_strength, 1),
        "total_inflammation": round(total_inflammation, 1),
        "immune_potency": round(immune_potency, 2),
        "overall_immune_health": round(overall, 1),
        "has_autoimmune": any(r["is_autoimmune"] for r in responses),
    }

