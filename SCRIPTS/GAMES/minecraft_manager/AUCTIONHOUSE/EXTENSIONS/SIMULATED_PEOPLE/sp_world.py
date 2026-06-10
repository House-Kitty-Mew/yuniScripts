"""
sp_world.py — Simulated world with regions, areas, resources, and territories.

Creates and manages a living Minecraft world that personas inhabit:
  - 3 regions (overworld, nether, end) with 18+ named areas
  - Each area has resources (common/uncommon/rare/unique)
  - Territories can be claimed by personas
  - World events affect regional economics

The world is generated once on first load, with AI-generated names
and descriptions.  On subsequent loads, it reads from the database.
"""

import json, random, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_database import get_db as ah_get_db
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_db, ensure_schema
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ── AI Area Name Templates ───────────────────────────────────────────

_AREA_NAMES_BY_BIOME = {
    "plains": [
        "The Verdant Reach", "Golden Fields", "Sunstone Meadows",
        "The Wandering Flats", "Amber Grasslands", "Windwalker Plains",
    ],
    "forest": [
        "Whispering Woods", "The Deepwood", "Emerald Canopy",
        "Shadowbark Forest", "The Mossy Thicket", "Silverwood Grove",
    ],
    "desert": [
        "Scorchfield Basin", "The Endless Dunes", "Sunfang Expanse",
        "Bonewhite Wastes", "Mirage Valley", "Sandreaver's Rest",
    ],
    "swamp": [
        "The Murkfen", "Bogrot Hollow", "Stagnant Mere",
        "Vilebriar Depths", "The Whispering Marsh", "Festerwood Bog",
    ],
    "mountains": [
        "The Stonejaw Peaks", "Highcrest Ridge", "Frostbite Summit",
        "The Anvil Range", "Thunderhorn Mountains", "Eagle's Perch",
    ],
    "ocean": [
        "The Endless Blue", "Kelpgrave Shallows", "The Sunken Reach",
        "Abyssal Trench", "Coralbone Atoll", "The Silent Depths",
    ],
    "tundra": [
        "The Frostwind Expanse", "Permafrost Hollow", "Glacial Maw",
        "The Icebone Plateau", "Snowgrave Fields", "Winter's Grasp",
    ],
    "nether_wastes": [
        "The Ashen Scar", "Soulsand Basin", "Magmaflow Plains",
        "The Crimson Reach", "Charstone Ridge", "Brimstone Valley",
    ],
    "crimson_forest": [
        "The Bleeding Wood", "Crimson Thicket", "Warped Grove's Edge",
        "The Shroomlight Glade", "Fungal Mire", "The Weeping Vines",
    ],
    "end_islands": [
        "The Silent Void", "Chorus Heights", "Obsidian Spire",
        "The Outer Reach", "Nullspace Atoll", "Eternal Dusk",
    ],
    "deep_dark": [
        "The Soundless Depths", "Sculkheart Cavern", "Ancient's Rest",
        "The Skulkway", "Echoing Vault", "Midnight Abyss",
    ],
}

_AREA_DESCRIPTIONS = {
    "plains": [
        "Rolling golden grasses stretch to the horizon under a wide sky.",
        "Wildflowers dot the landscape where the earth is soft and fertile.",
        "A gentle breeze carries the scent of hay and distant rain.",
    ],
    "forest": [
        "Ancient oaks form a canopy that filters sunlight into emerald beams.",
        "The undergrowth rustles with unseen life among the towering trees.",
        "Moss-covered roots weave paths between the trunks of giants.",
    ],
    "desert": [
        "Endless dunes shift with the wind, hiding ancient secrets beneath.",
        "The sun beats down on cracked earth where only the hardy survive.",
        "Heat shimmers dance across the horizon, distorting distant shapes.",
    ],
    "swamp": [
        "Bubbling pools release gases that glow faintly in the twilight.",
        "Knee-deep water stretches between gnarled roots and hanging moss.",
        "The air is thick with the smell of decay and blooming water flowers.",
    ],
    "mountains": [
        "Sharp peaks pierce the clouds, their slopes dotted with hardy pines.",
        "The wind howls through crevices carved by ages of relentless weather.",
        "Snowcaps gleam far above the treeline, where only the bold ascend.",
    ],
    "nether_wastes": [
        "A sea of ash and magma stretches beneath a blood-red sky.",
        "Heat shimmers warp the air above rivers of molten rock.",
        "The ground groans with pressure, occasionally venting steam and flame.",
    ],
    "crimson_forest": [
        "Giant crimson fungi tower like grotesque trees in the crimson light.",
        "The air shimmers with spores that glow with an eerie red luminescence.",
        "Warped roots twist through the ground, pulsing with otherworldly energy.",
    ],
    "end_islands": [
        "Floating fragments of obsidian drift in a void of infinite stars.",
        "The silence here is absolute, broken only by the distant chime of chorus fruit.",
        "Purple-tinted darkness stretches forever in every direction.",
    ],
    "deep_dark": [
        "An oppressive silence blankets this cavern. Every sound echoes too far.",
        "Sculk veins pulse with a rhythm that feels almost like a heartbeat.",
        "The darkness here has weight. It presses in from all sides.",
    ],
}

# ── Resource Templates ──────────────────────────────────────────────

_RESOURCE_TEMPLATES = {
    "plains": {
        "common": {"minecraft:dirt": 5000, "minecraft:grass_block": 2000, "minecraft:wheat": 800},
        "uncommon": {"minecraft:iron_ore": 200, "minecraft:coal": 400},
        "rare": {"minecraft:emerald": 30},
        "unique": {},
    },
    "forest": {
        "common": {"minecraft:oak_log": 3000, "minecraft:dirt": 4000, "minecraft:stick": 2000},
        "uncommon": {"minecraft:iron_ore": 150, "minecraft:coal": 300},
        "rare": {"minecraft:diamond": 15, "minecraft:emerald": 20},
        "unique": {},
    },
    "desert": {
        "common": {"minecraft:sand": 8000, "minecraft:sandstone": 3000, "minecraft:cactus": 400},
        "uncommon": {"minecraft:gold_ore": 150, "minecraft:iron_ore": 100},
        "rare": {"minecraft:diamond": 10, "minecraft:ancient_debris": 5},
        "unique": {"minecraft:desert_pyramid_treasure": 1},
    },
    "swamp": {
        "common": {"minecraft:clay": 1000, "minecraft:dirt": 3000, "minecraft:vine": 600},
        "uncommon": {"minecraft:iron_ore": 100, "minecraft:coal": 200, "minecraft:slime_ball": 50},
        "rare": {"minecraft:diamond": 8},
        "unique": {"minecraft:swamp_hut_treasure": 1},
    },
    "mountains": {
        "common": {"minecraft:stone": 8000, "minecraft:cobblestone": 5000, "minecraft:coal": 1000},
        "uncommon": {"minecraft:iron_ore": 400, "minecraft:gold_ore": 200, "minecraft:lapis_lazuli": 100},
        "rare": {"minecraft:diamond": 50, "minecraft:emerald": 80},
        "unique": {"minecraft:ancient_debris": 3},
    },
    "ocean": {
        "common": {"minecraft:gravel": 3000, "minecraft:sand": 2000, "minecraft:kelp": 800},
        "uncommon": {"minecraft:prismarine_shard": 100, "minecraft:nautilus_shell": 20},
        "rare": {"minecraft:heart_of_the_sea": 5},
        "unique": {"minecraft:buried_treasure": 2},
    },
    "tundra": {
        "common": {"minecraft:snow_block": 4000, "minecraft:ice": 2000, "minecraft:dirt": 2000},
        "uncommon": {"minecraft:iron_ore": 150, "minecraft:coal": 200, "minecraft:blue_ice": 50},
        "rare": {"minecraft:diamond": 20, "minecraft:emerald": 15},
        "unique": {"minecraft:ancient_debris": 2},
    },
    "nether_wastes": {
        "common": {"minecraft:netherrack": 10000, "minecraft:glowstone": 500, "minecraft:quartz": 1000},
        "uncommon": {"minecraft:nether_quartz_ore": 400, "minecraft:magma_cream": 50},
        "rare": {"minecraft:ancient_debris": 20},
        "unique": {"minecraft:nether_fortress_loot": 1},
    },
    "crimson_forest": {
        "common": {"minecraft:crimson_stem": 2000, "minecraft:netherrack": 5000},
        "uncommon": {"minecraft:nether_wart": 200, "minecraft:shroomlight": 50},
        "rare": {"minecraft:ancient_debris": 10},
        "unique": {"minecraft:bastion_treasure": 1},
    },
    "end_islands": {
        "common": {"minecraft:end_stone": 5000, "minecraft:chorus_fruit": 300},
        "uncommon": {"minecraft:popped_chorus_fruit": 100, "minecraft:ender_pearl": 30},
        "rare": {"minecraft:elytra": 2, "minecraft:dragon_head": 1},
        "unique": {"minecraft:end_city_treasure": 2},
    },
    "deep_dark": {
        "common": {"minecraft:deepslate": 5000, "minecraft:sculk": 2000},
        "uncommon": {"minecraft:echo_shard": 20, "minecraft:sculk_catalyst": 10},
        "rare": {"minecraft:swift_sneak_enchant": 5},
        "unique": {"minecraft:ancient_city_treasure": 1},
    },
}

# ── Area neighbors (graph edges) ────────────────────────────────────

_AREA_GRAPH = {
    "overworld": [
        (0, 1), (0, 2), (1, 3), (1, 4),
        (2, 5), (3, 6), (4, 6), (5, 6),
    ],
}


def _pick_name_for_biome(biome: str) -> str:
    """Pick an AI-style area name for a biome."""
    names = _AREA_NAMES_BY_BIOME.get(biome, _AREA_NAMES_BY_BIOME["plains"])
    return random.choice(names)


def _pick_description_for_biome(biome: str) -> str:
    """Pick a description for a biome."""
    descs = _AREA_DESCRIPTIONS.get(biome, _AREA_DESCRIPTIONS["plains"])
    return random.choice(descs)


def _generate_resources(biome: str) -> dict:
    """Generate resource dict for an area based on biome.

    Returns {item_id: {"remaining": N, "max": N, "rarity": "common"|...}}
    """
    templates = _RESOURCE_TEMPLATES.get(biome, _RESOURCE_TEMPLATES["plains"])
    result = {}
    for rarity, items in templates.items():
        for item_id, amount in items.items():
            # Add some variance
            var = random.uniform(0.7, 1.3)
            remaining = max(1, int(amount * var))
            result[item_id] = {
                "remaining": remaining,
                "max": remaining,
                "rarity": rarity,
            }
    return result


# ── Biome Assignment ───────────────────────────────────────────────

def _biome_for_region(region: str) -> str:
    """Pick a random biome for a region."""
    biomes = {
        "overworld": ["plains", "forest", "desert", "swamp", "mountains", "ocean", "tundra"],
        "nether": ["nether_wastes", "crimson_forest"],
        "end": ["end_islands"],
        "deep_dark": ["deep_dark"],
    }
    return random.choice(biomes.get(region, ["plains"]))


# ── World Generation ───────────────────────────────────────────────

def generate_world() -> int:
    """Generate the initial world with areas and resources.

    Creates:
      - Overworld: 6-8 areas
      - Nether: 2-3 areas
      - End: 1-3 areas
      - Deep Dark: 1 area

    Returns number of areas created.
    """
    try:
        ensure_schema()

    except Exception as e:
        log.error(f"generate_world failed: {e}")
        return 0
    db = get_db()

    now = datetime.now(timezone.utc).isoformat()
    regions = {
        "overworld": 7,
        "nether": 3,
        "end": 2,
        "deep_dark": 1,
    }

    count = 0
    all_areas = {}

    for region, num_areas in regions.items():
        for i in range(num_areas):
            area_uuid = str(uuid_mod.uuid4())
            biome = _biome_for_region(region)
            name = _pick_name_for_biome(biome)
            description = _pick_description_for_biome(biome)
            resources = _generate_resources(biome)

            db.execute("""
                INSERT INTO ext_sp_world_areas
                (area_uuid, name, region, biome_type, resources_json, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (area_uuid, name, region, biome, json.dumps(resources), description, now))

            all_areas.setdefault(region, []).append(area_uuid)
            count += 1

    # Create territories for each area (1-2 per area)
    for region, area_ids in all_areas.items():
        for area_uuid in area_ids:
            num_territories = random.randint(1, 2)
            for t in range(num_territories):
                terr_uuid = str(uuid_mod.uuid4())
                biome = next(
                    (b for b, n in _AREA_NAMES_BY_BIOME.items()
                     if random.random() < 0.01), "wilderness"
                )
                terr_name = f"{'Upper' if t == 0 else 'Lower'} Plot {t+1}"
                db.execute("""
                    INSERT INTO ext_sp_territories
                    (territory_uuid, area_uuid, name, defense_rating, income_per_tick)
                    VALUES (?, ?, ?, 1, ?)
                """, (terr_uuid, area_uuid, terr_name, round(random.uniform(0.1, 1.5), 2)))

    # Link neighbors (simple ring topology per region)
    for region, area_ids in all_areas.items():
        for idx, area_uuid in enumerate(area_ids):
            neighbors = []
            if idx > 0:
                neighbors.append(area_ids[idx - 1])
            if idx < len(area_ids) - 1:
                neighbors.append(area_ids[idx + 1])
            # Cross-region connections
            if region == "overworld" and idx == len(area_ids) // 2:
                if "nether" in all_areas:
                    neighbors.append(all_areas["nether"][0])
            if region == "nether" and idx == 0:
                if "end" in all_areas:
                    neighbors.append(all_areas["end"][0])
            db.execute(
                "UPDATE ext_sp_world_areas SET neighbor_ids = ? WHERE area_uuid = ?",
                (json.dumps(list(set(neighbors))), area_uuid)
            )

    log.info("sp_world", f"Generated world: {count} areas across {len(regions)} regions")
    return count


def get_area(area_uuid: str) -> Optional[dict]:
    """Get a single area by UUID."""
    db = get_db()
    return db.fetch_one("SELECT * FROM ext_sp_world_areas WHERE area_uuid = ?", (area_uuid,))


def get_all_areas(region: Optional[str] = None) -> list[dict]:
    """Get areas, optionally filtered by region."""
    db = get_db()
    if region:
        return db.fetch_all(
            "SELECT * FROM ext_sp_world_areas WHERE region = ? ORDER BY name",
            (region,))
    return db.fetch_all("SELECT * FROM ext_sp_world_areas ORDER BY region, name")


def get_area_count() -> dict:
    """Get count of areas per region."""
    db = get_db()
    rows = db.fetch_all("SELECT region, COUNT(*) as c FROM ext_sp_world_areas GROUP BY region")
    return {r["region"]: r["c"] for r in rows}


def resource_available(area_uuid: str, item_id: str) -> Optional[dict]:
    """Check if a resource is available in an area.

    Returns the resource dict or None.
    """
    area = get_area(area_uuid)
    if not area:
        return None
    try:
        resources = json.loads(area["resources_json"]) if isinstance(area["resources_json"], str) else (area["resources_json"] or {})
    except (json.JSONDecodeError, TypeError):
        resources = {}
    return resources.get(item_id)


def deplete_resource(area_uuid: str, item_id: str, amount: int = 1) -> bool:
    """Reduce a resource count in an area. Returns True if successful."""
    area = get_area(area_uuid)
    if not area:
        return False
    try:
        resources = json.loads(area["resources_json"]) if isinstance(area["resources_json"], str) else (area["resources_json"] or {})
    except (json.JSONDecodeError, TypeError):
        return False

    if item_id not in resources:
        return False
    res = resources[item_id]
    res["remaining"] = max(0, res["remaining"] - amount)
    resources[item_id] = res

    db = get_db()
    db.execute("UPDATE ext_sp_world_areas SET resources_json = ? WHERE area_uuid = ?",
               (json.dumps(resources), area_uuid))
    return True


def get_territories(claimed_by: Optional[str] = None) -> list[dict]:
    """Get territories, optionally filtered by claimant."""
    db = get_db()
    if claimed_by:
        return db.fetch_all(
            "SELECT t.*, a.name as area_name, a.region "
            "FROM ext_sp_territories t "
            "JOIN ext_sp_world_areas a ON t.area_uuid = a.area_uuid "
            "WHERE t.claimed_by = ?", (claimed_by,))
    return db.fetch_all(
        "SELECT t.*, a.name as area_name, a.region "
        "FROM ext_sp_territories t "
        "JOIN ext_sp_world_areas a ON t.area_uuid = a.area_uuid")


def get_unclaimed_territories() -> list[dict]:
    """Get all unclaimed territories."""
    db = get_db()
    return db.fetch_all(
        "SELECT t.*, a.name as area_name, a.region "
        "FROM ext_sp_territories t "
        "JOIN ext_sp_world_areas a ON t.area_uuid = a.area_uuid "
        "WHERE t.claimed_by IS NULL")


def assign_persona_area(persona_uuid: str, archetype: str) -> str:
    """Assign a starting area to a new persona based on archetype.

    Returns the area_uuid.
    """
    db = get_db()

    # Archetype → preferred biome mapping
    biome_prefs = {
        "miner": ["mountains", "deep_dark", "nether_wastes"],
        "farmer": ["plains", "forest"],
        "warrior": ["desert", "nether_wastes", "mountains"],
        "merchant": ["plains", "forest", "ocean"],
        "builder": ["forest", "plains", "mountains"],
        "mage": ["end_islands", "crimson_forest", "swamp"],
        "adventurer": ["desert", "mountains", "crimson_forest"],
        "vagabond": ["swamp", "tundra", "ocean"],
    }

    prefs = biome_prefs.get(archetype, ["plains"])
    chosen_biome = random.choice(prefs)

    # Find an area with that biome
    area = db.fetch_one(
        "SELECT * FROM ext_sp_world_areas WHERE biome_type = ? ORDER BY RANDOM() LIMIT 1",
        (chosen_biome,))
    if not area:
        area = db.fetch_one(
            "SELECT * FROM ext_sp_world_areas ORDER BY RANDOM() LIMIT 1")

    area_uuid = area["area_uuid"] if area else None
    if not area_uuid:
        return ""

    # Assign location
    db.execute("""
        INSERT OR REPLACE INTO ext_sp_persona_location (persona_uuid, area_uuid, moved_last_tick)
        VALUES (?, ?, 0)
    """, (persona_uuid, area_uuid))

    return area_uuid


def get_persona_area(persona_uuid: str) -> Optional[dict]:
    """Get the current area a persona is in."""
    db = get_db()
    loc = db.fetch_one(
        "SELECT area_uuid FROM ext_sp_persona_location WHERE persona_uuid = ?",
        (persona_uuid,))
    if not loc:
        return None
    return get_area(loc["area_uuid"])


def move_persona(persona_uuid: str, target_area_uuid: str) -> bool:
    """Move a persona to a new area."""
    db = get_db()
    current = get_persona_area(persona_uuid)
    if not current:
        return False

    # Check if target is a neighbor
    try:
        neighbors = json.loads(current["neighbor_ids"]) if isinstance(current["neighbor_ids"], str) else (current["neighbor_ids"] or [])
    except (json.JSONDecodeError, TypeError):
        neighbors = []

    if target_area_uuid not in neighbors and target_area_uuid != current["area_uuid"]:
        return False  # Can only move to neighbors

    db.execute("""
        UPDATE ext_sp_persona_location SET area_uuid = ?, moved_last_tick = 1
        WHERE persona_uuid = ?
    """, (target_area_uuid, persona_uuid))
    return True


def add_player_message(persona_uuid: str, player_name: str,
                        message_text: str, transaction_uuid: str = ""):
    """Record an AI-generated message from a persona to a player."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_sp_player_messages
        (persona_uuid, player_name, message_text, transaction_uuid, sent_at)
        VALUES (?, ?, ?, ?, ?)
    """, (persona_uuid, player_name, message_text, transaction_uuid, now))


def get_player_messages(player_name: str, limit: int = 20) -> list[dict]:
    """Get recent messages sent to a player from personas."""
    db = get_db()
    return db.fetch_all("""
        SELECT p.name as persona_name, p.archetype,
               m.message_text, m.transaction_uuid, m.sent_at
        FROM ext_sp_player_messages m
        JOIN ext_sp_profiles p ON m.persona_uuid = p.persona_uuid
        WHERE m.player_name = ?
        ORDER BY m.sent_at DESC LIMIT ?
    """, (player_name, limit))

