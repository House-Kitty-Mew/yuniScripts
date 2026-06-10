"""
sp_combat.py — Combat, wound, and injury system for the living world.

Every violent interaction (persona vs animal, animal vs animal, persona vs
persona) produces wounds on body parts.  Wounds bleed, cause pain, become
infected, and directly accelerate the health-decay cascade.

Environment (terrain, weather, light) modifies every combat action — a
night-time fight in a snowstorm on a mountain slope is vastly different
from a noon duel on dry plains.

Wounds are tracked per-body-part with:
  - Type (cut, puncture, crush, fracture, laceration, burn)
  - Severity (minor→critical)
  - Bleed rate (ml/tick)
  - Infection state (clean → infected → sepsis)
  - Pain level (affects all actions)
  - Bandage state (slows bleeding, prevents infection)
"""

import json, random, math, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_db, add_memory, add_life_event, get_active_personas,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import (
    get_item_def, give_item, remove_item, count_item, get_inventory,
    get_equipped_stats, burn_calories,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import (
    get_persona_area, get_area, get_all_areas,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import (
    get_persona_health, modify_health, add_memory as _hm,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Wound table and body parts
# ══════════════════════════════════════════════════════════════════════

BODY_PARTS = ["head", "torso", "left_arm", "right_arm", "left_leg", "right_leg"]

WOUND_TYPES = {
    "cut":       {"verbs": ["slash", "gash", "cut"], "bleed_mult": 1.0, "infect_base": 0.3},
    "puncture":  {"verbs": ["stab", "pierce", "impale"], "bleed_mult": 0.7, "infect_base": 0.5},
    "crush":     {"verbs": ["crush", "smash", "batter"], "bleed_mult": 0.3, "infect_base": 0.2},
    "laceration": {"verbs": ["tear", "rip", "lacerate"], "bleed_mult": 1.3, "infect_base": 0.6},
    "fracture":  {"verbs": ["break", "snap", "fracture"], "bleed_mult": 0.5, "infect_base": 0.1},
    "burn":      {"verbs": ["burn", "scorch", "sear"], "bleed_mult": 0.0, "infect_base": 0.7},
}

# Severity → base values
SEVERITY = {
    1: {"name": "Minor", "bleed_rate": 1,   "pain": 10, "heal_days": 3,  "infect_mod": 0.3},
    2: {"name": "Moderate", "bleed_rate": 5,   "pain": 25, "heal_days": 7,  "infect_mod": 0.6},
    3: {"name": "Severe", "bleed_rate": 15,  "pain": 50, "heal_days": 14, "infect_mod": 0.8},
    4: {"name": "Critical", "bleed_rate": 40, "pain": 80, "heal_days": 30, "infect_mod": 1.0},
}

# Body part → bleed severity multiplier (head/neck bleed faster)
BODY_PART_BLEED_MULT = {
    "head": 1.5, "torso": 1.2, "left_arm": 0.7,
    "right_arm": 0.7, "left_leg": 0.8, "right_leg": 0.8,
}

# ══════════════════════════════════════════════════════════════════════
# Terrain & weather combat modifiers
# ══════════════════════════════════════════════════════════════════════

TERRAIN_MODIFIERS = {
    "plains":      {"agility": 0, "move": 0, "perception": 0, "slip_chance": 0.02},
    "forest":      {"agility": -5, "move": -10, "perception": -10, "slip_chance": 0.05},
    "desert":      {"agility": -10, "move": -10, "perception": 5, "slip_chance": 0.03},
    "swamp":       {"agility": -15, "move": -20, "perception": -10, "slip_chance": 0.15},
    "mountains":   {"agility": -15, "move": -20, "perception": 10, "slip_chance": 0.20},
    "ocean":       {"agility": -30, "move": -40, "perception": -20, "slip_chance": 0.30},
    "tundra":      {"agility": -20, "move": -25, "perception": 15, "slip_chance": 0.20},
    "nether_wastes": {"agility": -10, "move": -10, "perception": -5, "slip_chance": 0.10},
    "crimson_forest": {"agility": -10, "move": -15, "perception": -15, "slip_chance": 0.10},
    "end_islands": {"agility": -15, "move": -10, "perception": -10, "slip_chance": 0.15},
    "deep_dark":   {"agility": -10, "move": -10, "perception": -40, "slip_chance": 0.10},
}

WEATHER_MODIFIERS = {
    "rain":        {"weapon_skill": -5, "perception": -5, "ranged": -10},
    "snow":        {"weapon_skill": -10, "move": -10, "perception": -10, "ranged": -15},
    "fog":         {"perception": -40, "ranged": -60},
    "wind":        {"ranged": -20, "perception": -5},
}

# ══════════════════════════════════════════════════════════════════════
# Combat stats calculator
# ══════════════════════════════════════════════════════════════════════

def get_combat_stats(persona_uuid: str,
                     target_uuid: Optional[str] = None) -> dict:
    """Calculate full combat attributes for a persona.

    Derived from: health stats, equipped items, skills, area terrain,
    weather, and active wounds.

    Returns a dict of all combat-relevant stats.
    """
    health = get_persona_health(persona_uuid)
    if not health:
        return {"strength": 5, "agility": 5, "endurance": 5,
                "perception": 5, "pain_threshold": 50,
                "armor_rating": 0, "weapon_skill": 5}

    # Base from health
    food_factor = max(0.5, health["food"] / 100)
    hyd_factor = max(0.5, health["hydration"] / 100)
    energy_factor = max(0.3, health["energy"] / 100)
    immune_factor = max(0.3, health["immune"] / 100)

    strength = 10 * food_factor * energy_factor
    agility = 10 * energy_factor * hyd_factor
    endurance = 10 * energy_factor * food_factor * immune_factor
    perception = 10 * hyd_factor * energy_factor

    # Pain penalty from wounds
    pain_penalty = _get_pain_level(persona_uuid)
    pain_factor = max(0.3, 1.0 - pain_penalty / 100)
    agility *= pain_factor
    perception *= pain_factor
    strength *= max(0.5, pain_factor)

    # Equipment
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import get_equipped_stats
    eq = get_equipped_stats(persona_uuid)
    armor = eq.get("total_armor", 0)

    # Weapon skill from equipped weapon type
    db = get_db()
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills
        skills = get_skills(persona_uuid) or {}
        combat_skill = skills.get("combat", 10)
    except Exception:
        combat_skill = 10

    weapon_skill = combat_skill + (eq.get("has_weapon", False) and 5 or 0)

    # Terrain modifiers
    area = get_persona_area(persona_uuid)
    biome = area["biome_type"] if area else "plains"
    terrain = TERRAIN_MODIFIERS.get(biome, TERRAIN_MODIFIERS["plains"])
    agility += terrain.get("agility", 0)
    perception += terrain.get("perception", 0)

    # Weather modifiers
    weather = db.fetch_one(
        "SELECT * FROM ext_sp_weather WHERE area_uuid = ?",
        (area["area_uuid"],)) if area else None
    if weather:
        if weather["is_raining"]:
            wm = WEATHER_MODIFIERS["rain"]
            weapon_skill += wm.get("weapon_skill", 0)
            perception += wm.get("perception", 0)
        if weather["is_snowing"]:
            wm = WEATHER_MODIFIERS["snow"]
            weapon_skill += wm.get("weapon_skill", 0)
            perception += wm.get("perception", 0)
        if weather["cloud_cover"] > 0.8 and weather["humidity"] > 80:
            # Fog-like conditions
            perception -= 20

        # Temperature
        temp = weather["temperature"]
        if temp < -5:
            weapon_skill -= 10  # Cold hands
        elif temp > 35:
            endurance *= 0.6  # Heat exhaustion

    # Light level (hour-based)
    hour = (datetime.now(timezone.utc).hour + 8) % 24
    if hour < 6 or hour > 20:
        perception -= 30  # Night
    elif hour < 8 or hour > 18:
        perception -= 15  # Twilight

    return {
        "strength": round(max(1, strength), 1),
        "agility": round(max(1, agility), 1),
        "endurance": round(max(1, endurance), 1),
        "perception": round(max(1, perception), 1),
        "pain_threshold": max(10, 50 - pain_penalty * 0.3),
        "armor_rating": round(armor, 1),
        "weapon_skill": round(max(1, weapon_skill), 1),
        "pain_penalty": round(pain_penalty, 1),
        "terrain": biome,
        "weapon": eq.get("tools", {}).get("spear", 0) or
                  (1.0 if eq.get("tools", {}).get("knife", 0) > 0 else 0),
    }


def _get_pain_level(persona_uuid: str) -> float:
    """Sum pain from all active wounds on a persona."""
    db = get_db()
    wounds = db.fetch_all("""
        SELECT pain_level FROM ext_sp_wounds
        WHERE owner_uuid = ? AND is_healed = 0
    """, (persona_uuid,))
    total = sum(w["pain_level"] for w in wounds)
    # Diminishing returns on pain stacking
    if total > 100:
        total = 100 + (total - 100) * 0.5
    return min(150, total)


# ══════════════════════════════════════════════════════════════════════
# Combat resolution
# ══════════════════════════════════════════════════════════════════════

def resolve_melee_attack(attacker_uuid: str, defender_uuid: str,
                         weapon_type: Optional[str] = None,
                         damage_bonus: float = 0) -> dict:
    """Resolve a single melee attack between two entities.

    Returns wound info or miss.
    """
    try:
        atk_stats = get_combat_stats(attacker_uuid)

    except Exception as e:
        log.error(f"resolve_melee_attack failed: {e}")
        return {}
    def_stats = get_combat_stats(defender_uuid)

    # Hit chance
    base_hit = 50
    hit_mod = (atk_stats["weapon_skill"] - def_stats["agility"]) * 2
    terrain_mod = _get_terrain_hit_mod(attacker_uuid)
    slip_penalty = _get_slip_penalty(attacker_uuid)

    hit_chance = base_hit + hit_mod + terrain_mod + slip_penalty
    hit_chance = max(10, min(95, hit_chance))

    roll = random.randint(1, 100)
    if roll > hit_chance:
        return {"hit": False, "message": "attack missed",
                "roll": roll, "hit_chance": hit_chance}

    # Critical hit
    critical = roll <= 5

    # Determine body part
    body_part = _roll_body_part()
    severity = _calc_severity(atk_stats, critical, damage_bonus)
    wound_type = _pick_wound_type(weapon_type)

    # Apply armor reduction
    armor = def_stats["armor_rating"]
    if armor > 0 and body_part in ("head", "torso", "left_arm", "right_arm"):
        # Armor reduces severity
        severity = max(1, severity - int(armor / 2))
        if severity <= 1 and random.random() < armor / 10:
            return {"hit": False, "message": "deflected by armor"}

    base_damage = SEVERITY[severity]
    bleed = base_damage["bleed_rate"] * BODY_PART_BLEED_MULT.get(body_part, 1.0)
    pain = base_damage["pain"] + (20 if critical else 0)

    # Create wound
    wound = _create_wound(
        owner_uuid=defender_uuid,
        body_part=body_part,
        wound_type=wound_type,
        severity=severity,
        bleed_rate=bleed,
        pain_level=pain,
        attacker_uuid=attacker_uuid,
    )

    # Damage impact on health
    food_loss = bleed * 0.5 + pain * 0.1
    hyd_loss = bleed * 0.3
    modify_health(defender_uuid,
                  food=-food_loss,
                  hydration=-hyd_loss,
                  energy=-pain * 0.3,
                  immune=(-bleed * 0.2 if not wound.get("is_bandaged") else 0))

    add_memory(attacker_uuid, "combat_hit", detail={
        "target": defender_uuid[:8],
        "body_part": body_part,
        "wound_type": wound_type,
        "severity": SEVERITY[severity]["name"],
        "damage": round(food_loss + hyd_loss, 1),
    }, emotional_weight=6)

    add_memory(defender_uuid, "combat_wounded", detail={
        "attacker": attacker_uuid[:8],
        "body_part": body_part,
        "wound_type": wound_type,
        "severity": SEVERITY[severity]["name"],
        "pain": round(pain, 1),
    }, emotional_weight=8)

    # Phase 3 bridge: log skill use to SHM for XP tracking
    _bridge_log_skill_use(attacker_uuid, weapon_type or "unarmed")

    return {
        "hit": True,
        "critical": critical,
        "body_part": body_part,
        "wound_type": wound_type,
        "severity": SEVERITY[severity]["name"],
        "bleed_rate": round(bleed, 1),
        "pain": round(pain, 1),
        "message": f"{wound_type} to {body_part} ({SEVERITY[severity]['name']})",
    }


def _bridge_log_skill_use(persona_uuid: str, weapon_type: str):
    """Log combat skill usage to SHM bridge. Graceful if SHM unavailable."""
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_bridge import (
            bridge_log_skill_use
        )
        bridge_log_skill_use(persona_uuid, weapon_type)
    except ImportError:
        pass  # SHM not installed
    except Exception as e:
        log.debug("sp_combat", f"Skill use bridge error: {e}")


def _get_terrain_hit_mod(persona_uuid: str) -> float:
    """Hit modifier from terrain."""
    area = get_persona_area(persona_uuid)
    if not area:
        return 0
    tm = TERRAIN_MODIFIERS.get(area["biome_type"], TERRAIN_MODIFIERS["plains"])
    return tm.get("agility", 0) * 0.3


def _get_slip_penalty(persona_uuid: str) -> float:
    """Additional penalty from slipping on terrain."""
    area = get_persona_area(persona_uuid)
    if not area:
        return 0
    tm = TERRAIN_MODIFIERS.get(area["biome_type"], TERRAIN_MODIFIERS["plains"])
    slip = tm.get("slip_chance", 0)
    if random.random() < slip:
        return -15  # Slipped, big penalty
    return 0


def _roll_body_part() -> str:
    """Random body part, weighted toward torso."""
    parts = ["head"] * 1 + ["torso"] * 4 + ["left_arm"] * 2 + \
            ["right_arm"] * 2 + ["left_leg"] * 2 + ["right_leg"] * 2
    return random.choice(parts)


def _calc_severity(stats: dict, critical: bool, bonus: float) -> int:
    """Calculate wound severity (1-4)."""
    base = 2 if critical else 1
    roll = random.random()
    if roll < 0.1 + stats["strength"] * 0.01 + bonus:
        base += 1
    if roll < 0.02:
        base += 1  # Rare critical+
    return min(4, max(1, base))


def _pick_wound_type(weapon_type: Optional[str] = None) -> str:
    """Pick wound type based on weapon."""
    try:
        if weapon_type == "spear":
            return random.choices(
                ["puncture", "laceration", "cut"],
                weights=[0.6, 0.2, 0.2])[0]
        elif weapon_type in ("axe", "pickaxe"):
            return random.choices(
                ["cut", "crush", "laceration"],
                weights=[0.5, 0.3, 0.2])[0]
        elif weapon_type == "knife":
            return random.choices(
                ["cut", "puncture", "laceration"],
                weights=[0.3, 0.4, 0.3])[0]
        elif weapon_type in ("fist", "blunt"):
            return random.choices(
                ["crush", "fracture"],
                weights=[0.7, 0.3])[0]
        else:  # Teeth/claws
            return random.choices(
                ["laceration", "puncture", "crush"],
                weights=[0.4, 0.4, 0.2])[0]
    except Exception as e:
        log.error(f"_pick_wound_type failed: {e}")
        return ""


def _create_wound(owner_uuid: str, body_part: str, wound_type: str,
                  severity: int, bleed_rate: float, pain_level: float,
                  attacker_uuid: Optional[str] = None,
                  impact_force: float = 1.0) -> dict:
    """Create a wound record in the database with transaction safety.

    GAP #1 fix: Wraps wound INSERT + SHM bridge hook in a logical
    transaction. If the bridge hook fails (SHM unavailable, DB error),
    the wound INSERT is rolled back, preventing silent desync.

    GAP #2 fix: Passes wound details through to bridge which uses
    per-wound-type bone target mapping (WOUND_BONE_MAP).
    """
    db = get_db()
    wound_uuid = str(uuid_mod.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    inf_chance = WOUND_TYPES.get(wound_type, {}).get("infect_base", 0.3)
    inf_chance *= SEVERITY[severity]["infect_mod"]

    try:
        # BEGIN transaction
        # Note: sqlite3 auto-transactions mean each execute() is atomic.
        # For a true multi-statement rollback we use immediate mode.
        db.execute("BEGIN IMMEDIATE")

        db.execute("""
            INSERT INTO ext_sp_wounds
            (wound_uuid, owner_uuid, body_part, wound_type, severity,
             bleed_rate, pain_level, infection_chance, infection_progress,
             is_infected, is_bandaged, is_healed, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?)
        """, (wound_uuid, owner_uuid, body_part, wound_type, severity,
              bleed_rate, min(100, pain_level), inf_chance,
              attacker_uuid or "", now))

        # Fire SHM bridge hook after wound INSERT (within transaction)
        # If bridge fails, the ROLLBACK below reverts the INSERT
        _call_bridge_on_wound_created(
            wound_uuid=wound_uuid,
            owner_uuid=owner_uuid,
            body_part=body_part,
            wound_type=wound_type,
            severity=severity,
            bleed_rate=bleed_rate,
            pain_level=pain_level,
            impact_force=impact_force,
        )

        db.execute("COMMIT")

    except Exception as bridge_error:
        # Bridge hook failed — rollback the wound INSERT to prevent desync
        try:
            db.execute("ROLLBACK")
        except Exception:
            pass
        log.warn("sp_combat",
                 f"Bridge hook failed for wound {wound_uuid[:8]}, "
                 f"rolled back: {bridge_error}")
        # Still create the wound without bridge (graceful degradation)
        db.execute("""
            INSERT INTO ext_sp_wounds
            (wound_uuid, owner_uuid, body_part, wound_type, severity,
             bleed_rate, pain_level, infection_chance, infection_progress,
             is_infected, is_bandaged, is_healed, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?)
        """, (wound_uuid, owner_uuid, body_part, wound_type, severity,
              bleed_rate, min(100, pain_level), inf_chance,
              attacker_uuid or "", now))

    return {
        "wound_uuid": wound_uuid,
        "owner_uuid": owner_uuid,
        "body_part": body_part,
        "wound_type": wound_type,
        "severity": severity,
        "bleed_rate": bleed_rate,
        "pain_level": pain_level,
        "is_bandaged": False,
    }


def _call_bridge_on_wound_created(wound_uuid: str, owner_uuid: str,
                                   body_part: str, wound_type: str,
                                   severity: int, bleed_rate: float,
                                   pain_level: float,
                                   impact_force: float = 1.0):
    """Try to call the SHM bridge on_wound_created hook.

    Gracefully handles missing SHM module — sp_combat works without it.
    """
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_bridge import (
            bridge_on_wound_created, get_impact_force
        )
        force = get_impact_force(severity, wound_type)
        wound = {
            "wound_uuid": wound_uuid,
            "owner_uuid": owner_uuid,
            "body_part": body_part,
            "wound_type": wound_type,
            "severity": severity,
            "bleed_rate": bleed_rate,
            "pain_level": pain_level,
        }
        bridge_on_wound_created(wound, impact_force=force)
    except ImportError:
        pass  # SHM not installed — this is fine
    except Exception as e:
        # Log but don't crash — caller handles rollback
        log.debug("sp_combat", f"Bridge on_wound_created error: {e}")
        raise  # Re-raise so caller can rollback


# ══════════════════════════════════════════════════════════════════════
# Dodge / parry
# ══════════════════════════════════════════════════════════════════════

def attempt_dodge(defender_uuid: str) -> bool:
    """Dodge attempt against an incoming attack.

    Returns True if dodge succeeds.
    """
    stats = get_combat_stats(defender_uuid)

    terrain = _get_terrain_hit_mod(defender_uuid)
    slip = _get_slip_penalty(defender_uuid)

    dodge_chance = 30 + stats["agility"] * 2 + terrain + slip
    dodge_chance = max(5, min(90, dodge_chance))

    success = random.randint(1, 100) <= dodge_chance

    if success:
        # Dodging costs energy
        modify_health(defender_uuid, energy=-2)
    else:
        # Failed dodge may cause stumble damage
        if random.random() < 0.2:
            modify_health(defender_uuid, energy=-5)

    return success


def attempt_parry(defender_uuid: str) -> bool:
    """Parry attempt using equipped weapon.

    Returns True if parry succeeds.
    """
    stats = get_combat_stats(defender_uuid)

    if not stats.get("weapon"):
        return False

    parry_chance = 20 + stats["weapon_skill"] * 1.5
    parry_chance = max(5, min(85, parry_chance))

    success = random.randint(1, 100) <= parry_chance

    if success:
        modify_health(defender_uuid, energy=-3)
    else:
        # Failed parry = opening
        modify_health(defender_uuid, energy=-4)

    return success


# ══════════════════════════════════════════════════════════════════════
# Wound processing (bleeding, infection, healing)
# ══════════════════════════════════════════════════════════════════════

def process_wounds_tick() -> dict:
    """Process all active wounds — bleeding, infection, natural healing.

    Called every simulation tick (or daily for natural healing).
    Bleeding wounds:
      - Drain food (blood loss → weakness)
      - Drain hydration
      - Drain energy
      - Drain immune

    Infected wounds:
      - Cause fever (temperature increase)
      - Accelerate immune drain
      - Can progress to sepsis (death)

    Bandaged wounds:
      - Bleeding greatly reduced
      - Infection progression slowed
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"process_wounds_tick failed: {e}")
        return {}
    if not cfg.get("ecosystem_enabled", True):
        return {"status": "disabled"}

    db = get_db()
    wounds = db.fetch_all("""
        SELECT * FROM ext_sp_wounds WHERE is_healed = 0
    """)

    results = {
        "total_wounds": len(wounds),
        "bleeding": 0,
        "infected": 0,
        "healed": 0,
        "deaths": 0,
    }

    for w in wounds:
        owner_health = get_persona_health(w["owner_uuid"])
        if not owner_health or not owner_health.get("alive", 1):
            continue

        # ── Bleeding ──────────────────────────────────────────────
        bleed = w["bleed_rate"]
        if w["is_bandaged"]:
            bleed *= 0.2  # Bandage reduces bleeding 80%

        if bleed > 0:
            results["bleeding"] += 1
            # Blood loss = food + hydration + energy drain
            modify_health(w["owner_uuid"],
                          food=-bleed * 0.3,
                          hydration=-bleed * 0.2,
                          energy=-bleed * 0.5,
                          immune=-bleed * 0.1)

        # ── Infection progression ──────────────────────────────
        if not w["is_bandaged"] and not w["is_infected"]:
            # Infection chance per tick
            inf_progress = w["infection_progress"] + w["infection_chance"] * 0.05
            if inf_progress >= 1.0:
                db.execute("""
                    UPDATE ext_sp_wounds SET infection_progress = 1.0, is_infected = 1
                    WHERE id = ?
                """, (w["id"],))
                results["infected"] += 1
                add_memory(w["owner_uuid"], "wound_infected", detail={
                    "body_part": w["body_part"],
                    "wound_type": w["wound_type"],
                }, emotional_weight=7)
                add_life_event(w["owner_uuid"], "infection",
                               f"Wound on {w['body_part']} has become infected!",
                               financial_impact=0, mood_impact="stressed",
                               duration_hours=72)
            else:
                db.execute("""
                    UPDATE ext_sp_wounds SET infection_progress = ?
                    WHERE id = ?
                """, (inf_progress, w["id"]))

        # ── Infected wound effects ──────────────────────────────
        if w["is_infected"]:
            # Fever
            modify_health(w["owner_uuid"],
                          temperature=5,
                          immune=-w["bleed_rate"] * 0.5,
                          energy=-w["bleed_rate"] * 0.3)

            # Sepsis progression: if immune drops to 0 and severe wound
            if (w["severity"] >= 3 and
                    owner_health["immune"] < 20 and
                    owner_health["energy"] < 20):
                modify_health(w["owner_uuid"],
                              temperature=10, food=-10,
                              hydration=-10, energy=-15)

        # ── Bandaged wound natural healing ───────────────────────
        if w["is_bandaged"] and random.random() < 0.1:
            # Reduce pain gradually
            new_pain = max(0, w["pain_level"] - random.uniform(1, 5))
            # Reduce bleed rate (healing wound)
            new_bleed = max(0, w["bleed_rate"] - random.uniform(0.1, 0.5))

            if new_bleed <= 0.1 and new_pain <= 5:
                # Wound healed
                db.execute("""
                    UPDATE ext_sp_wounds SET is_healed = 1, pain_level = 0, bleed_rate = 0
                    WHERE id = ?
                """, (w["id"],))
                results["healed"] += 1
                add_memory(w["owner_uuid"], "wound_healed", detail={
                    "body_part": w["body_part"],
                    "days": (datetime.now(timezone.utc) - datetime.fromisoformat(
                        w["created_at"])).days if w.get("created_at") else "?",
                }, emotional_weight=5)
            else:
                db.execute("""
                    UPDATE ext_sp_wounds SET pain_level = ?, bleed_rate = ?
                    WHERE id = ?
                """, (round(new_pain, 1), round(new_bleed, 1), w["id"]))

    # ── Sync infection state to SHM disease system (§3.4 fix) ──
    # SHM owns infection outcomes; bridge triggers wound_infection
    # disease when sp_combat marks a wound as infected
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_bridge import (
            bridge_sync_infection_to_shm
        )
        # Group wounds by persona for efficient sync
        from collections import defaultdict
        wounds_by_persona = defaultdict(list)
        for w in wounds:
            wounds_by_persona[w["owner_uuid"]].append(w)
        for puuid, pwounds in wounds_by_persona.items():
            try:
                bridge_sync_infection_to_shm(puuid, pwounds)
            except Exception:
                pass
    except ImportError:
        pass  # SHM not installed
    except Exception as e:
        log.debug("sp_combat", f"Infection sync error: {e}")

    # ── Check for sepsis deaths ─────────────────────────────────
    severe_infections = db.fetch_all("""
        SELECT w.*, h.immune, h.energy, h.food, h.alive
        FROM ext_sp_wounds w
        JOIN ext_sp_health h ON w.owner_uuid = h.persona_uuid
        WHERE w.is_infected = 1 AND w.severity >= 3
          AND h.alive = 1 AND w.is_healed = 0
    """)
    for si in severe_infections:
        if si.get("immune", 50) <= 5 and si.get("energy", 50) <= 5:
            modify_health(si["owner_uuid"], immune=-50)  # Kill
            add_life_event(si["owner_uuid"], "sepsis",
                           "Died from septic shock from an infected wound.",
                           financial_impact=0, mood_impact="stressed",
                           duration_hours=9999)
            log.info("sp_combat", f"Persona {si['owner_uuid'][:8]} died of sepsis")
            results["deaths"] += 1

    return results


# ══════════════════════════════════════════════════════════════════════
# First aid / healing
# ══════════════════════════════════════════════════════════════════════

def apply_bandage(persona_uuid: str, wound_uuid: str) -> dict:
    """Apply a bandage from inventory to a wound.

    Reduces bleeding, slows infection. Also uses clean water if available.
    """
    try:
        db = get_db()


        # Check for bandage

    except Exception as e:
        log.error(f"apply_bandage failed: {e}")
        return {}
    bandage_count = count_item(persona_uuid, "bandage")
    if bandage_count < 1:
        return {"error": "no bandages in inventory"}

    wound = db.fetch_one(
        "SELECT * FROM ext_sp_wounds WHERE wound_uuid = ?",
        (wound_uuid,))
    if not wound or wound["is_healed"]:
        return {"error": "wound not found or already healed"}
    if wound["is_bandaged"]:
        return {"error": "wound is already bandaged"}

    # Consume bandage
    remove_item(persona_uuid, "bandage", 1)

    # Check for clean water to clean wound first
    water_count = count_item(persona_uuid, "clean_water")
    used_water = False
    if water_count >= 1:
        remove_item(persona_uuid, "clean_water", 1)
        used_water = True

    # Apply bandage
    new_bleed = wound["bleed_rate"] * 0.2  # 80% reduction
    new_infect_chance = wound["infection_chance"] * 0.3  # 70% reduction
    if used_water:
        new_infect_chance *= 0.5  # Clean water further reduces

    pain_reduction = random.uniform(5, 15)
    new_pain = max(0, wound["pain_level"] - pain_reduction)

    db.execute("""
        UPDATE ext_sp_wounds SET
            is_bandaged = 1,
            bleed_rate = ?,
            infection_chance = ?,
            pain_level = ?
        WHERE id = ?
    """, (new_bleed, new_infect_chance, new_pain, wound["id"]))

    add_memory(persona_uuid, "first_aid", detail={
        "wound_type": wound["wound_type"],
        "body_part": wound["body_part"],
        "used_water": used_water,
    }, emotional_weight=4)

    return {
        "success": True,
        "message": f"Bandaged {wound['body_part']} ({wound['wound_type']})" +
                   (" with clean water" if used_water else ""),
        "bleed_new": round(new_bleed, 2),
        "pain_reduced": round(pain_reduction, 1),
    }


def apply_herbs(persona_uuid: str, wound_uuid: str) -> dict:
    """Apply medicinal herbs to a wound for infection prevention.

    Can be used on infected wounds to fight the infection.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"apply_herbs failed: {e}")
        return {}

    herb_count = count_item(persona_uuid, "herbs")
    if herb_count < 1:
        return {"error": "no herbs in inventory"}

    wound = db.fetch_one(
        "SELECT * FROM ext_sp_wounds WHERE wound_uuid = ?",
        (wound_uuid,))
    if not wound or wound["is_healed"]:
        return {"error": "wound not found or already healed"}

    remove_item(persona_uuid, "herbs", 1)

    if wound["is_infected"]:
        # Fight infection
        new_progress = max(0, wound["infection_progress"] - 0.3)
        db.execute("""
            UPDATE ext_sp_wounds SET infection_progress = ?
            WHERE id = ?
        """, (new_progress, wound["id"]))

        if new_progress <= 0:
            db.execute("""
                UPDATE ext_sp_wounds SET is_infected = 0 WHERE id = ?
            """, (wound["id"],))

        add_memory(persona_uuid, "herbal_treatment", detail={
            "wound_type": wound["wound_type"],
            "body_part": wound["body_part"],
            "infection_reduced": 0.3,
        }, emotional_weight=5)

        return {"success": True,
                "message": f"Applied herbs to infected {wound['body_part']} wound"}

    else:
        # Prevent infection
        new_infect = max(0, wound["infection_chance"] * 0.3)
        pain_reduction = random.uniform(3, 10)
        db.execute("""
            UPDATE ext_sp_wounds SET infection_chance = ?,
                pain_level = MAX(0, pain_level - ?)
            WHERE id = ?
        """, (new_infect, pain_reduction, wound["id"]))

        add_memory(persona_uuid, "herbal_treatment", detail={
            "wound_type": wound["wound_type"],
            "body_part": wound["body_part"],
            "infection_reduced": new_infect,
        }, emotional_weight=4)

        return {"success": True,
                "message": f"Applied herbs to {wound['body_part']} — infection risk reduced"}


def get_active_wounds(persona_uuid: str) -> list[dict]:
    """Get all active (unhealed) wounds for a persona."""
    db = get_db()
    return db.fetch_all("""
        SELECT * FROM ext_sp_wounds
        WHERE owner_uuid = ? AND is_healed = 0
        ORDER BY severity DESC, created_at DESC
    """, (persona_uuid,))


# ══════════════════════════════════════════════════════════════════════
# Self-harm (environment & action accidents)
# ══════════════════════════════════════════════════════════════════════

def check_terrain_self_harm(persona_uuid: str) -> dict:
    """Check if the persona hurts themselves moving through terrain.

    Called when a persona moves between areas.
    Returns wound info or empty dict.
    """
    try:
        area = get_persona_area(persona_uuid)

    except Exception as e:
        log.error(f"check_terrain_self_h failed: {e}")
        return {}
    if not area:
        return {}

    biome = area["biome_type"]
    terrain = TERRAIN_MODIFIERS.get(biome, TERRAIN_MODIFIERS["plains"])
    slip_chance = terrain.get("slip_chance", 0)

    # Weather can increase slip chance
    db = get_db()
    weather = db.fetch_one(
        "SELECT * FROM ext_sp_weather WHERE area_uuid = ?",
        (area["area_uuid"],))
    if weather:
        if weather["is_raining"]:
            slip_chance += 0.05
        if weather["is_snowing"] or weather.get("snow_cover", 0) > 0:
            slip_chance += 0.10

    # Fatigue increases slip chance
    health = get_persona_health(persona_uuid)
    if health:
        if health["energy"] < 30:
            slip_chance += 0.10
        if health["food"] < 20:
            slip_chance += 0.05

    stats = get_combat_stats(persona_uuid)
    slip_chance = max(0, slip_chance - stats["agility"] * 0.005)

    if random.random() < slip_chance:
        # Fall! Minor to moderate wound
        severity = 1 if random.random() < 0.7 else 2
        body_part = random.choice(["left_leg", "right_leg", "left_arm", "torso"])
        wound_type = random.choice(["crush", "cut", "fracture"])

        wound = _create_wound(
            owner_uuid=persona_uuid,
            body_part=body_part,
            wound_type=wound_type,
            severity=severity,
            bleed_rate=SEVERITY[severity]["bleed_rate"] * 0.5,
            pain_level=SEVERITY[severity]["pain"] * 0.7,
        )

        pain = wound["pain_level"]
        modify_health(persona_uuid, energy=-pain * 0.5)

        add_memory(persona_uuid, "terrain_injury", detail={
            "terrain": biome,
            "body_part": body_part,
            "wound_type": wound_type,
            "severity": SEVERITY[severity]["name"],
        }, emotional_weight=5)

        log.info("sp_combat",
                 f"{persona_uuid[:8]} slipped on {biome} — {wound_type} to {body_part}")

        return {
            "self_harm": True,
            "body_part": body_part,
            "wound_type": wound_type,
            "severity": SEVERITY[severity]["name"],
            "pain": round(pain, 1),
        }

    return {}


def check_crafting_self_harm(persona_uuid: str, tool_type: str,
                              skill_level: int) -> dict:
    """Check if a crafting action causes injury.

    Low skill + poor tool + complex recipe = accident chance.
    """
    try:
        if random.random() > 0.05:  # Base 5% chance for dangerous crafts

            return {}


        # Accident modifiers

    except Exception as e:
        log.error(f"check_crafting_self_ failed: {e}")
        return {}
    accident_chance = 0.05
    if skill_level < 15:
        accident_chance += 0.08
    if tool_type == "knife":
        accident_chance += 0.10  # Knives are dangerous
    if tool_type == "axe":
        accident_chance += 0.12  # Axes are dangerous

    if random.random() < accident_chance:
        severity = 1  # Always minor for crafting
        body_part = random.choice(["left_arm", "right_arm", "left_hand", "right_hand"])
        wound_type = "cut"

        wound = _create_wound(
            owner_uuid=persona_uuid,
            body_part=body_part,
            wound_type=wound_type,
            severity=severity,
            bleed_rate=SEVERITY[severity]["bleed_rate"] * 0.5,
            pain_level=SEVERITY[severity]["pain"],
        )

        modify_health(persona_uuid, energy=-5, immune=-2)

        add_memory(persona_uuid, "crafting_accident", detail={
            "tool": tool_type,
            "body_part": body_part,
            "severity": SEVERITY[severity]["name"],
        }, emotional_weight=4)

        return {
            "self_harm": True,
            "body_part": body_part,
            "wound_type": wound_type,
            "severity": SEVERITY[severity]["name"],
            "pain": round(wound["pain_level"], 1),
        }

    return {}

