"""
shm_genetics.py — Genetic system for persona health mechanics.

Genetics are derived from the persona's archetype and personality traits,
then stored as multipliers that affect all health subsystems.

Provides functions to read genetics and compute health modifiers.
"""

from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import (
    get_db, ensure_schema
)

log = get_logger()


def get_genetics(persona_uuid: str) -> Optional[dict]:
    """Get full genetic profile for a persona."""
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))


def compute_health_modifiers(persona_uuid: str) -> dict:
    """Compute all health-modifying multipliers from genetics.

    Returns dict with all modifier values as floats.
    """
    genetics = get_genetics(persona_uuid)
    if not genetics:
        return {
            "metabolic_rate": 1.0,
            "immune_potency": 1.0,
            "pain_tolerance": 1.0,
            "healing_factor": 1.0,
            "disease_resistance": 1.0,
            "muscle_growth_rate": 1.0,
            "toxin_resistance": 1.0,
            "blood_efficiency": 1.0,
            "organ_vitality": 1.0,
            "nerve_density": 1.0,
        }

    return {
        "metabolic_rate": genetics["metabolic_rate"],
        "immune_potency": genetics["immune_potency"],
        "pain_tolerance": genetics["pain_tolerance"],
        "healing_factor": genetics["healing_factor"],
        "disease_resistance": genetics["disease_resistance"],
        "muscle_growth_rate": genetics["muscle_growth_rate"],
        "toxin_resistance": genetics["toxin_resistance"],
        "blood_efficiency": genetics["blood_efficiency"],
        "organ_vitality": genetics["organ_vitality"],
        "nerve_density": genetics["nerve_density"],
    }


def get_disease_resistance_chance(persona_uuid: str) -> float:
    """Get the base disease resistance chance (0.0-1.0).

    Higher = more resistant to catching diseases.
    """
    genetics = get_genetics(persona_uuid)
    if not genetics:
        return 0.5
    return 0.3 + (genetics["disease_resistance"] * 0.35)


def get_wound_infection_chance(persona_uuid: str) -> float:
    """Get wound infection chance modifier.

    Lower immune_potency + higher nerve_density = higher infection chance.
    """
    genetics = get_genetics(persona_uuid)
    if not genetics:
        return 0.3
    base = 0.3
    immune_factor = 1.0 / genetics["immune_potency"]
    nerve_factor = genetics["nerve_density"]  # More nerves = more pathways
    return min(0.9, base * immune_factor * nerve_factor)


def get_heal_time_multiplier(persona_uuid: str) -> float:
    """Get healing time multiplier.

    1.0 = normal, <1.0 = faster healing, >1.0 = slower healing.
    """
    genetics = get_genetics(persona_uuid)
    if not genetics:
        return 1.0
    return 1.0 / max(0.1, genetics["healing_factor"])


def get_pain_reduction(persona_uuid: str) -> float:
    """Get pain reduction factor.

    1.0 = normal pain, <1.0 = less pain, >1.0 = more pain.
    """
    genetics = get_genetics(persona_uuid)
    if not genetics:
        return 1.0
    return genetics["nerve_density"] / max(0.1, genetics["pain_tolerance"])
