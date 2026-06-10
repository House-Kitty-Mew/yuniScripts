"""
tr_world_events.py — Resource depletion & world events for SIMULATED_TRADE.

Rare world events that shake up the economy:
  - Resource depletion (resources vanish, slow regen)
  - Shortages (price spikes on specific goods)
  - Booms (abundance, price crashes)
  - Natural disasters (region-wide impact)
  - Integration with trade economy
"""

import json, random, uuid
from datetime import datetime, timezone
from typing import Any, Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import fire_hook
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    create_world_event, get_active_world_events,
    tick_world_events, ensure_resource_state,
    get_resource_state, update_resource_abundance,
    get_all_resource_states, get_db,
)

log = get_logger()

# ── Resource definitions ─────────────────────────────────────────────

RESOURCES_BY_REGION = {
    "overworld": [
        "minecraft:wood", "minecraft:stone", "minecraft:iron_ore",
        "minecraft:coal", "minecraft:wheat", "minecraft:diamond",
        "minecraft:redstone", "minecraft:gold_ore", "minecraft:emerald",
    ],
    "nether": [
        "minecraft:netherrack", "minecraft:nether_quartz",
        "minecraft:netherite_scrap", "minecraft:glowstone",
        "minecraft:blaze_rod", "minecraft:magma_cream",
    ],
    "end": [
        "minecraft:end_stone", "minecraft:chorus_fruit",
        "minecraft:purpur_block", "minecraft:shulker_shell",
        "minecraft:dragon_breath",
    ],
    "deep_dark": [
        "minecraft:echo_shard", "minecraft:disc_fragment",
        "minecraft:sculk", "minecraft:sculk_catalyst",
    ],
    "ocean": [
        "minecraft:prismarine", "minecraft:prismarine_crystals",
        "minecraft:sponge", "minecraft:nautilus_shell",
        "minecraft:heart_of_the_sea",
    ],
}

EVENT_TEMPLATES = {
    "depletion": {
        "names": [
            "The Vein Runs Dry",
            "Fickle Earth",
            "Exhausted Bounty",
            "Depleted Stores",
            "The Land Gives No More",
        ],
        "severity_range": (0.5, 0.9),
        "duration_range": (500, 2000),
        "price_mult_range": (1.5, 2.5),
    },
    "shortage": {
        "names": [
            "Critical Shortage",
            "Supply Chain Broken",
            "Scarcity Hits",
            "Rationing Begins",
            "Famine's Shadow",
        ],
        "severity_range": (0.3, 0.7),
        "duration_range": (200, 1000),
        "price_mult_range": (1.5, 3.0),
    },
    "boom": {
        "names": [
            "Bountiful Harvest",
            "Rich Vein Discovered",
            "Surplus Abounds",
            "Golden Age of Trade",
            "Market Flood",
        ],
        "severity_range": (0.3, 0.7),
        "duration_range": (300, 1500),
        "price_mult_range": (0.3, 0.7),
    },
    "disaster": {
        "names": [
            "The Great Quake",
            "Fire From Below",
            "Plague of Decay",
            "Eldritch Blight",
            "The Withering",
        ],
        "severity_range": (0.7, 1.0),
        "duration_range": (500, 3000),
        "price_mult_range": (2.0, 3.0),
    },
}


def initialize_resource_states() -> bool:
    """Initialize resource states for all known regions and resources.

    Returns:
        True on success.
    """
    for region, resources in RESOURCES_BY_REGION.items():
        for resource_id in resources:
            ensure_resource_state(region, resource_id)
    log.info("tr_world_events", "All resource states initialized")
    return True


def check_and_trigger_events() -> dict:
    """Check if a random world event should trigger this tick.

    Called once per check interval.

    Returns:
        Dict with event info if triggered, or {"triggered": False}.
    """
    # Count active events
    active = len(get_active_world_events())
    max_active = get_config("world_events", "max_active_events", 3)
    if active >= max_active:
        return {"triggered": False, "reason": "max_active_reached"}

    # Roll for each event type
    events = []
    event_configs = [
        ("depletion", get_config("world_events", "depletion_chance", 0.01)),
        ("shortage", get_config("world_events", "shortage_chance", 0.005)),
        ("boom", get_config("world_events", "boom_chance", 0.003)),
        ("disaster", get_config("world_events", "disaster_chance", 0.001)),
    ]

    for event_type, chance in event_configs:
        if random.random() < chance:
            event = _generate_event(event_type)
            if event:
                events.append(event)

    if not events:
        return {"triggered": False}

    result = {"triggered": True, "events": events}

    for event in events:
        _apply_event_to_resources(event)
        log.info("tr_world_events",
                 f"Event triggered: {event['event_name']} ({event['event_type']}) "
                 f"in {event['affected_region']}")

    return result


def process_world_events_tick() -> dict:
    """Process all active world events for one tick.

    - Decrease duration
    - Update resource levels
    - Handle event expiry
    - Trigger resource regeneration

    Returns:
        Stats dict.
    """
    stats = {
        "events_expired": 0,
        "resources_regenerated": 0,
        "events_active": 0,
    }

    # Tick event durations
    expired = tick_world_events()
    stats["events_expired"] = expired

    # Process resource regeneration
    for row in get_all_resource_states():
        region = row["region_id"]
        resource = row["resource_id"]
        current = row["current_abundance"]
        base = row["base_abundance"]
        is_event = row["event_active"]
        regen_rate = row["regeneration_rate"]

        if current < base:
            # Regenerate
            if is_event:
                rate = get_config("world_events", "regen_rate_depleted", 0.0005)
            else:
                rate = get_config("world_events", "regen_rate_normal", 0.001)

            new_val = min(base, current + rate)
            success = update_resource_abundance(region, resource, new_val)
            if success:
                stats["resources_regenerated"] += 1

    # Update active event count
    stats["events_active"] = len(get_active_world_events())

    # Update state registry
    state = get_state()
    state.set("active_world_events", get_active_world_events(), "SIMULATED_TRADE")
    state.set("resource_states", get_all_resource_states(), "SIMULATED_TRADE")

    return stats


def get_region_price_multiplier(region_id: str, resource_id: str) -> float:
    """Get the price multiplier for a resource in a region.

    Accounts for both active events and resource depletion.

    Returns:
        Multiplier (1.0 = normal, >1 = more expensive, <1 = cheaper).
    """
    try:
        resource = get_resource_state(region_id, resource_id)

    except Exception as e:
        log.error(f"get_region_price_mul failed: {e}")
        return 0.0
    if not resource:
        return 1.0

    # Depletion multiplier from resource state
    depletion_mult = resource.get("depletion_mult", 1.0)

    # Event multipliers
    event_mult = 1.0
    active_events = get_active_world_events()
    for event in active_events:
        if event["affected_region"] == region_id:
            affected = json.loads(event.get("affected_resources", "[]"))
            if resource_id in affected:
                event_mult *= event.get("price_multiplier", 1.0)

    return depletion_mult * event_mult


def get_resource_abundance(region_id: str, resource_id: str) -> float:
    """Get the current abundance of a resource in a region.

    Returns:
        0.0-1.0 abundance level.
    """
    resource = get_resource_state(region_id, resource_id)
    if resource:
        return resource.get("current_abundance", 1.0)
    return 1.0


def is_resource_scarce(region_id: str, resource_id: str, threshold: float = 0.5) -> bool:
    """Check if a resource is scarce in a region."""
    abundance = get_resource_abundance(region_id, resource_id)
    return abundance < threshold


# ── Internal helpers ─────────────────────────────────────────────────

def _generate_event(event_type: str) -> Optional[dict]:
    """Generate a random world event of the given type.

    Returns:
        Event dict ready for DB insertion, or None.
    """
    try:
        template = EVENT_TEMPLATES.get(event_type)

    except Exception as e:
        log.error(f"_generate_event failed: {e}")
        return None
    if not template:
        return None

    # Pick random region
    region = random.choice(list(RESOURCES_BY_REGION.keys()))

    # Pick resources to affect (1-3)
    available = RESOURCES_BY_REGION.get(region, [])
    if not available:
        return None
    num_resources = min(len(available), random.randint(1, 3))
    affected = random.sample(available, num_resources)

    # Generate parameters
    name = random.choice(template["names"])
    severity = random.uniform(*template["severity_range"])
    duration = random.randint(*template["duration_range"])
    price_mult = random.uniform(*template["price_mult_range"])

    # For boom events, price_mult < 1 means cheaper
    if event_type == "boom":
        price_mult = 1.0 - (1.0 - price_mult)  # Keep as is (it's already <1)

    # Description
    if event_type == "depletion":
        desc = f"A mysterious depletion has struck {region}'s {', '.join(affected)}. Resources are scarce and prices soar."
    elif event_type == "shortage":
        desc = f"A critical shortage of {', '.join(affected)} in {region} drives prices up sharply."
    elif event_type == "boom":
        desc = f"A boom of {', '.join(affected)} has hit {region}! Markets flooded with cheap goods."
    elif event_type == "disaster":
        desc = f"A devastating disaster in {region} has ravaged {', '.join(affected)}. Recovery will be slow."
    else:
        desc = f"Something is happening in {region} affecting {', '.join(affected)}."

    # Create event
    event_uuid = create_world_event(
        event_type=event_type,
        event_name=name,
        description=desc,
        affected_region=region,
        affected_resources=affected,
        severity=severity,
        duration_ticks=duration,
        price_multiplier=price_mult,
    )

    if not event_uuid:
        return None

    # Fire hook
    fire_hook("on_world_event",
              event_uuid=event_uuid,
              event_type=event_type,
              event_name=name,
              region=region,
              resources=affected,
              severity=severity,
              duration=duration)

    return {
        "event_uuid": event_uuid,
        "event_type": event_type,
        "event_name": name,
        "description": desc,
        "affected_region": region,
        "affected_resources": affected,
        "severity": severity,
        "duration_ticks": duration,
        "price_multiplier": price_mult,
    }


def _apply_event_to_resources(event: dict) -> None:
    """Apply a world event's effect to region resources.

    Depletion/shortage: reduce abundance
    Boom: increase abundance
    Disaster: reduce all resources
    """
    try:
        region = event["affected_region"]

    except Exception as e:
        log.error(f"_apply_event_to_reso failed: {e}")
        return None
    resources = event["affected_resources"]
    severity = event["severity"]
    event_type = event["event_type"]

    for resource_id in resources:
        ensure_resource_state(region, resource_id)
        current = get_resource_state(region, resource_id)
        base = current["base_abundance"] if current else 1.0

        if event_type in ("depletion", "disaster"):
            # Reduce to 10-30% of base
            new_val = base * (0.1 + (1.0 - severity) * 0.2)
        elif event_type == "shortage":
            # Reduce to 30-50%
            new_val = base * (0.3 + (1.0 - severity) * 0.2)
        elif event_type == "boom":
            # Increase to 200-300%
            new_val = base * (2.0 + severity)
        else:
            new_val = base

        update_resource_abundance(region, resource_id, min(base * 3.0, new_val))

        # Mark event active on resource
        db = get_db()
        db.execute("""
            UPDATE ext_tr_resource_state
            SET event_active = 1,
                depleted_at = CASE WHEN ? IN ('depletion', 'disaster') THEN ? ELSE depleted_at END,
                regeneration_rate = CASE WHEN ? IN ('depletion', 'disaster')
                    THEN ? ELSE regeneration_rate END
            WHERE region_id = ? AND resource_id = ?
        """, (
            event_type,
            datetime.now(timezone.utc).isoformat(),
            event_type,
            get_config("world_events", "regen_rate_depleted", 0.0005),
            region, resource_id,
        ))
        db.conn.commit()

