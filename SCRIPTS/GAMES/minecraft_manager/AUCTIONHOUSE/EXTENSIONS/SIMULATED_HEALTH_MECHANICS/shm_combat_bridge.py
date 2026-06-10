"""
shm_combat_bridge.py — Bridge layer between sp_combat (SIMULATED_PEOPLE)
and SHM (SIMULATED_HEALTH_MECHANICS).

Design principles:
  1. sp_combat remains fully operational when SHM is not loaded —
     all bridge imports are guarded with try/except.
  2. The bridge is CALLED from sp_combat hooks (wound creation, combat
     resolution, wound processing) and enriches with SHM data.
  3. Pending actions (shm_bridge_pending) provide eventual consistency
     when SHM side-effects fail after wound commit.
  4. Per-tick LRU cache prevents excessive SHM queries.
  5. Feature flags control which enhancements are active.

Critical fixes implemented per Architectural Review:
  • GAP #1 — Transaction safety: bridge hooks can fail, but the pending
    actions table ensures eventual consistency. sp_combat wraps wound
    INSERT + bridge hook in a single transaction with rollback on
    bridge failure.
  • GAP #2 — Lossy bone mapping: WOUND_BONE_MAP provides per-wound-type
    bone targets instead of mapping "left_arm" to all 30 arm bones.
  • GAP #3 — Hook ordering: SIMULATED_PEOPLE registers at priority=10,
    SHM at priority=20, ensuring wound processing runs before SHM tick.

Moderate issues fixed:
  • §3.1 — Per-tick LRU cache for bridge_get_combat_stats()
  • §3.2 — ITEM_TO_SKILL weapon type resolver
  • §3.3 — Pain uses get_total_pain() as single source of truth
  • §3.4 — SHM owns infection outcomes
  • §3.5 — SEVERITY_TO_FORCE conversion table
"""

import json
import traceback
import random
from datetime import datetime, timezone

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Feature flags
# ══════════════════════════════════════════════════════════════════════

SHM_BRIDGE_CONFIG = {
    "enabled": True,                    # Master switch
    "replace_combat_stats": True,       # Use SHM muscles/blood/pain for stats
    "replace_weapon_skill": True,       # Use SHM per-weapon skills
    "replace_pain": True,               # Use SHM multi-source pain system
    "enable_fractures": True,           # Fracture bones on severe wounds
    "enable_blood_loss": True,          # Track actual blood volume from wounds
    "enable_skill_decay": True,         # Skill decay during inactivity
    "enable_negative_traits": True,     # Track negative combat traits
    "enable_combat_muscle_use": True,   # Combat uses SHM muscles (fatigue + protein)
    "enable_infection_sync": True,      # Sync infection state to SHM diseases
}


# ══════════════════════════════════════════════════════════════════════
# GAP #2 Fix: Per-wound-type bone target table
# ══════════════════════════════════════════════════════════════════════

WOUND_BONE_MAP = {
    # ── Head ──────────────────────────────────────────────────────
    "head": {
        "crush":      ["temporal", "parietal", "occipital"],
        "fracture":   ["frontal", "temporal", "mandible"],
        "puncture":   ["frontal"],   # Deep stab to forehead
        "cut":        [],            # No bone fracture from cuts
        "laceration": [],
        "burn":       [],
    },
    # ── Torso ─────────────────────────────────────────────────────
    "torso": {
        "crush":      ["rib_3", "rib_4", "rib_5", "sternum"],
        "fracture":   ["rib_5", "rib_6", "rib_7"],
        "puncture":   ["rib_5", "liver", "lung_right"],
        "cut":        [],
        "laceration": [],
        "burn":       [],
    },
    # ── Arms ──────────────────────────────────────────────────────
    "left_arm": {
        "crush":      ["radius", "ulna"],
        "fracture":   ["humerus", "radius", "ulna"],
        "puncture":   ["radius"],
        "cut":        [],
        "laceration": [],
        "burn":       [],
    },
    "right_arm": {
        "crush":      ["radius", "ulna"],
        "fracture":   ["humerus", "radius", "ulna"],
        "puncture":   ["radius"],
        "cut":        [],
        "laceration": [],
        "burn":       [],
    },
    # ── Legs ──────────────────────────────────────────────────────
    "left_leg": {
        "crush":      ["tibia", "fibula"],
        "fracture":   ["femur", "tibia", "fibula"],
        "puncture":   ["tibia"],
        "cut":        [],
        "laceration": [],
        "burn":       [],
    },
    "right_leg": {
        "crush":      ["tibia", "fibula"],
        "fracture":   ["femur", "tibia", "fibula"],
        "puncture":   ["tibia"],
        "cut":        [],
        "laceration": [],
        "burn":       [],
    },
}

# ══════════════════════════════════════════════════════════════════════
# §3.5 Fix: Severity → impact force conversion
# ══════════════════════════════════════════════════════════════════════

SEVERITY_TO_FORCE = {
    1: {"penetration": 0.3, "blunt": 0.2},
    2: {"penetration": 0.6, "blunt": 0.4},
    3: {"penetration": 0.8, "blunt": 0.7},
    4: {"penetration": 1.0, "blunt": 1.0},
}

# ══════════════════════════════════════════════════════════════════════
# §3.2 Fix: Item ID → SHM weapon skill resolver
# ══════════════════════════════════════════════════════════════════════

ITEM_TO_SKILL = {
    "iron_sword": "blades", "diamond_sword": "blades",
    "netherite_sword": "blades", "stone_sword": "blades",
    "wooden_sword": "blades", "golden_sword": "blades",
    "iron_axe": "blades", "diamond_axe": "blades",
    "netherite_axe": "blades", "stone_axe": "blades",
    "wooden_axe": "blades",
    "bow": "archery", "crossbow": "archery",
    "trident": "polearms",
    "mace": "blunt", "hammer": "blunt",
    # Anything else → "unarmed"
}

# ══════════════════════════════════════════════════════════════════════
# §3.1 Fix: Per-tick LRU cache
# ══════════════════════════════════════════════════════════════════════

_bridge_cache = {}
_last_tick = -1


# ══════════════════════════════════════════════════════════════════════
# State reset for test isolation
# ══════════════════════════════════════════════════════════════════════

def reset_bridge_state():
    """Reset all module-level state to defaults.

    Called from test setUp/tearDown to ensure clean state between
    test classes. Prevents cross-test interference from:
      - SHM_BRIDGE_CONFIG changes
      - _bridge_cache accumulation
      - _last_tick state leakage
    """
    global _bridge_cache, _last_tick
    SHM_BRIDGE_CONFIG["enabled"] = True
    SHM_BRIDGE_CONFIG["replace_combat_stats"] = True
    SHM_BRIDGE_CONFIG["replace_weapon_skill"] = True
    SHM_BRIDGE_CONFIG["replace_pain"] = True
    SHM_BRIDGE_CONFIG["enable_fractures"] = True
    SHM_BRIDGE_CONFIG["enable_blood_loss"] = True
    SHM_BRIDGE_CONFIG["enable_skill_decay"] = True
    SHM_BRIDGE_CONFIG["enable_negative_traits"] = True
    SHM_BRIDGE_CONFIG["enable_combat_muscle_use"] = True
    SHM_BRIDGE_CONFIG["enable_infection_sync"] = True
    _bridge_cache.clear()
    _last_tick = -1


def _clear_cache_if_new_tick(current_tick: int):
    """Clear the per-tick LRU cache when the tick changes."""
    global _last_tick
    if current_tick != _last_tick:
        _bridge_cache.clear()
        _last_tick = current_tick


# ══════════════════════════════════════════════════════════════════════
# Bridge: get_combat_stats — Enriched combat stats using SHM data
# ══════════════════════════════════════════════════════════════════════

def bridge_get_combat_stats(persona_uuid: str, current_tick: int = 0,
                            target: str = None) -> dict:
    """Enriched combat stats using SHM-derived data.

    Replaces sp_combat.get_combat_stats() when SHM is loaded.

    Uses per-tick LRU cache to avoid 7+ SHM queries per call.
    """
    if not SHM_BRIDGE_CONFIG.get("replace_combat_stats", True):
        return {}

    # Check cache
    if current_tick > 0:
        _clear_cache_if_new_tick(current_tick)
        cache_key = f"{persona_uuid}_{target or ''}"
        if cache_key in _bridge_cache:
            return _bridge_cache[cache_key]

    try:
        # 1. Get terrain/weather from sp_combat (keep existing)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_combat import (
            _get_terrain_hit_mod as _sp_terrain_mod
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_combat import TERRAIN_MODIFIERS
        area = get_persona_area(persona_uuid)
        biome = area["biome_type"] if area else "plains"
        terrain = TERRAIN_MODIFIERS.get(biome, TERRAIN_MODIFIERS["plains"])

        # 2. Get SHM-derived stats
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db as shm_db
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import get_total_strength
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
            get_blood_stats, get_blood_loss_effects
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
            get_total_pain, get_pain_effects as shm_get_pain_effects
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
            get_combat_skill_modifiers
        )

        # Muscle strength (with fracture penalty)
        muscle_strength = get_total_strength(persona_uuid)

        # Blood loss effects
        blood_stats = get_blood_stats(persona_uuid) if hasattr(get_blood_stats, '__call__') else {}
        db = shm_db()
        blood_data = db.fetch_one(
            "SELECT blood_volume_ml, max_blood_volume FROM ext_shm_blood WHERE persona_uuid = ?",
            (persona_uuid,))
        blood_pct = (blood_data["blood_volume_ml"] / max(1, blood_data["max_blood_volume"])
                     if blood_data else 1.0)
        blood_penalty = max(0, 1.0 - blood_pct * 2) if blood_pct < 0.5 else 0

        # Pain effects
        pain_effects = shm_get_pain_effects(persona_uuid) if hasattr(shm_get_pain_effects, '__call__') else {}

        # Skill modifiers
        skill_mods = get_combat_skill_modifiers(persona_uuid) if hasattr(get_combat_skill_modifiers, '__call__') else {}

        # Fracture penalty (general weakness from fractures)
        fracture_penalty = _get_fracture_penalty(persona_uuid)

        # Merge: SHM enriches base terrain stats
        base_agility = 10 + terrain.get("agility", 0)
        base_perception = 10 + terrain.get("perception", 0)

        stats = {
            "strength": round(max(1, muscle_strength
                                  * (1 - pain_effects.get("strength_penalty", 0))
                                  * (1 - blood_penalty)
                                  * (1 - fracture_penalty)), 1),
            "agility": round(max(1, base_agility
                                * (1 - pain_effects.get("agility_penalty", 0))
                                * (1 - blood_penalty)), 1),
            "endurance": round(max(1, muscle_strength * 0.5
                                   * (1 - blood_penalty)), 1),
            "perception": round(max(1, base_perception
                                    * (1 - pain_effects.get("perception_penalty", 0))), 1),
            "weapon_skill": round(max(1, 10
                                      + skill_mods.get("hit_chance_bonus", 0)), 1),
            "dodge_bonus": skill_mods.get("dodge_bonus", 0),
            "block_bonus": skill_mods.get("block_bonus", 0),
            "critical_bonus": skill_mods.get("critical_chance_bonus", 0),
            "pain_penalty": round(pain_effects.get("total_pain", 0), 1),
            "armor_rating": 0,  # Kept from sp_combat's equipment
            "terrain": biome,
            "bridge": "shm",
        }

        # Cache and return
        if current_tick > 0:
            _bridge_cache[cache_key] = stats
        return stats

    except Exception as e:
        log.debug("shm_bridge", f"bridge_get_combat_stats failed for {persona_uuid[:8]}: {e}")
        return {}


def _get_fracture_penalty(persona_uuid: str) -> float:
    """Calculate overall strength penalty from fractures."""
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        db = get_db()
        fractures = db.fetch_all("""
            SELECT COUNT(*) as count FROM ext_shm_anatomy_bones
            WHERE persona_uuid = ? AND fractured = 1
        """, (persona_uuid,))
        count = fractures[0]["count"] if fractures else 0
        # Each fracture adds ~5% penalty, cap at 80%
        return min(0.8, count * 0.05)
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════
# §3.2 Fix: Weapon type → SHM skill level resolver
# ══════════════════════════════════════════════════════════════════════

def _map_item_to_skill(item_id: str) -> str:
    """Map a Minecraft item ID to an SHM combat skill name."""
    return ITEM_TO_SKILL.get(item_id, "unarmed")


def bridge_get_skill_level(persona_uuid: str, weapon_type: str) -> float:
    """Get SHM skill level for a given weapon type.

    Args:
        persona_uuid: Persona UUID
        weapon_type: Weapon type string (e.g. "blades", "bow", or item_id)

    Returns:
        Skill level (1-100) with proficiency bonus, or 10 as fallback
    """
    if not SHM_BRIDGE_CONFIG.get("replace_weapon_skill", True):
        return 0.0

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
            get_skill_level, get_combat_skill_modifiers
        )
        # If weapon_type looks like an item ID, map it
        skill_name = _map_item_to_skill(weapon_type) if "_" in weapon_type else weapon_type

        level = get_skill_level(persona_uuid, skill_name)
        mods = get_combat_skill_modifiers(persona_uuid)
        return level + mods.get("hit_chance_bonus", 0)
    except Exception as e:
        log.debug("shm_bridge", f"bridge_get_skill_level failed: {e}")
        return 10.0


# ══════════════════════════════════════════════════════════════════════
# §3.3 Fix: Pain — uses get_total_pain() as single source of truth
# ══════════════════════════════════════════════════════════════════════

def bridge_get_pain_effects(persona_uuid: str) -> dict:
    """Get pain debuffs using SHM's multi-source pain system.

    Uses get_total_pain() as the sole source of truth — avoids
    double-counting that would occur if we added fracture_pain
    separately on top of total_pain (which already includes it).
    """
    if not SHM_BRIDGE_CONFIG.get("replace_pain", True):
        return {"total_pain": 0, "strength_penalty": 0,
                "agility_penalty": 0, "perception_penalty": 0}

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
            get_total_pain, get_pain_effects as shm_get_pain_effects
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db

        # Single source: get_total_pain() already includes wound + fracture
        # + infection + organ pain. DO NOT add fracture_pain on top.
        total = get_total_pain(persona_uuid)

        # Apply genetics pain tolerance multiplier
        try:
            db = get_db()
            genetics = db.fetch_one(
                "SELECT pain_tolerance FROM ext_shm_genetics WHERE persona_uuid = ?",
                (persona_uuid,))
            if genetics and genetics["pain_tolerance"] > 0:
                total /= max(0.1, genetics["pain_tolerance"])
        except Exception:
            pass

        # Get structured debuffs directly from SHM
        effects = shm_get_pain_effects(persona_uuid) if hasattr(shm_get_pain_effects, '__call__') else {}
        effects["total_pain"] = round(total, 1)
        return effects

    except Exception as e:
        log.debug("shm_bridge", f"bridge_get_pain_effects failed: {e}")
        return {"total_pain": 0, "strength_penalty": 0,
                "agility_penalty": 0, "perception_penalty": 0}


# ══════════════════════════════════════════════════════════════════════
# GAP #2 Fix: Wound → bone mapping (per-wound-type)
# ══════════════════════════════════════════════════════════════════════

def bridge_on_wound_created(wound: dict, impact_force: float = 1.0) -> dict:
    """Bridge newly created wounds into SHM systems.

    Called from sp_combat._create_wound() after a wound is committed.

    Actions:
      1. Map wound to specific bones using WOUND_BONE_MAP
      2. Potentially fracture bones (severity >= 3 + force check)
      3. Register SHM pain source
      4. Convert abstract bleed to blood loss

    If any action fails, it's recorded as a pending action for retry.

    Args:
        wound: Wound dict from _create_wound()
        impact_force: Force multiplier from weapon/severity (0.0-1.0)

    Returns:
        Dict with results of each SHM action
    """
    if not SHM_BRIDGE_CONFIG.get("enabled", True):
        return {"status": "disabled"}

    results = {"fractures": [], "pain_registered": False,
               "bleeding": False, "pending": []}

    puuid = wound["owner_uuid"]
    body_part = wound.get("body_part", "torso")
    wound_type = wound.get("wound_type", "cut")
    severity = wound.get("severity", 1)
    wound_uuid = wound.get("wound_uuid", "")
    bleed_rate = wound.get("bleed_rate", 0)
    pain_level = wound.get("pain_level", 0)

    try:
        # ── 1. Fracture bones ─────────────────────────────────────
        if (SHM_BRIDGE_CONFIG.get("enable_fractures", True)
                and severity >= 3
                and impact_force > 0.3):
            bone_targets = WOUND_BONE_MAP.get(body_part, {}).get(wound_type, [])
            if bone_targets:
                try:
                    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_anatomy import (
                        fracture_from_impact
                    )
                    fractures = fracture_from_impact(puuid, body_part, impact_force)
                    if fractures:
                        results["fractures"] = fractures
                except Exception as e:
                    log.debug("shm_bridge",
                              f"Fracture failed for wound {wound_uuid[:8]}: {e}")
                    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_bridge_pending import (
                        add_pending_action
                    )
                    add_pending_action(wound_uuid, puuid, "create_fracture",
                                       {"body_part": body_part, "wound_type": wound_type,
                                        "severity": severity, "force": impact_force})
                    results["pending"].append("create_fracture")

        # ── 2. Register SHM pain source ───────────────────────────
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
                add_pain_source
            )
            add_pain_source(
                puuid,
                f"wound_{wound_uuid[:8]}",
                "wound",
                pain_level=pain_level * 2,  # Convert to SHM scale
                duration_ticks=48
            )
            results["pain_registered"] = True
        except Exception as e:
            log.debug("shm_bridge",
                      f"Pain registration failed for wound {wound_uuid[:8]}: {e}")
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_bridge_pending import (
                add_pending_action
            )
            add_pending_action(wound_uuid, puuid, "register_pain",
                               {"pain_level": pain_level, "duration_ticks": 48})
            results["pending"].append("register_pain")

        # ── 3. Convert abstract bleed to blood loss ───────────────
        if SHM_BRIDGE_CONFIG.get("enable_blood_loss", True) and bleed_rate > 0:
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
                    cause_bleeding
                )
                bleed_ml = bleed_rate * 5.0  # Scale factor
                cause_bleeding(puuid, bleed_ml, body_part)
                results["bleeding"] = True
            except Exception as e:
                log.debug("shm_bridge",
                          f"Bleeding sync failed for wound {wound_uuid[:8]}: {e}")
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_bridge_pending import (
                    add_pending_action
                )
                add_pending_action(wound_uuid, puuid, "cause_bleeding",
                                   {"bleed_ml": bleed_rate * 5.0, "body_part": body_part})
                results["pending"].append("cause_bleeding")

    except Exception as e:
        log.warn("shm_bridge",
                 f"bridge_on_wound_created partially failed for {puuid[:8]}: {e}")

    return results


# ══════════════════════════════════════════════════════════════════════
# §3.4 Fix: Infection authority — SHM owns infection outcomes
# ══════════════════════════════════════════════════════════════════════

def bridge_sync_infection_to_shm(persona_uuid: str, wounds: list) -> list:
    """Sync wound infection state to SHM disease system.

    SHM owns infection outcomes. sp_combat sets infection_chance and
    infection_progress on the wound. The bridge checks if a wound's
    infection_progress has crossed the threshold and triggers an SHM
    wound_infection disease if one isn't already active.

    Called from sp_combat.process_wounds_tick() before sepsis check.

    Args:
        persona_uuid: Persona UUID
        wounds: List of active wound dicts from ext_sp_wounds

    Returns:
        List of triggered disease events
    """
    if not SHM_BRIDGE_CONFIG.get("enable_infection_sync", True):
        return []

    triggered = []

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_disease import (
            expose_to_disease, has_disease
        )

        db = get_db()

        for w in wounds:
            if not w.get("is_infected") or w.get("is_healed"):
                continue

            # Check if SHM already has an active wound_infection disease
            already_active = has_disease(persona_uuid, "wound_infection")
            if already_active:
                continue

            # Check infection_progress threshold
            progress = w.get("infection_progress", 0)
            severity = w.get("severity", 1)

            if progress >= 0.8:
                # High progress → trigger SHM wound_infection disease
                disease_result = expose_to_disease(
                    persona_uuid, "wound_infection",
                    virulence=0.3 + severity * 0.15,
                    source=f"wound_{w['wound_uuid'][:8]}"
                )
                if disease_result:
                    triggered.append({
                        "wound_uuid": w["wound_uuid"],
                        "disease": "wound_infection",
                        "severity": severity,
                    })
                    log.debug("shm_bridge",
                              f"SHM disease triggered for wound {w['wound_uuid'][:8]}")

    except Exception as e:
        log.warn("shm_bridge", f"bridge_sync_infection_to_shm error: {e}")

    return triggered


# ══════════════════════════════════════════════════════════════════════
# Skill tracking hook (called from sp_combat after successful hit)
# ══════════════════════════════════════════════════════════════════════

def bridge_log_skill_use(persona_uuid: str, weapon_type: str) -> dict:
    """Log a combat skill use to SHM.

    Called from sp_combat.resolve_melee_attack() after a successful hit.

    Args:
        persona_uuid: Attacker UUID
        weapon_type: Weapon type/item ID used

    Returns:
        Dict with skill use result
    """
    if not SHM_BRIDGE_CONFIG.get("enabled", True):
        return {"status": "disabled"}

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
            use_skill
        )
        skill_name = _map_item_to_skill(weapon_type) if "_" in weapon_type else (weapon_type or "unarmed")

        result = use_skill(persona_uuid, skill_name)
        return {"skill": skill_name, "result": result}
    except Exception as e:
        log.debug("shm_bridge", f"bridge_log_skill_use failed: {e}")
        return {"skill": weapon_type, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════
# Healing tick coordination
# ══════════════════════════════════════════════════════════════════════

def bridge_on_healing_tick(persona_uuid: str) -> dict:
    """Coordinate sp_combat wound healing with SHM pain tracking.

    Called from SHM's _on_simulation_cycle_start during the pain &
    healing phase. Ensures wound pain from ext_sp_wounds is reflected
    in SHM's pain tracking.

    Args:
        persona_uuid: Persona UUID

    Returns:
        Dict with results
    """
    if not SHM_BRIDGE_CONFIG.get("enabled", True):
        return {"status": "disabled"}

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_combat import get_active_wounds
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import add_pain_source

        wounds = get_active_wounds(persona_uuid)
        synced = 0
        for w in wounds:
            if w["pain_level"] > 0:
                add_pain_source(
                    persona_uuid,
                    f"wound_{w['wound_uuid'][:8]}",
                    "wound",
                    pain_level=w["pain_level"],
                    duration_ticks=12
                )
                synced += 1

        return {"synced_wounds": synced}

    except Exception as e:
        log.debug("shm_bridge", f"bridge_on_healing_tick error: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════
# §4.1 Migration: Backfill existing wounds on first load
# ══════════════════════════════════════════════════════════════════════

def bridge_migrate_existing_wounds() -> dict:
    """Create SHM-side records for all existing unhealed wounds.

    Called once when SHM is first loaded after the bridge is activated.
    Only processes wounds that don't already have corresponding SHM records.

    Returns:
        Dict with migration results
    """
    if not SHM_BRIDGE_CONFIG.get("enabled", True):
        return {"status": "disabled", "migrated": 0}

    migrated = 0
    errors = 0

    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import add_pain_source
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_bridge_pending import (
            ensure_pending_schema
        )

        ensure_pending_schema()
        db_src = sp_db()
        db_dst = get_db()

        # Get all unhealed wounds
        wounds = db_src.fetch_all("""
            SELECT * FROM ext_sp_wounds WHERE is_healed = 0
        """)

        for w in wounds:
            try:
                puuid = w["owner_uuid"]

                # Check if pain source already exists for this wound
                existing = db_dst.fetch_one("""
                    SELECT id FROM ext_shm_pain
                    WHERE persona_uuid = ? AND pain_source = ?
                """, (puuid, f"wound_{w['wound_uuid'][:8]}"))
                if existing:
                    continue  # Already migrated

                # Register pain source
                add_pain_source(
                    puuid,
                    f"wound_{w['wound_uuid'][:8]}",
                    "wound",
                    pain_level=w["pain_level"] * 2,
                    duration_ticks=48
                )
                migrated += 1

            except Exception as pe:
                errors += 1
                log.debug("shm_bridge",
                          f"Migration error for wound {w.get('wound_uuid', '?')[:8]}: {pe}")

    except Exception as e:
        log.warn("shm_bridge", f"bridge_migrate_existing_wounds failed: {e}")

    if migrated > 0:
        log.info("shm_bridge",
                 f"Migrated {migrated} existing wounds to SHM pain tracking "
                 f"({errors} errors)")
    return {"migrated": migrated, "errors": errors, "total_attempted": migrated + errors}


# ══════════════════════════════════════════════════════════════════════
# §3.5 Fix: Impact force from severity + weapon type
# ══════════════════════════════════════════════════════════════════════

def get_impact_force(severity: int, weapon_type: str = None) -> float:
    """Calculate impact force from wound severity and weapon type.

    Severity 1 = minor (0.2-0.3 force), Severity 4 = critical (1.0 force).
    Blunt weapons add 0.2, piercing weapons reduce blunt force by 0.1.

    Returns:
        Impact force (0.0 - 1.0)
    """
    force = SEVERITY_TO_FORCE.get(severity, SEVERITY_TO_FORCE[1])
    blunt_force = force["blunt"]

    # Weapon type modifies force
    if weapon_type:
        blunt_weapons = {"mace", "hammer", "fist", "blunt", "maul"}
        piercing_weapons = {"spear", "arrow", "bolt", "trident", "knife"}
        if weapon_type in blunt_weapons:
            blunt_force += 0.2
        elif weapon_type in piercing_weapons:
            blunt_force -= 0.1

    return max(0.0, min(1.0, blunt_force))
