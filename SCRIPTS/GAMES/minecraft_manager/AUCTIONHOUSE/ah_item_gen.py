"""
ah_item_gen.py — Rare item generation & enchantment assignment.

Handles:
  - Random quality rolls matching sign_item.py's rarity system
  - Enchantment assignment by rarity tier
  - Flavor lore generation
  - Ultra-rare item generation (elytra with very low probability)

Rarity system (from sign_item.py):
  - Garbage:    45%    — no enchants
  - Common:     25%    — 0-1 enchants (max level 2)
  - Uncommon:   15%    — 1-2 enchants (max level 3)
  - Rare:        9%    — 2-3 enchants (max level 4)
  - Epic:        4%    — 3-4 enchants (max level 5)
  - Legendary:   1.5%  — 3-5 enchants (max level 5, can have curses)
  - Mythic:      0.499% — 4-6 enchants (max level 5, can combine incompatible)
  - Cosmic Perfection: 0.001% — 5-7 enchants (all max level, breaks rules)
"""

import random, json
from typing import Optional

from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()
cfg = get_config

# ──────────────────────────────────────────────────────────────────────
# Rarity Tier Definitions
# ──────────────────────────────────────────────────────────────────────

RARITY_TIERS = [
    {"name": "Garbage",           "prob": 45.0,   "color": "§7",  "color_name": "gray",          "quality_range": (1, 100)},
    {"name": "Common",            "prob": 25.0,   "color": "§6",  "color_name": "gold",          "quality_range": (1, 100)},
    {"name": "Uncommon",          "prob": 15.0,   "color": "§a",  "color_name": "green",         "quality_range": (1, 100)},
    {"name": "Rare",              "prob": 9.0,    "color": "§9",  "color_name": "blue",          "quality_range": (1, 100)},
    {"name": "Epic",              "prob": 4.0,    "color": "§d",  "color_name": "light_purple",   "quality_range": (1, 100)},
    {"name": "Legendary",         "prob": 1.5,    "color": "§c",  "color_name": "red",           "quality_range": (1, 100)},
    {"name": "Mythic",            "prob": 0.499,  "color": "§5",  "color_name": "dark_purple",   "quality_range": (1, 100)},
    {"name": "Cosmic Perfection", "prob": 0.001,  "color": None,  "color_name": "rainbow",       "quality_range": (100, 100)},
]

_RANDOM_COLORS = ["§c", "§6", "§e", "§b", "§a", "§d"]

# Cumulative thresholds for rarity roll
_cum = 0.0
_RARITY_THRESHOLDS = []
for tier in RARITY_TIERS:
    _cum += tier["prob"]
    _RARITY_THRESHOLDS.append(_cum)

TOTAL_QUALITY_WEIGHT = sum(range(1, 101))

# ──────────────────────────────────────────────────────────────────────
# Enchantment Pools
# ──────────────────────────────────────────────────────────────────────

# General equipment enchants
_ENCHANTS_EQUIPMENT = [
    "minecraft:protection", "minecraft:fire_protection",
    "minecraft:blast_protection", "minecraft:projectile_protection",
    "minecraft:feather_falling", "minecraft:thorns",
    "minecraft:respiration", "minecraft:aqua_affinity",
    "minecraft:depth_strider", "minecraft:swift_sneak",
    "minecraft:soul_speed", "minecraft:unbreaking",
    "minecraft:mending",
]

# Weapon enchants
_ENCHANTS_WEAPON = [
    "minecraft:sharpness", "minecraft:smite", "minecraft:bane_of_arthropods",
    "minecraft:fire_aspect", "minecraft:looting", "minecraft:sweeping_edge",
    "minecraft:knockback",
]

# Bow enchants
_ENCHANTS_BOW = [
    "minecraft:power", "minecraft:punch", "minecraft:flame",
    "minecraft:infinity",
]

# Tool enchants
_ENCHANTS_TOOL = [
    "minecraft:efficiency", "minecraft:fortune", "minecraft:silk_touch",
]

# Fishing rod enchants
_ENCHANTS_FISHING = [
    "minecraft:luck_of_the_sea", "minecraft:lure",
]

# Curses
_ENCHANTS_CURSES = [
    "minecraft:curse_of_binding", "minecraft:curse_of_vanishing",
]

# — Incompatible groups (MC rules) —
_INCOMPATIBLE_GROUPS = [
    {"minecraft:protection", "minecraft:fire_protection",
     "minecraft:blast_protection", "minecraft:projectile_protection"},
    {"minecraft:sharpness", "minecraft:smite", "minecraft:bane_of_arthropods"},
    {"minecraft:fortune", "minecraft:silk_touch"},
    {"minecraft:infinity", "minecraft:mending"},
]


def _get_enchant_pool(item_id: str) -> list[str]:
    """Determine which enchant pool applies to an item."""
    item_lower = item_id.lower()
    if "sword" in item_lower or "axe" in item_lower:
        return _ENCHANTS_WEAPON + _ENCHANTS_EQUIPMENT
    elif "bow" in item_lower or "crossbow" in item_lower:
        return _ENCHANTS_BOW + _ENCHANTS_EQUIPMENT
    elif "pickaxe" in item_lower or "shovel" in item_lower or "hoe" in item_lower:
        return _ENCHANTS_TOOL + _ENCHANTS_EQUIPMENT
    elif "helmet" in item_lower or "chestplate" in item_lower or "leggings" in item_lower or "boots" in item_lower:
        return _ENCHANTS_EQUIPMENT
    elif "fishing_rod" in item_lower:
        return _ENCHANTS_FISHING + _ENCHANTS_EQUIPMENT
    elif "trident" in item_lower:
        return ["minecraft:impaling", "minecraft:loyalty", "minecraft:channeling",
                "minecraft:riptide", "minecraft:unbreaking", "minecraft:mending"]
    elif "elytra" in item_lower:
        return ["minecraft:unbreaking", "minecraft:mending"]
    else:
        return _ENCHANTS_EQUIPMENT


def _is_compatible(ench_list: list[str], new_ench: str, break_rules: bool = False) -> bool:
    """Check if an enchantment is compatible with the existing list.

    Args:
        ench_list: Current enchantments
        new_ench: The enchantment being added
        break_rules: If True (Mythic/Cosmic Perfection), ignore incompatibility

    Returns:
        True if compatible
    """
    if break_rules:
        return True
    for group in _INCOMPATIBLE_GROUPS:
        if new_ench in group:
            for existing in ench_list:
                if existing in group:
                    return False
    return True


# ──────────────────────────────────────────────────────────────────────
# Quality Roll
# ──────────────────────────────────────────────────────────────────────

def roll_quality() -> int:
    """Generate a quality roll from 1-100 using weighted random.

    Uses the same method as sign_item.py: sum of 1..n weights.
    Higher qualities are harder to achieve.
    """
    r = random.random() * TOTAL_QUALITY_WEIGHT
    cum = 0
    for n in range(1, 101):
        cum += n
        if r <= cum:
            return n
    return 100


# ──────────────────────────────────────────────────────────────────────
# Rarity Roll
# ──────────────────────────────────────────────────────────────────────

def roll_rarity(bias_up: bool = False) -> dict:
    """Generate a rarity tier based on the weighted random system.

    Args:
        bias_up: If True, shift one tier up (for AI-generated items)

    Returns:
        Dict with name, color, color_name, quality
    """
    r = random.random() * 100.0
    chosen = RARITY_TIERS[-1]
    for i, t in enumerate(_RARITY_THRESHOLDS):
        if r <= t:
            chosen = RARITY_TIERS[i]
            break

    # Bias up if requested (shift one tier up)
    if bias_up and chosen["name"] != "Cosmic Perfection":
        idx = next(i for i, t in enumerate(RARITY_TIERS) if t["name"] == chosen["name"])
        if idx < len(RARITY_TIERS) - 1:
            chosen = RARITY_TIERS[idx + 1]

    quality = roll_quality()
    if chosen["name"] == "Cosmic Perfection":
        quality = 100

    return {
        "name": chosen["name"],
        "color": chosen["color"],
        "color_name": chosen["color_name"],
        "quality": quality,
    }


# ──────────────────────────────────────────────────────────────────────
# Enchantment Generation
# ──────────────────────────────────────────────────────────────────────

def _max_enchant_level(ench_id: str, rarity_name: str) -> int:
    """Return the max allowed level for an enchantment at this rarity."""
    max_levels = {
        "Garbage": 0,
        "Common": 2,
        "Uncommon": 3,
        "Rare": 4,
        "Epic": 5,
        "Legendary": 5,
        "Mythic": 5,
        "Cosmic Perfection": 7,  # can go above normal max
    }
    base_max = max_levels.get(rarity_name, 3)

    # Enchant-specific max (some enchants don't go above a certain level)
    specific_max = {
        "minecraft:fire_protection": 4,
        "minecraft:blast_protection": 4,
        "minecraft:projectile_protection": 4,
        "minecraft:feather_falling": 4,
        "minecraft:thorns": 3,
        "minecraft:respiration": 3,
        "minecraft:aqua_affinity": 1,
        "minecraft:depth_strider": 3,
        "minecraft:swift_sneak": 3,
        "minecraft:soul_speed": 3,
        "minecraft:fire_aspect": 2,
        "minecraft:looting": 3,
        "minecraft:sweeping_edge": 3,
        "minecraft:knockback": 2,
        "minecraft:punch": 2,
        "minecraft:flame": 1,
        "minecraft:infinity": 1,
        "minecraft:lure": 3,
        "minecraft:luck_of_the_sea": 3,
        "minecraft:loyalty": 3,
        "minecraft:channeling": 1,
        "minecraft:riptide": 3,
        "minecraft:impaling": 5,
        "minecraft:mending": 1,
        "minecraft:unbreaking": 3,
    }
    cap = specific_max.get(ench_id, 5)
    return min(base_max, cap) if rarity_name != "Cosmic Perfection" else max(base_max, cap)


def generate_enchantments(item_id: str, rarity: dict) -> list[dict]:
    """Generate appropriate enchantments for an item based on its rarity.

    Args:
        item_id: e.g. "minecraft:diamond_sword"
        rarity: Dict from roll_rarity()

    Returns:
        List of {"id": "minecraft:sharpness", "level": 5}
    """
    rarity_name = rarity["name"]
    if rarity_name == "Garbage":
        return []

    break_rules = rarity_name in ("Mythic", "Cosmic Perfection")
    pool = _get_enchant_pool(item_id)
    if not pool:
        return []

    random.shuffle(pool)

    # How many enchants to add
    ench_counts = {
        "Garbage": (0, 0),
        "Common": (0, 1),
        "Uncommon": (1, 2),
        "Rare": (2, 3),
        "Epic": (3, 4),
        "Legendary": (3, 5),
        "Mythic": (4, 6),
        "Cosmic Perfection": (5, 7),
    }
    min_e, max_e = ench_counts.get(rarity_name, (0, 3))
    count = random.randint(min_e, max_e)

    # Include curses? Only for Legendary+
    include_curses = rarity_name in ("Legendary", "Mythic", "Cosmic Perfection") and random.random() < 0.3

    enchants = []
    for ench_id in pool:
        if len(enchants) >= count:
            break
        if _is_compatible([e["id"] for e in enchants], ench_id, break_rules):
            level = _max_enchant_level(ench_id, rarity_name)
            if level > 0:
                enchants.append({"id": ench_id, "level": level})

    # Maybe add a curse
    if include_curses:
        curse = random.choice(_ENCHANTS_CURSES)
        if not any(e["id"] == curse for e in enchants):
            enchants.append({"id": curse, "level": 1})

    return enchants[:max_e]


# ──────────────────────────────────────────────────────────────────────
# Lore Generation
# ──────────────────────────────────────────────────────────────────────

_LORE_TEMPLATES = {
    "weapon": [
        "Forged in the heart of a dying star, this {item} remembers the heat of creation.",
        "The ancient dwarves carved this {item} from bedrock itself. It has never known failure.",
        "Wrapped in dragon breath and starlight, this {item} seeks only noble targets.",
        "Tempered in the fires of a thousand battles, this {item} thirsts for more.",
        "An old warrior's final gift — this {item} has seen empires rise and fall.",
        "Crackling with residual void energy, this {item} hums with otherworldly power.",
        "The runes along this {item}'s edge glow faintly, recounting ancient victories.",
        "This {item} was pulled from the corpse of a forgotten deity. It still remembers.",
    ],
    "armor": [
        "Woven from the silk of giant cave spiders, this {item} is surprisingly lightweight.",
        "Plated with scales from the Ender Dragon herself, this {item} radiates authority.",
        "The whispers of past wearers echo within this {item}, offering guidance in battle.",
        "Enchanted by a reclusive mage who lived atop the highest mountain for a century.",
        "This {item} was crafted from the remains of a fallen star. It feels impossibly light.",
    ],
    "tool": [
        "This {item} was used to carve the first blocks of spawn. It carries the memory of creation.",
        "The grip is worn smooth from generations of use. This {item} knows its craft.",
        "Infused with the essence of the earth itself, this {item} draws minerals toward it.",
        "An ancient builder's prized possession. This {item} has shaped monuments.",
    ],
    "general": [
        "Pulsing with a soft, steady glow, this {item} feels heavier than it looks.",
        "The sigils on this {item} shift and rearrange when no one is watching.",
        "This {item} was blessed by the Village Elders of a civilization long lost.",
        "Faint whispers emanate from this {item}, speaking of great deeds yet to come.",
        "This {item} has a will of its own. It chose you.",
    ],
    "elytra": [
        "The last vestige of an ancient dragon that circled the End for millennia.",
        "Its leather remembers the void between stars. It chose you.",
        "Stitched from the membrane of a creature that has never touched the ground.",
        "This elytra once belonged to the first explorer to map the outer End islands.",
    ],
}

_ITEM_NAMES_CACHE = {}


def _friendly_name(item_id: str) -> str:
    """Convert a Minecraft item ID to a friendly name."""
    if item_id in _ITEM_NAMES_CACHE:
        return _ITEM_NAMES_CACHE[item_id]
    if ":" in item_id:
        short = item_id.split(":")[1]
    else:
        short = item_id
    name = " ".join(w.replace("_", " ").capitalize() for w in short.split("_"))
    _ITEM_NAMES_CACHE[item_id] = name
    return name


def generate_lore(item_id: str, rarity: dict, item_name: str) -> list[str]:
    """Generate 1-3 lines of flavorful lore for an item.

    Args:
        item_id: Minecraft item ID
        rarity: Dict from roll_rarity()
        item_name: Human-readable item name

    Returns:
        List of lore strings (may contain MC color codes)
    """
    rarity_name = rarity["name"]
    color = rarity["color"] or "§d"
    friendly = _friendly_name(item_id)

    # Pick template pool
    if "elytra" in item_id:
        pool = _LORE_TEMPLATES["elytra"]
    elif "sword" in item_id or "axe" in item_id or "bow" in item_id or "trident" in item_id:
        pool = _LORE_TEMPLATES["weapon"]
    elif "helmet" in item_id or "chestplate" in item_id or "leggings" in item_id or "boots" in item_id:
        pool = _LORE_TEMPLATES["armor"]
    elif "pickaxe" in item_id or "shovel" in item_id or "hoe" in item_id or "fishing" in item_id:
        pool = _LORE_TEMPLATES["tool"]
    else:
        pool = _LORE_TEMPLATES["general"]

    template = random.choice(pool)
    lore_text = template.replace("{item}", friendly)

    lines = [f"{color}{lore_text}"]

    # For higher rarities, add a second line
    if rarity_name in ("Epic", "Legendary", "Mythic", "Cosmic Perfection"):
        second = random.choice([
            f"§7Quality Rating: {rarity['quality']}/100",
            f"§7\"{_generate_quote()}\"",
            f"§7Forged in the {random.choice(['Nether', 'End', 'Overworld', 'Void'])}",
        ])
        lines.append(f"{color}{second}")

    # Cosmic Perfection gets a third line
    if rarity_name == "Cosmic Perfection":
        lines.append(f"§6✦ This item transcends mortal limitations ✦")

    return lines


_QUOTES = [
    "Fortune favors the bold... and the well-armored.",
    "The mountain is not as far as it seems.",
    "Even the smallest pickaxe can carve the deepest cavern.",
    "Beware the night, but embrace its treasures.",
    "The best enchantment is a steady hand.",
    "Every block tells a story.",
    "Diamonds are forever, but Emeralds are currency.",
    "The End is just another beginning.",
    "In the deep dark, the ancient cities whisper.",
    "A true adventurer fears neither dragon nor creeper.",
]


def _generate_quote() -> str:
    return random.choice(_QUOTES)


# ──────────────────────────────────────────────────────────────────────
# Main generation entry point
# ──────────────────────────────────────────────────────────────────────

def generate_simulated_item(item_id: str,
                             rarity_override: Optional[dict] = None,
                             bias_up: bool = True) -> dict:
    """Generate a fully-formed simulated item ready to be listed.

    Args:
        item_id: Minecraft item ID
        rarity_override: If provided, use this rarity instead of rolling
        bias_up: If True, bias rarity one tier up (AI items are special)

    Returns:
        Dict with all fields needed for ``ah_core.list_item()`` with
        ``is_simulated=True``
    """
    if rarity_override:
        rarity = rarity_override
    else:
        rarity = roll_rarity(bias_up=bias_up)

    friendly = _friendly_name(item_id)

    # Generate display name
    if rarity["name"] == "Cosmic Perfection":
        display_name = f"✦ {friendly} of Transcendence ✦"
    elif rarity["name"] == "Mythic":
        display_name = f"{friendly} of the Ancients"
    elif rarity["name"] == "Legendary":
        display_name = f"Mythical {friendly}"
    elif rarity["name"] == "Epic":
        display_name = f"Enchanted {friendly}"
    elif rarity["name"] == "Rare":
        display_name = f"Fine {friendly}"
    elif rarity["name"] == "Uncommon":
        display_name = f"Polished {friendly}"
    else:
        display_name = friendly

    enchants = generate_enchantments(item_id, rarity)
    lore = generate_lore(item_id, rarity, friendly)

    # Sim durability (for tools/armor/elytra)
    durability = None
    if any(t in item_id for t in ["sword", "pickaxe", "axe", "shovel", "hoe",
                                    "helmet", "chestplate", "leggings", "boots",
                                    "elytra", "trident", "fishing_rod", "bow"]):
        durability = random.randint(50, 100)  # % remaining, scaled later
        # Higher rarity = better durability
        dur_bonus = {"Garbage": 0.3, "Common": 0.5, "Uncommon": 0.6,
                     "Rare": 0.7, "Epic": 0.8, "Legendary": 0.85,
                     "Mythic": 0.9, "Cosmic Perfection": 1.0}
        multiplier = dur_bonus.get(rarity["name"], 0.5)
        durability = int(durability * multiplier)

    # Base price from simulated inventory or calculated
    config = cfg()
    price = _calculate_price(item_id, rarity, enchants, durability)

    rarity_str = f"{rarity['color'] or ''}{rarity['name']} {rarity['quality']}"
    color_name = rarity["color_name"]

    return {
        "item_id": item_id,
        "item_name": display_name,
        "count": 1,
        "rarity": rarity,
        "rarity_str": rarity_str,
        "color_name": color_name,
        "enchantments": enchants,
        "lore": lore,
        "durability": durability,
        "price": price,
        "signed_name": f"{display_name} (Signed)" if rarity["name"] not in ("Garbage", "Common") else display_name,
        "quality": rarity["quality"],
    }


def _calculate_price(item_id: str, rarity: dict,
                     enchants: list[dict], durability: Optional[int]) -> float:
    """Calculate an appropriate price for a simulated item.

    Uses the simulated_inventory base price as reference, then applies
    rarity and enchantment multipliers.

    Args:
        item_id: Minecraft item ID
        rarity: Rarity dict
        enchants: List of enchantments
        durability: Remaining durability (0-100 scale)

    Returns:
        Price in emeralds
    """
    from AUCTIONHOUSE.ah_database import get_db as _get_db
    db = _get_db()
    sim = db.fetch_one(
        "SELECT base_price FROM simulated_inventory WHERE item_id = ?",
        (item_id,)
    )
    base_price = sim["base_price"] if sim else 1.0

    # Rarity multiplier
    rarity_multipliers = {
        "Garbage": 0.3,
        "Common": 0.5,
        "Uncommon": 1.0,
        "Rare": 2.0,
        "Epic": 5.0,
        "Legendary": 15.0,
        "Mythic": 50.0,
        "Cosmic Perfection": 200.0,
    }
    mult = rarity_multipliers.get(rarity["name"], 1.0)

    # Enchantment value
    ench_bonus = sum((e["level"] ** 1.5) for e in enchants) * 0.5

    # Durability penalty
    dur_factor = (durability or 100) / 100.0

    price = (base_price * mult) + ench_bonus
    price *= dur_factor

    # Clamp
    price = max(0.1, min(price, 5000.0))
    return round(price, 2)


# ──────────────────────────────────────────────────────────────────────
# Ultra-rare item presets
# ──────────────────────────────────────────────────────────────────────

_ULTRA_RARE_ITEMS = [
    "minecraft:elytra",
    "minecraft:netherite_sword",
    "minecraft:netherite_pickaxe",
    "minecraft:netherite_axe",
    "minecraft:netherite_chestplate",
    "minecraft:netherite_helmet",
    "minecraft:netherite_leggings",
    "minecraft:netherite_boots",
    "minecraft:trident",
    "minecraft:enchanted_golden_apple",
    "minecraft:dragon_egg",
    "minecraft:heavy_core",
]

_RARE_ITEMS = [
    "minecraft:diamond_sword",
    "minecraft:diamond_pickaxe",
    "minecraft:diamond_axe",
    "minecraft:diamond_chestplate",
    "minecraft:diamond_helmet",
    "minecraft:diamond_leggings",
    "minecraft:diamond_boots",
    "minecraft:bow",
    "minecraft:crossbow",
    "minecraft:fishing_rod",
]

_UNCOMMON_ITEMS = [
    "minecraft:iron_sword",
    "minecraft:iron_pickaxe",
    "minecraft:iron_axe",
    "minecraft:iron_chestplate",
    "minecraft:iron_helmet",
    "minecraft:iron_leggings",
    "minecraft:iron_boots",
    "minecraft:diamond",
    "minecraft:emerald_block",
    "minecraft:book",
]


def should_generate_rare(ultra_rare_override: Optional[float] = None) -> str:
    """Determine what kind of rare item to generate, if any.

    Uses configured probabilities. Returns:
      - 'ultra_rare' — elytra, netherite gear (very rare)
      - 'rare'       — enchanted diamond gear
      - 'uncommon'   — good iron gear, enchanted books
      - 'none'       — generate nothing this cycle

    The AI can override this with its own decision, but the system will
    also apply these probabilities when the AI doesn't specify.
    """
    config = cfg()
    r = random.random()

    if r < config.rare_item_ultra_rare_chance:  # 1%
        return "ultra_rare"
    elif r < config.rare_item_ultra_rare_chance + config.rare_item_rare_chance:  # 6%
        return "rare"
    elif r < config.rare_item_ultra_rare_chance + config.rare_item_rare_chance + config.rare_item_uncommon_chance:  # 21%
        return "uncommon"

    return "none"


def pick_item_for_rarity(rarity: str) -> str:
    """Pick a random item ID for the given rarity category."""
    if rarity == "ultra_rare":
        return random.choice(_ULTRA_RARE_ITEMS)
    elif rarity == "rare":
        return random.choice(_RARE_ITEMS)
    elif rarity == "uncommon":
        return random.choice(_UNCOMMON_ITEMS)
    # Fallback to common sim inventory
    return random.choice([
        "minecraft:coal", "minecraft:iron_ingot", "minecraft:gold_ingot",
        "minecraft:diamond", "minecraft:emerald", "minecraft:redstone"
    ])
