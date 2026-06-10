"""
sp_crafting.py — Crafting recipes, building construction, and crafting queues.

Provides:
  - RECIPES: master recipe catalogue (tools, food, materials, buildings)
  - BUILDING_MATERIALS: material properties for construction
  - Crafting queue: multi-tick crafting jobs that consume resources gradually
  - Building system: construct, upgrade, room system, defense
  - Material → strength/insulation/defense mapping
"""

import json, random, math, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db, add_memory, add_life_event
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import (
    get_item_def, give_item, remove_item, count_item, get_inventory,
    get_equipped_stats, burn_calories, ITEM_DEFS,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area, get_all_areas, get_area
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Building materials — defines properties of each construction material
# ══════════════════════════════════════════════════════════════════════

BUILDING_MATERIALS = {
    "thatch": {
        "name": "Thatch",
        "health_per_unit": 20,
        "defense_per_unit": 0.2,
        "insulation_score": 0.6,
        "build_time_mult": 0.8,
        "cost_mult": 0.5,
        "required_materials": {"fibre": 20, "stick": 5},
        "repair_materials": {"fibre": 5, "stick": 1},
        "description": "Quick to build, warm, but fragile.",
    },
    "wood": {
        "name": "Wood",
        "health_per_unit": 50,
        "defense_per_unit": 1.0,
        "insulation_score": 0.5,
        "build_time_mult": 1.0,
        "cost_mult": 1.0,
        "required_materials": {"wood_log": 10, "nail": 4, "stick": 5},
        "repair_materials": {"wood_log": 2},
        "description": "Sturdy and reliable. The standard building material.",
    },
    "stone": {
        "name": "Stone",
        "health_per_unit": 150,
        "defense_per_unit": 3.0,
        "insulation_score": 0.7,
        "build_time_mult": 2.0,
        "cost_mult": 3.0,
        "required_materials": {"stone_brick": 15, "clay": 5},
        "repair_materials": {"stone_brick": 3},
        "description": "Extremely durable, excellent defense, but slow to build.",
    },
    "iron_reinforced": {
        "name": "Iron-Reinforced",
        "health_per_unit": 250,
        "defense_per_unit": 5.0,
        "insulation_score": 0.6,
        "build_time_mult": 3.0,
        "cost_mult": 5.0,
        "required_materials": {"stone_brick": 10, "iron_ingot": 5, "wood_log": 5},
        "repair_materials": {"iron_ingot": 1, "stone_brick": 2},
        "description": "The pinnacle of survival engineering. Extremely tough.",
    },
}

# ══════════════════════════════════════════════════════════════════════
# Building types — defines purpose and room capacity
# ══════════════════════════════════════════════════════════════════════

BUILDING_TYPES = {
    "shelter": {
        "name": "Shelter",
        "base_rooms": 1,
        "max_rooms": 2,
        "base_health": 100,
        "insulation_base": 0.3,
        "defense_base": 0.0,
        "description": "A basic roof overhead. Keeps rain off.",
        "allowed_rooms": ["storage", "bedroom", "workshop"],
    },
    "house": {
        "name": "House",
        "base_rooms": 2,
        "max_rooms": 5,
        "base_health": 200,
        "insulation_base": 0.4,
        "defense_base": 1.0,
        "description": "A proper dwelling with walls and a door.",
        "allowed_rooms": ["storage", "bedroom", "workshop", "kitchen", "bathroom"],
    },
    "workshop": {
        "name": "Workshop",
        "base_rooms": 1,
        "max_rooms": 3,
        "base_health": 150,
        "insulation_base": 0.2,
        "defense_base": 0.5,
        "description": "A dedicated space for crafting and repairs.",
        "allowed_rooms": ["storage", "workshop", "forge"],
    },
    "fortress": {
        "name": "Fortress",
        "base_rooms": 3,
        "max_rooms": 8,
        "base_health": 500,
        "insulation_base": 0.5,
        "defense_base": 5.0,
        "description": "A fortified stronghold. Maximum protection.",
        "allowed_rooms": ["storage", "bedroom", "workshop", "kitchen",
                          "bathroom", "forge", "armory", "lookout"],
    },
    "watchtower": {
        "name": "Watchtower",
        "base_rooms": 1,
        "max_rooms": 2,
        "base_health": 100,
        "insulation_base": 0.1,
        "defense_base": 3.0,
        "description": "A tall structure for spotting threats from afar.",
        "allowed_rooms": ["lookout", "storage"],
    },
}

ROOM_TYPES = {
    "storage": {"name": "Storage Room", "effect": "+50% container capacity in building"},
    "bedroom": {"name": "Bedroom", "effect": "+15 energy recovery when resting"},
    "workshop": {"name": "Workshop", "effect": "-20% crafting time"},
    "kitchen": {"name": "Kitchen", "effect": "+50% food nutrition from cooking"},
    "bathroom": {"name": "Bathroom", "effect": "+20 hygiene recovery"},
    "forge": {"name": "Forge", "effect": "Enables metal smelting & smithing"},
    "armory": {"name": "Armory", "effect": "+2 defense rating"},
    "lookout": {"name": "Lookout", "effect": "+50% detection radius for threats"},
}

# ══════════════════════════════════════════════════════════════════════
# Crafting recipes
# ══════════════════════════════════════════════════════════════════════

RECIPES = {
    # ── Tools ──────────────────────────────────────────
    "stone_axe": {
        "name": "Craft Stone Axe",
        "category": "tools",
        "station": "hands",  # Can be made anywhere
        "time_ticks": 3,
        "inputs": {"stick": 1, "stone": 2, "fibre": 2},
        "outputs": {"stone_axe": 1},
        "skill": "crafting",
        "skill_req": 0,
        "description": "Tie a sharp stone to a wooden handle.",
    },
    "stone_pickaxe": {
        "name": "Craft Stone Pickaxe",
        "category": "tools",
        "station": "hands",
        "time_ticks": 3,
        "inputs": {"stick": 2, "stone": 3, "fibre": 2},
        "outputs": {"stone_pickaxe": 1},
        "skill": "crafting",
        "skill_req": 0,
        "description": "Bind a pointed stone to a sturdy handle.",
    },
    "stone_knife": {
        "name": "Craft Stone Knife",
        "category": "tools",
        "station": "hands",
        "time_ticks": 2,
        "inputs": {"flint": 1, "stick": 1, "fibre": 1},
        "outputs": {"stone_knife": 1},
        "skill": "crafting",
        "skill_req": 0,
        "description": "Knap a flint blade and lash it to a handle.",
    },
    "spear": {
        "name": "Craft Spear",
        "category": "weapons",
        "station": "hands",
        "time_ticks": 3,
        "inputs": {"stick": 2, "flint": 1, "fibre": 2},
        "outputs": {"spear": 1},
        "skill": "crafting",
        "skill_req": 5,
        "description": "Sharpen and harden a long wooden shaft.",
    },
    "fishing_rod": {
        "name": "Craft Fishing Rod",
        "category": "tools",
        "station": "hands",
        "time_ticks": 2,
        "inputs": {"stick": 2, "string": 1, "bone": 1},
        "outputs": {"fishing_rod": 1},
        "skill": "crafting",
        "skill_req": 5,
        "description": "Tie a line and hook to a flexible pole.",
    },
    "bone_needle": {
        "name": "Craft Bone Needle",
        "category": "tools",
        "station": "hands",
        "time_ticks": 2,
        "inputs": {"bone": 1},
        "outputs": {"bone_needle": 1},
        "skill": "crafting",
        "skill_req": 3,
        "description": "Sharpen and drill a small hole in a bone sliver.",
    },
    "fire_bow": {
        "name": "Craft Fire Bow",
        "category": "tools",
        "station": "hands",
        "time_ticks": 2,
        "inputs": {"stick": 1, "fibre": 2},
        "outputs": {"fire_bow": 1},
        "skill": "crafting",
        "skill_req": 3,
        "description": "A bow-drill friction fire starter.",
    },
    "waterskin": {
        "name": "Craft Waterskin",
        "category": "containers",
        "station": "workshop",
        "time_ticks": 4,
        "inputs": {"hide": 1, "string": 2},
        "outputs": {"waterskin": 1},
        "skill": "crafting",
        "skill_req": 10,
        "description": "Sew a leather pouch that holds water.",
    },
    "clay_pot": {
        "name": "Fire Clay Pot",
        "category": "containers",
        "station": "forge",
        "time_ticks": 5,
        "inputs": {"clay": 3},
        "outputs": {"clay_pot": 1},
        "skill": "crafting",
        "skill_req": 15,
        "description": "Shape clay and fire it into a durable pot.",
    },
    "cloth": {
        "name": "Weave Cloth",
        "category": "materials",
        "station": "hands",
        "time_ticks": 4,
        "inputs": {"fibre": 5},
        "outputs": {"cloth": 1},
        "skill": "crafting",
        "skill_req": 5,
        "description": "Weave plant fibres into fabric.",
    },
    "string": {
        "name": "Twist String",
        "category": "materials",
        "station": "hands",
        "time_ticks": 1,
        "inputs": {"fibre": 2},
        "outputs": {"string": 1},
        "skill": "crafting",
        "skill_req": 0,
        "description": "Twist fibres into strong cord.",
    },
    "rope": {
        "name": "Make Rope",
        "category": "materials",
        "station": "hands",
        "time_ticks": 2,
        "inputs": {"fibre": 5, "string": 2},
        "outputs": {"rope": 1},
        "skill": "crafting",
        "skill_req": 5,
        "description": "Braid multiple strands into a thick rope.",
    },
    "torch": {
        "name": "Make Torch",
        "category": "tools",
        "station": "hands",
        "time_ticks": 1,
        "inputs": {"stick": 1, "fibre": 1},
        "outputs": {"torch": 3},
        "skill": "crafting",
        "skill_req": 0,
        "description": "Wrap resin-soaked fibre around a stick.",
    },
    "bandage": {
        "name": "Craft Bandage",
        "category": "medicine",
        "station": "hands",
        "time_ticks": 1,
        "inputs": {"cloth": 1},
        "outputs": {"bandage": 3},
        "skill": "crafting",
        "skill_req": 5,
        "description": "Cut cloth into clean strips for wound dressing.",
    },
    "iron_axe": {
        "name": "Smith Iron Axe",
        "category": "tools",
        "station": "forge",
        "time_ticks": 6,
        "inputs": {"iron_ingot": 2, "stick": 1},
        "outputs": {"iron_axe": 1},
        "skill": "crafting",
        "skill_req": 30,
        "description": "Forge an iron head and fit it to a handle.",
    },
    "iron_pickaxe": {
        "name": "Smith Iron Pickaxe",
        "category": "tools",
        "station": "forge",
        "time_ticks": 6,
        "inputs": {"iron_ingot": 3, "stick": 1},
        "outputs": {"iron_pickaxe": 1},
        "skill": "crafting",
        "skill_req": 30,
        "description": "Forge an iron pick head and haft it.",
    },
    "iron_knife": {
        "name": "Smith Iron Knife",
        "category": "tools",
        "station": "forge",
        "time_ticks": 4,
        "inputs": {"iron_ingot": 1, "stick": 1},
        "outputs": {"iron_knife": 1},
        "skill": "crafting",
        "skill_req": 25,
        "description": "Forge a fine iron blade.",
    },

    # ── Food Processing ──────────────────────────────────
    "cook_meat": {
        "name": "Cook Meat",
        "category": "food",
        "station": "fire",
        "time_ticks": 3,
        "inputs": {"raw_meat": 1},
        "outputs": {"cooked_meat": 1},
        "skill": "farming",
        "skill_req": 0,
        "description": "Roast raw meat over a fire.",
    },
    "cook_fish": {
        "name": "Cook Fish",
        "category": "food",
        "station": "fire",
        "time_ticks": 2,
        "inputs": {"fish": 1},
        "outputs": {"cooked_fish": 1},
        "skill": "farming",
        "skill_req": 0,
        "description": "Roast fresh fish over a fire.",
    },
    "dry_meat": {
        "name": "Dry Meat",
        "category": "food",
        "station": "fire",
        "time_ticks": 6,
        "inputs": {"raw_meat": 2},
        "outputs": {"dried_meat": 1},
        "skill": "farming",
        "skill_req": 5,
        "description": "Smoke and dry meat over a low fire.",
    },

    # ── Smelting ─────────────────────────────────────────
    "smelt_iron": {
        "name": "Smelt Iron",
        "category": "materials",
        "station": "forge",
        "time_ticks": 5,
        "inputs": {"iron_ore": 2},
        "outputs": {"iron_ingot": 1},
        "skill": "crafting",
        "skill_req": 20,
        "description": "Smelt iron ore into usable ingots.",
    },

    # ── Leather Working ──────────────────────────────────
    "tan_hide": {
        "name": "Tan Hide",
        "category": "materials",
        "station": "workshop",
        "time_ticks": 6,
        "inputs": {"hide": 1, "herbs": 1},
        "outputs": {"leather": 1},
        "skill": "crafting",
        "skill_req": 10,
        "description": "Tan raw hide into supple leather.",
    },
    "hide_shirt": {
        "name": "Sew Hide Shirt",
        "category": "clothing",
        "station": "workshop",
        "time_ticks": 5,
        "inputs": {"leather": 2, "string": 2},
        "outputs": {"hide_shirt": 1},
        "skill": "crafting",
        "skill_req": 15,
        "description": "Stitch a leather tunic.",
    },
    "hide_pants": {
        "name": "Sew Hide Pants",
        "category": "clothing",
        "station": "workshop",
        "time_ticks": 4,
        "inputs": {"leather": 2, "string": 1},
        "outputs": {"hide_pants": 1},
        "skill": "crafting",
        "skill_req": 15,
        "description": "Stitch leather leggings.",
    },
    "hide_boots": {
        "name": "Sew Hide Boots",
        "category": "clothing",
        "station": "workshop",
        "time_ticks": 3,
        "inputs": {"leather": 1, "string": 1},
        "outputs": {"hide_boots": 1},
        "skill": "crafting",
        "skill_req": 12,
        "description": "Stitch leather boots.",
    },
    "fur_cloak": {
        "name": "Craft Fur Cloak",
        "category": "clothing",
        "station": "workshop",
        "time_ticks": 5,
        "inputs": {"hide": 3, "string": 2},
        "outputs": {"fur_cloak": 1},
        "skill": "crafting",
        "skill_req": 18,
        "description": "Sew a warm heavy cloak from cured furs.",
    },

    # ── Building Materials ───────────────────────────────
    "wooden_plank": {
        "name": "Saw Wooden Planks",
        "category": "materials",
        "station": "workshop",
        "time_ticks": 3,
        "inputs": {"wood_log": 1},
        "outputs": {"wooden_plank": 4},
        "skill": "crafting",
        "skill_req": 5,
        "description": "Saw logs into planks.",
    },
    "stone_brick": {
        "name": "Dress Stone Bricks",
        "category": "materials",
        "station": "workshop",
        "time_ticks": 4,
        "inputs": {"stone": 2},
        "outputs": {"stone_brick": 1},
        "skill": "crafting",
        "skill_req": 10,
        "description": "Shape rough stone into dressed bricks.",
    },
    "nail": {
        "name": "Forge Nails",
        "category": "materials",
        "station": "forge",
        "time_ticks": 2,
        "inputs": {"iron_ingot": 1},
        "outputs": {"nail": 16},
        "skill": "crafting",
        "skill_req": 15,
        "description": "Hammer iron into nails.",
    },
}


def get_recipe(recipe_id: str) -> Optional[dict]:
    """Get a recipe definition by ID."""
    return RECIPES.get(recipe_id)


def get_recipes_by_station(station: str) -> list[dict]:
    """Get all recipes available at a given station."""
    return [
        {"id": rid, **r} for rid, r in RECIPES.items()
        if r["station"] == station
    ]


def get_recipes_available(persona_uuid: str) -> list[dict]:
    """Get all recipes the persona has the skill and materials for."""
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills

    except Exception as e:
        log.error(f"get_recipes_availabl failed: {e}")
        return []
    skills = get_skills(persona_uuid) or {}

    available = []
    for rid, recipe in RECIPES.items():
        skill_name = recipe.get("skill", "crafting")
        skill_level = skills.get(skill_name, 0)
        if skill_level < recipe.get("skill_req", 0):
            continue

        # Check if persona has all inputs
        has_all = True
        for input_id, qty in recipe["inputs"].items():
            if count_item(persona_uuid, input_id) < qty:
                has_all = False
                break

        if has_all:
            available.append({"id": rid, **recipe})

    return available


# ══════════════════════════════════════════════════════════════════════
# Crafting queue — multi-tick crafting
# ══════════════════════════════════════════════════════════════════════

def start_crafting(persona_uuid: str, recipe_id: str,
                   area_uuid: Optional[str] = None) -> dict:
    """Start a crafting job. Consumes inputs immediately.

    Args:
        persona_uuid: The persona doing the crafting.
        recipe_id: Which recipe to craft.
        area_uuid: Where the crafting happens (defaults to persona's area).

    Returns:
        dict with job info or error.
    """
    try:
        recipe = RECIPES.get(recipe_id)

    except Exception as e:
        log.error(f"start_crafting failed: {e}")
        return {}
    if not recipe:
        return {"error": f"unknown recipe: {recipe_id}"}

    db = get_db()
    if not area_uuid:
        area = get_persona_area(persona_uuid)
        if not area:
            return {"error": "persona is not in any area"}
        area_uuid = area["area_uuid"]

    # Check if already crafting
    existing = db.fetch_one("""
        SELECT id FROM ext_sp_crafting_queue
        WHERE worker_uuid = ? AND status = 'in_progress'
    """, (persona_uuid,))
    if existing:
        return {"error": "already crafting"}

    # Consume inputs
    for input_id, qty in recipe["inputs"].items():
        removed = remove_item(persona_uuid, input_id, qty)
        if removed < qty:
            # Refund what was already removed
            for iid, rqty in recipe["inputs"].items():
                if iid == input_id:
                    break  # Don't refund this one (not fully consumed)
                give_item(persona_uuid, iid, rqty)
            give_item(persona_uuid, input_id, removed)  # refund partial
            return {"error": f"not enough {input_id}"}

    # Start crafting job
    output_item, output_qty = list(recipe["outputs"].items())[0]
    now = datetime.now(timezone.utc).isoformat()

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills
    skills = get_skills(persona_uuid) or {}
    skill_level = skills.get(recipe.get("skill", "crafting"), 10)

    # Time reduction from skill (10% per 10 skill levels)
    time_mult = max(0.3, 1.0 - skill_level * 0.01)
    total_ticks = max(1, recipe["time_ticks"] * time_mult)

    db.execute("""
        INSERT INTO ext_sp_crafting_queue
        (worker_uuid, recipe_id, area_uuid, progress, total_required, status,
         output_item_id, output_quantity, started_at)
        VALUES (?, ?, ?, 0, ?, 'in_progress', ?, ?, ?)
    """, (persona_uuid, recipe_id, area_uuid, total_ticks,
          output_item, output_qty, now))

    add_memory(persona_uuid, "started_crafting", item_id=output_item,
               detail={"recipe": recipe_id},
               emotional_weight=3)

    return {
        "job_started": True,
        "recipe": recipe_id,
        "name": recipe["name"],
        "outputs": recipe["outputs"],
        "total_ticks": round(total_ticks, 1),
    }


def process_crafting_queue() -> dict:
    """Advance all in-progress crafting jobs by one tick.

    Jobs that reach completion produce their output items.
    """
    db = get_db()
    jobs = db.fetch_all("""
        SELECT * FROM ext_sp_crafting_queue WHERE status = 'in_progress'
    """)

    completed = 0
    for job in jobs:
        new_progress = job["progress"] + 1

        if new_progress >= job["total_required"]:
            # Complete!
            give_item(job["worker_uuid"], job["output_item_id"],
                      job["output_quantity"])

            db.execute("""
                UPDATE ext_sp_crafting_queue SET status = 'complete', progress = ?
                WHERE id = ?
            """, (new_progress, job["id"]))

            # Skill improvement
            recipe = RECIPES.get(job["recipe_id"])
            if recipe:
                try:
                    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import improve_skill
                    improve_skill(job["worker_uuid"],
                                  recipe.get("skill", "crafting"),
                                  amount=2)
                except Exception:
                    pass

            add_memory(job["worker_uuid"], "crafted",
                       item_id=job["output_item_id"],
                       detail={"recipe": job["recipe_id"],
                               "qty": job["output_quantity"]},
                       emotional_weight=5)
            completed += 1
        else:
            db.execute("""
                UPDATE ext_sp_crafting_queue SET progress = ?
                WHERE id = ?
            """, (new_progress, job["id"]))

            # Burn calories for crafting
            try:
                burn_calories(job["worker_uuid"], "craft")
            except Exception:
                pass

    return {"completed": completed, "in_progress": len(jobs) - completed}


# ══════════════════════════════════════════════════════════════════════
# Building construction & management
# ══════════════════════════════════════════════════════════════════════

def start_building(owner_uuid: str, building_type: str, area_uuid: str,
                   material: str = "wood", name: Optional[str] = None) -> dict:
    """Start constructing a new building.

    Consumes the required materials from the owner's inventory.

    Returns:
        dict with building_uuid and status, or error.
    """
    try:
        bt = BUILDING_TYPES.get(building_type)

    except Exception as e:
        log.error(f"start_building failed: {e}")
        return {}
    if not bt:
        return {"error": f"unknown building type: {building_type}"}

    bm = BUILDING_MATERIALS.get(material)
    if not bm:
        return {"error": f"unknown material: {material}"}

    db = get_db()

    # Count existing buildings in this area
    existing_count = db.fetch_one(
        "SELECT COUNT(*) as c FROM ext_sp_buildings WHERE area_uuid = ?",
        (area_uuid,))
    if existing_count and existing_count["c"] >= 3:
        return {"error": "too many buildings in this area (max 3)"}

    # Consume materials
    for mat_id, mat_qty in bm["required_materials"].items():
        removed = remove_item(owner_uuid, mat_id, mat_qty)
        if removed < mat_qty:
            # Refund
            for mid, mqty in bm["required_materials"].items():
                if mid == mat_id:
                    break
                give_item(owner_uuid, mid, mqty)
            give_item(owner_uuid, mat_id, removed)
            return {"error": f"not enough {mat_id} for {material} building"}

    # Calculate stats based on material
    base_health = bt["base_health"] + bm["health_per_unit"]
    max_health = base_health * 1.5
    defense = bt["defense_base"] + bm["defense_per_unit"]
    insulation = max(bt["insulation_base"], bm["insulation_score"])

    building_uuid = str(uuid_mod.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    db.execute("""
        INSERT INTO ext_sp_buildings
        (building_uuid, area_uuid, owner_uuid, building_type, name,
         health, max_health, material, room_count, max_rooms,
         is_complete, build_progress, defense_rating, insulation_score, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
    """, (building_uuid, area_uuid, owner_uuid, building_type,
          name or f"{bt['name']} #{random.randint(1, 99)}",
          base_health, max_health, material,
          1, bt["max_rooms"],
          round(defense, 1), round(insulation, 2),
          now))

    add_memory(owner_uuid, "started_building", detail={
        "building_type": building_type,
        "material": material,
        "area_uuid": area_uuid,
        "defense": round(defense, 1),
    }, emotional_weight=7)

    return {
        "building_uuid": building_uuid,
        "type": building_type,
        "material": material,
        "name": name or "Unnamed",
        "defense": round(defense, 1),
        "insulation": round(insulation, 2),
        "max_health": round(max_health, 1),
        "message": f"Started building {bt['name']} ({material})",
    }


def advance_building(building_uuid: str, progress_amount: float = 1.0) -> dict:
    """Advance building construction progress.

    Returns dict with new progress or completion status.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"advance_building failed: {e}")
        return {}
    building = db.fetch_one(
        "SELECT * FROM ext_sp_buildings WHERE building_uuid = ?",
        (building_uuid,))
    if not building:
        return {"error": "building not found"}
    if building["is_complete"]:
        return {"error": "building already complete"}

    new_progress = building["build_progress"] + progress_amount
    total_needed = 10.0 * BUILDING_MATERIALS.get(
        building["material"], BUILDING_MATERIALS["wood"]
    )["build_time_mult"]

    if new_progress >= total_needed:
        db.execute("""
            UPDATE ext_sp_buildings SET
                build_progress = ?, is_complete = 1
            WHERE building_uuid = ?
        """, (total_needed, building_uuid))
        return {
            "complete": True,
            "message": f"{building.get('name', 'Building')} is complete!"
        }
    else:
        db.execute("""
            UPDATE ext_sp_buildings SET build_progress = ?
            WHERE building_uuid = ?
        """, (new_progress, building_uuid))
        return {
            "complete": False,
            "progress": round(new_progress / total_needed * 100, 1),
        }


def add_room(building_uuid: str, room_type: str) -> dict:
    """Add a utility room to an existing building.

    Rooms consume materials and provide bonuses.
    """
    try:
        bt_info = ROOM_TYPES.get(room_type)

    except Exception as e:
        log.error(f"add_room failed: {e}")
        return {}
    if not bt_info:
        return {"error": f"unknown room type: {room_type}"}

    db = get_db()
    building = db.fetch_one(
        "SELECT * FROM ext_sp_buildings WHERE building_uuid = ?",
        (building_uuid,))
    if not building:
        return {"error": "building not found"}
    if not building["is_complete"]:
        return {"error": "building must be complete first"}
    if building["room_count"] >= building["max_rooms"]:
        return {"error": "building has no room slots left"}

    # Cost per room (materials-based)
    mat = BUILDING_MATERIALS.get(building["material"], BUILDING_MATERIALS["wood"])
    mat_cost = {k: v // 2 for k, v in mat["required_materials"].items()}

    # Build room
    db.execute("""
        UPDATE ext_sp_buildings SET room_count = room_count + 1
        WHERE building_uuid = ?
    """, (building_uuid,))

    return {
        "room_added": room_type,
        "room_name": bt_info["name"],
        "room_count": building["room_count"] + 1,
        "max_rooms": building["max_rooms"],
        "bonus": bt_info["effect"],
    }


def get_building_shelter_score(persona_uuid: str) -> dict:
    """Get the best available shelter score for a persona in their area.

    Returns insulation, defense, and health bonuses from nearby buildings.
    """
    db = get_db()
    area = get_persona_area(persona_uuid)
    if not area:
        return {"insulation": 0, "defense": 0, "rest_bonus": 0}

    buildings = db.fetch_all("""
        SELECT * FROM ext_sp_buildings
        WHERE area_uuid = ? AND is_complete = 1
        ORDER BY insulation_score DESC
    """, (area["area_uuid"],))

    if not buildings:
        return {"insulation": 0, "defense": 0, "rest_bonus": 0}

    best = buildings[0]
    rest_bonus = 0
    for b in buildings:
        rest_bonus += b["room_count"] * 5  # More rooms = better rest

    return {
        "insulation": best["insulation_score"],
        "defense": best["defense_rating"],
        "rest_bonus": min(50, rest_bonus),
        "has_shelter": True,
        "building_name": best.get("name", "Unknown"),
        "building_type": best["building_type"],
        "material": best["material"],
    }


# ══════════════════════════════════════════════════════════════════════
# Building damage & war defense
# ══════════════════════════════════════════════════════════════════════

def damage_building(building_uuid: str, damage: float) -> dict:
    """Apply damage to a building. Reduces health.

    Returns whether the building was destroyed.
    """
    db = get_db()
    building = db.fetch_one(
        "SELECT * FROM ext_sp_buildings WHERE building_uuid = ?",
        (building_uuid,))
    if not building:
        return {"error": "building not found"}

    new_health = max(0, building["health"] - damage)
    destroyed = new_health <= 0

    db.execute("""
        UPDATE ext_sp_buildings SET health = ? WHERE building_uuid = ?
    """, (new_health, building_uuid))

    return {
        "damage": round(damage, 1),
        "health_remaining": round(new_health, 1),
        "destroyed": destroyed,
    }


def repair_building(persona_uuid: str, building_uuid: str) -> dict:
    """Repair a damaged building using materials from inventory."""
    try:
        db = get_db()

    except Exception as e:
        log.error(f"repair_building failed: {e}")
        return {}
    building = db.fetch_one(
        "SELECT * FROM ext_sp_buildings WHERE building_uuid = ?",
        (building_uuid,))
    if not building:
        return {"error": "building not found"}

    if building["health"] >= building["max_health"]:
        return {"error": "building is at full health"}

    mat = BUILDING_MATERIALS.get(building["material"])
    if not mat:
        return {"error": "unknown building material"}

    # Consume repair materials
    for mat_id, mat_qty in mat["repair_materials"].items():
        removed = remove_item(persona_uuid, mat_id, mat_qty)
        if removed < mat_qty:
            give_item(persona_uuid, mat_id, removed)  # refund partial
            return {"error": f"not enough {mat_id} for repair"}

    # Heal
    heal = building["max_health"] * 0.2
    new_health = min(building["max_health"], building["health"] + heal)
    db.execute("""
        UPDATE ext_sp_buildings SET health = ? WHERE building_uuid = ?
    """, (new_health, building_uuid))

    add_memory(persona_uuid, "repaired_building", detail={
        "building": building.get("name", "?"),
        "healed": round(heal, 1),
    }, emotional_weight=3)

    return {
        "healed": round(heal, 1),
        "health": round(new_health, 1),
        "max_health": building["max_health"],
    }

