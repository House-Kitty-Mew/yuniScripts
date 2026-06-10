"""
sp_health.py — Persona health & physiology simulation.

Tracks 7 vital stats for every persona and processes decay/environmental
effects each simulation tick.  Personas that neglect their needs sicken,
decay visibly, and eventually die.

Stats tracked (0-100):
  food, hydration, energy, temperature, waste, hygiene, immune

Each stat has decay rates, danger thresholds, and death consequences.
Synergistic decay means neglect in one area accelerates others.
"""

import random
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_db, ensure_schema, add_memory, get_active_personas,
    get_active_world_events, add_life_event,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import deactivate_persona
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()


# ── Biome Environmental Effects ──────────────────────────────────────

_BIOME_TEMP_EFFECTS = {
    "plains": 0, "forest": 0,
    "desert": 5, "swamp": 1, "mountains": -3, "ocean": -1, "tundra": -5,
    "nether_wastes": 8, "crimson_forest": 4,
    "end_islands": -3, "deep_dark": -2,
}

_HYGIENE_DECAY_BONUS = {
    "swamp": 1, "nether_wastes": 1, "crimson_forest": 1, "deep_dark": 1,
}


# ── Health Initialization ──────────────────────────────────────────

def init_health(persona_uuid: str, wealth_tier: str, archetype: str):
    """Initialize health stats for a new persona.

    Args:
        persona_uuid: New persona's UUID
        wealth_tier: poor/working/middle/wealthy/elite
        archetype: miner/farmer/etc.
    """
    wealth_bonus = {"poor": -10, "working": 0, "middle": 5, "wealthy": 10, "elite": 15}
    wb = wealth_bonus.get(wealth_tier, 0)

    # Base values vary by archetype
    archetype_mods = {
        "miner": {"food": 5, "energy": 5, "immune": 5},
        "farmer": {"food": 10, "hygiene": -5, "hydration": 5},
        "warrior": {"food": 5, "energy": 10, "immune": 10},
        "merchant": {"hygiene": 10, "food": 5, "immune": -5},
        "builder": {"energy": 5, "hygiene": 0},
        "mage": {"hygiene": 5, "energy": -10, "food": -5},
        "adventurer": {"food": 10, "energy": 5, "immune": -5},
        "vagabond": {"food": -15, "hygiene": -15, "immune": -10, "energy": 5},
    }
    mods = archetype_mods.get(archetype, {})

    def _clamp(v): return max(20, min(100, v))

    defaults = {
        "food": _clamp(80 + wb + mods.get("food", 0)),
        "hydration": _clamp(80 + wb + mods.get("hydration", 0)),
        "energy": _clamp(75 + mods.get("energy", 0)),
        "temperature": 50,
        "waste": 0,
        "hygiene": _clamp(70 + wb + mods.get("hygiene", 0)),
        "immune": _clamp(70 + wb + mods.get("immune", 0)),
    }

    db = get_db()
    db.execute("""
        INSERT OR REPLACE INTO ext_sp_health
        (persona_uuid, food, hydration, energy, temperature, waste, hygiene, immune)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, defaults["food"], defaults["hydration"], defaults["energy"],
          defaults["temperature"], defaults["waste"], defaults["hygiene"],
          defaults["immune"]))


# ── Per-persona health processing ────────────────────────────────────

def process_persona_health(persona_uuid: str, archetype: str,
                           wealth_tier: str) -> dict:
    """Process one tick of health decay for a single persona.

    Args:
        persona_uuid: The persona
        archetype: For behavior modifiers
        wealth_tier: For recovery capability

    Returns:
        dict with status: 'healthy'|'declining'|'critical'|'dead'
    """
    db = get_db()
    cfg = get_config()

    health = db.fetch_one("SELECT * FROM ext_sp_health WHERE persona_uuid = ?",
                          (persona_uuid,))
    if not health or not health["alive"]:
        return {"status": "dead"}

    # Get current area for biome effects
    area = get_persona_area(persona_uuid)
    biome = area["biome_type"] if area else "plains"

    # ── 1. Temperature: biome effect + current weather ──────────
    temp_effect = _BIOME_TEMP_EFFECTS.get(biome, 0)
    shelter_mod = 0.5 if wealth_tier in ("middle", "wealthy", "elite") else 0.8

    # Apply live weather temperature
    if area:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import get_area_weather
        weather = get_area_weather(area["area_uuid"])
        if weather:
            ambient_temp = weather["temperature"]
            wind = weather.get("wind_speed", 0)
            # Wind chill: feels colder when windy and cold
            wind_chill = 0
            if ambient_temp < 15:
                wind_chill = wind * 0.3
            elif ambient_temp > 30:
                wind_chill = -wind * 0.1  # wind helps cool in heat
            # Body temp drifts toward ambient
            drift_rate = 0.08 + wind * 0.005
            body_drift = (ambient_temp / 60.0 * 100 - 50 - wind_chill - health["temperature"]) * drift_rate
            temp_delta = int(temp_effect * shelter_mod + body_drift)
        else:
            temp_delta = int(temp_effect * shelter_mod)
    else:
        temp_delta = int(temp_effect * shelter_mod)

    new_temp = max(0, min(100, health["temperature"] + temp_delta + random.randint(-1, 1)))
    if new_temp > 50:
        new_temp -= 1  # Natural cooling toward neutral
    elif new_temp < 50:
        new_temp += 1  # Natural warming toward neutral

    # ── 2. Food: base decay ──────────────────────────────────────
    activity_mod = 1.5 if archetype in ("miner", "warrior", "adventurer") else 1.0
    food_decay = max(1, int(random.uniform(1, 3) * activity_mod))
    new_food = max(0, health["food"] - food_decay)

    # ── 3. Hydration: base decay × weather ──────────────────────
    # Dry biomes with high temp dehydrate faster
    weather_dryness = 1.0
    if area:
        w = get_area_weather(area["area_uuid"])
        if w:
            if w["humidity"] < 30:
                weather_dryness = 1.8
            elif w["humidity"] > 80:
                weather_dryness = 0.7
            if w["temperature"] > 35:
                weather_dryness *= 1.5
    hydration_decay = max(1, int(random.uniform(2, 4) * weather_dryness))
    new_hydration = max(0, health["hydration"] - hydration_decay)

    # ── 4. Energy: base decay × weather (extreme temps drain energy) ─
    weather_strain = 1.0
    if area:
        w = get_area_weather(area["area_uuid"])
        if w:
            if w["temperature"] < 0 or w["temperature"] > 35:
                weather_strain = 2.0
            elif w["temperature"] < 10 or w["temperature"] > 30:
                weather_strain = 1.5
    energy_decay = max(1, int(random.uniform(2, 3) * activity_mod * weather_strain))
    new_energy = max(0, health["energy"] - energy_decay)

    # ── 5. Waste: accumulation ───────────────────────────────────
    waste_acc = random.randint(5, 12)
    new_waste = min(100, health["waste"] + waste_acc)

    # ── 6. Hygiene: decay × weather (rain keeps clean, droughts dirty) ─
    weather_hygiene = 1.0
    if area:
        w = get_area_weather(area["area_uuid"])
        if w:
            if w["is_raining"]:
                weather_hygiene = 0.3  # Rain washes
            elif w["humidity"] < 30:
                weather_hygiene = 1.5  # Dusty/dry
    hyg_decay = max(1, int(random.uniform(1, 2) * weather_hygiene + _HYGIENE_DECAY_BONUS.get(biome, 0)))
    new_hygiene = max(0, health["hygiene"] - hyg_decay)

    # ── 7. Immune system: synergistic decay ──────────────────────
    immune_base_decay = 1
    immune_extra = 0
    if new_food < 30: immune_extra += 1
    if new_hydration < 25: immune_extra += 2
    if new_energy < 20: immune_extra += 3
    if new_hygiene < 30: immune_extra += 1
    if new_waste > 70: immune_extra += 1
    if new_temp < 20 or new_temp > 80: immune_extra += 2

    new_immune = max(0, health["immune"] - immune_base_decay - immune_extra)

    # ── Autonomous survival behavior ─────────────────────────────
    # Personas try to keep themselves alive before decay fully applies.
    # These run even when the ecosystem extension is loaded (graceful skip
    # if not).
    if cfg.get("ecosystem_enabled", True):
        try:
            # Lazy import to avoid circular dependency at module level
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import (
                persona_forage, persona_hunt, persona_fish,
                persona_drink, persona_clean, persona_rest,
            )

            # ★ FOOD: Forage if hungry (< 40), then hunt/fish if starving (< 20)
            if new_food < 40:
                forage_result = persona_forage(persona_uuid)
                col = forage_result.get("food", 0)
                if col > 0:
                    new_food = min(100, new_food + col)
                # Still starving? Try hunting
                if new_food < 20:
                    hunt_result = persona_hunt(persona_uuid)
                    meat = hunt_result.get("meat", 0)
                    if meat > 0:
                        new_food = min(100, new_food + meat * 1.5)
                    else:
                        # Try fishing if in water biome
                        fish_result = persona_fish(persona_uuid)
                        fish = fish_result.get("fish", 0)
                        if fish > 0:
                            new_food = min(100, new_food + fish * 1.2)

                # Burn calories for foraging effort
                try:
                    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import burn_calories
                    burn_calories(persona_uuid, "forage")
                except Exception:
                    pass

            # ★ HYDRATION: Drink if thirsty (< 35)
            if new_hydration < 35:
                drink_result = persona_drink(persona_uuid)
                h_gain = drink_result.get("hydration", 0)
                if h_gain > 0:
                    new_hydration = min(100, new_hydration + h_gain)

                # Auto-drink from waterskin if available
                try:
                    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import (
                        count_item, give_item, get_item_def
                    )
                    water_count = count_item(persona_uuid, "clean_water")
                    if water_count > 0:
                        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import remove_item
                        removed = remove_item(persona_uuid, "clean_water", 1)
                        if removed > 0:
                            new_hydration = min(100, new_hydration + 30)
                except Exception:
                    pass

            # ★ HYGIENE/WASTE: Clean if dirty or backed up
            if new_hygiene < 30 or new_waste > 70:
                clean_result = persona_clean(persona_uuid)
                h_gain = clean_result.get("hygiene", 0)
                w_loss = clean_result.get("waste", 0)
                if h_gain > 0:
                    new_hygiene = min(100, new_hygiene + h_gain)
                if w_loss > 0:
                    new_waste = max(0, new_waste - w_loss)

            # ★ ENERGY: Rest if exhausted (< 25)
            if new_energy < 25:
                # Use building-enhanced rest if available
                try:
                    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_crafting import get_building_shelter_score
                    shelter = get_building_shelter_score(persona_uuid)
                    if shelter.get("has_shelter"):
                        e_gain = 15 + shelter.get("rest_bonus", 0)
                        new_energy = min(100, new_energy + e_gain)
                    else:
                        rest_result = persona_rest(persona_uuid)
                        e_gain = rest_result.get("energy", 0)
                        if e_gain > 0:
                            new_energy = min(100, new_energy + e_gain)
                except Exception:
                    rest_result = persona_rest(persona_uuid)
                    e_gain = rest_result.get("energy", 0)
                    if e_gain > 0:
                        new_energy = min(100, new_energy + e_gain)

            # ★ CLOTHING BONUS: Equipment insulation helps temperature
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import get_equipped_stats
                eq_stats = get_equipped_stats(persona_uuid)
                ins = eq_stats.get("total_insulation", 0)
                if ins > 0:
                    if new_temp < 35:  # Cold
                        new_temp = min(50, new_temp + ins * 3)
                    elif new_temp > 65:  # Hot (some clothing shades)
                        pass  # Heavy armor in heat = penalty elsewhere
            except Exception:
                pass

        except ImportError:
            # Ecosystem extension not loaded — personas must fend for
            # themselves via passive decay only
            pass
        except Exception as e:
            log.debug("sp_health",
                      f"Autonomous survival failed for {persona_uuid[:8]}: {e}")

    # ── Apply all stat changes ───────────────────────────────────
    db.execute("""
        UPDATE ext_sp_health SET
            food = ?, hydration = ?, energy = ?, temperature = ?,
            waste = ?, hygiene = ?, immune = ?
        WHERE persona_uuid = ?
    """, (new_food, new_hydration, new_energy, new_temp,
          new_waste, new_hygiene, new_immune, persona_uuid))

    # ── Death check ──────────────────────────────────────────────
    survival_score = (new_food + new_hydration + new_energy +
                      (100 - abs(new_temp - 50) * 2) +
                      (100 - new_waste) + new_hygiene + new_immune) / 7

    decay_timer = health["decay_timer"] or 0
    any_critical = (new_food == 0 or new_hydration == 0 or new_energy == 0 or
                    new_temp <= 5 or new_temp >= 95 or new_immune == 0)

    if any_critical:
        decay_timer += 1
    else:
        decay_timer = 0  # Reset if recovering

    # Determine death thresholds per stat
    death_cause = None
    if new_food == 0 and decay_timer >= 3:
        death_cause = "starvation"
    elif new_hydration == 0 and decay_timer >= 2:
        death_cause = "dehydration"
    elif new_energy == 0 and decay_timer >= 5:
        death_cause = "exhaustion"
    elif new_temp <= 5 and decay_timer >= 2:
        death_cause = "hypothermia"
    elif new_temp >= 95 and decay_timer >= 2:
        death_cause = "hyperthermia"
    elif new_immune == 0 and new_hygiene < 20:
        death_cause = "sepsis"
    elif new_waste >= 100 and decay_timer >= 2:
        death_cause = "toxin_poisoning"

    if death_cause:
        # Persona dies
        db.execute("""
            UPDATE ext_sp_health SET alive = 0, cause_of_death = ?, decay_timer = ?
            WHERE persona_uuid = ?
        """, (death_cause, decay_timer, persona_uuid))
        # Deactivate from simulation
        deactivate_persona(persona_uuid)
        # Record death in memory
        add_memory(persona_uuid, "life_event",
                   detail={"event": "death", "cause": death_cause},
                   emotional_weight=10)
        log.info("sp_health",
                 f"Persona {persona_uuid[:8]} died: {death_cause}")

        return {"status": "dead", "cause": death_cause}

    # Update decay timer
    db.execute("UPDATE ext_sp_health SET decay_timer = ? WHERE persona_uuid = ?",
               (decay_timer, persona_uuid))

    # Determine status based on survival score
    if survival_score < 10:
        return {"status": "critical"}
    elif survival_score < 30:
        return {"status": "declining"}
    else:
        return {"status": "healthy"}


# ── Health query helpers (for ecosystem & threat system) ────────────

def get_persona_health(persona_uuid: str) -> Optional[dict]:
    """Get raw health stats for a persona.

    Returns dict with food, hydration, energy, temperature,
    waste, hygiene, immune, alive, or None.
    """
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM ext_sp_health WHERE persona_uuid = ?",
        (persona_uuid,))


def modify_health(persona_uuid: str, **kwargs) -> bool:
    """Modify one or more health stats for a persona.

    Keyword args can be: food, hydration, energy, temperature,
    waste, hygiene, immune.  Positive = gain, negative = loss.

    Returns True if the persona was found and updated.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"modify_health failed: {e}")
        return False
    current = db.fetch_one(
        "SELECT * FROM ext_sp_health WHERE persona_uuid = ?",
        (persona_uuid,))
    if not current:
        return False

    updates = {}
    for key, delta in kwargs.items():
        if key in ("food", "hydration", "energy", "temperature",
                   "waste", "hygiene", "immune"):
            new_val = current[key] + delta
            updates[key] = max(0, min(100, new_val))

    if not updates:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [persona_uuid]
    db.execute(
        f"UPDATE ext_sp_health SET {set_clause} WHERE persona_uuid = ?",
        values)
    return True


# ── Bulk health tick ─────────────────────────────────────────────────

def process_health_tick() -> dict:
    """Process health for all active personas.

    Returns:
        dict with counts: processed, deaths, critical, declining
    """
    personas = get_active_personas()
    deaths = []
    critical = []
    declining = []

    for p in personas:
        try:
            result = process_persona_health(
                p["persona_uuid"],
                p.get("archetype", "adventurer"),
                p.get("wealth_tier", "working"),
            )
            if result["status"] == "dead":
                deaths.append({"name": p.get("name", "?"),
                               "cause": result.get("cause", "unknown")})
            elif result["status"] == "critical":
                critical.append(p.get("name", "?"))
            elif result["status"] == "declining":
                declining.append(p.get("name", "?"))
        except Exception as e:
            log.error("sp_health",
                      f"Health error for {p.get('name', '?')}: {e}")

    if deaths:
        log.info("sp_health", f"{len(deaths)} deaths, {len(critical)} critical, {len(declining)} declining")

    return {
        "processed": len(personas),
        "deaths": deaths,
        "critical": critical,
        "declining": declining,
    }

