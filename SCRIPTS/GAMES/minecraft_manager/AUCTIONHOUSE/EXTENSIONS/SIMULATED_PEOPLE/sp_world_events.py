"""
sp_world_events.py — World event generation and lifecycle management.

Generates two types of events:
  1. World events — Global or regional economic/weather/social phenomena
     that affect all personas in a region.
  2. Personal life events — Random individual experiences that affect a
     single persona's mood, finances, and behavior.

World events create a dynamic ecosystem where:
  - A "mining_boom" in the overworld makes miner personas richer
  - A "nether_trade_embargo" hurts merchant personas
  - A "ender_infestation" makes warrior personas buy more weapons
  - Personal events give each persona their own story
"""

import random, json, uuid as uuid_mod
from datetime import datetime, timezone, timedelta
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    add_world_event, end_world_event, get_active_world_events,
    get_active_personas, add_life_event, expire_life_events,
    get_personas_by_region, get_persona_count,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db

log = get_logger()


# ══════════════════════════════════════════════════════════════════════
# World Event Templates
# ══════════════════════════════════════════════════════════════════════

_WORLD_EVENTS = [
    {
        "name": "mining_boom",
        "description": "A rich vein of deepslate diamond ore has been discovered! Miners are flocking to the area.",
        "region": "overworld",
        "severity": "moderate",
        "income_multiplier": 2.0,
        "financial_multiplier": 1.5,
        "duration_hours": (24, 72),
        "affected_archetypes": ["miner"],
    },
    {
        "name": "nether_trade_embargo",
        "description": "Piglin traders have closed their borders! Nether goods are becoming scarce.",
        "region": "nether",
        "severity": "moderate",
        "income_multiplier": 0.5,
        "financial_multiplier": 0.7,
        "duration_hours": (48, 168),
        "affected_archetypes": ["merchant"],
    },
    {
        "name": "ender_infestation",
        "description": "Endermen are spawning in unusual numbers near the overworld portal! Warriors are needed.",
        "region": "overworld",
        "severity": "severe",
        "income_multiplier": 0.6,
        "financial_multiplier": 1.2,
        "duration_hours": (12, 48),
        "affected_archetypes": ["warrior", "adventurer"],
    },
    {
        "name": "great_frost",
        "description": "An unseasonable frost has settled over the land. Crops are failing and travel is dangerous.",
        "region": "overworld",
        "severity": "severe",
        "income_multiplier": 0.3,
        "financial_multiplier": 0.5,
        "duration_hours": (72, 336),
        "affected_archetypes": ["farmer", "vagabond"],
    },
    {
        "name": "end_aurora",
        "description": "The End sky is glowing with rare auroras! Mages say the veil between worlds is thin.",
        "region": "end",
        "severity": "minor",
        "income_multiplier": 1.3,
        "financial_multiplier": 1.2,
        "duration_hours": (24, 72),
        "affected_archetypes": ["mage"],
    },
    {
        "name": "nether_gold_rush",
        "description": "A massive vein of gold has been exposed in the Nether wastes! Prospectors rejoice.",
        "region": "nether",
        "severity": "moderate",
        "income_multiplier": 2.0,
        "financial_multiplier": 1.8,
        "duration_hours": (24, 96),
        "affected_archetypes": ["miner", "adventurer"],
    },
    {
        "name": "building_frenzy",
        "description": "The Ancient Builder civilization's blueprints have been uncovered! Everyone wants to build.",
        "region": "overworld",
        "severity": "minor",
        "income_multiplier": 1.2,
        "financial_multiplier": 1.1,
        "duration_hours": (48, 168),
        "affected_archetypes": ["builder"],
    },
    {
        "name": "deep_dark_awakening",
        "description": "The Warden stirs! Deep Dark expeditions are on hold. Miners and adventurers retreat.",
        "region": "deep_dark",
        "severity": "severe",
        "income_multiplier": 0.2,
        "financial_multiplier": 0.3,
        "duration_hours": (24, 72),
        "affected_archetypes": ["miner", "adventurer"],
    },
    {
        "name": "harvest_festival",
        "description": "The annual harvest festival is here! Food prices are up, and everyone's in a good mood.",
        "region": "overworld",
        "severity": "minor",
        "income_multiplier": 1.5,
        "financial_multiplier": 1.3,
        "duration_hours": (24, 48),
        "affected_archetypes": ["farmer", "merchant"],
    },
    {
        "name": "pvp_tournament",
        "description": "The arena is hosting a grand PvP tournament! Warriors and adventurers are gearing up.",
        "region": "overworld",
        "severity": "moderate",
        "income_multiplier": 1.0,
        "financial_multiplier": 1.4,
        "duration_hours": (24, 72),
        "affected_archetypes": ["warrior", "adventurer"],
    },
]


# ══════════════════════════════════════════════════════════════════════
# Personal Life Event Templates
# ══════════════════════════════════════════════════════════════════════

_PERSONAL_EVENTS = [
    {"event_type": "windfall", "description": "Found a hidden cache of emeralds while exploring!",
     "financial_impact_range": (20, 200), "mood_impact": "happy", "duration_hours": (12, 48)},
    {"event_type": "expense", "description": "Tools broke at the worst possible time. Had to buy replacements.",
     "financial_impact_range": (-50, -10), "mood_impact": "stressed", "duration_hours": (6, 24)},
    {"event_type": "discovery", "description": "Discovered a new cave system rich in resources!",
     "financial_impact_range": (30, 100), "mood_impact": "motivated", "duration_hours": (12, 36)},
    {"event_type": "accident", "description": "Fell into a ravine and lost some supplies.",
     "financial_impact_range": (-40, -5), "mood_impact": "cautious", "duration_hours": (12, 48)},
    {"event_type": "windfall", "description": "Completed a big commission! Payment came through.",
     "financial_impact_range": (50, 300), "mood_impact": "happy", "duration_hours": (6, 24)},
    {"event_type": "expense", "description": "Got fined for disturbing the villagers. Paperwork is expensive.",
     "financial_impact_range": (-20, -5), "mood_impact": "stressed", "duration_hours": (4, 12)},
    {"event_type": "discovery", "description": "Found a mysterious structure on the map. Planning an expedition!",
     "financial_impact_range": (0, 0), "mood_impact": "motivated", "duration_hours": (24, 72)},
    {"event_type": "windfall", "description": "A friend repaid an old debt! Totally forgot about it.",
     "financial_impact_range": (10, 60), "mood_impact": "happy", "duration_hours": (4, 12)},
    {"event_type": "accident", "description": "Creeper blew up my storage room!",
     "financial_impact_range": (-100, -20), "mood_impact": "stressed", "duration_hours": (24, 72)},
    {"event_type": "windfall", "description": "Struck it rich! Found multiple diamond veins.",
     "financial_impact_range": (100, 500), "mood_impact": "happy", "duration_hours": (12, 48)},
    {"event_type": "expense", "description": "Enchantment costs are getting ridiculous. The librarian raised prices again.",
     "financial_impact_range": (-30, -5), "mood_impact": "cautious", "duration_hours": (6, 24)},
    {"event_type": "discovery", "description": "Found an abandoned mineshaft with untouched chests!",
     "financial_impact_range": (20, 80), "mood_impact": "motivated", "duration_hours": (8, 24)},
    {"event_type": "windfall", "description": "Won a bet against another adventurer!",
     "financial_impact_range": (15, 80), "mood_impact": "happy", "duration_hours": (2, 8)},
    {"event_type": "accident", "description": "Piglin ambush! Lost some gold ingots escaping.",
     "financial_impact_range": (-30, -10), "mood_impact": "cautious", "duration_hours": (8, 24)},
    {"event_type": "expense", "description": "Medical bills after a close encounter with a Warden.",
     "financial_impact_range": (-80, -20), "mood_impact": "stressed", "duration_hours": (24, 72)},
    {"event_type": "windfall", "description": "The villagers held a collection for me! Turns out they appreciate my work.",
     "financial_impact_range": (10, 40), "mood_impact": "happy", "duration_hours": (6, 24)},
    {"event_type": "discovery", "description": "Found a ruined portal with a partially-filled chest.",
     "financial_impact_range": (10, 50), "mood_impact": "motivated", "duration_hours": (4, 12)},
    {"event_type": "windfall", "description": "Inherited some gear from a retired adventurer.",
     "financial_impact_range": (40, 150), "mood_impact": "motivated", "duration_hours": (12, 48)},
    {"event_type": "expense", "description": "My elytra got damaged beyond repair. Need a new one.",
     "financial_impact_range": (-200, -50), "mood_impact": "stressed", "duration_hours": (48, 168)},
    {"event_type": "windfall", "description": "Sold some old maps to a collector. They were worth more than I thought!",
     "financial_impact_range": (20, 100), "mood_impact": "happy", "duration_hours": (4, 16)},
]


# ══════════════════════════════════════════════════════════════════════
# Event Generation
# ══════════════════════════════════════════════════════════════════════

def maybe_start_world_event() -> Optional[dict]:
    """Roll to create a new world event.

    Returns the event dict if one was created, None otherwise.
    """
    cfg = get_config()
    chance = cfg.get("world_event_chance_per_tick", 0.05)
    if random.random() >= chance:
        return None

    # Don't start if too many already active
    active = get_active_world_events()
    if len(active) >= 2:
        return None

    template = random.choice(_WORLD_EVENTS)
    dur_hours = random.randint(template["duration_hours"][0], template["duration_hours"][1])

    event_uuid = add_world_event(
        name=template["name"],
        description=template["description"],
        region=template["region"],
        severity=template["severity"],
        financial_multiplier=template["financial_multiplier"],
        income_multiplier=template["income_multiplier"],
    )

    # Schedule end
    # (stored as ISO timestamp, checked in cleanup)
    db = sp_get_db()
    now = datetime.now(timezone.utc)
    end_time = now + timedelta(hours=dur_hours)
    db.execute(
        "UPDATE ext_sp_world_events SET ended_at = ? WHERE event_uuid = ?",
        (end_time.isoformat(), event_uuid))

    log.info("sp_world_events",
             f"World event started: {template['name']} ({template['severity']}) "
             f"in {template['region']} — lasts {dur_hours}h")

    return {"event_uuid": event_uuid, "name": template["name"],
            "region": template["region"], "severity": template["severity"],
            "duration_hours": dur_hours}


def maybe_end_world_events() -> int:
    """End world events that have passed their duration.

    Returns the count of events ended.
    """
    db = sp_get_db()
    now = datetime.now(timezone.utc).isoformat()
    expired = db.fetch_all(
        "SELECT * FROM ext_sp_world_events WHERE ended_at IS NOT NULL AND ended_at < ?",
        (now,))
    for evt in expired:
        end_world_event(evt["event_uuid"])
        log.info("sp_world_events", f"World event ended: {evt['name']}")
    return len(expired)


# ══════════════════════════════════════════════════════════════════════
# Personal Life Event Generation
# ══════════════════════════════════════════════════════════════════════

def maybe_generate_life_events(persona_uuid: str) -> Optional[dict]:
    """Roll to generate a personal life event for a persona.

    Returns the event dict, or None.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"maybe_generate_life_ failed: {e}")
        return None
    chance = cfg.get("life_event_chance_per_tick", 0.25)
    if random.random() >= chance:
        return None

    # Check max active events
    db = sp_get_db()
    active_count = db.fetch_one(
        "SELECT COUNT(*) as c FROM ext_sp_life_events WHERE persona_uuid = ? AND ended_at IS NULL",
        (persona_uuid,))
    max_events = cfg.get("max_active_life_events", 2)
    if active_count and active_count["c"] >= max_events:
        return None

    template = random.choice(_PERSONAL_EVENTS)
    impact = random.randint(template["financial_impact_range"][0],
                            template["financial_impact_range"][1])
    dur_hours = random.randint(template["duration_hours"][0],
                               template["duration_hours"][1])

    add_life_event(
        persona_uuid=persona_uuid,
        event_type=template["event_type"],
        description=template["description"],
        financial_impact=float(impact),
        mood_impact=template["mood_impact"],
        duration_hours=dur_hours,
    )

    # Apply immediate financial impact
    if impact != 0:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import update_finance
        update_finance(persona_uuid, float(impact), reason=template["event_type"])
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import add_memory
        add_memory(persona_uuid, "life_event",
                   detail={"event_type": template["event_type"],
                           "description": template["description"],
                           "impact": impact},
                   emotional_weight=7 if abs(impact) > 50 else 4)

    log.info("sp_world_events",
             f"Life event for {persona_uuid[:8]}: {template['event_type']} — {impact}em")

    return {"event_type": template["event_type"],
            "description": template["description"],
            "impact": impact,
            "mood": template["mood_impact"],
            "duration_hours": dur_hours}


def generate_life_events_for_all() -> int:
    """Roll for personal events for all active personas.

    Returns the count of events generated.
    """
    personas = get_active_personas()
    count = 0
    for persona in personas:
        try:
            result = maybe_generate_life_events(persona["persona_uuid"])
            if result:
                count += 1
        except Exception as e:
            log.error("sp_world_events",
                      f"Life event error for {persona.get('name', '?')}: {e}")
    return count


# ══════════════════════════════════════════════════════════════════════
# Ticker
# ══════════════════════════════════════════════════════════════════════

def run_world_event_tick() -> dict:
    """Run one tick of world/personal event processing.

    Returns a summary dict.
    """
    # 1. End expired events
    ended = maybe_end_world_events()

    # 2. Expire old life events
    expire_life_events()

    # 3. Try to start new world event
    started = maybe_start_world_event()

    # 4. Generate personal events for active personas
    personal_count = generate_life_events_for_all()

    return {
        "world_events_ended": ended,
        "world_event_started": started["name"] if started else None,
        "personal_events_generated": personal_count,
    }

