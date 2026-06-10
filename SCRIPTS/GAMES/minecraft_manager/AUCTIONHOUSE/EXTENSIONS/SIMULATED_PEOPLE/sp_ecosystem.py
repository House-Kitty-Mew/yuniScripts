"""
sp_ecosystem.py — Dynamic flora & fauna simulation for the Simulated People world.

Adds a living layer to every area: plants grow and die with the seasons,
animal agents roam the map, small creatures swarm at a density level,
and personacons must forage, hunt, fish, and defend themselves.

Architecture
────────────
  Plant system (per area)
    biomass_grass, biomass_shrub, biomass_tree, biomass_root
    Growth is temperature/water/light/fertility dependent.
    Layers compete for light (taller shades shorter).
    Litterfall → organic matter → soil fertility feedback.
    food_plant_abundance derived from edible parts.

  Animal agents (individual, limited to ~50 total)
    Deer, Wolf, Bear as tracked agents.
    Sense → decide → move → consume → metabolise each tick.
    Carnivores can attack weakened persona.

  Density-based fauna (per area scalar)
    Rabbit, Fox, Bird densities follow simple logistic dynamics.
    Predator–prey coupling (fox eats rabbit).

  Persona interactions
    forage(area) → collect food_plant_abundance → reduce it.
    hunt(area) → attempt against animal_agents → meat & hide.
    fish(area) → if water biome, reduce fish density.
    Threatened by wolves/bears when health is low.
"""

import json, math, random, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_db, ensure_schema, get_active_personas, add_memory,
    add_life_event, get_active_world_events,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import (
    get_all_areas, get_area, get_persona_area,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import (
    get_persona_health, modify_health,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Species definitions
# ══════════════════════════════════════════════════════════════════════

# ── Plant biomass defaults per biome (starting values) ──────────────

_PLANT_DEFAULTS = {
    "plains":         {"grass": 800,  "shrub": 150,  "tree": 50,   "root": 200, "lai": 3.0, "food": 80},
    "forest":         {"grass": 200,  "shrub": 400,  "tree": 2000, "root": 150, "lai": 5.0, "food": 120},
    "desert":         {"grass": 20,   "shrub": 30,   "tree": 5,    "root": 40,  "lai": 0.2, "food": 10},
    "swamp":          {"grass": 300,  "shrub": 500,  "tree": 1200, "root": 100, "lai": 4.0, "food": 60},
    "mountains":      {"grass": 100,  "shrub": 80,   "tree": 300,  "root": 80,  "lai": 1.5, "food": 30},
    "ocean":          {"grass": 5,    "shrub": 0,    "tree": 0,    "root": 100, "lai": 0.0, "food": 200},
    "tundra":         {"grass": 40,   "shrub": 20,   "tree": 0,    "root": 30,  "lai": 0.3, "food": 15},
    "nether_wastes":  {"grass": 0,    "shrub": 0,    "tree": 0,    "root": 0,   "lai": 0.0, "food": 0},
    "crimson_forest": {"grass": 0,    "shrub": 100,  "tree": 500,  "root": 0,   "lai": 2.0, "food": 20},
    "end_islands":    {"grass": 0,    "shrub": 0,    "tree": 0,    "root": 0,   "lai": 0.0, "food": 5},
    "deep_dark":      {"grass": 0,    "shrub": 10,   "tree": 0,    "root": 20,  "lai": 0.0, "food": 5},
}

# ── Agent animal species ─────────────────────────────────────────────

ANIMAL_SPECIES = {
    "deer": {
        "diet": "herbivore",
        "body_mass_kg": 80,
        "metabolic_rate": 2.0,        # energy consumed per tick
        "max_energy": 100,
        "max_hydration": 100,
        "speed_cells": 2,
        "reproduction_rate": 0.02,    # chance per tick when conditions good
        "lifespan_days": 365,
        "preferred_biomes": ["plains", "forest", "swamp"],
        "threat_level": 0,            # 0 = harmless
        "diet_items": ["grass", "shrub"],
        "density_prey": False,
        "pack_size": 4,
        "diurnal": True,
        "hibernates": False,
    },
    "wolf": {
        "diet": "carnivore",
        "body_mass_kg": 40,
        "metabolic_rate": 3.0,
        "max_energy": 100,
        "max_hydration": 100,
        "speed_cells": 3,
        "reproduction_rate": 0.01,
        "lifespan_days": 600,
        "preferred_biomes": ["forest", "plains", "tundra", "mountains"],
        "threat_level": 5,            # dangerous to weak personas
        "diet_items": ["deer", "rabbit"],
        "density_prey": True,
        "pack_size": 5,
        "diurnal": False,             # nocturnal
        "hibernates": False,
    },
    "bear": {
        "diet": "omnivore",
        "body_mass_kg": 200,
        "metabolic_rate": 4.0,
        "max_energy": 150,
        "max_hydration": 100,
        "speed_cells": 2,
        "reproduction_rate": 0.005,
        "lifespan_days": 1200,
        "preferred_biomes": ["forest", "mountains", "tundra", "swamp"],
        "threat_level": 7,            # very dangerous
        "diet_items": ["deer", "rabbit", "fish", "berries", "root"],
        "density_prey": False,
        "pack_size": 1,
        "diurnal": True,
        "hibernates": True,
    },
}

# ── Density-based small creature species ─────────────────────────────

DENSITY_SPECIES = {
    "rabbit": {
        "diet": "herbivore",
        "base_growth": 0.08,
        "base_mortality": 0.04,
        "max_density": 80,
        "preferred_biomes": ["plains", "forest", "swamp"],
        "carry_capacity_factor": 0.01,
        "edible": True,
        "predator_species": ["wolf", "fox"],
    },
    "fox": {
        "diet": "carnivore",
        "base_growth": 0.05,
        "base_mortality": 0.03,
        "max_density": 20,
        "preferred_biomes": ["forest", "plains", "mountains"],
        "carry_capacity_factor": 0.005,
        "edible": False,
        "predator_species": ["wolf", "bear"],
    },
    "bird": {
        "diet": "scavenger",
        "base_growth": 0.06,
        "base_mortality": 0.05,
        "max_density": 100,
        "preferred_biomes": ["plains", "forest", "swamp", "ocean", "mountains"],
        "carry_capacity_factor": 0.015,
        "edible": True,
        "predator_species": ["fox"],
    },
    "fish": {
        "diet": "herbivore",
        "base_growth": 0.07,
        "base_mortality": 0.04,
        "max_density": 200,
        "preferred_biomes": ["ocean", "swamp"],
        "carry_capacity_factor": 0.05,
        "edible": True,
        "predator_species": ["bear"],
    },
}

# ── Forage yields per biome ──────────────────────────────────────────

_FORAGE_YIELD = {
    "plains":         {"berries": 0.3, "herbs": 0.4, "mushrooms": 0.1, "nuts": 0.1, "tubers": 0.1},
    "forest":         {"berries": 0.3, "herbs": 0.2, "mushrooms": 0.2, "nuts": 0.2, "tubers": 0.1},
    "desert":         {"berries": 0.0, "herbs": 0.1, "mushrooms": 0.0, "nuts": 0.0, "tubers": 0.9},
    "swamp":          {"berries": 0.1, "herbs": 0.2, "mushrooms": 0.4, "nuts": 0.1, "tubers": 0.2},
    "mountains":      {"berries": 0.2, "herbs": 0.3, "mushrooms": 0.2, "nuts": 0.1, "tubers": 0.2},
    "ocean":          {"berries": 0.0, "herbs": 0.0, "mushrooms": 0.0, "nuts": 0.0, "tubers": 0.0},
    "tundra":         {"berries": 0.1, "herbs": 0.2, "mushrooms": 0.0, "nuts": 0.0, "tubers": 0.7},
    "nether_wastes":  {"berries": 0.0, "herbs": 0.0, "mushrooms": 0.0, "nuts": 0.0, "tubers": 0.0},
    "crimson_forest": {"berries": 0.0, "herbs": 0.0, "mushrooms": 0.5, "nuts": 0.0, "tubers": 0.5},
    "end_islands":    {"berries": 0.0, "herbs": 0.0, "mushrooms": 0.0, "nuts": 0.0, "tubers": 0.0},
    "deep_dark":      {"berries": 0.0, "herbs": 0.0, "mushrooms": 0.8, "nuts": 0.0, "tubers": 0.2},
}

_ANIMAL_NAMES_MALE = ["Rusty", "Shadow", "Boulder", "Storm", "Oak", "Ash",
                       "Thorn", "Claw", "Snap", "Howl", "Fang", "Grizzle"]
_ANIMAL_NAMES_FEMALE = ["Doe", "Luna", "Willow", "Hazel", "Fern", "Ivy",
                         "Snow", "Rose", "Clover", "Maple", "Olive", "Sable"]


# ══════════════════════════════════════════════════════════════════════
# Initialization
# ══════════════════════════════════════════════════════════════════════

def init_ecosystem():
    """Seed plant biomass and animal density for all world areas.

    Safe to call multiple times – already-seeded areas are skipped.
    """
    db = get_db()
    areas = get_all_areas()
    cfg = get_config()

    for area in areas:
        biome = area["biome_type"]

        # Skip if already seeded
        existing = db.fetch_one(
            "SELECT id FROM ext_sp_ecosystem WHERE area_uuid = ?",
            (area["area_uuid"],))
        if existing:
            continue

        defaults = _PLANT_DEFAULTS.get(biome, _PLANT_DEFAULTS["plains"])
        db.execute("""
            INSERT INTO ext_sp_ecosystem
            (area_uuid, biomass_grass, biomass_shrub, biomass_tree,
             biomass_root, leaf_area_index, seeds_in_soil,
             food_plant_abundance, litter_biomass, last_growth_tick)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            area["area_uuid"],
            defaults["grass"]  * random.uniform(0.8, 1.2),
            defaults["shrub"]  * random.uniform(0.8, 1.2),
            defaults["tree"]   * random.uniform(0.8, 1.2),
            defaults["root"]   * random.uniform(0.8, 1.2),
            defaults["lai"]    * random.uniform(0.8, 1.2),
            defaults["grass"] * 0.5,
            defaults["food"]   * random.uniform(0.8, 1.2),
            defaults["grass"] * 0.3,
        ))

        # Seed density-based species
        for sp_name, sp_data in DENSITY_SPECIES.items():
            if biome in sp_data["preferred_biomes"]:
                base = 5 if sp_name == "fish" else 2
            else:
                base = 0
            db.execute("""
                INSERT OR IGNORE INTO ext_sp_animal_density
                (area_uuid, species, density)
                VALUES (?, ?, ?)
            """, (area["area_uuid"], sp_name,
                  base + random.randint(0, int(base))))

    # Spawn initial animal agents
    _spawn_initial_agents()

    log.info("sp_ecosystem", "Ecosystem initialized for all areas")


def _spawn_initial_agents():
    """Create the first set of animal agents across the world."""
    db = get_db()
    cfg = get_config()
    max_agents = cfg.get("max_animal_agents", 50)
    areas = get_all_areas()

    # Count existing agents
    existing = db.fetch_one("SELECT COUNT(*) as c FROM ext_sp_animal_agents WHERE is_alive = 1")
    if existing and existing["c"] >= max_agents:
        return

    target = min(max_agents, len(areas) * 2)
    already = existing["c"] if existing else 0
    to_spawn = target - already

    if to_spawn <= 0:
        return

    for _ in range(to_spawn):
        area = random.choice(areas)
        biome = area["biome_type"]

        # Roll species
        species = _roll_species_for_biome(biome)
        name = random.choice(_ANIMAL_NAMES_MALE + _ANIMAL_NAMES_FEMALE)

        db.execute("""
            INSERT INTO ext_sp_animal_agents
            (animal_uuid, species, name, area_uuid, energy, hydration,
             health, age, age_days, is_alive, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
        """, (
            str(uuid_mod.uuid4()),
            species,
            f"{name} the {species.title()}",
            area["area_uuid"],
            100.0, 100.0, 100.0,
            random.uniform(0.5, 5.0),
            datetime.now(timezone.utc).isoformat(),
        ))


def _roll_species_for_biome(biome: str) -> str:
    """Pick an animal species appropriate for a biome."""
    candidates = []
    for sp_name, sp_data in ANIMAL_SPECIES.items():
        if biome in sp_data["preferred_biomes"]:
            candidates.append(sp_name)
    if not candidates:
        candidates = ["deer", "wolf"]  # fallback
    weights = {"deer": 5, "wolf": 2, "bear": 1}
    return random.choices(
        candidates,
        weights=[weights.get(s, 1) for s in candidates],
        k=1
    )[0]


# ══════════════════════════════════════════════════════════════════════
# Plant system – daily tick
# ══════════════════════════════════════════════════════════════════════

def process_plant_tick(tick_number: int) -> dict:
    """Run one day of plant growth across all areas.

    Args:
        tick_number: Current simulation tick (used for throttling).

    Returns:
        dict summary of growth events.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"process_plant_tick failed: {e}")
        return {}
    if not cfg.get("ecosystem_enabled", True):
        return {"status": "disabled"}

    db = get_db()
    areas = get_all_areas()
    total_growth = 0
    total_death = 0
    area_count = 0

    for area in areas:
        eco = db.fetch_one(
            "SELECT * FROM ext_sp_ecosystem WHERE area_uuid = ?",
            (area["area_uuid"],))
        if not eco:
            continue

        # Get weather for this area
        weather = db.fetch_one(
            "SELECT * FROM ext_sp_weather WHERE area_uuid = ?",
            (area["area_uuid"],))
        biome = area["biome_type"]

        # Only process every N ticks (configurable)
        interval = cfg.get("ecosystem_tick_interval", 6)
        if interval < 1:
            interval = 1
        if tick_number % interval != 0:
            continue

        # ── Climate factors ──────────────────────────────────
        temp = weather["temperature"] if weather else 20.0
        humidity = weather["humidity"] if weather else 50.0
        soil_moisture = humidity / 100.0
        rainfall = weather["precipitation_mm"] if weather else 0

        # ── Growth factors ───────────────────────────────────
        # Temperature factor: optimal 15-30°C
        temp_factor = max(0, min(1,
            (temp - (-5)) / 25 if temp < 15 else
            1.0 if temp <= 30 else
            max(0, 1 - (temp - 30) / 20)
        ))

        # Water factor
        water_factor = min(1.0, soil_moisture * 1.5 + rainfall / 50)

        # Season factor (from rainfall and temp)
        season_factor = temp_factor * water_factor

        # ── Layer-specific growth ────────────────────────────
        growth_rate = cfg.get("plant_growth_rate", 1.0)

        # Grass (fastest, needs light)
        lai_above = eco["leaf_area_index"] * 0.3  # light reaching ground
        light_factor_grass = max(0.1, 1.0 - eco["leaf_area_index"] * 0.15)
        grass_growth = 15 * season_factor * light_factor_grass * growth_rate
        grass_loss = eco["biomass_grass"] * 0.02 * (1 + (1 - temp_factor) * 2)

        # Shrub (moderate, shade-tolerant)
        light_factor_shrub = max(0.1, 1.0 - (eco["leaf_area_index"] * 0.1))
        shrub_growth = 8 * season_factor * light_factor_shrub * growth_rate
        shrub_loss = eco["biomass_shrub"] * 0.015 * (1 + (1 - temp_factor) * 2)

        # Tree (slow but tall)
        light_factor_tree = 0.8  # Trees get most light
        tree_growth = 3 * season_factor * light_factor_tree * growth_rate
        tree_loss = eco["biomass_tree"] * 0.005 * (1 + (1 - temp_factor) * 3)

        # Root (feeds from all layers)
        root_growth = (grass_growth + shrub_growth + tree_growth) * 0.1
        root_loss = eco["biomass_root"] * 0.01

        # Update biomass
        new_grass = max(0, eco["biomass_grass"] + grass_growth - grass_loss)
        new_shrub = max(0, eco["biomass_shrub"] + shrub_growth - shrub_loss)
        new_tree = max(0, eco["biomass_tree"] + tree_growth - tree_loss)
        new_root = max(0, eco["biomass_root"] + root_growth - root_loss)

        # Recalculate LAI from total biomass
        total_biomass = new_grass + new_shrub + new_tree
        new_lai = min(8.0, total_biomass * 0.002 + 0.2)

        # Recaculate food abundance
        food_abundance = (
            new_grass * 0.02 +       # seeds/grains
            new_shrub * 0.05 +       # berries on shrubs
            new_tree * 0.03 +        # fruits/nuts
            new_root * 0.10          # edible tubers
        )
        food_abundance = max(0, min(500, food_abundance))

        # Litter decomposition → organic matter
        litter_decay = eco["litter_biomass"] * 0.05 * temp_factor
        new_litter = eco["litter_biomass"] - litter_decay + (
            grass_loss + shrub_loss + tree_loss + root_loss
        ) * 0.5
        new_litter = max(0, min(2000, new_litter))

        # Seeds in soil (partial dispersal from grass reproduction)
        new_seeds = eco["seeds_in_soil"] * 0.9 + new_grass * 0.02
        new_seeds = min(500, new_seeds)

        # Extreme condition death
        if temp < -10 or temp > 50 or soil_moisture < 0.05:
            death_factor = 0.3 if temp < -20 or temp > 55 else 0.1
            new_grass *= (1 - death_factor)
            new_shrub *= (1 - death_factor * 0.5)
            death_penalty = new_tree * death_factor * 0.3
            new_tree -= death_penalty
            new_tree = max(0, new_tree)

        db.execute("""
            UPDATE ext_sp_ecosystem SET
                biomass_grass = ?, biomass_shrub = ?, biomass_tree = ?,
                biomass_root = ?, leaf_area_index = ?,
                seeds_in_soil = ?, food_plant_abundance = ?,
                litter_biomass = ?, last_growth_tick = ?
            WHERE area_uuid = ?
        """, (
            round(new_grass, 1), round(new_shrub, 1),
            round(new_tree, 1), round(new_root, 1),
            round(new_lai, 2), round(new_seeds, 1),
            round(food_abundance, 1), round(new_litter, 1),
            tick_number, area["area_uuid"],
        ))

        total_growth += grass_growth + shrub_growth + tree_growth
        total_death += grass_loss + shrub_loss + tree_loss
        area_count += 1

    return {
        "areas_processed": area_count,
        "total_growth_kg": round(total_growth, 1),
        "total_death_kg": round(total_death, 1),
    }


# ══════════════════════════════════════════════════════════════════════
# Animal agents – per-tick processing
# ══════════════════════════════════════════════════════════════════════

# TODO: Refactor into smaller helper functions (250+ lines)
def process_animal_agents_tick(tick_number: int) -> dict:
    """Process all alive animal agents for one tick.

    Each agent: sense → decide → move → consume → metabolise.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"process_animal_agent failed: {e}")
        return {}
    if not cfg.get("ecosystem_enabled", True):
        return {"status": "disabled"}

    db = get_db()
    agents = db.fetch_all(
        "SELECT * FROM ext_sp_animal_agents WHERE is_alive = 1")

    interval = cfg.get("ecosystem_tick_interval", 6)
    if interval < 1:
        interval = 1

    results = {
        "processed": 0,
        "moved": 0,
        "fed": 0,
        "drank": 0,
        "attacked": 0,
        "reproduced": 0,
        "died": 0,
        "hibernated": 0,
    }

    for agent in agents:
        species = agent["species"]
        sp_data = ANIMAL_SPECIES.get(species)
        if not sp_data:
            continue

        results["processed"] += 1

        # Skip if hibernating (processed separately)
        if agent["is_hibernating"]:
            continue

        # ── Sense area state ──────────────────────────────────
        current_area = get_area(agent["area_uuid"])
        if not current_area:
            continue

        eco = db.fetch_one(
            "SELECT * FROM ext_sp_ecosystem WHERE area_uuid = ?",
            (agent["area_uuid"],))
        weather = db.fetch_one(
            "SELECT * FROM ext_sp_weather WHERE area_uuid = ?",
            (agent["area_uuid"],))

        biome = current_area["biome_type"]
        temp = weather["temperature"] if weather else 20
        soil_moisture = (weather["humidity"] / 100.0) if weather else 0.5

        # ── Check hibernation trigger (bear only) ─────────────
        if sp_data.get("hibernates") and temp < -2:
            if not agent["is_hibernating"]:
                db.execute(
                    "UPDATE ext_sp_animal_agents SET is_hibernating = 1, last_action = 'hibernate' WHERE animal_uuid = ?",
                    (agent["animal_uuid"],))
                results["hibernated"] += 1
            continue

        # ── Check wake from hibernation ───────────────────────
        if agent["is_hibernating"] and temp > 5:
            db.execute(
                "UPDATE ext_sp_animal_agents SET is_hibernating = 0, last_action = 'wake' WHERE animal_uuid = ?",
                (agent["animal_uuid"],))

        # ── Hunger & thirst timers ────────────────────────────
        hunger = agent["hunger_timer"] + 1
        thirst = agent["thirst_timer"] + 1

        # ── DECISION: what to do ──────────────────────────────
        action = "idle"

        # Priority: flee if nearby threats (for herbivores)
        # (Simplified: small random chance to flee if predator density high)
        if sp_data["diet"] == "herbivore":
            nearby_predators = db.fetch_all("""
                SELECT COUNT(*) as c FROM ext_sp_animal_agents
                WHERE area_uuid = ? AND species IN ('wolf', 'bear')
                  AND is_alive = 1
            """, (agent["area_uuid"],))
            predator_count = nearby_predators[0]["c"] if nearby_predators else 0
            if predator_count > 0 and random.random() < 0.3:
                action = "flee"
        elif sp_data["diet"] == "carnivore":
            # Check for prey in area
            herbivore_count = _count_herbivores_in_area(db, agent["area_uuid"])
            if herbivore_count > 0 and hunger > 5:
                action = "hunt"

        # Hydration priority
        if thirst > 8 or agent["hydration"] < 30:
            action = "drink"

        # Hunger priority (if not already hunting/fleeing)
        if hunger > 10 or agent["energy"] < 30:
            if action not in ("hunt", "flee"):
                action = "forage"

        # Mating (if energy high and random roll)
        if (agent["energy"] > 60 and agent["mate_cooldown"] <= 0
                and random.random() < sp_data["reproduction_rate"] * 5):
            action = "mate"

        # ── EXECUTE action ────────────────────────────────────

        # Forage (eat)
        if action == "forage":
            if sp_data["diet"] == "herbivore" and eco:
                food_found = _herbivore_eat(db, agent, eco, sp_data)
                if food_found:
                    results["fed"] += 1
                    hunger = max(0, hunger - 8)
                    db.execute(
                        "UPDATE ext_sp_animal_agents SET energy = ?, hunger_timer = ? WHERE animal_uuid = ?",
                        (min(sp_data["max_energy"], agent["energy"] + 20), hunger, agent["animal_uuid"]))
                else:
                    # No food – move
                    action = "move"
            elif sp_data["diet"] == "omnivore" and eco:
                food_found = _herbivore_eat(db, agent, eco, sp_data)
                if not food_found and agent["hunger_timer"] > 8:
                    action = "move"  # Move to find food
                if food_found:
                    results["fed"] += 1
                    hunger = max(0, hunger - 6)
                    db.execute(
                        "UPDATE ext_sp_animal_agents SET energy = ?, hunger_timer = ? WHERE animal_uuid = ?",
                        (min(sp_data["max_energy"], agent["energy"] + 15), hunger, agent["animal_uuid"]))

        # Hunt (carnivore/omnivore)
        if action == "hunt":
            killed = _carnivore_hunt(db, agent, sp_data)
            if killed:
                results["fed"] += 1
                results["attacked"] += 1
                hunger = max(0, hunger - 15)
                db.execute(
                    "UPDATE ext_sp_animal_agents SET energy = ?, hunger_timer = ? WHERE animal_uuid = ?",
                    (min(sp_data["max_energy"], agent["energy"] + 30), hunger, agent["animal_uuid"]))
            else:
                action = "move"  # Failed hunting, move on

        # Drink
        if action == "drink":
            if soil_moisture > 0.3 or (weather and weather["precipitation_mm"] > 0):
                results["drank"] += 1
                thirst = max(0, thirst - 12)
                db.execute(
                    "UPDATE ext_sp_animal_agents SET hydration = ?, thirst_timer = ? WHERE animal_uuid = ?",
                    (min(sp_data["max_hydration"], agent["hydration"] + 25), thirst, agent["animal_uuid"]))

        # Move
        if action in ("move", "flee") or (action == "idle" and random.random() < cfg.get("animal_agent_move_chance", 0.3)):
            success = _move_agent(db, agent, sp_data, action == "flee")
            if success:
                results["moved"] += 1

        # Mate
        if action == "mate" and random.random() < sp_data["reproduction_rate"]:
            # Check for same species in area
            potential_mates = db.fetch_all("""
                SELECT COUNT(*) as c FROM ext_sp_animal_agents
                WHERE area_uuid = ? AND species = ? AND is_alive = 1
                  AND animal_uuid != ?
            """, (agent["area_uuid"], agent["species"], agent["animal_uuid"]))
            if potential_mates and potential_mates[0]["c"] > 0:
                results["reproduced"] += 1
                _spawn_offspring(db, agent, sp_data)

        # ── Metabolism (burn energy) ──────────────────────────
        metabolism = sp_data["metabolic_rate"] * (1.5 if action in ("move", "flee", "hunt") else 1.0)
        new_energy = agent["energy"] - metabolism
        new_hydration = agent["hydration"] - 0.5

        # ── Age ───────────────────────────────────────────────
        new_age_days = agent["age_days"] + 1
        new_age = agent["age"] + (1 / sp_data.get("lifespan_days", 365))

        # ── Starvation / dehydration death ────────────────────
        if new_energy <= 0 or new_hydration <= 0:
            db.execute("""
                UPDATE ext_sp_animal_agents SET
                    is_alive = 0, health = 0, energy = 0,
                    last_action = 'starved',
                    last_action_tick = ?
                WHERE animal_uuid = ?
            """, (tick_number, agent["animal_uuid"]))
            results["died"] += 1
            continue

        # ── Old age ───────────────────────────────────────────
        if new_age >= 1.0:
            db.execute("""
                UPDATE ext_sp_animal_agents SET
                    is_alive = 0, health = 0, energy = 0,
                    last_action = 'old_age',
                    last_action_tick = ?
                WHERE animal_uuid = ?
            """, (tick_number, agent["animal_uuid"]))
            results["died"] += 1
            continue

        # ── Mate cooldown tickdown ────────────────────────────
        new_cooldown = max(0, agent["mate_cooldown"] - 1)

        db.execute("""
            UPDATE ext_sp_animal_agents SET
                energy = ?, hydration = ?,
                age = ?, age_days = ?,
                hunger_timer = ?, thirst_timer = ?,
                mate_cooldown = ?,
                last_action = ?, last_action_tick = ?
            WHERE animal_uuid = ?
        """, (
            round(new_energy, 1), round(new_hydration, 1),
            round(new_age, 3), new_age_days,
            hunger, thirst,
            new_cooldown,
            action, tick_number,
            agent["animal_uuid"],
        ))

    # Clean up carcasses (turn into litter)
    _process_carcasses(db, tick_number)

    return results


def _count_herbivores_in_area(db, area_uuid: str) -> int:
    """Count alive herbivore agents (deer, rabbit) in an area."""
    row = db.fetch_one("""
        SELECT COUNT(*) as c FROM ext_sp_animal_agents
        WHERE area_uuid = ? AND is_alive = 1
          AND species IN ('deer', 'rabbit')
    """, (area_uuid,))
    return row["c"] if row else 0


def _herbivore_eat(db, agent: dict, eco: dict, sp_data: dict) -> bool:
    """Herbivore/omnivore consumes plant biomass.

    Returns True if food was found.
    """
    try:
        diet_items = sp_data.get("diet_items", ["grass"])

    except Exception as e:
        log.error(f"_herbivore_eat failed: {e}")
        return False
    ate = False

    for item in diet_items:
        if item == "grass" and eco["biomass_grass"] > 10:
            consumption = min(20, eco["biomass_grass"] * 0.05)
            db.execute(
                "UPDATE ext_sp_ecosystem SET biomass_grass = MAX(0, biomass_grass - ?) WHERE area_uuid = ?",
                (consumption, agent["area_uuid"]))
            ate = True
            break
        elif item == "shrub" and eco["biomass_shrub"] > 5:
            consumption = min(10, eco["biomass_shrub"] * 0.03)
            db.execute(
                "UPDATE ext_sp_ecosystem SET biomass_shrub = MAX(0, biomass_shrub - ?) WHERE area_uuid = ?",
                (consumption, agent["area_uuid"]))
            ate = True
            break
        elif item == "berries" and eco["food_plant_abundance"] > 5:
            consumption = min(10, eco["food_plant_abundance"] * 0.1)
            db.execute(
                "UPDATE ext_sp_ecosystem SET food_plant_abundance = MAX(0, food_plant_abundance - ?) WHERE area_uuid = ?",
                (consumption, agent["area_uuid"]))
            ate = True
            break
        elif item == "root" and eco["biomass_root"] > 5:
            consumption = min(10, eco["biomass_root"] * 0.05)
            db.execute(
                "UPDATE ext_sp_ecosystem SET biomass_root = MAX(0, biomass_root - ?) WHERE area_uuid = ?",
                (consumption, agent["area_uuid"]))
            ate = True
            break

    return ate


def _carnivore_hunt(db, agent: dict, sp_data: dict) -> bool:
    """Attempt to kill a prey agent in the same area.

    Builds a dynamic prey_species list from the carnivore's diet_items
    and tries each species — density-based prey (rabbit, fish, etc.)
    have their density reduced, while agent-based prey (deer, etc.)
    are individually tracked and killed.

    Returns True if a kill was made.
    """
    try:
        prey_species = sp_data.get("diet_items", [])

        for species in prey_species:
            # Skip non-animal diet items (berries, roots, etc.)
            if species not in DENSITY_SPECIES and species not in ANIMAL_SPECIES:
                continue

            # Density-based prey (ext_sp_animal_density table)
            if species in DENSITY_SPECIES:
                row = db.fetch_one(
                    "SELECT density FROM ext_sp_animal_density WHERE area_uuid = ? AND species = ?",
                    (agent["area_uuid"], species))
                if row and row["density"] > 2:
                    kill = max(1, int(row["density"] * 0.3))
                    db.execute(
                        "UPDATE ext_sp_animal_density SET density = MAX(0, density - ?) WHERE area_uuid = ? AND species = ?",
                        (kill, agent["area_uuid"], species))
                    return True

            # Agent-based prey (ext_sp_animal_agents table)
            elif species in ANIMAL_SPECIES:
                prey = db.fetch_one(
                    "SELECT animal_uuid FROM ext_sp_animal_agents"
                    " WHERE area_uuid = ? AND species = ? AND is_alive = 1"
                    " ORDER BY random() LIMIT 1",
                    (agent["area_uuid"], species))
                if prey:
                    db.execute(
                        "UPDATE ext_sp_animal_agents SET is_alive = 0, health = 0, last_action = 'killed' WHERE animal_uuid = ?",
                        (prey["animal_uuid"],))
                    return True

    except Exception as e:
        log.error(f"_carnivore_hunt failed: {e}")
        return False

    return False


def _move_agent(db, agent: dict, sp_data: dict, fleeing: bool = False) -> bool:
    """Move an agent to a neighboring or preferred area.

    Returns True if moved.
    """
    try:
        current_area = get_area(agent["area_uuid"])

    except Exception as e:
        log.error(f"_move_agent failed: {e}")
        return False
    if not current_area:
        return False

    neighbors = _get_area_neighbors(current_area)
    if not neighbors:
        return False

    if fleeing:
        # Flee to a random neighbor
        target = random.choice(neighbors)
    else:
        # Prefer biome that matches species preferences
        preferred = [n for n in neighbors
                     if _area_biome(n) in sp_data.get("preferred_biomes", [])]
        if preferred:
            target = random.choice(preferred)
        else:
            # If hungry/thirsty, seek areas with more resources
            if agent["energy"] < 40 or agent["hydration"] < 40:
                # Pick neighbor with most plant biomass or water
                best = max(neighbors, key=lambda n: _area_resource_score(n, agent))
                target = best
            else:
                target = random.choice(neighbors)

    if target:
        db.execute("""
            UPDATE ext_sp_animal_agents SET area_uuid = ? WHERE animal_uuid = ?
        """, (target["area_uuid"], agent["animal_uuid"]))
        return True
    return False


def _area_biome(area: dict) -> str:
    """Get biome type from area dict."""
    return area.get("biome_type", "plains")


def _area_resource_score(area: dict, agent: dict) -> float:
    """Score how good an area is for an agent (food + water availability)."""
    db = get_db()
    score = 0.5

    # Plant food
    eco = db.fetch_one(
        "SELECT biomass_grass + biomass_shrub + biomass_tree as total FROM ext_sp_ecosystem WHERE area_uuid = ?",
        (area["area_uuid"],))
    if eco:
        score += eco["total"] / 2000

    # Water (precipitation)
    weather = db.fetch_one(
        "SELECT precipitation_mm, humidity FROM ext_sp_weather WHERE area_uuid = ?",
        (area["area_uuid"],))
    if weather:
        score += weather["precipitation_mm"] / 50 + weather["humidity"] / 200

    return score


def _get_area_neighbors(area: dict) -> list:
    """Get neighbor areas of a given area."""
    db = get_db()
    neighbor_ids = area.get("neighbor_ids", "")
    if not neighbor_ids:
        return []
    try:
        ids = json.loads(neighbor_ids)
    except (json.JSONDecodeError, TypeError):
        return []

    neighbors = []
    for nid in ids:
        n = get_area(nid)
        if n:
            neighbors.append(n)
    return neighbors


def _spawn_offspring(db, parent: dict, sp_data: dict):
    """Create a baby animal in the same area."""
    import uuid as _uuid_mod
    pack_sizes = {"deer": (1, 2), "wolf": (2, 5), "bear": (1, 2)}
    lo, hi = pack_sizes.get(parent["species"], (1, 2))
    count = random.randint(lo, hi)

    for _ in range(count):
        db.execute("""
            INSERT INTO ext_sp_animal_agents
            (animal_uuid, species, name, area_uuid, energy, hydration,
             health, age, age_days, is_alive, mate_cooldown, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 10, ?)
        """, (
            str(_uuid_mod.uuid4()),
            parent["species"],
            f"Baby {parent['species'].title()}",
            parent["area_uuid"],
            60.0, 80.0, 80.0,
            0.01,
            datetime.now(timezone.utc).isoformat(),
        ))

    # Parent cooldown
    db.execute(
        "UPDATE ext_sp_animal_agents SET mate_cooldown = 20 WHERE animal_uuid = ?",
        (parent["animal_uuid"],))


def _process_carcasses(db, tick_number: int):
    """Turn dead animals into organic matter in their area."""
    carcasses = db.fetch_all("""
        SELECT a.*, e.litter_biomass
        FROM ext_sp_animal_agents a
        LEFT JOIN ext_sp_ecosystem e ON a.area_uuid = e.area_uuid
        WHERE a.is_alive = 0 AND a.last_action_tick = ?
    """, (tick_number - 1,))  # Previous tick's deaths

    for car in carcasses:
        sp = ANIMAL_SPECIES.get(car["species"], {})
        mass = sp.get("body_mass_kg", 50)
        litter_boost = mass * 0.3

        db.execute("""
            UPDATE ext_sp_ecosystem SET
                litter_biomass = MIN(2000, litter_biomass + ?)
            WHERE area_uuid = ?
        """, (litter_boost, car["area_uuid"]))


# ══════════════════════════════════════════════════════════════════════
# Density-based species – daily update
# ══════════════════════════════════════════════════════════════════════

def process_density_tick(tick_number: int) -> dict:
    """Update density-based (small creature) populations across all areas.

    Uses a simple logistic growth model with predator-prey coupling.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"process_density_tick failed: {e}")
        return {}
    if not cfg.get("ecosystem_enabled", True):
        return {"status": "disabled"}

    db = get_db()
    areas = get_all_areas()
    interval = cfg.get("ecosystem_tick_interval", 6)
    if interval < 1:
        interval = 1
    if tick_number % interval != 0:
        return {"status": "skipped"}

    total_delta = 0

    for area in areas:
        biome = area["biome_type"]
        densities = db.fetch_all(
            "SELECT * FROM ext_sp_animal_density WHERE area_uuid = ?",
            (area["area_uuid"],))

        weather = db.fetch_one(
            "SELECT temperature, precipitation_mm FROM ext_sp_weather WHERE area_uuid = ?",
            (area["area_uuid"],))
        temp = weather["temperature"] if weather else 20
        precip = weather["precipitation_mm"] if weather else 0

        for row in densities:
            sp_name = row["species"]
            sp_data = DENSITY_SPECIES.get(sp_name)
            if not sp_data:
                continue

            density = row["density"]

            # Skip if not suitable biome
            if biome not in sp_data["preferred_biomes"]:
                # Slowly fade out
                new_density = max(0, density - density * 0.1)
            else:
                # Logistic growth
                growth_rate = sp_data["base_growth"]
                mortality = sp_data["base_mortality"]
                max_dens = sp_data["max_density"]

                # Temperature effect
                temp_factor = max(0.1, 1 - abs(temp - 20) / 40)
                precip_factor = min(1.0, 0.3 + precip / 30)

                # Carrying capacity
                carrying = max_dens * temp_factor * precip_factor

                # Predation loss
                predation = 0
                for pred_species in sp_data.get("predator_species", []):
                    pred_row = db.fetch_one(
                        "SELECT density FROM ext_sp_animal_density WHERE area_uuid = ? AND species = ?",
                        (area["area_uuid"], pred_species))
                    if pred_row:
                        predation += pred_row["density"] * 0.02

                # Agent predation (wolves, bears on rabbits)
                if sp_name == "rabbit":
                    agent_pred = db.fetch_one("""
                        SELECT COUNT(*) as c FROM ext_sp_animal_agents
                        WHERE area_uuid = ? AND is_alive = 1
                          AND species IN ('wolf', 'bear')
                    """, (area["area_uuid"],))
                    if agent_pred:
                        predation += agent_pred["c"] * 0.5

                delta = (growth_rate * (1 - density / carrying) - mortality) * density - predation
                new_density = max(0, density + delta)

            db.execute("""
                UPDATE ext_sp_animal_density SET density = ?
                WHERE area_uuid = ? AND species = ?
            """, (round(new_density, 2), area["area_uuid"], sp_name))

            total_delta += new_density - density

    return {"total_delta": round(total_delta, 1)}


# ══════════════════════════════════════════════════════════════════════
# Hibernation check (seasonal)
# ══════════════════════════════════════════════════════════════════════

def process_hibernation_tick(tick_number: int) -> dict:
    """Wake hibernating animals when temperatures rise.

    Called every tick independently of ecosystem_tick_interval.
    """
    cfg = get_config()
    if not cfg.get("ecosystem_enabled", True):
        return {"status": "disabled"}

    db = get_db()
    hibernators = db.fetch_all("""
        SELECT a.*, w.temperature
        FROM ext_sp_animal_agents a
        JOIN ext_sp_weather w ON a.area_uuid = w.area_uuid
        WHERE a.is_alive = 1 AND a.is_hibernating = 1
    """)

    woke = 0
    for h in hibernators:
        temp = h["temperature"]
        if temp > 5:  # Spring awakening
            db.execute("""
                UPDATE ext_sp_animal_agents SET
                    is_hibernating = 0,
                    energy = 40,  # Waking is costly
                    last_action = 'wake',
                    last_action_tick = ?
                WHERE animal_uuid = ?
            """, (tick_number, h["animal_uuid"]))
            woke += 1

    return {"woke": woke}


# ══════════════════════════════════════════════════════════════════════
# Evapotranspiration feedback → weather
# ══════════════════════════════════════════════════════════════════════

def calculate_evapotranspiration() -> dict:
    """Compute moisture flux from plant-covered areas into the atmosphere.

    Returns a dict mapping area_uuid → humidity_delta for the weather
    system to apply.
    """
    cfg = get_config()
    if not cfg.get("ecosystem_enabled", True):
        return {}

    db = get_db()
    areas = get_all_areas()
    factor = cfg.get("evapotranspiration_factor", 0.3)
    results = {}

    for area in areas:
        eco = db.fetch_one(
            "SELECT * FROM ext_sp_ecosystem WHERE area_uuid = ?",
            (area["area_uuid"],))
        if not eco:
            continue

        # Total leaf biomass drives evapotranspiration
        total_biomass = eco["biomass_grass"] + eco["biomass_shrub"] + eco["biomass_tree"]
        lai = eco["leaf_area_index"]

        # Evapotranspiration rate (0-1)
        et_rate = min(1.0, (total_biomass / 2000) * (lai / 3.0) * factor)

        # Actual moisture added to air
        humidity_boost = et_rate * 15  # up to 15% humidity increase

        results[area["area_uuid"]] = round(humidity_boost, 1)

    return results


# ══════════════════════════════════════════════════════════════════════
# Persona interactions
# ══════════════════════════════════════════════════════════════════════

def persona_forage(persona_uuid: str) -> dict:
    """A persona tries to forage for food in their current area.

    Args:
        persona_uuid: The persona doing the foraging.

    Returns:
        dict with food collected (kcal) and description, or empty dict.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"persona_forage failed: {e}")
        return {}
    db = get_db()
    area = get_persona_area(persona_uuid)
    if not area:
        return {"food": 0, "message": "nowhere to forage"}

    eco = db.fetch_one(
        "SELECT * FROM ext_sp_ecosystem WHERE area_uuid = ?",
        (area["area_uuid"],))
    if not eco or eco["food_plant_abundance"] < 1:
        return {"food": 0, "message": "nothing edible here"}

    biome = area["biome_type"]
    yield_map = _FORAGE_YIELD.get(biome, _FORAGE_YIELD["plains"])

    # Skill affects yield
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills, improve_skill
    skills = get_skills(persona_uuid)
    farming_skill = skills.get("farming", 10) if skills else 10
    exploration_skill = skills.get("exploration", 10) if skills else 10

    skill_mult = 1 + (farming_skill / 100) * cfg.get("forage_skill_mult", 0.5)
    base_yield = cfg.get("forage_base_yield", 20)

    # Available food cap
    available = eco["food_plant_abundance"]
    collected = min(available, base_yield * skill_mult * random.uniform(0.5, 1.5))
    collected = round(collected, 1)

    # Reduce food abundance
    db.execute("""
        UPDATE ext_sp_ecosystem SET
            food_plant_abundance = MAX(0, food_plant_abundance - ?)
        WHERE area_uuid = ?
    """, (collected, area["area_uuid"]))

    # Skill improvement
    improve_skill(persona_uuid, "farming", amount=1 if collected > 10 else 0)
    improve_skill(persona_uuid, "exploration", amount=1)

    # Determine type of food
    food_types = [k for k, v in yield_map.items() if v > 0]
    if food_types:
        ftype = random.choices(food_types, weights=[yield_map[k] for k in food_types])[0]
    else:
        ftype = "unknown plant"

    add_memory(persona_uuid, "forage", detail={
        "food": collected, "type": ftype, "area": area.get("name", "unknown")
    }, emotional_weight=3)

    return {
        "food": collected,
        "type": ftype,
        "message": f"Gathered {collected:.0f}g of {ftype}",
        "biome": biome,
    }


def persona_hunt(persona_uuid: str) -> dict:
    """A persona attempts to hunt an animal in their current area.

    Args:
        persona_uuid: The persona hunting.

    Returns:
        dict with meat (kg), hide (bool), message, or failure.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"persona_hunt failed: {e}")
        return {}
    db = get_db()
    area = get_persona_area(persona_uuid)
    if not area:
        return {"meat": 0, "message": "nowhere to hunt"}

    # Find eligible prey agents in this area
    prey = db.fetch_all("""
        SELECT * FROM ext_sp_animal_agents
        WHERE area_uuid = ? AND is_alive = 1
          AND species IN ('deer', 'rabbit')
        ORDER BY random() LIMIT 3
    """, (area["area_uuid"],))

    if not prey:
        return {"meat": 0, "message": "no prey in this area"}

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills, improve_skill
    skills = get_skills(persona_uuid)
    combat_skill = skills.get("combat", 10) if skills else 10

    hunt_chance = cfg.get("hunt_base_chance", 0.3) + combat_skill * cfg.get("hunt_skill_mult", 0.05)
    hunt_chance = min(0.95, hunt_chance)

    if random.random() < hunt_chance:
        # Successful hunt
        target = prey[0]
        sp = ANIMAL_SPECIES.get(target["species"], {})

        # Kill the prey
        db.execute("""
            UPDATE ext_sp_animal_agents SET
                is_alive = 0, health = 0, last_action = 'hunted',
                last_action_tick = 0
            WHERE animal_uuid = ?
        """, (target["animal_uuid"],))

        # Meat yield
        meat = sp.get("body_mass_kg", 20) * random.uniform(0.2, 0.4)
        hide = random.random() < 0.6

        # Skill improvement
        improve_skill(persona_uuid, "combat", amount=2)
        improve_skill(persona_uuid, "exploration", amount=1)

        add_memory(persona_uuid, "hunt_success", detail={
            "species": target["species"],
            "meat_kg": round(meat, 1),
            "hide": hide,
        }, emotional_weight=7)

        return {
            "meat": round(meat, 1),
            "hide": hide,
            "species": target["species"],
            "message": f"Hunted a {target['species']}! Got {meat:.0f}kg of meat" +
                       (" and a hide" if hide else ""),
        }
    else:
        # Failed hunt – prey escapes
        improve_skill(persona_uuid, "exploration", amount=1)
        add_memory(persona_uuid, "hunt_fail", detail={
            "species": prey[0]["species"],
            "reason": "missed"
        }, emotional_weight=2)

        return {"meat": 0, "message": f"the {prey[0]['species']} got away"}


def persona_fish(persona_uuid: str) -> dict:
    """A persona tries to fish in their current area (water biome needed).

    Args:
        persona_uuid: The persona fishing.

    Returns:
        dict with fish (kg), message, or failure.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"persona_fish failed: {e}")
        return {}
    area = get_persona_area(persona_uuid)
    if not area:
        return {"fish": 0, "message": "nowhere to fish"}

    biome = area["biome_type"]
    if biome not in ("ocean", "swamp"):
        return {"fish": 0, "message": "no water here to fish in"}

    density = db.fetch_one(
        "SELECT density FROM ext_sp_animal_density WHERE area_uuid = ? AND species = 'fish'",
        (area["area_uuid"],))
    if not density or density["density"] < 1:
        return {"fish": 0, "message": "no fish in these waters"}

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills, improve_skill
    skills = get_skills(persona_uuid)
    exploration_skill = skills.get("exploration", 10) if skills else 10

    catch = min(density["density"], random.uniform(1, 5 + exploration_skill * 0.2))

    # Reduce fish density
    db.execute("""
        UPDATE ext_sp_animal_density SET density = MAX(0, density - ?)
        WHERE area_uuid = ? AND species = 'fish'
    """, (catch, area["area_uuid"]))

    improve_skill(persona_uuid, "exploration", amount=2)

    add_memory(persona_uuid, "fish", detail={
        "catch_kg": round(catch, 1), "area": area.get("name", "unknown")
    }, emotional_weight=4)

    return {
        "fish": round(catch, 1),
        "message": f"Caught {catch:.0f} fish!",
    }


# ── Self-care: drink, wash, rest, relieve ──────────────────────────

def persona_drink(persona_uuid: str) -> dict:
    """Persona tries to find and drink water in their current area.

    Checks weather (rain/snow) and biome type. Returns hydration gained.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"persona_drink failed: {e}")
        return {}
    area = get_persona_area(persona_uuid)
    if not area:
        return {"hydration": 0, "message": "nowhere to drink"}

    weather = db.fetch_one(
        "SELECT * FROM ext_sp_weather WHERE area_uuid = ?",
        (area["area_uuid"],))

    biome = area["biome_type"]
    has_water = biome in ("ocean", "swamp")
    is_raining = weather and weather["is_raining"]
    precip = weather["precipitation_mm"] if weather else 0
    humidity = weather["humidity"] if weather else 0

    # Water availability score (0-100)
    if has_water:
        water_score = 100
    elif is_raining:
        water_score = min(80, precip * 10)
    elif humidity > 60:
        water_score = humidity * 0.5  # Dew/groundwater
    else:
        water_score = 10  # Minimal — find a stream

    if water_score < 20:
        return {"hydration": 0, "message": "no water available"}

    gained = min(40, water_score * 0.3 + random.uniform(5, 15))
    gained = round(max(0, gained), 1)

    add_memory(persona_uuid, "drank", detail={
        "hydration_gained": gained, "area": area.get("name", "?"),
        "source": "rain" if is_raining else ("water_body" if has_water else "dew")
    }, emotional_weight=2)

    return {"hydration": gained, "message": f"Drank {gained:.0f} hydration worth of water"}


def persona_clean(persona_uuid: str) -> dict:
    """Persona tries to wash and relieve themselves.

    Hygiene improves if near water (rain, river biome, etc.).
    Waste decreases automatically (relieving).
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"persona_clean failed: {e}")
        return {}
    area = get_persona_area(persona_uuid)
    if not area:
        return {"hygiene": 0, "waste": 0, "message": "nowhere to clean"}

    weather = db.fetch_one(
        "SELECT * FROM ext_sp_weather WHERE area_uuid = ?",
        (area["area_uuid"],))

    biome = area["biome_type"]
    has_water = biome in ("ocean", "swamp")
    is_raining = weather and weather["is_raining"]
    precip = weather["precipitation_mm"] if weather else 0

    # Can they wash?
    if has_water or is_raining or precip > 2:
        hygiene_gain = random.uniform(10, 25)
        waste_reduction = random.uniform(10, 30)
        add_memory(persona_uuid, "cleaned", detail={
            "hygiene_gain": round(hygiene_gain, 1),
            "area": area.get("name", "?")
        }, emotional_weight=2)
        return {
            "hygiene": round(hygiene_gain, 1),
            "waste": round(waste_reduction, 1),
            "message": "Cleaned up near water"
        }

    # No water — minimal cleaning with leaves/snow
    if biome == "tundra":
        return {"hygiene": round(random.uniform(3, 8), 1),
                "waste": round(random.uniform(5, 15), 1),
                "message": "Scrubbed with snow"}
    if biome == "desert":
        return {"hygiene": round(random.uniform(1, 5), 1),
                "waste": round(random.uniform(5, 15), 1),
                "message": "Cleaned with sand"}

    return {"hygiene": 0, "waste": 0, "message": "no water to wash"}


def persona_rest(persona_uuid: str) -> dict:
    """Persona rests to recover energy.

    Rest effectiveness depends on biome safety and shelter.
    """
    db = get_db()
    area = get_persona_area(persona_uuid)
    if not area:
        return {"energy": 0, "message": "nowhere to rest"}

    biome = area["biome_type"]
    weather = db.fetch_one(
        "SELECT * FROM ext_sp_weather WHERE area_uuid = ?",
        (area["area_uuid"],))

    temp = weather["temperature"] if weather else 20

    # Shelter quality by biome
    shelter_quality = {
        "plains": 0.5, "forest": 0.8, "desert": 0.3,
        "swamp": 0.4, "mountains": 0.6, "ocean": 0.1,
        "tundra": 0.3, "nether_wastes": 0.0, "crimson_forest": 0.2,
        "end_islands": 0.1, "deep_dark": 0.7,
    }
    quality = shelter_quality.get(biome, 0.5)

    # Temperature penalty
    temp_comfort = max(0, 1 - abs(temp - 20) / 30)

    energy_gain = round(quality * temp_comfort * random.uniform(10, 25), 1)

    add_memory(persona_uuid, "rested", detail={
        "energy_gained": energy_gain,
        "biome": biome,
        "temp": round(temp, 1),
    }, emotional_weight=2)

    return {"energy": energy_gain, "message": f"Rested and recovered {energy_gain:.0f} energy"}


def persona_threat_check(persona_uuid: str) -> dict:
    """Check if any dangerous animals in the area may attack this persona.

    Returns threat info: whether attacked, damage, animal species, etc.

    This is meant to be called from the health system when a persona
    enters a new area or during the main tick.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"persona_threat_check failed: {e}")
        return {}
    if not cfg.get("ecosystem_enabled", True):
        return {"threat": False}

    db = get_db()
    area = get_persona_area(persona_uuid)
    if not area:
        return {"threat": False}

    # Find threatening animals (wolves, bears) in this area
    threats = db.fetch_all("""
        SELECT * FROM ext_sp_animal_agents
        WHERE area_uuid = ? AND is_alive = 1
          AND species IN ('wolf', 'bear')
          AND is_hibernating = 0
    """, (area["area_uuid"],))

    if not threats:
        return {"threat": False}

    # Get persona health – weakened personas are more likely to be attacked
    health = get_persona_health(persona_uuid)
    if not health:
        return {"threat": False}

    # Attack chance increases if persona is weak
    avg_health = (health["food"] + health["hydration"] + health["energy"] +
                  health["immune"]) / 4
    health_factor = max(0.3, 1.0 - avg_health / 100)
    attack_chance = cfg.get("animal_attack_chance", 0.15) * health_factor

    if random.random() < attack_chance:
        attacker = random.choice(threats)
        sp = ANIMAL_SPECIES.get(attacker["species"], {})
        threat_level = sp.get("threat_level", 5)

        # Damage based on threat level and attack power
        damage = random.randint(5, 15) * (threat_level / 5)
        damage = min(50, damage)

        # Apply to health
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
        modify_health(persona_uuid, energy=-damage * 0.5, immune=-damage * 0.3)

        add_memory(persona_uuid, "animal_attack", detail={
            "species": attacker["species"],
            "damage": round(damage, 1),
            "threat_level": threat_level,
        }, emotional_weight=9)

        add_life_event(persona_uuid, "animal_attack",
                       f"Attacked by a {attacker['species']}!",
                       financial_impact=0, mood_impact="stressed",
                       duration_hours=48)

        return {
            "threat": True,
            "species": attacker["species"],
            "damage": round(damage, 1),
            "message": f"Attacked by a {attacker['species']}! Took {damage:.0f} damage.",
        }

    return {"threat": False}


# ══════════════════════════════════════════════════════════════════════
# Master tick runner
# ══════════════════════════════════════════════════════════════════════

def process_ecosystem_tick(tick_number: int) -> dict:
    """Run one complete ecosystem tick (plants + animals + density).

    Call this from the main simulation loop.
    """
    cfg = get_config()
    if not cfg.get("ecosystem_enabled", True):
        return {"status": "disabled"}

    results = {}

    # 1. Plant growth
    try:
        plant_result = process_plant_tick(tick_number)
        results["plants"] = plant_result
    except Exception as e:
        log.warn("sp_ecosystem", f"Plant tick error: {e}")
        results["plants"] = {"error": str(e)}

    # 2. Animal agent tick
    try:
        animal_result = process_animal_agents_tick(tick_number)
        results["animals"] = animal_result
    except Exception as e:
        log.warn("sp_ecosystem", f"Animal tick error: {e}")
        results["animals"] = {"error": str(e)}

    # 3. Density species tick
    try:
        density_result = process_density_tick(tick_number)
        results["density"] = density_result
    except Exception as e:
        log.warn("sp_ecosystem", f"Density tick error: {e}")
        results["density"] = {"error": str(e)}

    # 4. Hibernation check
    try:
        sleep_result = process_hibernation_tick(tick_number)
        results["hibernation"] = sleep_result
    except Exception as e:
        log.warn("sp_ecosystem", f"Hibernation tick error: {e}")
        results["hibernation"] = {"error": str(e)}

    return results

