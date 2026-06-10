"""
shm_anatomy.py — Full human anatomy for persona health simulation.

Tracks 206 bones across the complete skeleton, 13 organs, and provides
fracture/damage/healing mechanics for every body part.

Integrates with combat and health systems via bone location mapping.
"""

import random, math
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import (
    get_db, ALL_BONES, ALL_ORGANS, ensure_schema
)

log = get_logger()

# Bone locations → combat body part mapping
_LOCATION_MAP = {
    "head": "head",
    "face": "head",
    "neck": "head",
    "chest": "torso",
    "upper_back": "torso",
    "lower_back": "torso",
    "pelvis": "torso",
    "shoulder": "torso",
    "upper_arm": "left_arm",
    "forearm": "left_arm",
    "wrist": "left_arm",
    "palm": "left_arm",
    "finger": "left_arm",
    "thigh": "left_leg",
    "shin": "left_leg",
    "knee": "left_leg",
    "hip": "torso",
    "heel": "left_leg",
    "ankle": "left_leg",
    "midfoot": "left_leg",
    "forefoot": "left_leg",
    "toe": "left_leg",
}

# Bones that cause critical damage when broken
_CRITICAL_BONES = {
    "frontal_bone", "occipital_bone", "temporal_bone_left", "temporal_bone_right",
    "cervical_vertebra_1_atlas", "cervical_vertebra_2_axis",
    "cervical_vertebra_3", "cervical_vertebra_4",
    "cervical_vertebra_5", "cervical_vertebra_6", "cervical_vertebra_7",
    "sternum",
}


def get_all_bones(persona_uuid: str) -> list[dict]:
    """Get all bones for a persona."""
    db = get_db()
    return db.fetch_all(
        "SELECT * FROM ext_shm_anatomy_bones WHERE persona_uuid = ? ORDER BY bone_group",
        (persona_uuid,))


def get_fractured_bones(persona_uuid: str) -> list[dict]:
    """Get all fractured bones for a persona."""
    db = get_db()
    return db.fetch_all(
        "SELECT * FROM ext_shm_anatomy_bones WHERE persona_uuid = ? AND fractured = 1",
        (persona_uuid,))


def fracture_bone(persona_uuid: str, bone_name: str,
                   force_level: float = 1.0) -> dict:
    """Fracture a specific bone.

    Args:
        persona_uuid: The persona
        bone_name: Which bone to fracture
        force_level: 0.0-2.0, how hard the impact was

    Returns fracture results.
    """
    db = get_db()
    bone = db.fetch_one(
        "SELECT * FROM ext_shm_anatomy_bones WHERE persona_uuid = ? AND bone_name = ?",
        (persona_uuid, bone_name))
    if not bone:
        return {"error": f"bone not found: {bone_name}"}

    if bone["fractured"]:
        return {"error": "already fractured", "bone": bone_name}

    # Determine if fracture occurs based on force
    fracture_threshold = 0.5 + random.random() * 0.5
    if force_level < fracture_threshold:
        # Stress fracture (hairline)
        severity = 1
        pain = 15 + force_level * 20
    else:
        # Complete fracture
        severity = 2 if force_level < 1.5 else 3
        pain = 30 + force_level * 30

    # Check if critical bone
    is_critical = bone_name in _CRITICAL_BONES
    if is_critical:
        pain *= 1.5

    db.execute("""
        UPDATE ext_shm_anatomy_bones SET
            fractured = 1,
            healing_progress = 0.0,
            pain_contribution = ?
        WHERE persona_uuid = ? AND bone_name = ?
    """, (pain, persona_uuid, bone_name))

    # Register pain source
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
            add_pain_source
        )
        add_pain_source(persona_uuid, f"fracture_{bone_name}",
                        "fracture", pain, duration_ticks=72)
    except Exception:
        pass

    # Critical bone fractures cause immediate health effects
    if is_critical:
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
            modify_health(persona_uuid, energy=-20, immune=-10)
        except Exception:
            pass

    location = bone["location"]
    combat_part = _LOCATION_MAP.get(location, "torso")

    log.info("shm_anatomy",
             f"Fracture: {persona_uuid[:8]} broke {bone_name} ({combat_part}, severity={severity})")

    return {
        "bone": bone_name,
        "bone_group": bone["bone_group"],
        "location": location,
        "combat_part": combat_part,
        "severity": severity,
        "is_critical": is_critical,
        "pain_level": round(pain, 1),
    }


def fracture_from_impact(persona_uuid: str, body_part: str,
                          impact_force: float = 1.0) -> list[dict]:
    """Fracture bone(s) from a combat impact on a body part.

    Picks appropriate bones based on the body part hit.

    Args:
        persona_uuid: The persona
        body_part: 'head', 'torso', 'left_arm', 'right_arm', 'left_leg', 'right_leg'
        impact_force: 0.0-2.0

    Returns list of fracture results.
    """
    # Map body part to likely bones
    part_bones = {
        "head": ["frontal_bone", "parietal_bone_left", "parietal_bone_right",
                 "occipital_bone", "mandible", "nasal_bone_left", "nasal_bone_right",
                 "zygomatic_bone_left", "zygomatic_bone_right"],
        "torso": ["sternum", "rib_3_left", "rib_3_right", "rib_4_left", "rib_4_right",
                  "rib_5_left", "rib_5_right", "rib_6_left", "rib_6_right",
                  "lumbar_vertebra_3", "lumbar_vertebra_4", "clavicle_left", "clavicle_right",
                  "hip_bone_left", "hip_bone_right"],
        "left_arm": ["humerus_left", "radius_left", "ulna_left",
                     "carpal_scaphoid_left", "metacarpal_3_left",
                     "proximal_phalanx_3_left"],
        "right_arm": ["humerus_right", "radius_right", "ulna_right",
                      "carpal_scaphoid_right", "metacarpal_3_right",
                      "proximal_phalanx_3_right"],
        "left_leg": ["femur_left", "tibia_left", "fibula_left", "patella_left",
                     "calcaneus_left", "metatarsal_3_left"],
        "right_leg": ["femur_right", "tibia_right", "fibula_right", "patella_right",
                      "calcaneus_right", "metatarsal_3_right"],
    }

    bones_to_check = part_bones.get(body_part, ["rib_5_left", "rib_5_right"])
    results = []

    for bone_name in bones_to_check:
        # Roll for fracture chance based on force
        fracture_chance = impact_force * 0.15  # 15% per bone at force 1.0
        if random.random() < fracture_chance:
            result = fracture_bone(persona_uuid, bone_name, impact_force)
            if "error" not in result:
                results.append(result)

    return results


def heal_bone_tick(persona_uuid: str) -> list[dict]:
    """Process natural bone healing for all fractured bones.

    Healing requires:
    - Protein (from muscles)
    - Calcium (from food intake, tracked via health food stat)
    - Energy (rest)
    - Time (3-6 ticks for simple, more for complex)

    Returns list of healing results.
    """
    db = get_db()
    fractured = get_fractured_bones(persona_uuid)
    if not fractured:
        return []

    # Get genetics for healing factor
    genetics = db.fetch_one(
        "SELECT healing_factor FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    healing_factor = genetics["healing_factor"] if genetics else 1.0

    # Get health stats for protein/energy availability
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
    except Exception:
        health = None

    food_factor = (health["food"] / 100) if health else 0.5
    energy_factor = (health["energy"] / 100) if health else 0.5

    # Get muscle protein level
    protein_levels = db.fetch_all(
        "SELECT protein_level FROM ext_shm_muscles WHERE persona_uuid = ?",
        (persona_uuid,))
    avg_protein = sum(r["protein_level"] for r in protein_levels) / len(protein_levels) if protein_levels else 50

    results = []
    for bone in fractured:
        # Base healing rate per tick
        base_heal = 1.0 + random.uniform(0.5, 1.5)
        heal_amount = (base_heal * healing_factor * food_factor *
                       energy_factor * (avg_protein / 100))

        new_progress = min(100, bone["healing_progress"] + heal_amount)

        # Consume protein for bone healing
        for r in protein_levels:
            new_protein = max(0, r["protein_level"] - heal_amount * 0.1)
            db.execute("""
                UPDATE ext_shm_muscles SET protein_level = ?
                WHERE persona_uuid = ? AND protein_level = ?
            """, (new_protein, persona_uuid, r["protein_level"]))

        db.execute("""
            UPDATE ext_shm_anatomy_bones SET
                healing_progress = ?,
                pain_contribution = MAX(0, pain_contribution - ?)
            WHERE id = ?
        """, (new_progress, heal_amount * 0.5, bone["id"]))

        # Bone has healed
        if new_progress >= 100:
            pain_residual = bone["pain_contribution"] * 0.3  # 30% residual pain
            db.execute("""
                UPDATE ext_shm_anatomy_bones SET
                    fractured = 0,
                    healing_progress = 0.0,
                    pain_contribution = ?
                WHERE id = ?
            """, (pain_residual, bone["id"]))

            # Log healing
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
                    remove_pain_source
                )
                remove_pain_source(persona_uuid, f"fracture_{bone['bone_name']}")
            except Exception:
                pass

            log.info("shm_anatomy",
                     f"Bone healed: {persona_uuid[:8]} - {bone['bone_name']}")

            results.append({
                "bone": bone["bone_name"],
                "healed": True,
                "pain_residual": round(pain_residual, 1),
            })
        else:
            results.append({
                "bone": bone["bone_name"],
                "healed": False,
                "progress": round(new_progress, 1),
                "heal_amount": round(heal_amount, 1),
            })

    return results


def get_organ(persona_uuid: str, organ_name: str) -> Optional[dict]:
    """Get an organ's health data."""
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_shm_anatomy_organs WHERE persona_uuid = ? AND organ_name = ?",
        (persona_uuid, organ_name))


def get_all_organs(persona_uuid: str) -> list[dict]:
    """Get all organs for a persona."""
    db = get_db()
    return db.fetch_all(
        "SELECT * FROM ext_shm_anatomy_organs WHERE persona_uuid = ?",
        (persona_uuid,))


def damage_organ(persona_uuid: str, organ_name: str, damage: float,
                 damage_type: str = "trauma") -> dict:
    """Damage an organ.

    Args:
        persona_uuid: The persona
        organ_name: Which organ
        damage: Amount of damage (0-100)
        damage_type: 'trauma', 'toxin', 'infection', 'autoimmune', 'ischemia'

    Returns organ damage result.
    """
    db = get_db()
    organ = get_organ(persona_uuid, organ_name)
    if not organ:
        return {"error": f"organ not found: {organ_name}"}

    # Organ vitality genetics
    genetics = db.fetch_one(
        "SELECT organ_vitality FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    vitality = genetics["organ_vitality"] if genetics else 1.0

    effective_damage = damage / vitality
    new_health = max(0, organ["health"] - effective_damage)

    db.execute("""
        UPDATE ext_shm_anatomy_organs SET
            health = ?,
            is_damaged = ?,
            damage_type = ?
        WHERE persona_uuid = ? AND organ_name = ?
    """, (new_health, 1 if new_health < 100 else 0, damage_type,
          persona_uuid, organ_name))

    # Organ failure check
    if new_health <= 0:
        log.warn("shm_anatomy",
                 f"Organ failure: {persona_uuid[:8]} - {organ_name}")

        # Critical organ failure can cause death
        critical_organs = ["brain", "heart", "liver", "lung_left", "lung_right"]
        if organ_name in critical_organs:
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
                modify_health(persona_uuid, food=-50, energy=-50, immune=-50)
            except Exception:
                pass

    return {
        "organ": organ_name,
        "damage_dealt": round(effective_damage, 1),
        "new_health": round(new_health, 1),
        "organ_failed": new_health <= 0,
        "damage_type": damage_type,
    }


def heal_organ_tick(persona_uuid: str) -> list[dict]:
    """Process natural organ healing."""
    db = get_db()
    organs = get_all_organs(persona_uuid)
    genetics = db.fetch_one(
        "SELECT healing_factor, organ_vitality FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    healing_factor = genetics["healing_factor"] if genetics else 1.0

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
    except Exception:
        health = None
    rest_factor = (health["energy"] / 100) if health else 0.5

    results = []
    for organ in organs:
        if organ["health"] >= 100:
            continue

        # Liver and kidneys heal faster, brain slower
        organ_heal_rates = {
            "liver": 2.0, "kidney_left": 1.5, "kidney_right": 1.5,
            "brain": 0.3, "heart": 0.5,
        }
        base_rate = organ_heal_rates.get(organ["organ_name"], 1.0)

        heal = (base_rate * healing_factor * rest_factor *
                random.uniform(0.5, 1.5))

        new_health = min(100, organ["health"] + heal)

        db.execute("""
            UPDATE ext_shm_anatomy_organs SET
                health = ?,
                is_damaged = CASE WHEN ? < 100 THEN 1 ELSE 0 END
            WHERE id = ?
        """, (new_health, new_health, organ["id"]))

        if new_health >= 100:
            results.append({
                "organ": organ["organ_name"],
                "healed": True,
            })

    return results


def get_fracture_pain(persona_uuid: str) -> float:
    """Get total pain contribution from all fractures."""
    db = get_db()
    bones = get_fractured_bones(persona_uuid)
    return sum(b["pain_contribution"] for b in bones)


def get_location_strength_penalty(persona_uuid: str, limb: str) -> float:
    """Get strength penalty for a limb based on fractures.

    Args:
        persona_uuid: The persona
        limb: 'head', 'torso', 'left_arm', 'right_arm', 'left_leg', 'right_leg'

    Returns penalty multiplier (0.0 = no penalty, 1.0 = full strength).
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"get_location_strengt failed: {e}")
        return 0.0
    location = limb
    if limb == "left_arm":
        location = "left_arm"
    elif limb == "right_arm":
        location = "right_arm"
    elif limb == "left_leg":
        location = "left_leg"
    elif limb == "right_leg":
        location = "right_leg"

    part_locations = {
        "head": "head",
        "torso": "torso",
        "left_arm": "left_arm",
        "right_arm": "right_arm",
        "left_leg": "left_leg",
        "right_leg": "right_leg",
    }

    # Map limb to locations
    loc_to_limb = {
        "head": "head", "face": "head", "neck": "head",
        "chest": "torso", "upper_back": "torso", "lower_back": "torso",
        "pelvis": "torso", "hip": "torso", "shoulder": "torso",
        "upper_arm": limb, "forearm": limb, "wrist": limb,
        "palm": limb, "finger": limb,
        "thigh": limb, "shin": limb, "knee": limb,
        "heel": limb, "ankle": limb, "midfoot": limb,
        "forefoot": limb, "toe": limb,
    }

    relevant_locations = set()
    for loc, mapped_limb in loc_to_limb.items():
        if mapped_limb == limb:
            relevant_locations.add(loc)

    if not relevant_locations:
        return 1.0  # No penalty

    placeholders = ",".join("?" * len(relevant_locations))
    fractures = db.fetch_all(f"""
        SELECT COUNT(*) as cnt FROM ext_shm_anatomy_bones
        WHERE persona_uuid = ? AND fractured = 1 AND location IN ({placeholders})
    """, (persona_uuid,) + tuple(relevant_locations))

    fracture_count = fractures[0]["cnt"] if fractures else 0
    if fracture_count == 0:
        return 1.0

    # Each fracture reduces strength by ~15%
    penalty = max(0.1, 1.0 - (fracture_count * 0.15))
    return round(penalty, 2)

