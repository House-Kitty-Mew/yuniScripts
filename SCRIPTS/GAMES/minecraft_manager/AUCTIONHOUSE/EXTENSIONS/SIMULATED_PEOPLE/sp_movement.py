"""
sp_movement.py — Persona movement and migration system.

Active personas move between areas each tick based on:
  1. Their highest skill (miners go to mountains, farmers to plains)
  2. World events (some areas become dangerous)
  3. Resource availability (chasing scarce resources)
  4. Random wandering (vagabonds move more)

AI generates reasons for movement, giving each move a narrative.
"""

import random, json
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import (
    get_all_areas, get_area, get_persona_area, move_persona
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import (
    get_skills, best_skill, improve_skill
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ── Archetype → preferred biome (for movement targeting) ────────────

_ARCHETYPE_BIOME_PREFS = {
    "miner":      ["mountains", "deep_dark", "nether_wastes"],
    "farmer":     ["plains", "forest", "swamp"],
    "warrior":    ["desert", "nether_wastes", "mountains", "crimson_forest"],
    "merchant":   ["plains", "forest", "ocean"],
    "builder":    ["forest", "plains", "mountains"],
    "mage":       ["end_islands", "crimson_forest", "swamp", "deep_dark"],
    "adventurer": ["desert", "mountains", "crimson_forest", "end_islands"],
    "vagabond":   ["swamp", "tundra", "ocean", "desert"],
}

_MOVE_REASONS = [
    "seeking better opportunities",
    "following rumors of rich resources",
    "the current area has grown too crowded",
    "heard the trading is better elsewhere",
    "exploring new territories",
    "avoiding a recent conflict",
    "searching for rare materials",
    "looking for a fresh start",
]


def _pick_move_reason(persona: dict) -> str:
    """Pick an appropriate movement reason for a persona."""
    archetype = persona.get("archetype", "adventurer")
    reasons = {
        "miner": ["seeking richer ore veins", "the local mines are depleted",
                   "heard of a new cave system opening up"],
        "farmer": ["looking for fertile soil", "the drought affected crops here",
                    "seeking better grazing land"],
        "warrior": ["answering a call to arms elsewhere", "the local guard pays poorly",
                     "seeking worthy opponents"],
        "merchant": ["following a trade route opportunity", "the local market is saturated",
                      "heard of a new settlement with demand for goods"],
        "vagabond": ["the open road calls", "got bored of the scenery",
                      "following a rumor about treasure"],
    }
    pool = reasons.get(archetype, _MOVE_REASONS)
    return random.choice(pool)


def process_movement_tick(persona: dict) -> Optional[str]:
    """Process movement for a single persona.

    Returns the reason they moved (or None if they stayed).

    The movement logic:
      1. Check if persona moved recently (cooldown)
      2. Determine if persona should move (based on archetype wanderlust)
      3. Pick a target area (preferred biome or random neighbor)
      4. Execute the move
      5. Improve exploration skill
    """
    cfg = get_config()
    db = get_db()

    # Cooldown: don't move every tick
    loc = db.fetch_one(
        "SELECT moved_last_tick FROM ext_sp_persona_location WHERE persona_uuid = ?",
        (persona["persona_uuid"],))
    if loc and loc["moved_last_tick"]:
        # Reset flag
        db.execute("UPDATE ext_sp_persona_location SET moved_last_tick = 0 WHERE persona_uuid = ?",
                   (persona["persona_uuid"],))
        return None

    # Determine move probability based on archetype
    move_chances = {
        "adventurer": 0.3, "merchant": 0.2, "builder": 0.1,
        "miner": 0.15, "farmer": 0.05, "warrior": 0.2,
        "mage": 0.1, "vagabond": 0.4,
    }
    chance = move_chances.get(persona.get("archetype", "adventurer"), 0.15)

    # World events can increase movement (migration waves)
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_active_world_events
    events = get_active_world_events()
    if events:
        chance *= 1.5  # Events cause more movement

    if random.random() > chance:
        return None

    # Decide to move
    current_area = get_persona_area(persona["persona_uuid"])
    if not current_area:
        return None

    # Get neighbors
    try:
        neighbors = json.loads(current_area["neighbor_ids"]) if isinstance(current_area["neighbor_ids"], str) else (current_area["neighbor_ids"] or [])
    except (json.JSONDecodeError, TypeError):
        neighbors = []

    if not neighbors:
        return None

    # Pick target: prefer areas matching archetype biome
    prefs = _ARCHETYPE_BIOME_PREFS.get(persona.get("archetype", "adventurer"), [])
    target = None

    # Try to find a preferred biome neighbor
    random.shuffle(neighbors)
    for n_id in neighbors:
        n_area = get_area(n_id)
        if n_area and n_area["biome_type"] in prefs:
            target = n_id
            break

    # If no preferred neighbor, pick random
    if not target:
        target = random.choice(neighbors)

    # Execute move
    reason = _pick_move_reason(persona)
    if move_persona(persona["persona_uuid"], target):
        # Improve exploration skill
        try:
            improve_skill(persona["persona_uuid"], "exploration", 1)
        except Exception:
            pass

        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import add_memory
        try:
            target_area = get_area(target)
            area_name = target_area["name"] if target_area else "unknown"
            add_memory(persona["persona_uuid"], "life_event",
                       detail={"event": "moved", "from": current_area.get("name", "?"),
                               "to": area_name, "reason": reason},
                       emotional_weight=3)
        except Exception:
            pass

        return reason

    return None
