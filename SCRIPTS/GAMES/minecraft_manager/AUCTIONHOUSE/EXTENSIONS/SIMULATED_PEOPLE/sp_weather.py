"""
sp_weather.py — Dynamic weather system for the Simulated People world.

Models temperature, humidity, clouds, precipitation, wind, and seasons
across all world areas.  Weather emerges from simplified physics (solar
radiation, pressure gradients, adiabatic cooling) rather than random rolls.

Weather state is stored per-area and updated every simulation tick (~1 hour).
The system connects to persona health: extreme temps/dryness/wetness
affect health decay rates and persona behavior.
"""

import math, random, json
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_db, ensure_schema, get_active_world_events,
    add_memory, update_finance,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas, get_area
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import save_skills, get_skills
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ── Constants ─────────────────────────────────────────────────────────

_BASE_TEMPS = {
    "plains": 20, "forest": 18, "desert": 35, "swamp": 25,
    "mountains": 8, "ocean": 16, "tundra": -5,
    "nether_wastes": 45, "crimson_forest": 35,
    "end_islands": 5, "deep_dark": 10,
}

_HUMIDITY_BASES = {
    "plains": 60, "forest": 70, "desert": 15, "swamp": 85,
    "mountains": 50, "ocean": 80, "tundra": 40,
    "nether_wastes": 5, "crimson_forest": 40,
    "end_islands": 10, "deep_dark": 50,
}

_ELEVATIONS = {
    "plains": 100, "forest": 200, "desert": 300, "swamp": 50,
    "mountains": 1500, "ocean": 0, "tundra": 500,
    "nether_wastes": 64, "crimson_forest": 64,
    "end_islands": 128, "deep_dark": -400,
}

_OCEAN_BIOMES = {"ocean"}

_TICK_HOUR = 1  # Each tick = 1 game-hour
_TICKS_PER_DAY = 24

# Global tick counter for the weather system
_weather_tick = 0


# ── Solar radiation ─────────────────────────────────────────────────

def _solar_radiation(hour: int, day_of_year: int, latitude: float = 0.5,
                     cloud_cover: float = 0.0) -> float:
    """Calculate incoming solar radiation at a given time and location.

    Args:
        hour: 0-23 (game hour)
        day_of_year: 0-365
        latitude: 0-1 (0=north pole, 0.5=equator, 1=south pole)
        cloud_cover: 0-1 fraction

    Returns:
        Radiation in arbitrary units (0-1000)
    """
    # Solar angle based on hour
    angle = math.sin(math.pi * (hour - 6) / 12) if 6 <= hour <= 18 else 0
    if angle <= 0:
        return 0  # Night

    # Seasonal tilt
    season_angle = math.sin(2 * math.pi * (day_of_year - 80) / 365)
    latitude_factor = 1 - abs(latitude - 0.5) * 1.2
    tilt = season_angle * 0.3 * latitude_factor

    solar_elevation = max(0, angle + tilt)
    radiation = 1000 * solar_elevation

    # Cloud attenuation
    radiation *= (1 - 0.6 * cloud_cover)

    return max(0, radiation)


# ── Seasonal temperature offset ──────────────────────────────────────

def _seasonal_offset(day_of_year: int, latitude: float = 0.5) -> float:
    """Calculate seasonal temperature offset.

    Returns °C offset (e.g., -8 in winter, +8 in summer at mid-latitude).
    """
    # TODO: Support configurable hemisphere (currently assumes Northern)
    # Day 0 = Jan 1 (northern hemisphere winter)
    season_angle = 2 * math.pi * (day_of_year - 15) / 365
    amplitude = 10 * (1 - abs(latitude - 0.5) * 0.8)
    return amplitude * math.sin(season_angle)


# ── Initialize weather for all areas ─────────────────────────────────

def init_weather():
    """Initialize weather state for all world areas.

    Sets starting temperature, humidity, pressure based on biome.
    """
    db = get_db()
    areas = get_all_areas()
    now = datetime.now(timezone.utc).isoformat()
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday

    for area in areas:
        biome = area["biome_type"]
        elevation = _ELEVATIONS.get(biome, 100)
        base_temp = _BASE_TEMPS.get(biome, 20)
        season = _seasonal_offset(day_of_year)
        elevated_bonus = -elevation * 0.006  # Lapse rate: 6°C per 1000m
        starting_temp = base_temp + season * 0.3 + elevated_bonus + 5

        db.execute("""
            INSERT OR REPLACE INTO ext_sp_weather
            (area_uuid, temperature, humidity, cloud_cover,
             precipitation_mm, wind_speed, wind_direction, pressure,
             is_raining, is_snowing, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        """, (area["area_uuid"],
              round(starting_temp, 1),
              _HUMIDITY_BASES.get(biome, 50),
              0.2 + random.random() * 0.2,
              0.0,
              2 + random.random() * 3,
              random.random() * 360,
              1013 + random.randint(-20, 20),
              now))

    log.info("sp_weather", f"Weather initialized for {len(areas)} areas")


# ── Get weather for an area ─────────────────────────────────────────

def get_area_weather(area_uuid: str) -> Optional[dict]:
    """Get current weather for an area."""
    db = get_db()
    weather = db.fetch_one(
        "SELECT * FROM ext_sp_weather WHERE area_uuid = ?", (area_uuid,))
    return weather


def get_weather_summary() -> str:
    """Get a human-readable summary of weather across the world."""
    db = get_db()
    areas = db.fetch_all("""
        SELECT a.name, a.biome_type, w.temperature, w.humidity,
               w.cloud_cover, w.precipitation_mm, w.wind_speed,
               w.is_raining, w.is_snowing
        FROM ext_sp_weather w
        JOIN ext_sp_world_areas a ON w.area_uuid = a.area_uuid
        ORDER BY w.temperature DESC
        LIMIT 5
    """)
    lines = ["§6═══ §eWorld Weather §6═══"]
    for a in areas:
        icon = "☀" if not a["is_raining"] else "🌧" if a["is_raining"] and not a["is_snowing"] else "❄"
        lines.append(
            f" {icon} §f{a['name']:22s} §7{a['temperature']:>5.1f}°C  "
            f"☂{a['humidity']:3.0f}%  💨{a['wind_speed']:4.1f}m/s"
        )
    return "\n".join(lines)


# ── Weather tick ────────────────────────────────────────────────────

def update_weather() -> dict:
    """Run one weather tick for all areas.

    Returns:
        Dict with counts: updated, extremes
    """
    global _weather_tick
    _weather_tick += 1
    db = get_db()

    # Constants for the tick
    hour = _weather_tick % _TICKS_PER_DAY
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday + (_weather_tick // _TICKS_PER_DAY) % 365

    now = datetime.now(timezone.utc).isoformat()
    areas = get_all_areas()
    extremes = []

    # Step 1: Gather current state for all areas
    all_weather = {}
    for area in areas:
        w = get_area_weather(area["area_uuid"])
        if w:
            all_weather[area["area_uuid"]] = w

    # Step 2: Build neighbor temperature/pressure gradients
    for area in areas:
        area_uuid = area["area_uuid"]
        w = all_weather.get(area_uuid)
        if not w:
            continue

        biome = area["biome_type"]
        neighbors = []
        try:
            neighbors = json.loads(area["neighbor_ids"]) if isinstance(area["neighbor_ids"], str) else (area["neighbor_ids"] or [])
        except (json.JSONDecodeError, TypeError):
            pass

        old_temp = w["temperature"]
        old_humidity = w["humidity"]
        old_cloud = w["cloud_cover"]

        # --- Solar radiation ---
        radiation = _solar_radiation(hour, day_of_year)
        heating_rate = 0.005  # °C per unit radiation per tick
        solar_heating = radiation * heating_rate

        # --- Cloud cooling (clouds block sun during day, trap heat at night) ---
        if 6 <= hour <= 18:
            cloud_effect = -old_cloud * 0.5  # Cooling during day
        else:
            cloud_effect = old_cloud * 0.2  # Insulation at night

        # --- Advection from neighbors ---
        adv_temp = 0
        adv_humidity = 0
        adv_cloud = 0
        n_count = 0
        for n_id in neighbors:
            nw = all_weather.get(n_id)
            if nw:
                adv_temp += (nw["temperature"] - old_temp) * 0.02
                adv_humidity += (nw["humidity"] - old_humidity) * 0.01
                adv_cloud += (nw["cloud_cover"] - old_cloud) * 0.01
                n_count += 1
        if n_count > 0:
            adv_temp /= n_count
            adv_humidity /= n_count
            adv_cloud /= n_count

        # --- Seasonal baseline ---
        season = _seasonal_offset(day_of_year)
        baseline = _BASE_TEMPS.get(biome, 20)
        elevation = _ELEVATIONS.get(biome, 100)
        lapse = -elevation * 0.006
        target_temp = baseline + season * 0.1 + lapse

        # Drift toward baseline (world keeps its character)
        drift = (target_temp - old_temp) * 0.005

        # --- Temperature update ---
        new_temp = old_temp + solar_heating + cloud_effect + adv_temp + drift + random.uniform(-0.2, 0.2)
        new_temp = max(-20, min(60, new_temp))

        # --- Humidity update ---
        is_ocean = biome in _OCEAN_BIOMES
        ocean_evap = 2.0 if is_ocean else 0.0
        rain_evap = -0.5 if old_cloud > 0.7 else 0.0
        daytime_dry = -0.2 if 10 <= hour <= 16 else 0.0

        new_humidity = old_humidity + adv_humidity + ocean_evap + rain_evap + daytime_dry + random.uniform(-0.5, 0.5)

        # ── Evapotranspiration from plants ──────────────────────
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import calculate_evapotranspiration
            et_data = calculate_evapotranspiration()
            et_boost = et_data.get(area_uuid, 0)
            new_humidity += et_boost
        except Exception:
            pass  # Ecosystem not loaded or error — harmless

        new_humidity = max(5, min(100, new_humidity))

        # --- Cloud cover ---
        # Higher humidity = more clouds. Also affected by temperature (adiabatic cooling)
        target_cloud = max(0, (new_humidity - 40) / 60) if new_humidity > 40 else 0
        new_cloud = old_cloud + (target_cloud - old_cloud) * 0.05 + adv_cloud + random.uniform(-0.02, 0.02)
        new_cloud = max(0, min(1, new_cloud))

        # --- Precipitation ---
        is_raining = 0
        is_snowing = 0
        precip = 0.0

        if new_cloud > 0.7 and new_humidity > 70:
            precip = max(0, (new_cloud - 0.7) * 15 * (new_humidity / 100))
            is_raining = 1
            is_snowing = 1 if new_temp <= 0 else 0
            # Rain removes cloud water
            new_cloud = max(0, new_cloud - precip * 0.01)

        # --- Pressure ---
        # Higher temp → lower pressure (thermal low)
        pressure_target = 1013 - (new_temp - 15) * 2 + random.uniform(-5, 5)
        new_pressure = w["pressure"] + (pressure_target - w["pressure"]) * 0.05

        # --- Wind ---
        wind_target = max(1, abs(1013 - new_pressure) * 0.1 + random.uniform(0, 3))
        new_wind_speed = w["wind_speed"] + (wind_target - w["wind_speed"]) * 0.05
        new_wind_direction = (w["wind_direction"] + random.uniform(-10, 10)) % 360

        # --- Save ---
        db.execute("""
            UPDATE ext_sp_weather SET
                temperature = ?, humidity = ?, cloud_cover = ?,
                precipitation_mm = ?, wind_speed = ?, wind_direction = ?,
                pressure = ?, is_raining = ?, is_snowing = ?, updated_at = ?
            WHERE area_uuid = ?
        """, (round(new_temp, 1), round(new_humidity, 1), round(new_cloud, 3),
              round(precip, 2), round(new_wind_speed, 1), round(new_wind_direction, 1),
              round(new_pressure, 1), is_raining, is_snowing, now, area_uuid))

        # --- Check for extremes ---
        if new_temp >= 40:
            extremes.append({"area": area["name"], "type": "heatwave", "temp": new_temp})
        elif new_temp <= -10:
            extremes.append({"area": area["name"], "type": "blizzard", "temp": new_temp})
        elif precip > 10:
            extremes.append({"area": area["name"], "type": "storm", "precip": precip})

    # Step 3: Log extreme weather
    if extremes and _weather_tick % 6 == 0:  # Every 6 hours
        for e in extremes[:3]:
            log.info("sp_weather",
                     f"Extreme weather: {e['type']} in {e.get('area', '?')} "
                     f"({e.get('temp', '')}{e.get('precip', '')})")

    # Step 4: Update all areas' descriptions with current weather
    # (stored in ext_sp_world_areas for the UI to display)
    if _weather_tick % 6 == 0:  # Every 6 game-hours
        extremes_clean = [e for e in extremes if e.get("type") in ("heatwave", "blizzard", "storm")]
        pass  # TODO: Store weather description for UI display; currently queryable via get_weather_summary()

    return {
        "updated": len(areas),
        "extremes": len(extremes),
        "hour": hour,
        "day": day_of_year,
        "weather_events": extremes[:5],
    }


# ══════════════════════════════════════════════════════════════════════
# Persona Quest System
# ══════════════════════════════════════════════════════════════════════

_QUEST_TEMPLATES = [
    ("dungeon_delve", "descends into a dark cave system seeking treasure",
     "returned with {item}", 10),
    ("hunting_expedition", "ventures into the wilderness to hunt",
     "brought back {item} from the hunt", 8),
    ("fishing_trip", "spends the day fishing at the nearest river",
     "caught a {item} while fishing", 5),
    ("foraging_run", "gathers wild plants and materials",
     "found {item} while foraging", 6),
    ("trade_run", "travels to a neighboring settlement",
     "acquired {item} through trade", 12),
    ("scouting_mission", "explores uncharted territory",
     "discovered {item} on the frontier", 15),
    ("repair_work", "takes on repair contracts from locals",
     "earned coins repairing {item}", 7),
]

_QUEST_ITEMS = [
    "minecraft:iron_ingot", "minecraft:gold_ingot", "minecraft:diamond",
    "minecraft:emerald", "minecraft:cooked_beef", "minecraft:bread",
    "minecraft:arrow", "minecraft:leather", "minecraft:iron_sword",
    "minecraft:ender_pearl", "minecraft:blaze_rod", "minecraft:bone",
    "minecraft:redstone", "minecraft:lapis_lazuli",
]


def process_quests() -> dict:
    """Roll for and process persona quests.

    Personas with high energy, good health, and sufficient funds
    may go on a quest (adventure) that takes them out of the market
    for 1-3 ticks and rewards them with items.

    Returns:
        Dict with count of quests started/completed
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"process_quests failed: {e}")
        return {}
    cfg = get_config()
    started = 0
    completed = 0

    # Get personas that could quest (active, not already questing)
    personas = db.fetch_all("""
        SELECT p.*, h.energy, h.food, h.hydration
        FROM ext_sp_profiles p
        JOIN ext_sp_health h ON p.persona_uuid = h.persona_uuid
        WHERE p.active = 1 AND h.alive = 1
          AND h.energy > 40 AND h.food > 30 AND h.hydration > 30
          AND (p.region = 'overworld')
        ORDER BY RANDOM()
        LIMIT 5
    """)

    for p in personas:
        # Check mood: persona must have enough energy and a bit of spirit
        if random.random() > 0.2:
            continue

        # Pick quest
        quest = random.choice(_QUEST_TEMPLATES)
        quest_type = quest[0]
        quest_desc = quest[1]
        quest_reward_desc = quest[2]
        quest_cooldown = quest[3]

        item_reward = random.choice(_QUEST_ITEMS)
        coin_reward = round(random.uniform(1, 15), 2)

        # Add quest memory
        add_memory(p["persona_uuid"], "life_event",
                       detail={"event": f"quest_{quest_type}",
                               "description": f"{p['name']} {quest_desc}",
                               "reward_item": item_reward,
                               "reward_coins": coin_reward},
                       emotional_weight=6)

        # Credit reward
        update_finance(p["persona_uuid"], coin_reward, reason=f"quest_{quest_type}")

        # Boost skills
        skill_boost = {"combat": 1, "exploration": 1, "crafting": 1}

        skills = get_skills(p["persona_uuid"]) or {}
        for s, boost in skill_boost.items():
            if s in skills:
                skills[s] = min(100, skills[s] + boost)
        if skills:
            save_skills(p["persona_uuid"], skills)

        log.info("sp_weather",
                 f"Quest: {p['name']} {quest_desc} — "
                 f"earned {coin_reward}em + {item_reward}")
        started += 1

    return {"quests_started": started}

