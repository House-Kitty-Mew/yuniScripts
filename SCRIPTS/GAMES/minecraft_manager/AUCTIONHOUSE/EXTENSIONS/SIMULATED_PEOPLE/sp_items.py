"""
sp_items.py — Item definitions, inventory system, equipment, and consumption.

Provides:
  - ITEM_DEFS: master catalogue of all items in the world
  - Inventory CRUD (give, take, count, list per container)
  - Equipment system (equip/unequip, stat aggregation)
  - Consumption (eat, drink) tied directly into health stats
  - Inventory decay (perishable food rots over time)
  - Calorie tracking (actions burn food/water)

Every item exists physically in a container on a persona or building.
"""

import json, random, math
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db, add_memory
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Item definitions — the master catalogue
# ══════════════════════════════════════════════════════════════════════

# Each entry: item_id -> {name, category, weight_kg, ...crafting_tags,
#   calories, hydration, equip_slot, armor, insulation, tool_power, ...}

ITEM_DEFS = {
    # ── Food & Drink ──────────────────────────────────────────
    "berry": {
        "name": "Wild Berries", "category": "food", "weight_kg": 0.05,
        "perishable": True, "decay_per_day": 0.3, "calories_per_unit": 15,
        "hydration_per_unit": 0.7, "crafting_tags": ["edible", "plant"],
        "description": "Small sweet berries, edible raw.",
    },
    "raw_meat": {
        "name": "Raw Meat", "category": "food", "weight_kg": 0.5,
        "perishable": True, "decay_per_day": 0.5, "calories_per_unit": 250,
        "hydration_per_unit": 0.3, "toxicity": 2,
        "crafting_tags": ["edible", "animal", "raw"],
        "description": "Fresh raw meat. Will spoil quickly. Cook before eating.",
    },
    "cooked_meat": {
        "name": "Cooked Meat", "category": "food", "weight_kg": 0.4,
        "perishable": True, "decay_per_day": 0.1, "calories_per_unit": 350,
        "hydration_per_unit": 0.2, "crafting_tags": ["edible", "animal", "cooked"],
        "description": "Tasty cooked meat. Lasts much longer than raw.",
    },
    "dried_meat": {
        "name": "Dried Meat", "category": "food", "weight_kg": 0.15,
        "perishable": True, "decay_per_day": 0.02, "calories_per_unit": 400,
        "hydration_per_unit": 0.05, "crafting_tags": ["edible", "animal", "dried", "preserved"],
        "description": "Jerky-style preserved meat. Lasts for months.",
    },
    "fish": {
        "name": "Raw Fish", "category": "food", "weight_kg": 0.3,
        "perishable": True, "decay_per_day": 0.4, "calories_per_unit": 200,
        "hydration_per_unit": 0.4, "toxicity": 1,
        "crafting_tags": ["edible", "animal", "raw"],
        "description": "Freshly caught fish. Cook or dry soon.",
    },
    "cooked_fish": {
        "name": "Cooked Fish", "category": "food", "weight_kg": 0.25,
        "perishable": True, "decay_per_day": 0.08, "calories_per_unit": 280,
        "hydration_per_unit": 0.3, "crafting_tags": ["edible", "animal", "cooked"],
        "description": "Delicious cooked fish.",
    },
    "herbs": {
        "name": "Medicinal Herbs", "category": "medicine", "weight_kg": 0.02,
        "perishable": True, "decay_per_day": 0.1, "calories_per_unit": 5,
        "crafting_tags": ["edible", "plant", "medicine"],
        "description": "Wild herbs with mild healing properties.",
    },
    "clean_water": {
        "name": "Clean Water", "category": "drink", "weight_kg": 1.0,
        "perishable": False, "calories_per_unit": 0, "hydration_per_unit": 1.0,
        "crafting_tags": ["drinkable"],
        "description": "A portion of clean drinking water.",
    },
    "dirty_water": {
        "name": "Dirty Water", "category": "drink", "weight_kg": 1.0,
        "perishable": False, "calories_per_unit": 0, "hydration_per_unit": 0.5,
        "toxicity": 3, "crafting_tags": ["drinkable", "contaminated"],
        "description": "Murky water. Drink only in emergencies — risk of disease.",
    },

    # ── Materials ──────────────────────────────────────────
    "wood_log": {
        "name": "Wood Log", "category": "material", "weight_kg": 15.0,
        "stackable": True, "max_stack": 16,
        "crafting_tags": ["wood", "fuel", "building_material", "heavy"],
        "description": "A hefty log of untreated timber.",
    },
    "stick": {
        "name": "Stick", "category": "material", "weight_kg": 0.3,
        "stackable": True, "max_stack": 64,
        "crafting_tags": ["wood", "fuel", "haft", "light"],
        "description": "A sturdy branch, useful for tool handles and kindling.",
    },
    "stone": {
        "name": "Stone", "category": "material", "weight_kg": 2.0,
        "stackable": True, "max_stack": 32,
        "crafting_tags": ["stone", "building_material", "blade"],
        "description": "A fist-sized chunk of rock.",
    },
    "flint": {
        "name": "Flint", "category": "material", "weight_kg": 0.2,
        "stackable": True, "max_stack": 64,
        "crafting_tags": ["stone", "blade", "fire_starter"],
        "description": "A sharp-edged flint nodule. Sparks when struck.",
    },
    "fibre": {
        "name": "Plant Fibre", "category": "material", "weight_kg": 0.05,
        "stackable": True, "max_stack": 64,
        "crafting_tags": ["fibre", "cordage", "tinder"],
        "description": "Tough plant fibres, good for rope and bandages.",
    },
    "clay": {
        "name": "Clay", "category": "material", "weight_kg": 1.0,
        "stackable": True, "max_stack": 32,
        "crafting_tags": ["clay", "pottery"],
        "description": "Sticky, workable clay from a riverbank.",
    },
    "hide": {
        "name": "Raw Hide", "category": "material", "weight_kg": 2.0,
        "perishable": True, "decay_per_day": 0.05,
        "crafting_tags": ["animal", "leather", "hide"],
        "description": "Fresh animal skin. Needs tanning before use.",
    },
    "leather": {
        "name": "Leather", "category": "material", "weight_kg": 1.5,
        "crafting_tags": ["animal", "leather", "leather_armor"],
        "description": "Tanned, supple leather. Ready for crafting.",
    },
    "bone": {
        "name": "Bone", "category": "material", "weight_kg": 0.5,
        "stackable": True, "max_stack": 32,
        "crafting_tags": ["animal", "bone", "blade", "needle"],
        "description": "A clean animal bone. Can be shaped into tools.",
    },
    "iron_ore": {
        "name": "Iron Ore", "category": "material", "weight_kg": 3.0,
        "stackable": True, "max_stack": 16,
        "crafting_tags": ["ore", "metal", "heavy"],
        "description": "Raw iron-bearing rock. Must be smelted.",
    },
    "iron_ingot": {
        "name": "Iron Ingot", "category": "material", "weight_kg": 2.0,
        "stackable": True, "max_stack": 16,
        "crafting_tags": ["metal", "iron"],
        "description": "A refined bar of iron. Ready for smithing.",
    },
    "string": {
        "name": "String", "category": "material", "weight_kg": 0.02,
        "stackable": True, "max_stack": 64,
        "crafting_tags": ["fibre", "cordage", "light"],
        "description": "Strong twisted cord made from plant fibre or sinew.",
    },
    "cloth": {
        "name": "Cloth", "category": "material", "weight_kg": 0.1,
        "stackable": True, "max_stack": 32,
        "crafting_tags": ["fibre", "cloth", "bandage"],
        "description": "Woven fabric. Can be used for clothing or bandages.",
    },

    # ── Tools ────────────────────────────────────────────
    "stone_axe": {
        "name": "Stone Axe", "category": "tool", "weight_kg": 1.5,
        "stackable": False, "perishable": False,
        "tool_power": 1.0, "tool_type": "axe",
        "crafting_tags": ["tool", "axe", "hand_tool"],
        "description": "A crude stone axe. Good for chopping wood.",
    },
    "iron_axe": {
        "name": "Iron Axe", "category": "tool", "weight_kg": 1.8,
        "stackable": False, "perishable": False,
        "tool_power": 3.0, "tool_type": "axe",
        "crafting_tags": ["tool", "axe", "metal_tool"],
        "description": "A sharp iron axe. Excellent for forestry.",
    },
    "stone_pickaxe": {
        "name": "Stone Pickaxe", "category": "tool", "weight_kg": 1.5,
        "stackable": False, "perishable": False,
        "tool_power": 1.0, "tool_type": "pickaxe",
        "crafting_tags": ["tool", "pickaxe", "hand_tool"],
        "description": "A crude stone-headed pick. Good for basic mining.",
    },
    "iron_pickaxe": {
        "name": "Iron Pickaxe", "category": "tool", "weight_kg": 1.8,
        "stackable": False, "perishable": False,
        "tool_power": 3.0, "tool_type": "pickaxe",
        "crafting_tags": ["tool", "pickaxe", "metal_tool"],
        "description": "A sturdy iron pick for serious mining.",
    },
    "stone_knife": {
        "name": "Stone Knife", "category": "tool", "weight_kg": 0.3,
        "stackable": False, "perishable": False,
        "tool_power": 0.8, "tool_type": "knife",
        "crafting_tags": ["tool", "knife", "blade", "hand_tool"],
        "description": "A sharp flint blade set in a wooden handle.",
    },
    "iron_knife": {
        "name": "Iron Knife", "category": "tool", "weight_kg": 0.4,
        "stackable": False, "perishable": False,
        "tool_power": 2.5, "tool_type": "knife",
        "crafting_tags": ["tool", "knife", "blade", "metal_tool"],
        "description": "A fine iron blade. Multi-purpose cutting tool.",
    },
    "spear": {
        "name": "Spear", "category": "weapon", "weight_kg": 2.0,
        "stackable": False, "perishable": False,
        "tool_power": 2.0, "tool_type": "spear",
        "crafting_tags": ["weapon", "polearm", "hunting"],
        "description": "A long wooden shaft tipped with sharp stone or bone.",
    },
    "fishing_rod": {
        "name": "Fishing Rod", "category": "tool", "weight_kg": 0.5,
        "stackable": False, "perishable": False,
        "tool_power": 1.0, "tool_type": "fishing",
        "crafting_tags": ["tool", "fishing"],
        "description": "A simple line-and-hook fishing rod.",
    },
    "waterskin": {
        "name": "Waterskin", "category": "container", "weight_kg": 0.2,
        "stackable": False, "perishable": False,
        "tool_power": 0, "tool_type": "container",
        "crafting_tags": ["container", "water_carrier"],
        "description": "A leather bag that holds up to 3L of water.",
    },
    "clay_pot": {
        "name": "Clay Pot", "category": "container", "weight_kg": 1.0,
        "stackable": False, "perishable": False,
        "crafting_tags": ["container", "pottery", "storage"],
        "description": "A fired clay pot. Stores dry goods and keeps them fresh.",
    },
    "bandage": {
        "name": "Cloth Bandage", "category": "medicine", "weight_kg": 0.05,
        "stackable": True, "max_stack": 16, "perishable": False,
        "crafting_tags": ["medicine", "bandage", "cloth"],
        "description": "A clean cloth strip for wound dressing.",
    },

    # ── Clothing / Armour ───────────────────────────────────
    "straw_hat": {
        "name": "Straw Hat", "category": "clothing", "weight_kg": 0.1,
        "stackable": False, "perishable": False,
        "equip_slot": "head", "insulation_value": 0.1, "armor_value": 0.1,
        "crafting_tags": ["clothing", "headwear"],
        "description": "Provides basic shade from the sun.",
    },
    "hide_shirt": {
        "name": "Hide Shirt", "category": "clothing", "weight_kg": 1.5,
        "stackable": False, "perishable": False,
        "equip_slot": "chest", "insulation_value": 0.4, "armor_value": 1.0,
        "crafting_tags": ["clothing", "leather_armor"],
        "description": "A tough leather tunic. Moderate protection.",
    },
    "hide_pants": {
        "name": "Hide Pants", "category": "clothing", "weight_kg": 1.0,
        "stackable": False, "perishable": False,
        "equip_slot": "legs", "insulation_value": 0.3, "armor_value": 0.8,
        "crafting_tags": ["clothing", "leather_armor"],
        "description": "Sturdy leather leggings.",
    },
    "hide_boots": {
        "name": "Hide Boots", "category": "clothing", "weight_kg": 0.8,
        "stackable": False, "perishable": False,
        "equip_slot": "feet", "insulation_value": 0.3, "armor_value": 0.5,
        "crafting_tags": ["clothing", "leather_armor", "footwear"],
        "description": "Simple leather boots. Keep your feet dry.",
    },
    "cloth_shirt": {
        "name": "Cloth Shirt", "category": "clothing", "weight_kg": 0.3,
        "stackable": False, "perishable": False,
        "equip_slot": "chest", "insulation_value": 0.2, "armor_value": 0.2,
        "crafting_tags": ["clothing", "cloth"],
        "description": "Light, breathable cloth shirt.",
    },
    "fur_cloak": {
        "name": "Fur Cloak", "category": "clothing", "weight_kg": 2.0,
        "stackable": False, "perishable": False,
        "equip_slot": "back", "insulation_value": 0.8, "armor_value": 0.5,
        "crafting_tags": ["clothing", "fur", "warm"],
        "description": "A heavy fur cloak for extreme cold.",
    },
    "iron_helmet": {
        "name": "Iron Helmet", "category": "armor", "weight_kg": 2.5,
        "stackable": False, "perishable": False,
        "equip_slot": "head", "insulation_value": 0.3, "armor_value": 3.0,
        "crafting_tags": ["armor", "metal_armor"],
        "description": "Forged iron helmet. Excellent head protection.",
    },
    "iron_chestplate": {
        "name": "Iron Chestplate", "category": "armor", "weight_kg": 8.0,
        "stackable": False, "perishable": False,
        "equip_slot": "chest", "insulation_value": 0.4, "armor_value": 5.0,
        "crafting_tags": ["armor", "metal_armor"],
        "description": "Full iron chestplate. Heavy but very protective.",
    },

    # ── Building Materials ──────────────────────────────────
    "wooden_plank": {
        "name": "Wooden Plank", "category": "building", "weight_kg": 5.0,
        "stackable": True, "max_stack": 32,
        "crafting_tags": ["building_material", "wood"],
        "description": "A sawn wooden plank. Basic building component.",
    },
    "stone_brick": {
        "name": "Stone Brick", "category": "building", "weight_kg": 8.0,
        "stackable": True, "max_stack": 16,
        "crafting_tags": ["building_material", "stone"],
        "description": "A dressed stone block. Sturdy construction material.",
    },
    "nail": {
        "name": "Iron Nail", "category": "building", "weight_kg": 0.02,
        "stackable": True, "max_stack": 128,
        "crafting_tags": ["building_material", "metal"],
        "description": "Small iron nails for joinery.",
    },
    "rope": {
        "name": "Rope", "category": "material", "weight_kg": 0.5,
        "stackable": True, "max_stack": 16,
        "crafting_tags": ["fibre", "cordage", "building_material"],
        "description": "Strong rope made from twisted plant fibre.",
    },
    "torch": {
        "name": "Torch", "category": "tool", "weight_kg": 0.3,
        "stackable": True, "max_stack": 16, "perishable": False,
        "crafting_tags": ["tool", "light_source", "fuel"],
        "description": "A stick with a resin-soaked head. Burns for several hours.",
    },

    # ── Miscellaneous ─────────────────────────────────────
    "bone_needle": {
        "name": "Bone Needle", "category": "tool", "weight_kg": 0.01,
        "stackable": False, "perishable": False,
        "tool_power": 0.5, "tool_type": "needle",
        "crafting_tags": ["tool", "needle", "bone"],
        "description": "A fine bone needle with a thread hole. For sewing.",
    },
    "fire_bow": {
        "name": "Fire Bow", "category": "tool", "weight_kg": 0.1,
        "stackable": False, "perishable": False,
        "tool_power": 1.0, "tool_type": "fire_starter",
        "crafting_tags": ["tool", "fire_starter"],
        "description": "A bow-drill fire-starting kit. Creates sparks.",
    },
}

# Equipment slots
EQUIP_SLOTS = ["head", "chest", "legs", "feet", "back", "main_hand", "off_hand", "necklace", "ring"]

# Container definitions (weight/volume capacity)
CONTAINER_DEFS = {
    "hands": {"max_weight_kg": 10, "max_volume_l": 20, "slots": 2},
    "belt_pouch": {"max_weight_kg": 2, "max_volume_l": 3, "slots": 4},
    "backpack": {"max_weight_kg": 20, "max_volume_l": 40, "slots": 12},
    "quiver": {"max_weight_kg": 3, "max_volume_l": 5, "slots": 6},
    "waterskin": {"max_weight_kg": 4, "max_volume_l": 4, "slots": 1, "insulated": True},
    "clay_pot": {"max_weight_kg": 8, "max_volume_l": 10, "slots": 3, "insulated": True},
    "storage_chest": {"max_weight_kg": 100, "max_volume_l": 200, "slots": 27},
}


# ══════════════════════════════════════════════════════════════════════
# Initialization — seed item definitions
# ══════════════════════════════════════════════════════════════════════

def seed_item_defs():
    """Populate the ext_sp_item_defs table with master item definitions.

    Safe to call multiple times (ON CONFLICT DO NOTHING pattern).
    """
    db = get_db()
    count = 0
    for item_id, defn in ITEM_DEFS.items():
        existing = db.fetch_one(
            "SELECT id FROM ext_sp_item_defs WHERE item_id = ?", (item_id,))
        if existing:
            continue
        tags = json.dumps(defn.get("crafting_tags", []))
        db.execute("""
            INSERT INTO ext_sp_item_defs
            (item_id, name, category, weight_kg, volume_l, stackable, max_stack,
             perishable, decay_per_day, calories_per_unit, hydration_per_unit,
             toxicity, material, crafting_tags, equip_slot,
             armor_value, insulation_value, tool_power, tool_type, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item_id,
            defn.get("name", item_id),
            defn.get("category", "misc"),
            defn.get("weight_kg", 0.1),
            defn.get("volume_l", 0.1),
            1 if defn.get("stackable", True) else 0,
            defn.get("max_stack", 64),
            1 if defn.get("perishable", False) else 0,
            defn.get("decay_per_day", 0),
            defn.get("calories_per_unit", 0),
            defn.get("hydration_per_unit", 0),
            defn.get("toxicity", 0),
            defn.get("material", None),
            tags,
            defn.get("equip_slot", None),
            defn.get("armor_value", 0),
            defn.get("insulation_value", 0),
            defn.get("tool_power", 0),
            defn.get("tool_type", None),
            defn.get("description", ""),
        ))
        count += 1
    if count:
        log.info("sp_items", f"Seeded {count} item definitions")


def get_item_def(item_id: str) -> Optional[dict]:
    """Get the full item definition dict for an item_id."""
    defn = ITEM_DEFS.get(item_id)
    if defn:
        return {"item_id": item_id, **defn}
    return None


# ══════════════════════════════════════════════════════════════════════
# Inventory CRUD
# ══════════════════════════════════════════════════════════════════════

def give_item(owner_uuid: str, item_id: str, quantity: float = 1.0,
              container: str = "hands", owner_type: str = "persona",
              condition_val: float = 1.0, equip: bool = False,
              properties: Optional[dict] = None) -> bool:
    """Add an item to a persona's inventory.

    Args:
        owner_uuid: Persona (or building) UUID.
        item_id: Item type to add.
        quantity: How many / how much.
        container: Which container to put it in.
        condition_val: 0-1 for tool/equipment condition.
        equip: If True, auto-equip to the appropriate slot.
        properties: Optional extra key-value data.

    Returns True on success.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"give_item failed: {e}")
        return False
    now = datetime.now(timezone.utc).isoformat()

    # Try to stack with existing items of same type in same container
    existing = db.fetch_one("""
        SELECT id, quantity FROM ext_sp_inventory
        WHERE owner_uuid = ? AND owner_type = ? AND container = ?
          AND item_id = ? AND is_equipped = 0
        ORDER BY id LIMIT 1
    """, (owner_uuid, owner_type, container, item_id))

    defn = get_item_def(item_id)
    max_stack = defn.get("max_stack", 64) if defn else 64

    if existing:
        new_qty = existing["quantity"] + quantity
        if new_qty <= max_stack:
            db.execute(
                "UPDATE ext_sp_inventory SET quantity = ? WHERE id = ?",
                (new_qty, existing["id"]))
            return True
        else:
            # Fill existing stack, put remainder in new slot
            remainder = new_qty - max_stack
            db.execute(
                "UPDATE ext_sp_inventory SET quantity = ? WHERE id = ?",
                (max_stack, existing["id"]))
            quantity = remainder

    props_json = json.dumps(properties) if properties else None
    db.execute("""
        INSERT INTO ext_sp_inventory
        (owner_uuid, owner_type, container, item_id, quantity,
         condition_val, properties_json, is_equipped, slot_index, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (owner_uuid, owner_type, container, item_id, quantity,
          condition_val, props_json, 1 if equip else 0, 0, now))

    # Auto-equip if requested
    if equip and defn and defn.get("equip_slot"):
        _auto_equip(db, owner_uuid, item_id)

    return True


def remove_item(owner_uuid: str, item_id: str, quantity: float = 1.0,
                owner_type: str = "persona") -> float:
    """Remove items from inventory. Returns actual amount removed.
    
    Automatically logs each removal to the trash database for audit trail.
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal

    db = get_db()
    removed = 0.0

    stacks = db.fetch_all("""
        SELECT id, quantity, container FROM ext_sp_inventory
        WHERE owner_uuid = ? AND owner_type = ? AND item_id = ?
          AND is_equipped = 0
        ORDER BY id
    """, (owner_uuid, owner_type, item_id))

    for stack in stacks:
        if removed >= quantity:
            break
        take = min(stack["quantity"], quantity - removed)
        new_qty = stack["quantity"] - take
        if new_qty <= 0:
            db.execute("DELETE FROM ext_sp_inventory WHERE id = ?", (stack["id"],))
        else:
            db.execute("UPDATE ext_sp_inventory SET quantity = ? WHERE id = ?",
                       (new_qty, stack["id"]))

        # Log removal to trash database
        try:
            log_item_removal(
                persona_uuid=owner_uuid,
                item_id=item_id,
                quantity=take,
                reason="discarded",
                container=stack.get("container", "unknown"),
                container_id=stack["id"],
            )
        except Exception:
            pass  # Trash logging is non-critical

        removed += take

    return removed


def count_item(owner_uuid: str, item_id: str, owner_type: str = "persona",
               include_equipped: bool = True) -> float:
    """Count total quantity of an item in inventory."""
    db = get_db()
    extra = "" if include_equipped else " AND is_equipped = 0"
    row = db.fetch_one(f"""
        SELECT COALESCE(SUM(quantity), 0) as total
        FROM ext_sp_inventory
        WHERE owner_uuid = ? AND owner_type = ? AND item_id = ?{extra}
    """, (owner_uuid, owner_type, item_id))
    return row["total"] if row else 0


def get_inventory(owner_uuid: str, owner_type: str = "persona",
                  container: Optional[str] = None) -> list[dict]:
    """Get all inventory items for an owner, optionally filtered by container."""
    db = get_db()
    if container:
        return db.fetch_all("""
            SELECT i.*, d.name, d.category, d.weight_kg, d.calories_per_unit,
                   d.hydration_per_unit, d.decay_per_day, d.perishable,
                   d.armor_value, d.insulation_value, d.tool_power, d.tool_type,
                   d.equip_slot, d.crafting_tags, d.description
            FROM ext_sp_inventory i
            LEFT JOIN ext_sp_item_defs d ON i.item_id = d.item_id
            WHERE i.owner_uuid = ? AND i.owner_type = ? AND i.container = ?
            ORDER BY i.is_equipped DESC, i.slot_index, i.id
        """, (owner_uuid, owner_type, container))
    return db.fetch_all("""
        SELECT i.*, d.name, d.category, d.weight_kg, d.calories_per_unit,
               d.hydration_per_unit, d.decay_per_day, d.perishable,
               d.armor_value, d.insulation_value, d.tool_power, d.tool_type,
               d.equip_slot, d.crafting_tags, d.description
        FROM ext_sp_inventory i
        LEFT JOIN ext_sp_item_defs d ON i.item_id = d.item_id
        WHERE i.owner_uuid = ? AND i.owner_type = ?
        ORDER BY i.container, i.is_equipped DESC, i.slot_index, i.id
    """, (owner_uuid, owner_type))


def get_container_weight(owner_uuid: str, container: str,
                         owner_type: str = "persona") -> float:
    """Calculate total weight in a container."""
    items = get_inventory(owner_uuid, owner_type, container)
    total = 0
    for it in items:
        w = it.get("weight_kg", 0.1) or 0.1
        total += w * it["quantity"]
    return round(total, 2)


def get_total_weight(owner_uuid: str, owner_type: str = "persona") -> float:
    """Get total carried weight across all containers."""
    items = get_inventory(owner_uuid, owner_type)
    total = 0
    for it in items:
        # Equipped items' weight is considered carried on the body
        if it["is_equipped"]:
            continue
        w = it.get("weight_kg", 0.1) or 0.1
        total += w * it["quantity"]
    return round(total, 2)


# ══════════════════════════════════════════════════════════════════════
# Equipment system
# ══════════════════════════════════════════════════════════════════════

def _auto_equip(db, owner_uuid: str, item_id: str):
    """Equip an item to the appropriate slot, unequipping any conflict."""
    defn = get_item_def(item_id)
    if not defn:
        return
    slot = defn.get("equip_slot")
    if not slot:
        return

    # Unequip anything in that slot
    db.execute("""
        UPDATE ext_sp_inventory SET is_equipped = 0
        WHERE owner_uuid = ? AND owner_type = 'persona' AND
              item_id IN (
                  SELECT item_id FROM ext_sp_item_defs
                  WHERE equip_slot = ?
              ) AND is_equipped = 1
    """, (owner_uuid, slot))

    # Equip this item
    db.execute("""
        UPDATE ext_sp_inventory SET is_equipped = 1, slot_index = 1
        WHERE owner_uuid = ? AND owner_type = 'persona' AND item_id = ?
        LIMIT 1
    """, (owner_uuid, item_id))


def equip_item(inventory_id: int) -> bool:
    """Equip an inventory item to its appropriate slot."""
    db = get_db()
    item = db.fetch_one(
        "SELECT * FROM ext_sp_inventory WHERE id = ?", (inventory_id,))
    if not item:
        return False

    defn = get_item_def(item["item_id"])
    if not defn or not defn.get("equip_slot"):
        return False

    slot = defn["equip_slot"]
    # Unequip any existing item in that slot
    db.execute("""
        UPDATE ext_sp_inventory SET is_equipped = 0
        WHERE owner_uuid = ? AND owner_type = ? AND is_equipped = 1
          AND item_id IN (
              SELECT item_id FROM ext_sp_item_defs WHERE equip_slot = ?
          )
    """, (item["owner_uuid"], item["owner_type"], slot))

    db.execute("""
        UPDATE ext_sp_inventory SET is_equipped = 1, slot_index = 1
        WHERE id = ?
    """, (inventory_id,))
    return True


def unequip_item(owner_uuid: str, slot: str) -> bool:
    """Unequip whatever is in the given equipment slot."""
    db = get_db()
    db.execute("""
        UPDATE ext_sp_inventory SET is_equipped = 0
        WHERE owner_uuid = ? AND owner_type = 'persona'
          AND is_equipped = 1
          AND item_id IN (
              SELECT item_id FROM ext_sp_item_defs WHERE equip_slot = ?
          )
    """, (owner_uuid, slot))
    return True


def get_equipped_stats(owner_uuid: str) -> dict:
    """Aggregate stats from all equipped items.

    Returns:
        dict with total_armor, total_insulation, best_tool_power (by type),
        carry_capacity_bonus.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"get_equipped_stats failed: {e}")
        return {}
    equipped = db.fetch_all("""
        SELECT i.*, d.armor_value, d.insulation_value, d.tool_power,
               d.tool_type, d.equip_slot, d.weight_kg
        FROM ext_sp_inventory i
        LEFT JOIN ext_sp_item_defs d ON i.item_id = d.item_id
        WHERE i.owner_uuid = ? AND i.owner_type = 'persona' AND i.is_equipped = 1
    """, (owner_uuid,))

    stats = {
        "total_armor": 0.0,
        "total_insulation": 0.0,
        "tools": {},  # tool_type -> best_power
        "equipped_weight": 0.0,
        "slots_filled": [],
        "has_weapon": False,
        "has_water_container": False,
    }

    for item in equipped:
        stats["total_armor"] += item.get("armor_value", 0) or 0
        stats["total_insulation"] += item.get("insulation_value", 0) or 0
        stats["equipped_weight"] += (item.get("weight_kg", 0) or 0) * item["quantity"]
        if item["equip_slot"]:
            stats["slots_filled"].append(item["equip_slot"])

        tt = item.get("tool_type")
        tp = item.get("tool_power", 0) or 0
        if tt and tp > 0:
            current = stats["tools"].get(tt, 0)
            if tp > current:
                stats["tools"][tt] = tp

        cat = item.get("category", "")
        if cat in ("weapon",):
            stats["has_weapon"] = True
        if item["item_id"] == "waterskin":
            stats["has_water_container"] = True

    return stats


# ══════════════════════════════════════════════════════════════════════
# Consumption (eat, drink, apply medicine)
# ══════════════════════════════════════════════════════════════════════

def consume_item(persona_uuid: str, inventory_id: int,
                 quantity: float = 1.0) -> dict:
    """Consume a food/drink/medicine item from inventory.

    Applies nutrition, hydration, toxicity effects to the persona.

    Returns dict with calories, hydration, toxicity applied.
    """
    db = get_db()
    item = db.fetch_one("""
        SELECT i.*, d.calories_per_unit, d.hydration_per_unit, d.toxicity,
               d.perishable, d.name, d.category
        FROM ext_sp_inventory i
        LEFT JOIN ext_sp_item_defs d ON i.item_id = d.item_id
        WHERE i.id = ?
    """, (inventory_id,))

    if not item or item["quantity"] < quantity:
        return {"calories": 0, "hydration": 0, "toxicity": 0, "error": "not_enough"}

    # Calculate effects
    calories = (item["calories_per_unit"] or 0) * quantity
    hydration = (item["hydration_per_unit"] or 0) * quantity
    toxicity = (item["toxicity"] or 0) * quantity

    # Apply via health system
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
    if calories > 0:
        modify_health(persona_uuid, food=min(100, calories * 0.3))
    if hydration > 0:
        modify_health(persona_uuid, hydration=min(100, hydration * 50))
    if toxicity > 0:
        modify_health(persona_uuid, immune=-toxicity * 2,
                      energy=-toxicity * 0.5)

    # Remove consumed quantity
    new_qty = item["quantity"] - quantity
    if new_qty <= 0:
        db.execute("DELETE FROM ext_sp_inventory WHERE id = ?", (inventory_id,))
    else:
        db.execute("UPDATE ext_sp_inventory SET quantity = ? WHERE id = ?",
                   (new_qty, inventory_id))

    # Log consumption to trash database
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal
        log_item_removal(
            persona_uuid=persona_uuid,
            item_id=item["item_id"],
            quantity=quantity,
            reason="consumed",
            container=item.get("container", "inventory"),
            container_id=inventory_id,
            details={
                "name": item.get("name", "?"),
                "calories": round(calories, 1),
                "hydration": round(hydration, 1),
                "toxicity": round(toxicity, 1),
            },
        )
    except Exception:
        pass  # Trash logging is non-critical

    # Record memory
    add_memory(persona_uuid, "consumed", item_id=item["item_id"],
               detail={"name": item.get("name", "?"),
                       "calories": round(calories, 1),
                       "hydration": round(hydration, 1)},
               emotional_weight=2)

    return {
        "calories": round(calories, 1),
        "hydration": round(hydration, 1),
        "toxicity": round(toxicity, 1),
        "name": item.get("name", "?"),
    }


def auto_consume(persona_uuid: str) -> dict:
    """Persona automatically consumes food/drink if available and hungry.

    Called during the health tick. Scans inventory for edible items and
    consumes the most suitable one.

    Returns dict of what was consumed.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"auto_consume failed: {e}")
        return {}
    health = db.fetch_one(
        "SELECT food, hydration FROM ext_sp_health WHERE persona_uuid = ?",
        (persona_uuid,))
    if not health:
        return {"consumed": False}

    result = {"consumed": False, "food": 0, "drink": 0}

    # Priority: consume food if hungry (< 60)
    if health["food"] < 60:
        edibles = db.fetch_all("""
            SELECT i.id, i.item_id, i.quantity, i.condition_val,
                   d.calories_per_unit, d.hydration_per_unit, d.toxicity,
                   d.name, d.category
            FROM ext_sp_inventory i
            JOIN ext_sp_item_defs d ON i.item_id = d.item_id
            WHERE i.owner_uuid = ? AND i.owner_type = 'persona'
              AND i.is_equipped = 0
              AND (d.calories_per_unit > 0 OR d.hydration_per_unit > 0)
              AND i.condition_val > 0.3
            ORDER BY d.calories_per_unit DESC, d.toxicity ASC
            LIMIT 5
        """, (persona_uuid,))

        for item in edibles:
            if health["food"] >= 60 and health["hydration"] >= 60:
                break
            qty = min(1.0, item["quantity"])
            cons = consume_item(persona_uuid, item["id"], qty)
            if cons.get("calories", 0) > 0:
                result["food"] += cons["calories"]
                result["consumed"] = True
            if cons.get("hydration", 0) > 0:
                result["drink"] += cons["hydration"]
                result["consumed"] = True

    return result


# ══════════════════════════════════════════════════════════════════════
# Inventory decay (perishable items rot)
# ══════════════════════════════════════════════════════════════════════

def process_inventory_decay() -> dict:
    """Process daily decay of perishable items in all inventories.

    Perishable food/medicine rots over time based on temperature.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"process_inventory_de failed: {e}")
        return {}
    cfg = get_config()
    if not cfg.get("ecosystem_enabled", True):
        return {"status": "disabled"}

    perishable = db.fetch_all("""
        SELECT i.*, d.decay_per_day, d.name, d.perishable
        FROM ext_sp_inventory i
        JOIN ext_sp_item_defs d ON i.item_id = d.item_id
        WHERE d.perishable = 1 AND i.quantity > 0
    """)

    decayed_count = 0
    destroyed_count = 0

    for item in perishable:
        decay = item.get("decay_per_day", 0) or 0
        if decay <= 0:
            continue

        # Temperature effect (Q10 approximation)
        # For simplicity, use base decay
        loss = decay * item["quantity"]
        new_qty = max(0, item["quantity"] - loss)

        if new_qty <= 0:
            db.execute("DELETE FROM ext_sp_inventory WHERE id = ?", (item["id"],))
            destroyed_count += 1
        else:
            db.execute("UPDATE ext_sp_inventory SET quantity = ?, condition_val = MAX(0, condition_val - ?) WHERE id = ?",
                       (new_qty, decay * 0.1, item["id"]))
            decayed_count += 1

    return {"decayed": decayed_count, "destroyed": destroyed_count}


# ══════════════════════════════════════════════════════════════════════
# Calorie burn for actions
# ══════════════════════════════════════════════════════════════════════

ACTION_CALORIES = {
    "idle": 1.0,
    "walk": 3.0,
    "run": 6.0,
    "forage": 2.5,
    "hunt": 5.0,
    "fish": 2.0,
    "mine": 8.0,
    "chop_wood": 7.0,
    "craft": 2.0,
    "build": 6.0,
    "fight": 9.0,
    "rest": -2.0,  # Recovers
    "sleep": -5.0,
}


def burn_calories(persona_uuid: str, action: str) -> dict:
    """Apply calorie/metabolic cost of an action to the persona.

    Reduces food and hydration based on action intensity.
    Returns dict of what was burned.
    """
    base = ACTION_CALORIES.get(action, 2.0)

    # Carry weight penalty (more weight = more burn)
    total_weight = get_total_weight(persona_uuid)
    weight_penalty = max(0, (total_weight - 15) * 0.05)

    burn = base + weight_penalty

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
    food_loss = burn * 1.5
    hydration_loss = burn * 0.5

    modify_health(persona_uuid, food=-food_loss, hydration=-hydration_loss,
                  energy=-burn)

    return {
        "action": action,
        "calories_burned": round(burn * 1.5, 1),
        "hydration_burned": round(hydration_loss, 1),
    }

