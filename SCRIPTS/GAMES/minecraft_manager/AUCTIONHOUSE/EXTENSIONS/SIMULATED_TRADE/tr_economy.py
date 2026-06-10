"""
tr_economy.py — Supply/demand economy simulation for SIMULATED_TRADE.

Models regional supply and demand dynamics:
  - Supply/demand tracking per region
  - Price calculation with elasticity
  - Trade flow effects on supply/demand
  - Integration with world events and depletion
"""

import json, math
from typing import Any, Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import fire_hook
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    get_all_resource_states, get_db,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_world_events import (
    get_region_price_multiplier, get_resource_abundance,
)

log = get_logger()

# ── In-memory supply/demand tracking ─────────────────────────────────

_supply: dict[str, dict[str, float]] = {}  # region_id -> {item_id: supply_level}
_demand: dict[str, dict[str, float]] = {}  # region_id -> {item_id: demand_level}


def initialize_economy(regions: Optional[list[str]] = None) -> None:
    """Initialize supply/demand for all regions.

    Args:
        regions: List of region IDs. If None, uses defaults.
    """
    try:
        global _supply, _demand

    except Exception as e:
        log.error(f"initialize_economy failed: {e}")
        return None

    if regions is None:
        regions = ["overworld", "nether", "end", "deep_dark", "ocean"]

    base_supply = get_config("economy", "base_supply_per_region", 100.0)
    base_demand = get_config("economy", "base_demand_per_region", 100.0)

    for region in regions:
        if region not in _supply:
            _supply[region] = {}
        if region not in _demand:
            _demand[region] = {}

    log.info("tr_economy", f"Economy initialized for {len(regions)} regions")

    # Sync to state registry
    state = get_state()
    state.set("economy_supply", _supply, "SIMULATED_TRADE")
    state.set("economy_demand", _demand, "SIMULATED_TRADE")


def calculate_price(item_id: str, region_id: str,
                    base_value: float = 10.0) -> float:
    """Calculate the current market price for an item in a region.

    Accounts for:
      - Supply/demand balance
      - Resource abundance
      - Active world events

    Args:
        item_id: The item to price
        region_id: The region to price in
        base_value: The item's base value

    Returns:
        Final price in gold.
    """
    if region_id not in _supply:
        return base_value

    supply = _supply[region_id].get(item_id, 100.0)
    demand = _demand[region_id].get(item_id, 100.0)

    # Determine elasticity by item category
    elasticity = _get_elasticity(item_id)

    # Supply/demand price modifier
    if supply > 0:
        sd_modifier = 1.0 + (demand - supply) * elasticity / max(supply, 1.0)
    else:
        sd_modifier = get_config("economy", "price_max_multiplier", 3.0)

    # Clamp to min/max
    min_mult = get_config("economy", "price_min_multiplier", 0.5)
    max_mult = get_config("economy", "price_max_multiplier", 3.0)
    sd_modifier = max(min_mult, min(max_mult, sd_modifier))

    # Resource abundance modifier
    abundance = get_resource_abundance(region_id, item_id)
    abundance_mod = 1.0 / max(0.1, abundance)

    # World event modifier
    event_mod = get_region_price_multiplier(region_id, item_id)

    # Final price
    price = base_value * sd_modifier * abundance_mod * event_mod
    return max(1.0, round(price, 2))


def adjust_supply(item_id: str, region_id: str, delta: float) -> None:
    """Adjust the supply level of an item in a region.

    Negative delta = consumption/scarcity
    Positive delta = production/new supply

    Args:
        item_id: The item
        region_id: The region
        delta: Change in supply level
    """
    if region_id not in _supply:
        _supply[region_id] = {}
    _supply[region_id][item_id] = _supply[region_id].get(item_id, 100.0) + delta
    _supply[region_id][item_id] = max(0.0, _supply[region_id][item_id])

    log.debug("tr_economy", f"Supply {item_id} in {region_id}: {delta:+.1f} = {_supply[region_id][item_id]:.1f}")

    # Update state
    state = get_state()
    state.set("economy_supply", _supply, "SIMULATED_TRADE")


def adjust_demand(item_id: str, region_id: str, delta: float) -> None:
    """Adjust the demand level of an item in a region.

    Positive delta = increased demand/scarcity pressure.

    Args:
        item_id: The item
        region_id: The region
        delta: Change in demand level
    """
    if region_id not in _demand:
        _demand[region_id] = {}
    _demand[region_id][item_id] = _demand[region_id].get(item_id, 100.0) + delta
    _demand[region_id][item_id] = max(0.0, _demand[region_id][item_id])

    log.debug("tr_economy", f"Demand {item_id} in {region_id}: {delta:+.1f} = {_demand[region_id][item_id]:.1f}")

    state = get_state()
    state.set("economy_demand", _demand, "SIMULATED_TRADE")


def process_trade_economy_impact(trade_type: str, items_offered: dict,
                                  items_received: dict, region_id: str) -> None:
    """Apply economic impacts from a completed trade.

    Selling items increases supply in the region.
    Buying items decreases supply and increases demand.

    Args:
        trade_type: 'currency', 'barter', 'banditry'
        items_offered: Items given by seller (increase supply in receiving region)
        items_received: Items received by buyer (decrease supply in sending region)
        region_id: Where the trade occurred
    """
    shift = get_config("economy", "demand_shift_per_trade", 0.02)

    # Items leaving the region -> decrease supply, increase demand
    for item_id, qty in items_offered.items():
        adjust_supply(item_id, region_id, -qty * shift)
        adjust_demand(item_id, region_id, qty * shift * 0.5)

    # Items arriving in the region -> increase supply, decrease demand
    for item_id, qty in items_received.items():
        adjust_supply(item_id, region_id, qty * shift * 0.8)

    log.info("tr_economy", f"Trade impact in {region_id}: {len(items_offered)} items out, {len(items_received)} items in")


def process_caravan_economy_impact(region_id: str, cargo: dict,
                                    is_arrival: bool) -> None:
    """Apply economic impact from a caravan arrival or departure.

    Args:
        region_id: Region affected
        cargo: Items being transported
        is_arrival: True if caravan arrived, False if departed
    """
    shift = get_config("economy", "demand_shift_per_trade", 0.02)

    if is_arrival:
        # Arrival adds supply
        for item_id, qty in cargo.items():
            adjust_supply(item_id, region_id, qty * shift)
    else:
        # Departure reduces supply
        for item_id, qty in cargo.items():
            adjust_supply(item_id, region_id, -qty * shift)


def get_economy_report(region_id: str) -> dict:
    """Get an economic report for a region.

    Returns:
        Dict with supply/demand data and notable items.
    """
    supply = _supply.get(region_id, {})
    demand = _demand.get(region_id, {})

    scarce_items = []
    surplus_items = []

    all_items = set(list(supply.keys()) + list(demand.keys()))
    for item_id in all_items:
        s = supply.get(item_id, 100.0)
        d = demand.get(item_id, 100.0)
        ratio = d / max(s, 0.1)

        if ratio > 2.0:
            scarce_items.append({"item_id": item_id, "ratio": round(ratio, 2)})
        elif ratio < 0.5:
            surplus_items.append({"item_id": item_id, "ratio": round(ratio, 2)})

    scarce_items.sort(key=lambda x: x["ratio"], reverse=True)
    surplus_items.sort(key=lambda x: x["ratio"])

    return {
        "region": region_id,
        "total_items_tracked": len(all_items),
        "scarce_items": scarce_items[:10],
        "surplus_items": surplus_items[:10],
        "overall_health": "inflation" if sum(demand.values()) > sum(supply.values()) else "deflation",
    }


def process_economy_tick() -> dict:
    """Process one tick of the economy simulation.

    - Regenerate supply
    - Apply random demand shifts
    - Update state registry

    Returns:
        Stats dict.
    """
    stats = {
        "supply_adjusted": 0,
        "demand_adjusted": 0,
        "regions_processed": 0,
    }

    regen = get_config("economy", "supply_regen_per_tick", 0.01)

    for region_id, supplies in _supply.items():
        for item_id, level in list(supplies.items()):
            # Regenerate supply toward base 100
            if level < 100.0:
                _supply[region_id][item_id] = min(100.0, level + regen * 10.0)
                stats["supply_adjusted"] += 1

        stats["regions_processed"] += 1

    # Random small demand shifts
    for region_id, demands in _demand.items():
        for item_id in list(demands.keys()):
            if random.random() < 0.01:  # 1% chance per item per tick
                shift = (random.random() - 0.5) * 2.0
                demands[item_id] = max(0.0, demands[item_id] + shift)
                stats["demand_adjusted"] += 1

    # Sync to state
    state = get_state()
    state.set("economy_supply", _supply, "SIMULATED_TRADE")
    state.set("economy_demand", _demand, "SIMULATED_TRADE")

    return stats


# ── Internal helpers ─────────────────────────────────────────────────

def _get_elasticity(item_id: str) -> float:
    """Get price elasticity for an item category."""
    commodity_items = [
        "minecraft:wheat", "minecraft:wood", "minecraft:stone",
        "minecraft:coal", "minecraft:iron_ore", "minecraft:netherrack",
        "minecraft:end_stone", "minecraft:bread", "minecraft:apple",
    ]
    luxury_items = [
        "minecraft:diamond", "minecraft:emerald", "minecraft:netherite_ingot",
        "minecraft:elytra", "minecraft:shulker_shell", "minecraft:heart_of_the_sea",
        "minecraft:dragon_breath",
    ]

    if item_id in commodity_items:
        return get_config("economy", "elasticity_commodity", 0.005)
    elif item_id in luxury_items:
        return get_config("economy", "elasticity_luxury", 0.001)
    else:
        return get_config("economy", "elasticity_manufactured", 0.002)


# Need random for tick processing
import random

