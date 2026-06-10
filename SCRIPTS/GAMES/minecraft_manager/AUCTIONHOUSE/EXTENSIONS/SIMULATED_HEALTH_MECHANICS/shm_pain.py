"""
shm_pain.py — Pain tracking and healing mechanics.

Pain accumulates from wounds, fractures, infections, and organ damage.
High pain levels debilitate all actions and can lead to shock.

Healing is tracked per-source and requires nutrition, rest, and time.
Pain spikes during active healing (tissue regeneration is painful).
"""

import random, math
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db

log = get_logger()


def add_pain_source(persona_uuid: str, source: str, source_type: str,
                    pain_level: float, duration_ticks: int = 10) -> dict:
    """Register a new pain source.

    Args:
        persona_uuid: The persona
        source: Unique identifier for this pain source
        source_type: 'wound', 'fracture', 'infection', 'organ', 'muscle_strain', 'inflammation'
        pain_level: 0-100 initial pain level
        duration_ticks: How many ticks this pain lasts

    Returns registration result.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Genetics pain tolerance
    genetics = db.fetch_one(
        "SELECT pain_tolerance, nerve_density FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    pain_tolerance = genetics["pain_tolerance"] if genetics else 1.0
    nerve_density = genetics["nerve_density"] if genetics else 1.0

    # Effective pain = base * (nerve_density / pain_tolerance)
    effective_pain = pain_level * (nerve_density / max(0.1, pain_tolerance))

    db.execute("""
        INSERT OR REPLACE INTO ext_shm_pain
        (persona_uuid, pain_source, source_type, pain_level, max_pain,
         duration_ticks, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)
    """, (persona_uuid, source, source_type, effective_pain,
          effective_pain, duration_ticks, now))

    return {
        "source": source,
        "base_pain": round(pain_level, 1),
        "effective_pain": round(effective_pain, 1),
        "pain_tolerance_factor": round(pain_tolerance / nerve_density, 2),
        "duration_ticks": duration_ticks,
    }


def get_total_pain(persona_uuid: str) -> float:
    """Get total active pain for a persona across all sources."""
    db = get_db()
    pains = db.fetch_all("""
        SELECT pain_level FROM ext_shm_pain
        WHERE persona_uuid = ? AND is_active = 1
    """, (persona_uuid,))

    if not pains:
        return 0.0

    total = sum(p["pain_level"] for p in pains)
    # Diminishing returns for stacking pain
    if total > 100:
        total = 100 + (total - 100) * 0.5
    return min(150, total)


def get_active_pains(persona_uuid: str) -> list[dict]:
    """Get all active pain sources for a persona."""
    db = get_db()
    return db.fetch_all("""
        SELECT * FROM ext_shm_pain
        WHERE persona_uuid = ? AND is_active = 1
        ORDER BY pain_level DESC
    """, (persona_uuid,))


def remove_pain_source(persona_uuid: str, source: str) -> bool:
    """Mark a pain source as resolved."""
    db = get_db()
    r = db.execute("""
        UPDATE ext_shm_pain SET is_active = 0, pain_level = 0
        WHERE persona_uuid = ? AND pain_source = ?
    """, (persona_uuid, source))
    return r.rowcount > 0


def process_pain_decay_tick(persona_uuid: str) -> list[dict]:
    """Process one tick of pain decay for all active pains.

    Pain naturally decreases over time, but may spike during healing.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"process_pain_decay_t failed: {e}")
        return []
    pains = get_active_pains(persona_uuid)
    if not pains:
        return []

    genetics = db.fetch_one(
        "SELECT healing_factor FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    healing_factor = genetics["healing_factor"] if genetics else 1.0

    results = []
    for pain in pains:
        remaining = pain["duration_ticks"] - 1

        # Pain decay rate
        decay_base = random.uniform(1, 5)
        decay = decay_base * (pain["pain_level"] / pain["max_pain"]) * 0.5
        decay = max(0.5, decay)

        # Healing spike: when pain is reducing, it may temporarily spike
        healing_spike = 0
        if random.random() < 0.1 and pain["pain_level"] < pain["max_pain"] * 0.7:
            healing_spike = random.uniform(2, 8)  # Tissue regeneration

        new_pain = max(0, pain["pain_level"] - decay + healing_spike)
        new_pain = min(pain["max_pain"], new_pain)

        if remaining <= 0 or new_pain <= 1:
            # Pain source resolved
            db.execute("""
                UPDATE ext_shm_pain SET
                    pain_level = 0,
                    is_active = 0,
                    duration_ticks = 0
                WHERE id = ?
            """, (pain["id"],))
            results.append({
                "source": pain["pain_source"],
                "resolved": True,
                "final_pain": round(pain["pain_level"], 1),
            })
        else:
            db.execute("""
                UPDATE ext_shm_pain SET
                    pain_level = ?,
                    duration_ticks = ?
                WHERE id = ?
            """, (new_pain, remaining, pain["id"]))
            results.append({
                "source": pain["pain_source"],
                "resolved": False,
                "pain_level": round(new_pain, 1),
                "decay": round(decay, 2),
                "healing_spike": round(healing_spike, 2),
            })

    return results


def get_pain_effects(persona_uuid: str) -> dict:
    """Get stat penalties from current pain level.

    Returns dict with debuffs to all stats.
    """
    try:
        total_pain = get_total_pain(persona_uuid)

    except Exception as e:
        log.error(f"get_pain_effects failed: {e}")
        return {}

    if total_pain <= 0:
        return {
            "agility_penalty": 0,
            "strength_penalty": 0,
            "perception_penalty": 0,
            "endurance_penalty": 0,
            "pain_shock_risk": False,
            "consciousness_risk": False,
        }

    effects = {}
    if total_pain <= 20:
        effects = {"agility_penalty": 0.05, "strength_penalty": 0.05,
                    "perception_penalty": 0.05, "endurance_penalty": 0.05}
    elif total_pain <= 50:
        effects = {"agility_penalty": 0.15, "strength_penalty": 0.10,
                    "perception_penalty": 0.10, "endurance_penalty": 0.15}
    elif total_pain <= 80:
        effects = {"agility_penalty": 0.30, "strength_penalty": 0.20,
                    "perception_penalty": 0.15, "endurance_penalty": 0.30}
    elif total_pain <= 100:
        effects = {"agility_penalty": 0.50, "strength_penalty": 0.35,
                    "perception_penalty": 0.25, "endurance_penalty": 0.50}
    else:
        effects = {"agility_penalty": 0.70, "strength_penalty": 0.50,
                    "perception_penalty": 0.40, "endurance_penalty": 0.70}

    effects["pain_shock_risk"] = total_pain > 80
    effects["consciousness_risk"] = total_pain > 100
    effects["total_pain"] = round(total_pain, 1)

    return effects


def process_healing_tick(persona_uuid: str) -> dict:
    """Process natural healing for all wound/injury types.

    Healing requires:
    - Protein from muscles (>30 protein for effective healing)
    - Rest (energy > 40 for fast healing)
    - Time (each wound heals at its own rate)
    - Medical care (bandages, herbs accelerate)

    Weakness: during healing, strength in affected area is reduced 20-50%.
    """
    results = {}

    # Heal bones
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import (
            heal_bone_tick, heal_organ_tick
        )
        bone_healing = heal_bone_tick(persona_uuid)
        if bone_healing:
            results["bones"] = bone_healing

        organ_healing = heal_organ_tick(persona_uuid)
        if organ_healing:
            results["organs"] = organ_healing
    except Exception as e:
        pass

    # Heal muscles
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import heal_muscle_tick
        muscle_healing = heal_muscle_tick(persona_uuid)
        if muscle_healing:
            results["muscles"] = muscle_healing
    except Exception as e:
        pass

    # Process pain decay
    try:
        pain_decay = process_pain_decay_tick(persona_uuid)
        if pain_decay:
            results["pain"] = pain_decay
    except Exception as e:
        pass

    # Log healing progress
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    for category, items in results.items():
        for item in items if isinstance(items, list) else [items]:
            if isinstance(item, dict) and "healed" in item:
                try:
                    db.execute("""
                        INSERT INTO ext_shm_healing_log
                        (persona_uuid, healing_target, target_type, progress_after, tick, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (persona_uuid, item.get("bone") or item.get("organ") or
                          item.get("muscle") or item.get("source", "unknown"),
                          category, 100.0 if item.get("healed") else
                          item.get("progress", 0), 0, now))
                except Exception:
                    pass

    return results

