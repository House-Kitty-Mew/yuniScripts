"""
shm_hygiene.py — Hygiene system & bacteria spread simulation.

Tracks 5 hygiene metrics: personal cleanliness, oral hygiene, clothing
cleanliness, wound care, and environment cleanliness.

Hygiene directly affects disease susceptibility, bacteria spread,
and immune system effectiveness.
"""

import random, math
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db

log = get_logger()


def get_hygiene(persona_uuid: str) -> Optional[dict]:
    """Get full hygiene data for a persona."""
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_shm_hygiene WHERE persona_uuid = ?",
        (persona_uuid,))


def process_hygiene_decay_tick(persona_uuid: str, activity_type: str = "idle") -> dict:
    """Process one tick of hygiene decay based on activity.

    Different activities affect different hygiene metrics.
    """
    db = get_db()
    hygiene = get_hygiene(persona_uuid)
    if not hygiene:
        return {"error": "no hygiene data"}

    # Activity effects on hygiene
    activity_effects = {
        "idle": {"personal": -0.5, "oral": -0.3, "clothing": -0.2, "environment": -0.1},
        "mining": {"personal": -3.0, "oral": -0.5, "clothing": -5.0, "environment": -0.5},
        "combat": {"personal": -5.0, "oral": -1.0, "clothing": -8.0, "wound_care": -3.0},
        "running": {"personal": -4.0, "oral": -0.5, "clothing": -3.0, "environment": 0},
        "farming": {"personal": -3.0, "oral": -0.3, "clothing": -4.0, "environment": -1.0},
        "crafting": {"personal": -1.0, "oral": -0.2, "clothing": -1.0, "environment": 0},
        "eating": {"personal": -0.5, "oral": -4.0, "clothing": -0.5, "environment": -1.0},
    }

    effects = activity_effects.get(activity_type, activity_effects["idle"])

    # Apply weather effects
    try:
        area = db.fetch_one("""
            SELECT area_uuid FROM ext_sp_persona_location WHERE persona_uuid = ?
        """, (persona_uuid,))
        if area:
            weather = db.fetch_one("""
                SELECT is_raining, is_snowing, humidity FROM ext_sp_weather
                WHERE area_uuid = ?
            """, (area["area_uuid"],))
            if weather:
                if weather["is_raining"]:
                    effects["clothing"] -= 2.0  # Rain cleans clothes
                    effects["personal"] += 1.0  # But rain alone doesn't clean body
                if weather["is_snowing"]:
                    effects["clothing"] += 1.0  # Snow is slightly cleaning
                if weather["humidity"] > 80:
                    effects["personal"] += 0.5  # Humid = more sweat
                if weather["humidity"] < 30:
                    effects["clothing"] += 0.5  # Dry = dusty
    except Exception:
        pass

    new_personal = max(0, min(100, hygiene["personal_cleanliness"] + effects.get("personal", 0)))
    new_oral = max(0, min(100, hygiene["oral_hygiene"] + effects.get("oral", 0)))
    new_clothing = max(0, min(100, hygiene["clothing_cleanliness"] + effects.get("clothing", 0)))
    new_wound_care = max(0, min(100, hygiene["wound_care"] + effects.get("wound_care", 0)))
    new_environment = max(0, min(100, hygiene["environment_cleanliness"] + effects.get("environment", 0)))

    db.execute("""
        UPDATE ext_shm_hygiene SET
            personal_cleanliness = ?,
            oral_hygiene = ?,
            clothing_cleanliness = ?,
            wound_care = ?,
            environment_cleanliness = ?
        WHERE persona_uuid = ?
    """, (new_personal, new_oral, new_clothing, new_wound_care,
          new_environment, persona_uuid))

    return {
        "personal": round(new_personal, 1),
        "oral": round(new_oral, 1),
        "clothing": round(new_clothing, 1),
        "wound_care": round(new_wound_care, 1),
        "environment": round(new_environment, 1),
    }


def clean_persona(persona_uuid: str, clean_type: str = "full") -> dict:
    """Persona cleans themselves.

    clean_type: 'full', 'wash', 'brush_teeth', 'change_clothes', 'clean_wound', 'clean_environment'

    Requires water (for washing) and/or clean clothes.
    """
    db = get_db()
    hygiene = get_hygiene(persona_uuid)
    if not hygiene:
        return {"error": "no hygiene data"}

    clean_amounts = {
        "full": {"personal": 40, "oral": 30, "clothing": 30, "wound_care": 20, "environment": 10},
        "wash": {"personal": 30, "oral": 0, "clothing": 0, "wound_care": 10, "environment": 0},
        "brush_teeth": {"personal": 0, "oral": 40, "clothing": 0, "wound_care": 0, "environment": 0},
        "change_clothes": {"personal": 5, "oral": 0, "clothing": 50, "wound_care": 0, "environment": 0},
        "clean_wound": {"personal": 0, "oral": 0, "clothing": 0, "wound_care": 50, "environment": 0},
        "clean_environment": {"personal": 0, "oral": 0, "clothing": -5, "wound_care": 0, "environment": 40},
    }

    amounts = clean_amounts.get(clean_type, clean_amounts["wash"])

    new_personal = min(100, hygiene["personal_cleanliness"] + amounts["personal"])
    new_oral = min(100, hygiene["oral_hygiene"] + amounts["oral"])
    new_clothing = min(100, hygiene["clothing_cleanliness"] + amounts["clothing"])
    new_wound_care = min(100, hygiene["wound_care"] + amounts["wound_care"])
    new_environment = min(100, hygiene["environment_cleanliness"] + amounts["environment"])

    db.execute("""
        UPDATE ext_shm_hygiene SET
            personal_cleanliness = ?,
            oral_hygiene = ?,
            clothing_cleanliness = ?,
            wound_care = ?,
            environment_cleanliness = ?,
            last_washed_tick = ?
        WHERE persona_uuid = ?
    """, (new_personal, new_oral, new_clothing, new_wound_care,
          new_environment, 0, persona_uuid))

    return {
        "clean_type": clean_type,
        "new_personal": round(new_personal, 1),
        "new_oral": round(new_oral, 1),
        "new_clothing": round(new_clothing, 1),
        "new_wound_care": round(new_wound_care, 1),
        "new_environment": round(new_environment, 1),
    }


def get_hygiene_infection_modifier(persona_uuid: str) -> float:
    """Get infection susceptibility modifier from hygiene.

    Returns multiplier: 1.0 = normal, >1.0 = more susceptible, <1.0 = less.
    """
    try:
        hygiene = get_hygiene(persona_uuid)

    except Exception as e:
        log.error(f"get_hygiene_infectio failed: {e}")
        return 0.0
    if not hygiene:
        return 1.0

    avg = ((hygiene["personal_cleanliness"] +
            hygiene["oral_hygiene"] +
            hygiene["clothing_cleanliness"] +
            hygiene["wound_care"] +
            hygiene["environment_cleanliness"]) / 5)

    # Low hygiene increases susceptibility by up to 200%
    if avg < 30:
        return 2.0 + (30 - avg) / 15  # 2.0 - 6.67
    elif avg < 50:
        return 1.5 - (avg - 30) / 40  # 1.5 at 30 -> 1.0 at 50
    elif avg < 80:
        return 1.0 - (avg - 50) / 60  # 1.0 at 50 -> 0.5 at 80
    else:
        return 0.5


def spread_bacteria_between_personas(source_uuid: str, target_uuid: str) -> dict:
    """Simulate bacteria spread between two personas in proximity.

    Spread probability depends on:
    - Source's hygiene level
    - Target's hygiene level
    - Proximity (same area)
    - Target's immune system
    """
    db = get_db()
    source_hygiene = get_hygiene(source_uuid)
    target_hygiene = get_hygiene(target_uuid)

    if not source_hygiene or not target_hygiene:
        return {"spread": False}

    # Source's bacteria "load" based on their hygiene
    source_bacteria = 1.0 - (source_hygiene["personal_cleanliness"] / 100)
    target_resistance = target_hygiene["personal_cleanliness"] / 100

    # Target's immune resistance
    genetics = db.fetch_one(
        "SELECT immune_potency, disease_resistance FROM ext_shm_genetics WHERE persona_uuid = ?",
        (target_uuid,))
    immune_factor = (genetics["immune_potency"] * 0.5 +
                    genetics["disease_resistance"] * 0.5) if genetics else 1.0

    # Spread chance
    spread_chance = source_bacteria * (1 - target_resistance * 0.5) / immune_factor
    spread_chance = min(0.3, max(0.01, spread_chance))

    if random.random() < spread_chance:
        # Target's hygiene drops slightly (picked up bacteria)
        new_cleanliness = max(0, target_hygiene["personal_cleanliness"] -
                              random.uniform(1, 5))
        db.execute("""
            UPDATE ext_shm_hygiene SET personal_cleanliness = ?
            WHERE persona_uuid = ?
        """, (new_cleanliness, target_uuid))

        return {
            "spread": True,
            "source": source_uuid[:8],
            "target": target_uuid[:8],
            "hygiene_drop": round(target_hygiene["personal_cleanliness"] - new_cleanliness, 1),
        }

    return {"spread": False}


def get_environment_bacteria_level(area_uuid: str) -> float:
    """Get the bacteria level of an environment.

    Based on presence of dirty personas, animals, and weather.
    0.0 = sterile, 1.0 = very contaminated.
    """
    try:
        db = get_db()

        # Count dirty personas in area

    except Exception as e:
        log.error(f"get_environment_bact failed: {e}")
        return 0.0
    dirty_count = db.fetch_one("""
        SELECT COUNT(*) as cnt FROM ext_sp_persona_location pl
        JOIN ext_shm_hygiene h ON pl.persona_uuid = h.persona_uuid
        WHERE pl.area_uuid = ? AND h.personal_cleanliness < 40
    """, (area_uuid,))

    # Weather affects bacteria
    weather = db.fetch_one("""
        SELECT humidity, temperature, is_raining FROM ext_sp_weather
        WHERE area_uuid = ?
    """, (area_uuid,))

    base = dirty_count["cnt"] * 0.1 if dirty_count else 0.05
    if weather:
        if weather["humidity"] > 70:
            base *= 1.5  # Humid = more bacteria
        if weather["temperature"] > 25:
            base *= 1.3  # Warm = more bacteria
        if weather["temperature"] < 5:
            base *= 0.3  # Cold = less bacteria
        if weather["is_raining"]:
            base *= 0.7  # Rain washes away some

    return min(1.0, base)

