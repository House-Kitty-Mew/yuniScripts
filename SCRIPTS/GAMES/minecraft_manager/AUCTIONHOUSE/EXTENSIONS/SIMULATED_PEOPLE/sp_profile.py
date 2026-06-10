"""
sp_profile.py — Persona profile generation.

Generates random simulated personas with:
  - Name, archetype, job, region
  - Personality traits (JSON dict of scores)
  - Wealth tier and initial financial state
  - Starter needs and memory

Each persona is fully independent with its own UUID.
"""

import random, uuid, json
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    ensure_schema, get_db, add_need, get_persona_count
)

log = get_logger()

# ── Archetype Definitions ────────────────────────────────────────────

ARCHETYPES = {
    "adventurer": {
        "name": "Adventurer",
        "traits": {"bravery": 8, "curiosity": 9, "patience": 3, "thrift": 3, "courage": 8},
        "spending_impulse": 0.7,  # 0-1, higher = more likely to buy
        "savings_rate": 0.15,
        "price_sensitivity": 0.4,  # 0-1, higher = more picky about price
        "item_preferences": ["weapon", "armor", "food", "torch"],
    },
    "merchant": {
        "name": "Merchant",
        "traits": {"shrewdness": 9, "patience": 7, "generosity": 3, "thrift": 9, "courage": 3},
        "spending_impulse": 0.3,
        "savings_rate": 0.5,
        "price_sensitivity": 0.9,
        "item_preferences": ["rare", "block", "trade_good"],
    },
    "builder": {
        "name": "Builder",
        "traits": {"creativity": 8, "diligence": 7, "moodiness": 4, "thrift": 5, "courage": 2},
        "spending_impulse": 0.5,
        "savings_rate": 0.3,
        "price_sensitivity": 0.5,
        "item_preferences": ["block", "wood", "stone", "glass", "stairs", "slab"],
    },
    "miner": {
        "name": "Miner",
        "traits": {"endurance": 9, "greed": 6, "simplicity": 3, "thrift": 7, "courage": 6},
        "spending_impulse": 0.4,
        "savings_rate": 0.6,
        "price_sensitivity": 0.6,
        "item_preferences": ["pickaxe", "torch", "food", "ore", "minecart"],
    },
    "farmer": {
        "name": "Farmer",
        "traits": {"patience": 8, "nurturing": 7, "adventure_urge": 2, "thrift": 6, "courage": 2},
        "spending_impulse": 0.3,
        "savings_rate": 0.4,
        "price_sensitivity": 0.7,
        "item_preferences": ["seed", "hoe", "fertilizer", "food", "wool", "leather"],
    },
    "warrior": {
        "name": "Warrior",
        "traits": {"aggression": 7, "honor": 6, "discipline": 8, "thrift": 4, "courage": 9},
        "spending_impulse": 0.6,
        "savings_rate": 0.2,
        "price_sensitivity": 0.3,
        "item_preferences": ["sword", "axe", "armor", "shield", "arrow", "totem"],
    },
    "mage": {
        "name": "Mage",
        "traits": {"wisdom": 9, "eccentricity": 7, "detachment": 5, "thrift": 3, "courage": 5},
        "spending_impulse": 0.5,
        "savings_rate": 0.3,
        "price_sensitivity": 0.5,
        "item_preferences": ["enchant", "book", "potion", "rare", "ender", "lapis"],
    },
    "vagabond": {
        "name": "Vagabond",
        "traits": {"freedom": 9, "impulsiveness": 8, "resourcefulness": 6, "thrift": 1, "courage": 7},
        "spending_impulse": 0.8,
        "savings_rate": 0.05,
        "price_sensitivity": 0.2,
        "item_preferences": ["food", "weapon", "bed", "map", "compass"],
    },
}

# ── Name Generation ──────────────────────────────────────────────────

_FIRST_NAMES = [
    "Aldric", "Brin", "Cassia", "Durin", "Elara", "Finn", "Greta",
    "Hakon", "Iris", "Jorunn", "Kael", "Lyra", "Magnus", "Nyx",
    "Orin", "Pella", "Quill", "Rune", "Saga", "Torvi", "Uma",
    "Vex", "Wren", "Xana", "Yorik", "Zara", "Bram", "Corvin",
    "Dwyn", "Eira", "Falk", "Gunnar", "Hilde", "Ivar", "Jora",
    "Rowan", "Soren", "Thyra", "Astrid", "Bjorn", "Freya", "Sigrid",
    "Alistair", "Brianna", "Cedric", "Dorian", "Eleanor", "Felix",
    "Genevieve", "Harold", "Isadora", "Jasper", "Katherine", "Lionel",
    "Meredith", "Nathaniel", "Ophelia", "Percival", "Quentin",
    "Rosalind", "Sebastian", "Tatiana", "Ulysses", "Valentina",
    "Wilhelmina", "Xavier", "Ysabel", "Zachariah", "Beatrice",
    "Ambrose", "Beatrix", "Caspian", "Desdemona", "Evander",
    "Florence", "Gideon", "Helena", "Ignatius", "Juliana",
]

_LAST_NAMES = [
    "Ironfoot", "Stonehelm", "Goldvein", "Deepdelve", "Brightwood",
    "Farsight", "Swiftwind", "Steelarm", "Redclay", "Greymist",
    "Oakenshield", "Riversong", "Frostbeard", "Moonshadow", "Thornheart",
    "Copperhand", "Spirecrest", "Dustwalker", "Firemane", "Nightwhisper",
    "Stormchaser", "Bronzebeard", "Silverstream", "Ravenholt", "Windrider",
    "Hollowgrove", "Stonemeadow", "Wildflower", "Darkwater", "Thunderclap",
    "Bloodmoon", "Ironbark", "Swiftarrow", "Copperhelm", "Goldenglow",
    "Mistweaver", "Ashvale", "Frostwind", "Shadowmere", "Brightflame",
    "Thunderhoof", "Silvermane", "Copperfield", "Goldenshield", "Ironwand",
    "Stormweaver", "Dawnbreaker", "Nightwhisper", "Brightspear", "Darkthorn",
    "Stonewarden", "Farsight", "Redwood", "Blackthorn", "Whitewillow",
    "Greenfield", "Highland", "Lowland", "Southwind", "Northpeak",
]

# ── Jobs ─────────────────────────────────────────────────────────────

_JOBS_BY_ARCHETYPE = {
    "adventurer": ["explorer", "dungeon_crawler", "treasure_hunter", "cartographer", "ruin_diver"],
    "merchant": ["trader", "shopkeeper", "haggler", "collector", "arbitrageur"],
    "builder": ["architect", "redstoner", "interior_designer", "landscaper", "bridgewright"],
    "miner": ["strip_miner", "caving_expert", "ore_prospector", "deep_delver", "quarry_foreman"],
    "farmer": ["crop_farmer", "livestock_rancher", "beekeeper", "vintner", "mushroom_farmer"],
    "warrior": ["guard", "pvper", "bounty_hunter", "arena_fighter", "sentinel"],
    "mage": ["enchanter", "alchemist", "potion_brewer", "scroll_keeper", "arcane_researcher"],
    "vagabond": ["nomad", "wanderer", "scavenger", "drifter", "freelancer"],
}

# ── Regions ──────────────────────────────────────────────────────────

_REGIONS = ["overworld", "overworld", "overworld", "overworld",
            "nether", "nether",
            "end",
            "deep_dark"]

# ── Initial Needs Generation ─────────────────────────────────────────

_NEED_TEMPLATES = {
    "adventurer": [
        ("minecraft:iron_sword", "Need a reliable weapon for the next expedition"),
        ("minecraft:shield", "My last shield broke against a creeper"),
        ("minecraft:cooked_beef", "Rations are running low"),
        ("minecraft:torch", "Always need more torches for caving"),
        ("minecraft:leather_boots", "My boots wore out from all the walking"),
        ("minecraft:iron_pickaxe", "My pickaxe can't handle the deep stone"),
        ("minecraft:compass", "Keep getting lost on expeditions"),
    ],
    "merchant": [
        ("minecraft:diamond", "Looking to expand my gem collection"),
        ("minecraft:emerald", "Need currency for a big upcoming deal"),
        ("minecraft:ender_pearl", "Heard there's profit in ender pearls"),
        ("minecraft:gold_ingot", "Gold prices are about to rise"),
        ("minecraft:name_tag", "Premium trading goods are in demand"),
    ],
    "builder": [
        ("minecraft:oak_log", "Starting a new build — need lumber"),
        ("minecraft:stone", "Foundation materials for my next project"),
        ("minecraft:glass", "Planning a massive window wall"),
        ("minecraft:smooth_stone", "Need flooring for the town hall"),
        ("minecraft:redstone", "Working on a complex redstone contraption"),
    ],
    "miner": [
        ("minecraft:iron_pickaxe", "My pickaxe broke at Y=-30"),
        ("minecraft:torch", "Going through torches faster than I can craft them"),
        ("minecraft:cooked_porkchop", "Need food for a long mining session"),
        ("minecraft:diamond_pickaxe", "Saving up for a fortune pickaxe"),
        ("minecraft:minecart", "Building a new transport shaft"),
    ],
    "farmer": [
        ("minecraft:wheat_seeds", "Expanding my wheat field"),
        ("minecraft:bone_meal", "Fertilizer for next season's crops"),
        ("minecraft:iron_hoe", "My old hoe finally gave out"),
        ("minecraft:leather", "Need materials for armor — the wolves are getting bold"),
    ],
    "warrior": [
        ("minecraft:diamond_sword", "My current sword isn't cutting it anymore"),
        ("minecraft:bow", "Need a bow for the upcoming tournament"),
        ("minecraft:shield", "A warrior without a shield is a dead warrior"),
        ("minecraft:iron_chestplate", "My armor has more holes than metal"),
        ("minecraft:totem_of_undying", "Preparing for a dangerous bounty"),
    ],
    "mage": [
        ("minecraft:enchanting_table", "Need a proper enchanting setup"),
        ("minecraft:lapis_lazuli", "Running low on essence stones"),
        ("minecraft:book", "Blank books for my research journal"),
        ("minecraft:ender_pearl", "Studying teleportation magic"),
        ("minecraft:blaze_rod", "Brewing stands need blaze rods"),
    ],
    "vagabond": [
        ("minecraft:bread", "Haven't eaten in two days"),
        ("minecraft:iron_sword", "Lost my sword in a bet"),
        ("minecraft:bed", "Sick of sleeping on the ground"),
        ("minecraft:leather_boots", "My boots have more holes than leather"),
    ],
}

# ── Generation ───────────────────────────────────────────────────────

def _pick_name() -> str:
    return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"


def _pick_archetype() -> str:
    """Pick a random archetype with weighted distribution."""
    archetypes = list(ARCHETYPES.keys())
    weights = [1.0] * len(archetypes)
    return random.choices(archetypes, weights=weights, k=1)[0]


def _calculate_wealth_tier(archetype: str) -> str:
    """Assign a wealth tier based on archetype income potential."""
    wealth_probs = {
        "adventurer": {"poor": 0.2, "working": 0.4, "middle": 0.3, "wealthy": 0.08, "elite": 0.02},
        "merchant": {"poor": 0.05, "working": 0.15, "middle": 0.35, "wealthy": 0.35, "elite": 0.1},
        "builder": {"poor": 0.1, "working": 0.3, "middle": 0.4, "wealthy": 0.15, "elite": 0.05},
        "miner": {"poor": 0.05, "working": 0.25, "middle": 0.4, "wealthy": 0.25, "elite": 0.05},
        "farmer": {"poor": 0.3, "working": 0.4, "middle": 0.2, "wealthy": 0.08, "elite": 0.02},
        "warrior": {"poor": 0.15, "working": 0.35, "middle": 0.3, "wealthy": 0.15, "elite": 0.05},
        "mage": {"poor": 0.1, "working": 0.25, "middle": 0.35, "wealthy": 0.25, "elite": 0.05},
        "vagabond": {"poor": 0.5, "working": 0.35, "middle": 0.12, "wealthy": 0.02, "elite": 0.01},
    }
    probs = wealth_probs.get(archetype, wealth_probs["adventurer"])
    return random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]


def _get_initial_balance(wealth_tier: str) -> float:
    """Get starting balance for a wealth tier."""
    ranges = {
        "poor": (5, 30),
        "working": (30, 100),
        "middle": (100, 500),
        "wealthy": (500, 2000),
        "elite": (2000, 10000),
    }
    lo, hi = ranges.get(wealth_tier, (30, 100))
    return round(random.uniform(lo, hi), 2)


def _get_income_per_tick(wealth_tier: str, archetype: str) -> float:
    """Get income per simulation tick for a persona.

    Hourly income.
    """
    # Base by wealth tier
    base_ranges = {
        "poor": (0.5, 2),
        "working": (2, 8),
        "middle": (8, 25),
        "wealthy": (25, 80),
        "elite": (80, 300),
    }
    lo, hi = base_ranges.get(wealth_tier, (2, 8))
    base = random.uniform(lo, hi)

    # Archetype multiplier
    multipliers = {
        "adventurer": 1.0, "merchant": 1.5, "builder": 0.8,
        "miner": 1.2, "farmer": 0.6, "warrior": 0.9,
        "mage": 1.1, "vagabond": 0.4,
    }
    mult = multipliers.get(archetype, 1.0)
    return round(base * mult, 2)


def _get_savings_goal(wealth_tier: str, archetype: str) -> float:
    """Get savings target based on wealth and archetype."""
    mult = ARCHETYPES[archetype]["savings_rate"]
    base = _get_initial_balance(wealth_tier)
    target = base * (1 + mult * random.uniform(0.5, 2.0))
    return round(target, 2)


def _generate_initial_needs(persona_uuid: str, archetype: str):
    """Generate 1-3 initial needs for a new persona."""
    templates = _NEED_TEMPLATES.get(archetype, _NEED_TEMPLATES["vagabond"])
    count = random.randint(1, 3)
    selected = random.sample(templates, min(count, len(templates)))
    for item_id, reason in selected:
        from AUCTIONHOUSE.ah_database import get_db
        # Find a reasonable max price from sim inventory
        db = get_db()
        price_row = db.fetch_one(
            "SELECT base_price FROM simulated_inventory WHERE item_id = ?",
            (item_id,))
        base_price = price_row["base_price"] if price_row else 1.0
        max_price = round(base_price * random.uniform(1.0, 2.5), 2)
        urgency = random.randint(3, 8)
        add_need(persona_uuid, item_id, urgency, max_price, random.randint(1, 3), reason)


def generate_persona(archetype_override: Optional[str] = None,
                     region_override: Optional[str] = None) -> dict:
    """Generate a complete new persona and insert into the database.

    Args:
        archetype_override: Force a specific archetype
        region_override: Force a specific region

    Returns:
        Dict of the generated persona profile
    """
    ensure_schema()
    db = get_db()

    archetype = archetype_override or _pick_archetype()
    region = region_override or random.choice(_REGIONS)
    wealth_tier = _calculate_wealth_tier(archetype)
    name = _pick_name()
    persona_uuid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    traits = dict(ARCHETYPES[archetype]["traits"])
    # Add randomized variance (±2 to each trait)
    for k in traits:
        traits[k] = max(1, min(10, traits[k] + random.randint(-2, 2)))

    job = random.choice(_JOBS_BY_ARCHETYPE[archetype])

    # Insert profile
    db.execute("""
        INSERT INTO ext_sp_profiles
        (persona_uuid, name, archetype, job, region, wealth_tier,
         personality_traits, active, created_at, last_active_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, (persona_uuid, name, archetype, job, region, wealth_tier,
          json.dumps(traits), now, now))

    # Insert finances
    balance = _get_initial_balance(wealth_tier)
    income = _get_income_per_tick(wealth_tier, archetype)
    savings = _get_savings_goal(wealth_tier, archetype)
    db.execute("""
        INSERT INTO ext_sp_finances
        (persona_uuid, balance, lifetime_income, lifetime_spending,
         income_per_tick, savings_goal, debt)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (persona_uuid, balance, balance, 0, income, savings))

    # Generate initial needs
    _generate_initial_needs(persona_uuid, archetype)

    # ── Assign skills ──────────────────────────────────────────────
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import (
        generate_skills, save_skills
    )
    skills = generate_skills(archetype)
    save_skills(persona_uuid, skills)

    # ── Initialize health ───────────────────────────────────────────
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import init_health
    init_health(persona_uuid, wealth_tier, archetype)

    # ── Assign world area ──────────────────────────────────────────
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import (
        assign_persona_area, get_area
    )
    area_uuid = assign_persona_area(persona_uuid, archetype)
    area = get_area(area_uuid) if area_uuid else None
    area_name = area["name"] if area else "unknown"

    log.info("sp_profile",
             f"Generated persona: {name} ({archetype}, {wealth_tier}) — "
             f"balance={balance}em, income={income}/tick, "
             f"skills={json.dumps(skills)}, area={area_name}")

    return {
        "persona_uuid": persona_uuid,
        "name": name,
        "archetype": archetype,
        "job": job,
        "region": region,
        "wealth_tier": wealth_tier,
        "traits": traits,
        "balance": balance,
        "income_per_tick": income,
        "savings_goal": savings,
        "skills": skills,
        "area_uuid": area_uuid,
        "area_name": area_name,
    }

def spawn_initial_population(target_size: int = 200):
    """Generate the initial population of personas.

    Args:
        target_size: How many personas to create

    Returns:
        Count of personas created
    """
    current = get_persona_count()
    needed = target_size - current["total"]
    if needed <= 0:
        log.info("sp_profile", f"Population already at {current['total']} — no new personas needed")
        return 0

    count = 0
    for _ in range(needed):
        generate_persona()
        count += 1

    log.info("sp_profile", f"Spawned {count} new personas (total: {current['total'] + count})")
    return count


def pick_random_inactive() -> Optional[dict]:
    """Pick a random inactive persona to reactivate.

    Returns the persona dict, or None if no inactive personas exist.
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_inactive_personas
    inactive = get_inactive_personas()
    if not inactive:
        return None
    return random.choice(inactive)


def activate_persona(persona_uuid: str):
    """Mark a persona as active again."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE ext_sp_profiles SET active = 1, last_active_at = ? WHERE persona_uuid = ?",
               (now, persona_uuid))
    log.info("sp_profile", f"Reactivated persona {persona_uuid[:8]}")


def deactivate_persona(persona_uuid: str):
    """Mark a persona as inactive (goes on hiatus)."""
    db = get_db()
    db.execute("UPDATE ext_sp_profiles SET active = 0 WHERE persona_uuid = ?", (persona_uuid,))
    log.info("sp_profile", f"Deactivated persona {persona_uuid[:8]}")
