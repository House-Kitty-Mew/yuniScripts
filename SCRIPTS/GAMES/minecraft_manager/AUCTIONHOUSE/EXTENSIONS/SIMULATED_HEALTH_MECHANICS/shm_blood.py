"""
shm_blood.py — Blood system for persona health mechanics.

Tracks: blood volume, toxicity, oxygen saturation, blood cells,
platelets, glucose, hemoglobin, and circulation efficiency.

Handles bleeding, clotting, detoxification, and blood regeneration.
"""

import random, math
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import (
    get_db, ensure_schema, ALL_BONES
)

log = get_logger()


def get_blood_stats(persona_uuid: str) -> Optional[dict]:
    """Get full blood stats for a persona."""
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_shm_blood WHERE persona_uuid = ?",
        (persona_uuid,))


def get_blood_regeneration(persona_uuid: str) -> Optional[dict]:
    """Get blood regeneration data for a persona."""
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_shm_blood_regeneration WHERE persona_uuid = ?",
        (persona_uuid,))


def cause_bleeding(persona_uuid: str, bleed_rate_ml: float,
                    wound_location: str) -> dict:
    """Cause blood loss from a wound.

    Args:
        persona_uuid: The wounded persona
        bleed_rate_ml: ML of blood lost per tick
        wound_location: Body part location for context

    Returns:
        Dict with blood loss results
    """
    db = get_db()
    blood = get_blood_stats(persona_uuid)
    if not blood:
        return {"error": "no blood data"}

    regen = get_blood_regeneration(persona_uuid)
    platelets_factor = blood["platelets"] / 100.0

    # Clotting: platelets reduce effective bleeding
    clotting_modifier = max(0.1, 1.0 - platelets_factor * 0.5)
    if blood["platelets"] < 30:
        clotting_modifier = 1.0  # Poor clotting

    actual_loss = bleed_rate_ml * clotting_modifier
    new_volume = max(0, blood["blood_volume_ml"] - actual_loss)

    # Blood loss affects oxygen saturation
    loss_pct = 1.0 - (new_volume / blood["max_blood_volume"])
    o2_loss = loss_pct * 30  # Max 30% O2 drop
    new_o2 = max(30, blood["oxygen_saturation"] - o2_loss)

    # Blood loss drains energy (body compensates)
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
        energy_drain = actual_loss * 0.05
        modify_health(persona_uuid, energy=-energy_drain)
    except Exception:
        pass

    db.execute("""
        UPDATE ext_shm_blood SET
            blood_volume_ml = ?,
            oxygen_saturation = ?
        WHERE persona_uuid = ?
    """, (new_volume, new_o2, persona_uuid))

    blood_loss_pct = (blood["blood_volume_ml"] - new_volume) / blood["max_blood_volume"] * 100

    return {
        "blood_lost_ml": round(actual_loss, 1),
        "new_volume_ml": round(new_volume, 1),
        "blood_loss_pct": round(blood_loss_pct, 1),
        "new_o2_saturation": round(new_o2, 1),
        "clotting_factor": round(clotting_modifier, 2),
    }


def process_bleeding_tick(persona_uuid: str) -> dict:
    """Process one tick of bleeding from active wounds.

    Returns cumulative blood loss this tick.
    """
    db = get_db()
    blood = get_blood_stats(persona_uuid)
    if not blood or blood["blood_volume_ml"] <= 0:
        return {"bleeding": False, "total_loss": 0}

    # Get active wounds
    wounds = db.fetch_all("""
        SELECT * FROM ext_sp_wounds
        WHERE owner_uuid = ? AND is_healed = 0 AND bleed_rate > 0
    """, (persona_uuid,))

    total_loss = 0.0
    for wound in wounds:
        bleed_rate = wound["bleed_rate"]
        if wound["is_bandaged"]:
            bleed_rate *= 0.2

        # Convert abstract bleed rate to ml blood loss
        bleed_ml = bleed_rate * 5.0  # Scale factor
        result = cause_bleeding(persona_uuid, bleed_ml, wound["body_part"])
        if "blood_lost_ml" in result:
            total_loss += result["blood_lost_ml"]

    # Check for critical blood loss consequences
    if blood["blood_volume_ml"] > 0:
        loss_pct = (blood["blood_volume_ml"] / blood["max_blood_volume"]) * 100

        severity = "none"
        if loss_pct < 60:
            severity = "critical"
        elif loss_pct < 70:
            severity = "severe"
        elif loss_pct < 85:
            severity = "moderate"
        elif loss_pct < 95:
            severity = "minor"

        if severity == "critical":
            # Massive blood loss → organ failure risk
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
                modify_health(persona_uuid, energy=-15, food=-10, immune=-10)
            except Exception:
                pass

        if loss_pct <= 0:
            # Exsanguination
            db.execute("""
                UPDATE ext_shm_blood SET blood_volume_ml = 0,
                    oxygen_saturation = 0
                WHERE persona_uuid = ?
            """, (persona_uuid,))

    return {
        "bleeding": total_loss > 0,
        "total_loss_ml": round(total_loss, 1),
        "current_volume_ml": round(blood["blood_volume_ml"], 1),
        "volume_pct": round((blood["blood_volume_ml"] / blood["max_blood_volume"]) * 100, 1)
        if blood["max_blood_volume"] > 0 else 0,
    }


def process_blood_regeneration_tick(persona_uuid: str) -> dict:
    """Process natural blood regeneration.

    Blood regenerates using:
    - Iron stores → hemoglobin production
    - B12 stores → RBC production
    - Hydration → plasma volume restoration
    - Food intake → glucose for cell production

    Returns regeneration results.
    """
    db = get_db()
    blood = get_blood_stats(persona_uuid)
    regen = get_blood_regeneration(persona_uuid)
    if not blood or not regen:
        return {"error": "missing data"}

    vol_pct = blood["blood_volume_ml"] / blood["max_blood_volume"]

    # Only regenerate if below max
    if vol_pct >= 1.0:
        return {"regenerating": False, "reason": "full_volume"}

    # Check if health (food/hydration) supports regeneration
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
    except Exception:
        health = None

    food_factor = (health["food"] / 100) if health else 0.5
    hyd_factor = (health["hydration"] / 100) if health else 0.5

    # Base regen rate ml per tick
    base_regen = 5.0  # ~5ml per tick (about 1 unit/hr)
    iron_factor = regen["iron_stores"] / 100.0
    b12_factor = regen["b12_stores"] / 100.0
    regen_rate = regen["regeneration_rate"]

    regen_ml = (base_regen * regen_rate * iron_factor * b12_factor *
                food_factor * hyd_factor * random.uniform(0.8, 1.2))

    new_volume = min(blood["max_blood_volume"], blood["blood_volume_ml"] + regen_ml)

    # Slowly restore oxygen saturation
    new_o2 = min(100, blood["oxygen_saturation"] + regen_ml * 0.05)

    # Consume iron and B12 stores for blood production
    iron_used = regen_ml * 0.02
    b12_used = regen_ml * 0.01
    new_iron = max(0, regen["iron_stores"] - iron_used)
    new_b12 = max(0, regen["b12_stores"] - b12_used)

    # Restore platelets slowly if low
    new_platelets = min(100, blood["platelets"] + random.uniform(0.1, 0.5))

    db.execute("""
        UPDATE ext_shm_blood SET
            blood_volume_ml = ?,
            oxygen_saturation = ?,
            platelets = ?
        WHERE persona_uuid = ?
    """, (new_volume, new_o2, new_platelets, persona_uuid))

    db.execute("""
        UPDATE ext_shm_blood_regeneration SET
            iron_stores = ?,
            b12_stores = ?
        WHERE persona_uuid = ?
    """, (new_iron, new_b12, persona_uuid))

    return {
        "regenerating": True,
        "regen_ml": round(regen_ml, 1),
        "new_volume_ml": round(new_volume, 1),
        "new_o2": round(new_o2, 1),
        "iron_used": round(iron_used, 2),
        "b12_used": round(b12_used, 2),
    }


def add_toxicity(persona_uuid: str, amount: float, source: str = "unknown") -> dict:
    """Add toxicity to a persona's blood.

    Sources: food poisoning, infection, organ failure, environmental toxins.

    Returns new toxicity level.
    """
    db = get_db()
    blood = get_blood_stats(persona_uuid)
    if not blood:
        return {"error": "no blood data"}

    # Genetics-based toxin resistance
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import (
        get_db as shm_db
    )
    genetics = shm_db().fetch_one(
        "SELECT toxin_resistance FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))

    resistance = genetics["toxin_resistance"] if genetics else 1.0
    effective_toxin = amount / resistance

    new_toxicity = min(100, blood["blood_toxicity"] + effective_toxin)

    db.execute("""
        UPDATE ext_shm_blood SET blood_toxicity = ?
        WHERE persona_uuid = ?
    """, (new_toxicity, persona_uuid))

    return {
        "added_toxicity": round(amount, 1),
        "resistance_factor": round(resistance, 2),
        "effective_added": round(effective_toxin, 1),
        "new_toxicity": round(new_toxicity, 1),
    }


def process_detoxification_tick(persona_uuid: str) -> dict:
    """Process natural detoxification through liver and kidneys.

    Liver and kidney health affect detox rate.
    Returns detox results.
    """
    db = get_db()
    blood = get_blood_stats(persona_uuid)
    if not blood or blood["blood_toxicity"] <= 0:
        return {"detox": False}

    # Check organ health
    liver = db.fetch_one(
        "SELECT health FROM ext_shm_anatomy_organs WHERE persona_uuid = ? AND organ_name = 'liver'",
        (persona_uuid,))
    kidneys = db.fetch_one(
        "SELECT health FROM ext_shm_anatomy_organs WHERE persona_uuid = ? AND organ_name = 'kidney_left'",
        (persona_uuid,))

    liver_health = (liver["health"] / 100) if liver else 0.5
    kidney_health = (kidneys["health"] / 100) if kidneys else 0.5

    # Hydration affects kidney function
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
        hyd_factor = (health["hydration"] / 100) if health else 0.5
    except Exception:
        hyd_factor = 0.5

    detox_rate = 0.5 + (liver_health * 3.0) + (kidney_health * 2.0) + (hyd_factor * 1.0)
    detox_amount = detox_rate * random.uniform(0.7, 1.3)

    new_toxicity = max(0, blood["blood_toxicity"] - detox_amount)

    db.execute("""
        UPDATE ext_shm_blood SET blood_toxicity = ?
        WHERE persona_uuid = ?
    """, (new_toxicity, persona_uuid))

    # Toxicity effects
    if blood["blood_toxicity"] > 50:
        # High toxicity damages organs
        if liver:
            new_liver_health = max(10, liver["health"] - blood["blood_toxicity"] * 0.01)
            db.execute("""
                UPDATE ext_shm_anatomy_organs SET health = ?
                WHERE persona_uuid = ? AND organ_name = 'liver'
            """, (new_liver_health, persona_uuid))

    return {
        "detox": True,
        "detox_amount": round(detox_amount, 1),
        "new_toxicity": round(new_toxicity, 1),
        "liver_health_factor": round(liver_health, 2),
        "kidney_factor": round(kidney_health, 2),
    }


def process_nutrient_absorption(persona_uuid: str, food_amount: float,
                                 food_type: str) -> dict:
    """Process nutrient absorption from food.

    Updates iron stores, B12 stores, and glucose levels.

    Args:
        persona_uuid: The persona
        food_amount: Amount of food consumed (0-100)
        food_type: 'meat', 'fish', 'plant', 'grain', 'dairy'

    Returns nutrient changes.
    """
    db = get_db()
    blood = get_blood_stats(persona_uuid)
    regen = get_blood_regeneration(persona_uuid)
    if not blood or not regen:
        return {"error": "missing data"}

    # Food type → nutrient profile
    nutrient_profiles = {
        "meat": {"iron": 0.3, "b12": 0.4, "glucose": 0.2},
        "fish": {"iron": 0.2, "b12": 0.3, "glucose": 0.1},
        "plant": {"iron": 0.1, "b12": 0.0, "glucose": 0.4},
        "grain": {"iron": 0.05, "b12": 0.0, "glucose": 0.5},
        "dairy": {"iron": 0.0, "b12": 0.2, "glucose": 0.3},
    }

    profile = nutrient_profiles.get(food_type, {"iron": 0.1, "b12": 0.1, "glucose": 0.2})

    iron_gain = food_amount * profile["iron"]
    b12_gain = food_amount * profile["b12"]
    glucose_gain = food_amount * profile["glucose"]

    new_iron = min(100, regen["iron_stores"] + iron_gain)
    new_b12 = min(100, regen["b12_stores"] + b12_gain)
    new_glucose = min(500, blood["glucose_mg_dl"] + glucose_gain * 10)

    db.execute("""
        UPDATE ext_shm_blood SET glucose_mg_dl = ?
        WHERE persona_uuid = ?
    """, (new_glucose, persona_uuid))

    db.execute("""
        UPDATE ext_shm_blood_regeneration SET
            iron_stores = ?,
            b12_stores = ?
        WHERE persona_uuid = ?
    """, (new_iron, new_b12, persona_uuid))

    return {
        "iron_gain": round(iron_gain, 1),
        "b12_gain": round(b12_gain, 1),
        "glucose_gain": round(glucose_gain, 1),
        "new_iron": round(new_iron, 1),
        "new_b12": round(new_b12, 1),
    }


def get_blood_loss_effects(blood_stats: dict) -> dict:
    """Get combat/health modifiers based on blood loss.

    Returns dict with strength, agility, endurance, perception penalties.
    """
    try:
        vol_pct = (blood_stats["blood_volume_ml"] / blood_stats["max_blood_volume"]) * 100

    except Exception as e:
        log.error(f"get_blood_loss_effec failed: {e}")
        return {}

    if vol_pct >= 85:
        return {"penalty": 0, "description": "normal"}
    elif vol_pct >= 70:
        return {"penalty": 0.1, "description": "mild_loss"}
    elif vol_pct >= 60:
        return {"penalty": 0.25, "description": "moderate_loss"}
    elif vol_pct >= 50:
        return {"penalty": 0.4, "description": "severe_loss"}
    elif vol_pct >= 40:
        return {"penalty": 0.6, "description": "critical_loss"}
    else:
        return {"penalty": 0.85, "description": "near_death"}


def check_exsanguination(persona_uuid: str) -> bool:
    """Check if persona has died from blood loss.

    Returns True if persona died.
    """
    blood = get_blood_stats(persona_uuid)
    if not blood:
        return False

    vol_pct = (blood["blood_volume_ml"] / blood["max_blood_volume"]) * 100

    if vol_pct < 30 and blood["oxygen_saturation"] < 40:
        # Fatal blood loss
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import (
                modify_health, get_persona_health
            )
            health = get_persona_health(persona_uuid)
            if health and health["alive"]:
                modify_health(persona_uuid, food=-100, hydration=-100,
                            energy=-100, immune=-100)
                log.info("shm_blood",
                         f"Persona {persona_uuid[:8]} died from exsanguination")
                return True
        except Exception:
            pass
    return False

