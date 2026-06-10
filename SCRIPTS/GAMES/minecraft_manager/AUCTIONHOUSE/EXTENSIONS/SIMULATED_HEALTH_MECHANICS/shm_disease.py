"""
shm_disease.py — Disease & infection system for persona health.

Tracks active diseases, infections, and bacteria with full immune
progression. No viruses (too strong for this virtual world).

Diseases progress through stages: incubation → acute → battle → resolution.
Bacteria spread through contact, airborne, and contaminated surfaces.
"""

import random, uuid
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Disease Definitions
# ══════════════════════════════════════════════════════════════════════

DISEASE_DEFS = {
    "food_poisoning": {
        "name": "Food Poisoning",
        "type": "digestive",
        "transmission": "ingestion",
        "severity_max": 3,
        "incubation_min": 1,
        "incubation_max": 4,
        "duration_min": 24,
        "duration_max": 72,
        "virulence_base": 0.4,
        "contagious": False,
        "symptoms": ["vomiting", "diarrhea", "fever", "dehydration"],
        "description": "Caused by contaminated food. Leads to vomiting, diarrhea, and dehydration.",
    },
    "wound_infection": {
        "name": "Wound Infection",
        "type": "infection",
        "transmission": "direct_wound",
        "severity_max": 4,
        "incubation_min": 2,
        "incubation_max": 6,
        "duration_min": 48,
        "duration_max": 336,
        "virulence_base": 0.5,
        "contagious": False,
        "symptoms": ["fever", "pus", "redness", "swelling"],
        "description": "Bacterial infection of an open wound. Can spread systemically.",
    },
    "pneumonia": {
        "name": "Bacterial Pneumonia",
        "type": "respiratory",
        "transmission": "airborne",
        "severity_max": 4,
        "incubation_min": 3,
        "incubation_max": 7,
        "duration_min": 168,
        "duration_max": 504,
        "virulence_base": 0.6,
        "contagious": True,
        "symptoms": ["cough", "fever", "breathing_difficulty", "chest_pain"],
        "description": "Lung infection causing cough, fever, and difficulty breathing.",
    },
    "skin_infection": {
        "name": "Skin Infection",
        "type": "dermatological",
        "transmission": "contact",
        "severity_max": 2,
        "incubation_min": 3,
        "incubation_max": 7,
        "duration_min": 72,
        "duration_max": 168,
        "virulence_base": 0.3,
        "contagious": True,
        "symptoms": ["rash", "itching", "redness", "minor_fever"],
        "description": "Bacterial skin infection from poor hygiene or contact.",
    },
    "urinary_tract": {
        "name": "Urinary Tract Infection",
        "type": "urinary",
        "transmission": "hygiene",
        "severity_max": 2,
        "incubation_min": 2,
        "incubation_max": 5,
        "duration_min": 72,
        "duration_max": 168,
        "virulence_base": 0.3,
        "contagious": False,
        "symptoms": ["painful_urination", "fever", "lower_back_pain"],
        "description": "Bacterial infection of the urinary tract from poor hygiene.",
    },
    "dental_infection": {
        "name": "Dental Infection",
        "type": "oral",
        "transmission": "hygiene",
        "severity_max": 3,
        "incubation_min": 4,
        "incubation_max": 10,
        "duration_min": 72,
        "duration_max": 240,
        "virulence_base": 0.35,
        "contagious": False,
        "symptoms": ["toothache", "fever", "swelling", "jaw_pain"],
        "description": "Tooth abscess from poor oral hygiene. Very painful.",
    },
    "dysentery": {
        "name": "Dysentery",
        "type": "digestive",
        "transmission": "water",
        "severity_max": 3,
        "incubation_min": 2,
        "incubation_max": 5,
        "duration_min": 72,
        "duration_max": 240,
        "virulence_base": 0.5,
        "contagious": True,
        "symptoms": ["diarrhea", "dehydration", "weakness", "fever", "cramps"],
        "description": "Severe diarrhea from contaminated water. Rapidly dehydrates.",
    },
    "gangrene": {
        "name": "Gangrene",
        "type": "necrosis",
        "transmission": "severe_wound",
        "severity_max": 4,
        "incubation_min": 5,
        "incubation_max": 12,
        "duration_min": 120,
        "duration_max": 480,
        "virulence_base": 0.7,
        "contagious": False,
        "symptoms": ["tissue_death", "sepsis_risk", "organ_failure", "necrosis"],
        "description": "Tissue death from severe wound infection. Life-threatening.",
    },
    "sepsis": {
        "name": "Sepsis",
        "type": "systemic",
        "transmission": "bloodstream",
        "severity_max": 5,
        "incubation_min": 2,
        "incubation_max": 4,
        "duration_min": 48,
        "duration_max": 168,
        "virulence_base": 0.9,
        "contagious": False,
        "symptoms": ["organ_failure", "septic_shock", "death_risk",
                     "high_fever", "confusion"],
        "description": "Systemic infection. Organ failure and death risk without treatment.",
    },
    "fungal_infection": {
        "name": "Fungal Infection",
        "type": "dermatological",
        "transmission": "environment",
        "severity_max": 2,
        "incubation_min": 5,
        "incubation_max": 14,
        "duration_min": 168,
        "duration_max": 720,
        "virulence_base": 0.2,
        "contagious": True,
        "symptoms": ["skin_irritation", "itching", "rash", "minor_debilitation"],
        "description": "Slow-growing fungal infection from damp conditions.",
    },
}


def get_active_diseases(persona_uuid: str) -> list[dict]:
    """Get all active diseases for a persona."""
    db = get_db()
    return db.fetch_all(
        "SELECT * FROM ext_shm_diseases WHERE persona_uuid = ? AND is_active = 1",
        (persona_uuid,))


def has_disease(persona_uuid: str, disease_name: str) -> bool:
    """Check if a persona has a specific active disease."""
    db = get_db()
    row = db.fetch_one(
        "SELECT id FROM ext_shm_diseases WHERE persona_uuid = ? AND disease_name = ? AND is_active = 1",
        (persona_uuid, disease_name))
    return row is not None


def expose_to_disease(persona_uuid: str, disease_name: str,
                       source: str = "unknown",
                       force_infection: Optional[bool] = None) -> dict:
    """Expose a persona to a disease source.

    Whether infection takes hold depends on:
    - Disease virulence
    - Persona's immune system (genetics + current health)
    - Hygiene level
    - Pre-existing immunity

    Args:
        persona_uuid: Target persona
        disease_name: Which disease
        source: How they were exposed
        force_infection: Override the infection check

    Returns infection result.
    """
    if disease_name not in DISEASE_DEFS:
        return {"error": f"unknown disease: {disease_name}"}

    if has_disease(persona_uuid, disease_name):
        return {"error": "already infected", "disease": disease_name}

    defs = DISEASE_DEFS[disease_name]
    db = get_db()

    # Get resistance factors
    genetics = db.fetch_one(
        "SELECT immune_potency, disease_resistance FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    immune_potency = genetics["immune_potency"] if genetics else 1.0
    disease_resistance = genetics["disease_resistance"] if genetics else 1.0

    # Get current health
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        health = get_persona_health(persona_uuid)
    except Exception:
        health = None

    food_factor = (health["food"] / 100) if health else 0.5
    energy_factor = (health["energy"] / 100) if health else 0.5
    hygiene_level = 50  # Default

    # Get hygiene level
    hygiene = db.fetch_one(
        "SELECT personal_cleanliness, oral_hygiene FROM ext_shm_hygiene WHERE persona_uuid = ?",
        (persona_uuid,))
    if hygiene:
        hygiene_level = (hygiene["personal_cleanliness"] + hygiene["oral_hygiene"]) / 2

    # Calculate infection probability
    if force_infection is not None:
        infection_roll = force_infection
    else:
        base_prob = defs["virulence_base"]
        immune_factor = 1.0 - (immune_potency * 0.2)
        resistance_factor = 1.0 - (disease_resistance * 0.2)
        health_factor = 1.0 - ((food_factor + energy_factor) / 2 * 0.3)
        hygiene_factor = 1.0 - (hygiene_level / 100 * 0.3)

        infection_roll = random.random() < (
            base_prob * immune_factor * resistance_factor *
            health_factor * hygiene_factor
        )

    if not infection_roll:
        # Resisted
        return {"infected": False, "disease": disease_name, "reason": "immune_resisted"}

    # Infection takes hold
    now = datetime.now(timezone.utc).isoformat()
    incubation = random.randint(defs["incubation_min"], defs["incubation_max"])
    duration = random.randint(defs["duration_min"], defs["duration_max"])
    virulence = defs["virulence_base"] + random.uniform(-0.1, 0.1)
    virulence = max(0.1, min(1.0, virulence))

    db.execute("""
        INSERT INTO ext_shm_diseases
        (persona_uuid, disease_name, disease_type, severity,
         stage, pathogen_count, virulence,
         incubation_ticks, total_incubation,
         duration_ticks, total_duration,
         is_contagious, transmission_mode, source, is_active, started_at)
        VALUES (?, ?, ?, ?, 'incubation', ?, ?, 0, ?, 0, ?, ?, ?, ?, 1, ?)
    """, (persona_uuid, disease_name, defs["type"],
          random.randint(1, defs["severity_max"]),
          virulence * 100, virulence,
          incubation, duration,
          1 if defs["contagious"] else 0,
          defs["transmission"], source, now))

    # Add memory of infection
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import add_memory
        add_memory(persona_uuid, "life_event", detail={
            "event": "infected",
            "disease": disease_name,
            "source": source,
        }, emotional_weight=7)
    except Exception:
        pass

    log.info("shm_disease",
             f"Persona {persona_uuid[:8]} infected with {disease_name} from {source}")

    return {
        "infected": True,
        "disease": disease_name,
        "incubation_ticks": incubation,
        "virulence": round(virulence, 2),
        "duration_ticks": duration,
    }


def process_disease_tick(persona_uuid: str) -> list[dict]:
    """Process one tick of all active diseases for a persona.

    Returns list of disease status updates.
    """
    db = get_db()
    diseases = get_active_diseases(persona_uuid)
    if not diseases:
        return []

    results = []
    for disease in diseases:
        result = _process_single_disease(persona_uuid, disease, db)
        results.append(result)

    return results


def _process_single_disease(persona_uuid: str, disease: dict, db) -> dict:
    """Process one tick of a single disease."""
    defs = DISEASE_DEFS.get(disease["disease_name"], {})
    genetics = db.fetch_one(
        "SELECT immune_potency FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    immune_potency = genetics["immune_potency"] if genetics else 1.0

    stage = disease["stage"]

    if stage == "incubation":
        # Pathogen multiplying, no symptoms yet
        new_incubation = disease["incubation_ticks"] + 1
        pathogen_growth = disease["virulence"] * random.uniform(0.3, 0.8)
        new_pathogen = disease["pathogen_count"] + pathogen_growth

        db.execute("""
            UPDATE ext_shm_diseases SET
                incubation_ticks = ?,
                pathogen_count = ?
            WHERE id = ?
        """, (new_incubation, new_pathogen, disease["id"]))

        if new_incubation >= disease["total_incubation"]:
            # Transition to acute phase
            db.execute("""
                UPDATE ext_shm_diseases SET stage = 'acute'
                WHERE id = ?
            """, (disease["id"],))

            # Trigger immune response
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_immune import (
                    start_immune_response
                )
                start_immune_response(
                    persona_uuid, "disease", disease["disease_name"],
                    disease["pathogen_count"], disease["virulence"]
                )
            except Exception:
                pass

            return {"disease": disease["disease_name"], "stage": "acute",
                    "status": "transitioned"}

        return {"disease": disease["disease_name"], "stage": "incubation",
                "progress": round(new_incubation / disease["total_incubation"] * 100, 1)}

    elif stage == "acute" or stage == "immune_battle":
        # Active battle between pathogen and immune system
        new_duration = disease["duration_ticks"] + 1

        # Get immune response battle state
        immune = db.fetch_one("""
            SELECT * FROM ext_shm_immune_response
            WHERE persona_uuid = ? AND target_id = ? AND is_active = 1
        """, (persona_uuid, disease["disease_name"]))

        if immune:
            # Immune system is fighting (scaled for gradual battle)
            battle_scale = 0.25  # Slower battle pacing
            immune_damage = (immune["immune_strength"] * immune_potency *
                             random.uniform(0.3, 0.8) * battle_scale)

            # Pathogen fights back
            pathogen_damage = (disease["pathogen_count"] * disease["virulence"] *
                               random.uniform(0.2, 0.6) * battle_scale)

            new_pathogen = max(0, disease["pathogen_count"] - immune_damage)
            new_immune_strength = max(0, immune["immune_strength"] - pathogen_damage)

            # Inflammation and fever
            inflammation = immune_damage * 0.1 + pathogen_damage * 0.05

            # Update immune response
            db.execute("""
                UPDATE ext_shm_immune_response SET
                    immune_strength = ?,
                    inflammation = MIN(100, inflammation + ?),
                    fever = MIN(40, fever + ?),
                    battle_tick = battle_tick + 1
                WHERE id = ?
            """, (new_immune_strength, inflammation * 0.3,
                  inflammation * 0.05, immune["id"]))

            # Effect on health stats
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
                energy_cost = immune_damage * 0.05 + pathogen_damage * 0.03
                modify_health(persona_uuid, energy=-energy_cost,
                            food=-energy_cost * 0.5)
            except Exception:
                pass

            db.execute("""
                UPDATE ext_shm_diseases SET
                    pathogen_count = ?,
                    duration_ticks = ?,
                    stage = 'immune_battle'
                WHERE id = ?
            """, (new_pathogen, new_duration, disease["id"]))

            # Check resolution
            if new_pathogen <= 0:
                return _resolve_disease_win(persona_uuid, disease, db)

            if new_immune_strength <= 0:
                return _resolve_disease_progression(persona_uuid, disease, db)

            return {
                "disease": disease["disease_name"],
                "stage": "immune_battle",
                "pathogen_remaining": round(new_pathogen, 1),
                "immune_remaining": round(new_immune_strength, 1),
            }
        else:
            # No immune response triggered, pathogen unchecked
            new_pathogen = disease["pathogen_count"] * (1 + disease["virulence"] * 0.1)

            db.execute("""
                UPDATE ext_shm_diseases SET
                    pathogen_count = ?,
                    duration_ticks = ?
                WHERE id = ?
            """, (new_pathogen, new_duration, disease["id"]))

            return {"disease": disease["disease_name"], "stage": "unchecked",
                    "pathogen_count": round(new_pathogen, 1)}

    return {"disease": disease["disease_name"], "stage": stage}


def _resolve_disease_win(persona_uuid: str, disease: dict, db) -> dict:
    """Immune system won - disease cleared."""
    db.execute("""
        UPDATE ext_shm_diseases SET
            stage = 'resolved',
            is_active = 0,
            pathogen_count = 0
        WHERE id = ?
    """, (disease["id"],))

    # Record immunity (temporary)
    immunity_ticks = random.randint(30, 90)
    db.execute("""
        UPDATE ext_shm_diseases SET immunity_ticks = ?
        WHERE id = ?
    """, (immunity_ticks, disease["id"]))

    # Resolve immune response
    try:
        db.execute("""
            UPDATE ext_shm_immune_response SET
                is_active = 0,
                resolved_at = datetime('now')
            WHERE persona_uuid = ? AND target_id = ? AND is_active = 1
        """, (persona_uuid, disease["disease_name"]))
    except Exception:
        pass

    log.info("shm_disease",
             f"Persona {persona_uuid[:8]} recovered from {disease['disease_name']}")

    return {"disease": disease["disease_name"], "stage": "recovered"}


def _resolve_disease_progression(persona_uuid: str, disease: dict, db) -> dict:
    """Disease won - progresses, may damage organs."""
    # Increase severity
    new_severity = min(5, disease["severity"] + 1)

    # Organ damage based on disease type
    organ_damage_map = {
        "pneumonia": ("lung_left", 15),
        "dysentery": ("intestines", 20),
        "gangrene": ("liver", 25),
        "sepsis": ("heart", 30),
        "food_poisoning": ("stomach", 10),
        "urinary_tract": ("kidney_left", 15),
        "dental_infection": ("stomach", 5),
    }

    if disease["disease_name"] in organ_damage_map:
        organ, damage = organ_damage_map[disease["disease_name"]]
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import damage_organ
            damage_organ(persona_uuid, organ, damage, "infection")
        except Exception:
            pass

    # Check if fatal
    fatal = new_severity >= 5
    if fatal:
        db.execute("""
            UPDATE ext_shm_diseases SET
                stage = 'fatal',
                severity = ?
            WHERE id = ?
        """, (new_severity, disease["id"]))

        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
            modify_health(persona_uuid, food=-50, immune=-50, energy=-50)
        except Exception:
            pass

        log.warn("shm_disease",
                 f"Persona {persona_uuid[:8]} - {disease['disease_name']} became FATAL")

        return {"disease": disease["disease_name"], "stage": "fatal",
                "severity": new_severity}

    db.execute("""
        UPDATE ext_shm_diseases SET severity = ?
        WHERE id = ?
    """, (new_severity, disease["id"]))

    return {"disease": disease["disease_name"], "stage": "worsened",
            "new_severity": new_severity}


def try_spread_disease(persona_uuid: str) -> list[dict]:
    """Try to spread contagious diseases to nearby personas.

    Returns list of spread attempts.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"try_spread_disease failed: {e}")
        return []
    contagious = db.fetch_all("""
        SELECT * FROM ext_shm_diseases
        WHERE persona_uuid = ? AND is_contagious = 1 AND is_active = 1
          AND stage IN ('acute', 'immune_battle')
    """, (persona_uuid,))

    if not contagious:
        return []

    # Find nearby active personas (same area)
    area = db.fetch_one("""
        SELECT area_uuid FROM ext_sp_persona_location WHERE persona_uuid = ?
    """, (persona_uuid,))
    if not area:
        return []

    nearby = db.fetch_all("""
        SELECT persona_uuid FROM ext_sp_persona_location
        WHERE area_uuid = ? AND persona_uuid != ?
    """, (area["area_uuid"], persona_uuid))

    if not nearby:
        return []

    results = []
    for disease in contagious:
        for target in nearby[:5]:  # Max 5 spread attempts
            spread_prob = disease["virulence"] * 0.1
            if random.random() < spread_prob:
                result = expose_to_disease(
                    target["persona_uuid"],
                    disease["disease_name"],
                    source=f"contact_with_{persona_uuid[:8]}"
                )
                if result.get("infected"):
                    # Record spread event
                    now = datetime.now(timezone.utc).isoformat()
                    db.execute("""
                        INSERT INTO ext_shm_disease_spread
                        (source_persona, target_persona, disease_name,
                         transmission_type, exposure_time, proximity_score,
                         infection_succeeded)
                        VALUES (?, ?, ?, 'airborne', ?, ?, 1)
                    """, (persona_uuid, target["persona_uuid"],
                          disease["disease_name"], now,
                          random.uniform(0.3, 0.9)))
                    results.append({
                        "from": persona_uuid[:8],
                        "to": target["persona_uuid"][:8],
                        "disease": disease["disease_name"],
                        "infected": True,
                    })

    return results

