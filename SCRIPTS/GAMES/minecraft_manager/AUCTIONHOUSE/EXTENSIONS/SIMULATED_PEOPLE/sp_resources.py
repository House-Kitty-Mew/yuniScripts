"""
sp_resources.py — World resource nodes, gathering, and dynamic distribution.

Every area of the world has resource nodes (trees, stone outcrops, clay
deposits, ore veins, berry bushes, etc.) that personas can gather from.
Nodes deplete with use and regrow (or not) based on type.

Gathering requires time, skill, and appropriate tools. The yield goes
directly into the persona's inventory.
"""

import json, random, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db, add_memory
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_all_areas, get_area, get_persona_area
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import (
    get_item_def, give_item, get_equipped_stats, burn_calories, ITEM_DEFS,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Resource node definitions — what nodes appear in which biomes
# ══════════════════════════════════════════════════════════════════════

RESOURCE_NODE_TYPES = {
    # (node_type) -> {items, quantity_range, regrowth, access, biome list}

    "tree": {
        "items": [("wood_log", 1, 0.7), ("stick", 2, 0.3)],
        "quantity_range": (50, 300),
        "regrowth_rate": 0.5,
        "access_difficulty": 1.0,
        "tool_type": "axe",
        "biomes": ["forest", "plains", "swamp", "mountains", "tundra"],
        "description": "A mature tree with a thick trunk.",
    },
    "berry_bush": {
        "items": [("berry", 3, 1.0)],
        "quantity_range": (10, 50),
        "regrowth_rate": 2.0,
        "access_difficulty": 0.3,
        "biomes": ["forest", "plains", "swamp", "mountains"],
        "description": "A bush laden with ripe berries.",
    },
    "stone_outcrop": {
        "items": [("stone", 2, 0.6), ("flint", 1, 0.2)],
        "quantity_range": (50, 200),
        "regrowth_rate": 0.01,  # Geologic — basically finite
        "access_difficulty": 2.0,
        "tool_type": "pickaxe",
        "biomes": ["mountains", "plains", "desert", "tundra"],
        "description": "An exposed rock face with loose stone.",
    },
    "clay_deposit": {
        "items": [("clay", 2, 1.0)],
        "quantity_range": (30, 150),
        "regrowth_rate": 0.3,
        "access_difficulty": 1.0,
        "biomes": ["swamp", "ocean", "forest"],
        "description": "Sticky clay along the water's edge.",
    },
    "fibre_plants": {
        "items": [("fibre", 3, 0.8), ("stick", 1, 0.2)],
        "quantity_range": (20, 80),
        "regrowth_rate": 3.0,
        "access_difficulty": 0.2,
        "biomes": ["plains", "forest", "swamp", "mountains"],
        "description": "Tall fibrous plants, good for cordage.",
    },
    "iron_vein": {
        "items": [("iron_ore", 1, 1.0)],
        "quantity_range": (10, 60),
        "regrowth_rate": 0.0,  # Finite
        "access_difficulty": 4.0,
        "tool_type": "pickaxe",
        "biomes": ["mountains", "deep_dark", "nether_wastes"],
        "description": "A vein of iron ore embedded in the rock.",
    },
    "herb_patch": {
        "items": [("herbs", 2, 1.0)],
        "quantity_range": (5, 25),
        "regrowth_rate": 1.0,
        "access_difficulty": 0.5,
        "biomes": ["forest", "plains", "swamp"],
        "description": "A cluster of wild medicinal herbs.",
    },
    "deadwood": {
        "items": [("stick", 3, 0.6), ("wood_log", 1, 0.4)],
        "quantity_range": (10, 40),
        "regrowth_rate": 1.0,
        "access_difficulty": 0.3,
        "biomes": ["forest", "plains", "swamp", "mountains", "tundra"],
        "description": "Deadfall branches and dried timber.",
    },
    "reed_bed": {
        "items": [("fibre", 2, 0.7)],
        "quantity_range": (15, 60),
        "regrowth_rate": 2.0,
        "access_difficulty": 0.5,
        "biomes": ["swamp", "ocean"],
        "description": "Tall reeds growing in shallow water.",
    },
}


# ══════════════════════════════════════════════════════════════════════
# Initialization — spawn resource nodes across the world
# ══════════════════════════════════════════════════════════════════════

def spawn_resource_nodes():
    """Place initial resource nodes in every area based on biome.

    Safe to call multiple times; already-seeded nodes are skipped.
    """
    db = get_db()
    areas = get_all_areas()
    now = datetime.now(timezone.utc).isoformat()
    count = 0

    for area in areas:
        biome = area["biome_type"]

        # Check if area already has nodes
        existing = db.fetch_one(
            "SELECT COUNT(*) as c FROM ext_sp_resource_nodes WHERE area_uuid = ?",
            (area["area_uuid"],))
        if existing and existing["c"] > 0:
            continue

        # Determine how many nodes per area (3-8 based on biome)
        node_counts = {
            "plains": (4, 6), "forest": (6, 8), "desert": (3, 5),
            "swamp": (5, 7), "mountains": (4, 7), "ocean": (3, 5),
            "tundra": (3, 5), "nether_wastes": (2, 4),
            "crimson_forest": (3, 5), "end_islands": (1, 3),
            "deep_dark": (2, 4),
        }
        lo, hi = node_counts.get(biome, (3, 5))
        num_nodes = random.randint(lo, hi)

        for _ in range(num_nodes):
            node_type = _pick_node_type(biome)
            nt = RESOURCE_NODE_TYPES.get(node_type)
            if not nt:
                continue

            qty = random.uniform(*nt["quantity_range"])
            db.execute("""
                INSERT OR IGNORE INTO ext_sp_resource_nodes
                (node_uuid, area_uuid, resource_type, quantity, max_quantity,
                 regrowth_rate, quality, access_difficulty, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(uuid_mod.uuid4()),
                area["area_uuid"],
                node_type,
                round(qty, 1),
                round(qty, 1),
                nt["regrowth_rate"],
                round(random.uniform(0.6, 1.0), 2),
                nt["access_difficulty"],
                now,
            ))
            count += 1

    log.info("sp_resources", f"Spawned {count} resource nodes across {len(areas)} areas")


def _pick_node_type(biome: str) -> str:
    """Pick a random resource node type appropriate for the biome."""
    candidates = []
    for nt_name, nt_data in RESOURCE_NODE_TYPES.items():
        if biome in nt_data["biomes"]:
            candidates.append(nt_name)
    if not candidates:
        return "stone_outcrop"
    return random.choice(candidates)


# ══════════════════════════════════════════════════════════════════════
# Gathering — convert world node → inventory items
# ══════════════════════════════════════════════════════════════════════

def gather_resource(persona_uuid: str, node_uuid: str,
                    quantity: float = 1.0) -> dict:
    """Gather from a resource node into the persona's inventory.

    Args:
        persona_uuid: The persona doing the gathering.
        node_uuid: The resource node to gather from.
        quantity: How many gathering attempts (each yields base items).

    Returns:
        dict with items gathered, message, or error.
    """
    db = get_db()
    node = db.fetch_one(
        "SELECT * FROM ext_sp_resource_nodes WHERE node_uuid = ?",
        (node_uuid,))
    if not node:
        return {"error": "node not found"}
    if node["depleted"] or node["quantity"] <= 0:
        return {"error": "node is depleted"}

    nt = RESOURCE_NODE_TYPES.get(node["resource_type"])
    if not nt:
        return {"error": f"unknown node type: {node['resource_type']}"}

    # Get equipped tools
    stats = get_equipped_stats(persona_uuid)

    # Check tool requirement
    required_tool = nt.get("tool_type")
    if required_tool:
        tool_power = stats["tools"].get(required_tool, 0)
        if tool_power <= 0:
            return {
                "error": f"need a {required_tool} to gather from this node",
                "required_tool": required_tool,
            }

    # Get persona skills
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills
        skills = get_skills(persona_uuid) or {}
    except Exception:
        skills = {}

    # Calculate gather rate
    tool_factor = 1 + (tool_power * 0.3) if required_tool else 1.5
    skill_factor = 1 + (skills.get("mining" if required_tool in ("pickaxe",) else "farming", 10) / 100)
    access_factor = 1.0 / max(0.5, node["access_difficulty"])

    gather_efficiency = tool_factor * skill_factor * access_factor

    # Amount to take from node
    take = min(node["quantity"], quantity * gather_efficiency)
    take = round(take, 1)

    if take <= 0:
        return {"error": "gathered nothing — node yield is too low"}

    # Reduce node quantity
    new_qty = max(0, node["quantity"] - take)
    depleted = 1 if new_qty <= 0 else 0
    db.execute("""
        UPDATE ext_sp_resource_nodes SET quantity = ?, depleted = ?
        WHERE node_uuid = ?
    """, (round(new_qty, 1), depleted, node_uuid))

    # Convert to items
    item_drops = nt["items"]
    items_gathered = []
    for item_id, base_qty, chance in item_drops:
        roll = random.random()
        if roll < chance:
            yield_qty = base_qty * (take / 10) * random.uniform(0.5, 1.5)
            yield_qty = max(0.5, min(yield_qty, take * 2))
            yield_qty = round(yield_qty, 1)

            give_item(persona_uuid, item_id, yield_qty)
            items_gathered.append({"item": item_id, "quantity": yield_qty})

    # Burn calories for the effort
    action_type = {
        "axe": "chop_wood",
        "pickaxe": "mine",
    }.get(required_tool, "forage") if required_tool else "forage"
    burn_calories(persona_uuid, action_type)

    # Skill improvement
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import improve_skill
        if required_tool == "pickaxe":
            improve_skill(persona_uuid, "mining", amount=1)
        elif required_tool == "axe":
            improve_skill(persona_uuid, "farming", amount=1)
        else:
            improve_skill(persona_uuid, "exploration", amount=1)
    except Exception:
        pass

    # Record memory
    items_str = ", ".join(f"{i['quantity']:.0f} {i['item']}" for i in items_gathered)
    add_memory(persona_uuid, "gathered", detail={
        "node_type": node["resource_type"],
        "items": items_gathered,
        "area_node": node_uuid[:8],
    }, emotional_weight=3)

    return {
        "items": items_gathered,
        "message": f"Gathered {items_str} from {node['resource_type']}",
        "node_remaining": round(new_qty, 1),
    }


def get_area_nodes(area_uuid: str) -> list[dict]:
    """Get all non-depleted resource nodes in an area."""
    db = get_db()
    return db.fetch_all("""
        SELECT n.*, nt.description
        FROM ext_sp_resource_nodes n
        LEFT JOIN (
            SELECT 'tree' as rt, 'A mature tree' as desc
        ) dummy ON 1=0
        WHERE n.area_uuid = ? AND n.depleted = 0 AND n.quantity > 0
        ORDER BY n.quantity DESC
    """, (area_uuid,))


def get_node_info(node_type: str) -> Optional[dict]:
    """Get metadata about a resource node type."""
    return RESOURCE_NODE_TYPES.get(node_type)


# ══════════════════════════════════════════════════════════════════════
# Node regrowth — daily tick
# ══════════════════════════════════════════════════════════════════════

def process_node_regrowth() -> dict:
    """Regrow depleted/partially-depleted resource nodes.

    Non-renewable nodes (ore veins) do not regrow.
    """
    db = get_db()
    cfg = get_config()
    if not cfg.get("ecosystem_enabled", True):
        return {"status": "disabled"}

    nodes = db.fetch_all("""
        SELECT * FROM ext_sp_resource_nodes
        WHERE quantity < max_quantity AND regrowth_rate > 0
    """)

    regrown = 0
    for node in nodes:
        growth = node["regrowth_rate"] * random.uniform(0.5, 1.5)
        new_qty = min(node["max_quantity"], node["quantity"] + growth)
        db.execute("""
            UPDATE ext_sp_resource_nodes SET quantity = ?, depleted = 0
            WHERE id = ?
        """, (round(new_qty, 1), node["id"]))
        regrown += 1

    return {"nodes_regrown": regrown}
