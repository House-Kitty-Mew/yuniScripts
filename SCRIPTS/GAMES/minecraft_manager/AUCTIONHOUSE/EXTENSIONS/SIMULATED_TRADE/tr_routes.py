"""
tr_routes.py — Trade routes & road construction for SIMULATED_TRADE extension.

Personas with claimed territories can build roads to other claims:
  - Construction with resource costs
  - Road levels with efficiency/speed bonuses
  - Maintenance requirements
  - Trade volume tracking
  - Integration with banditry system
"""

import json, math, uuid
from datetime import datetime, timezone
from typing import Any, Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import fire_hook
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    create_route, upgrade_route, maintain_route, degrade_route,
    get_routes_for_persona, get_routes_between_claims,
    log_route_trade, create_caravan, get_active_caravans_on_route,
    advance_caravan, ensure_reputation,
)

log = get_logger()


def construct_route(persona_uuid: str, from_claim_uuid: str,
                    to_claim_uuid: str, road_segments: list) -> dict:
    """Construct a new trade route between two claimed territories.

    Args:
        persona_uuid: Persona building the route
        from_claim_uuid: Source claim UUID
        to_claim_uuid: Destination claim UUID
        road_segments: List of coordinate waypoints

    Returns:
        {"ok": True, "route_uuid": str} or {"ok": False, "error": str}
    """
    # ── Validation ──────────────────────────────────────────────

    # Check persona owns the source claim
    if not _owns_claim(persona_uuid, from_claim_uuid):
        return {"ok": False, "error": "You don't own the source claim"}

    # Check destination claim exists
    if not _claim_exists(to_claim_uuid):
        return {"ok": False, "error": "Destination claim doesn't exist"}

    # Check if route already exists
    existing = get_routes_between_claims(from_claim_uuid, to_claim_uuid)
    if existing:
        return {"ok": False, "error": "A route already exists between these claims"}

    # Check resource costs
    distance = _calculate_route_distance(road_segments)
    wood_cost = get_config("routes", "construction_cost_wood_base", 64) * max(1, int(distance / 100))
    stone_cost = get_config("routes", "construction_cost_stone_base", 32) * max(1, int(distance / 100))

    inv = _get_persona_inventory(persona_uuid)
    wood_available = inv.get("minecraft:wood", 0) + inv.get("minecraft:oak_log", 0) + inv.get("minecraft:spruce_log", 0)
    stone_available = inv.get("minecraft:stone", 0) + inv.get("minecraft:cobblestone", 0)

    if wood_available < wood_cost:
        return {"ok": False, "error": f"Need {wood_cost} wood (have {wood_available})"}
    if stone_available < stone_cost:
        return {"ok": False, "error": f"Need {stone_cost} stone (have {stone_available})"}

    # ── Consume resources ───────────────────────────────────────
    _consume_construction_materials(persona_uuid, wood_cost, stone_cost)

    # ── Create route ────────────────────────────────────────────
    route_uuid = create_route(
        owner_uuid=persona_uuid,
        from_claim_uuid=from_claim_uuid,
        to_claim_uuid=to_claim_uuid,
        road_segments=road_segments,
        distance=distance,
    )

    if not route_uuid:
        return {"ok": False, "error": "Failed to create route"}

    log.info("tr_routes",
             f"Route {route_uuid} constructed by {persona_uuid}: "
             f"{from_claim_uuid} -> {to_claim_uuid} ({distance:.0f} blocks)")

    # ── Fire hook ───────────────────────────────────────────────
    fire_hook("on_route_constructed",
              persona_uuid=persona_uuid,
              route_uuid=route_uuid,
              from_claim=from_claim_uuid,
              to_claim=to_claim_uuid,
              distance=distance)

    # ── Update state ────────────────────────────────────────────
    state = get_state()
    routes = list(state.get("trade_routes", []))
    routes.append({
        "route_uuid": route_uuid,
        "owner": persona_uuid,
        "from": from_claim_uuid,
        "to": to_claim_uuid,
        "distance": distance,
        "level": 1,
    })
    state.set("trade_routes", routes, "SIMULATED_TRADE")

    return {"ok": True, "route_uuid": route_uuid, "distance": distance}


def upgrade_road(route_uuid: str, persona_uuid: str) -> dict:
    """Upgrade a trade route to the next level.

    Costs escalate with each level.

    Returns:
        {"ok": True, "new_level": int} or {"ok": False, "error": str}
    """
    try:
        route = _get_route(route_uuid)

    except Exception as e:
        log.error(f"upgrade_road failed: {e}")
        return {}
    if not route:
        return {"ok": False, "error": "Route not found"}
    if route["owner_uuid"] != persona_uuid:
        return {"ok": False, "error": "You don't own this route"}
    current_level = route["level"]
    if current_level >= get_config("routes", "max_route_level", 5):
        return {"ok": False, "error": "Route already at max level"}

    # Cost scales with level
    wood_cost = get_config("routes", "construction_cost_wood_base", 64) * current_level * 2
    stone_cost = get_config("routes", "construction_cost_stone_base", 32) * current_level * 2

    inv = _get_persona_inventory(persona_uuid)
    wood_available = inv.get("minecraft:wood", 0) + inv.get("minecraft:oak_log", 0)
    stone_available = inv.get("minecraft:stone", 0) + inv.get("minecraft:cobblestone", 0)

    if wood_available < wood_cost:
        return {"ok": False, "error": f"Need {wood_cost} wood for upgrade"}
    if stone_available < stone_cost:
        return {"ok": False, "error": f"Need {stone_cost} stone for upgrade"}

    _consume_construction_materials(persona_uuid, wood_cost, stone_cost)

    if upgrade_route(route_uuid):
        log.info("tr_routes", f"Route {route_uuid} upgraded to level {current_level + 1}")
        return {"ok": True, "new_level": current_level + 1}
    return {"ok": False, "error": "Failed to upgrade route"}


def perform_maintenance(route_uuid: str, persona_uuid: str) -> dict:
    """Perform maintenance on a trade route.

    Returns:
        {"ok": True} or {"ok": False, "error": str}
    """
    try:
        route = _get_route(route_uuid)

    except Exception as e:
        log.error(f"perform_maintenance failed: {e}")
        return {}
    if not route:
        return {"ok": False, "error": "Route not found"}
    if route["owner_uuid"] != persona_uuid:
        return {"ok": False, "error": "You don't own this route"}

    # Small resource cost for maintenance
    wood_cost = get_config("routes", "construction_cost_wood_base", 64) // 4
    stone_cost = get_config("routes", "construction_cost_stone_base", 32) // 4

    inv = _get_persona_inventory(persona_uuid)
    wood_available = inv.get("minecraft:wood", 0) + inv.get("minecraft:oak_log", 0)
    if wood_available < wood_cost:
        return {"ok": False, "error": f"Need {wood_cost} wood for maintenance"}

    _consume_construction_materials(persona_uuid, wood_cost, stone_cost)
    maintain_route(route_uuid)
    return {"ok": True}


def dispatch_caravan(persona_uuid: str, route_uuid: str,
                     cargo: dict[str, int], gold: float,
                     guards: int = 1) -> dict:
    """Dispatch a caravan along a trade route.

    Args:
        persona_uuid: Owner dispatching the caravan
        route_uuid: Route to travel
        cargo: {item_id: quantity} being transported
        gold: Gold being transported
        guards: Number of guard NPCs

    Returns:
        {"ok": True, "caravan_uuid": str} or {"ok": False, "error": str}
    """
    # ── Validation ──────────────────────────────────────────────
    route = _get_route(route_uuid)
    if not route:
        return {"ok": False, "error": "Route not found"}
    if route["owner_uuid"] != persona_uuid:
        return {"ok": False, "error": "You don't own this route"}

    # Check persona has the cargo
    inv = _get_persona_inventory(persona_uuid)
    for item_id, qty in cargo.items():
        if inv.get(item_id, 0) < qty:
            return {"ok": False, "error": f"Don't have enough {item_id} in inventory"}

    # Check persona has the gold
    finances = _get_persona_finances(persona_uuid)
    if finances.get("balance", 0) < gold:
        return {"ok": False, "error": "Not enough gold"}

    # ── Consume cargo from inventory ────────────────────────────
    for item_id, qty in cargo.items():
        _remove_items_from_persona(persona_uuid, item_id, qty)
    _remove_gold_from_persona(persona_uuid, gold)

    # ── Calculate guard power ──────────────────────────────────
    guard_combat = 5.0 + (guards * 3.0)  # Base + per guard
    speed = 1.0 + (route["level"] - 1) * get_config("routes", "speed_per_level_bonus", 0.3)
    total_segments = max(1, len(json.loads(route.get("road_segments", "[]"))))

    # ── Create caravan ──────────────────────────────────────────
    caravan_uuid = create_caravan(
        route_uuid=route_uuid,
        owner_uuid=persona_uuid,
        cargo=cargo,
        gold_carried=gold,
        guard_count=guards,
        guard_combat_power=guard_combat,
        total_segments=total_segments,
    )

    if not caravan_uuid:
        return {"ok": False, "error": "Failed to create caravan"}

    # ── Update state ────────────────────────────────────────────
    state = get_state()
    caravans = list(state.get("active_caravans", []))
    caravans.append({
        "caravan_uuid": caravan_uuid,
        "route_uuid": route_uuid,
        "owner": persona_uuid,
        "cargo": cargo,
        "gold": gold,
        "guards": guards,
        "combat_power": guard_combat,
        "status": "traveling",
    })
    state.set("active_caravans", caravans, "SIMULATED_TRADE")

    log.info("tr_routes",
             f"Caravan {caravan_uuid} dispatched by {persona_uuid} "
             f"on route {route_uuid} ({len(cargo)} items, {gold}g)")

    return {"ok": True, "caravan_uuid": caravan_uuid}


def process_caravan_arrival(caravan_uuid: str) -> dict:
    """Process a caravan that has arrived at its destination.

    Cargo is added back to the owner's inventory (or local market).

    Returns:
        {"ok": True, "cargo_delivered": dict, "gold_delivered": float}
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db
    db = get_db()
    cursor = db.execute(
        "SELECT * FROM ext_tr_caravans WHERE caravan_uuid = ? AND status = 'arrived'",
        (caravan_uuid,)
    )
    row = cursor.fetchone()
    if not row:
        return {"ok": False, "error": "Caravan not found or not arrived"}

    caravan = dict(row)
    owner = caravan["owner_uuid"]
    cargo = json.loads(caravan.get("cargo_json", "{}"))
    gold = caravan.get("gold_carried", 0.0)

    # Add cargo back to owner inventory (with small efficiency loss)
    efficiency = 0.95  # 5% loss in transit
    for item_id, qty in cargo.items():
        delivered = max(1, int(qty * efficiency))
        _add_items_to_persona(owner, item_id, delivered)

    # Add gold back
    _add_gold_to_persona(owner, gold)

    # Mark caravan complete
    db.execute(
        "UPDATE ext_tr_caravans SET status = 'completed' WHERE caravan_uuid = ?",
        (caravan_uuid,)
    )
    db.conn.commit()

    log.info("tr_routes",
             f"Caravan {caravan_uuid} arrived: {cargo} + {gold}g delivered to {owner}")

    return {"ok": True, "cargo_delivered": cargo, "gold_delivered": gold}


def process_routes_tick() -> dict:
    """Process all active routes for one tick.

    - Advance caravans
    - Check maintenance
    - Update bandit activity decay
    - Log trade volumes

    Returns:
        Stats dict with tick results.
    """
    stats = {
        "caravans_advanced": 0,
        "caravans_arrived": 0,
        "routes_degraded": 0,
        "routes_maintained": 0,
    }

    # Process caravans
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db
    db = get_db()
    cursor = db.execute(
        "SELECT * FROM ext_tr_caravans WHERE status = 'traveling'"
    )
    caravans = [dict(row) for row in cursor.fetchall()]

    for caravan in caravans:
        route = _get_route(caravan["route_uuid"])
        speed = route["level"] * get_config("routes", "speed_per_level_bonus", 0.3) if route else 1.0
        result = advance_caravan(caravan["caravan_uuid"], speed)
        if result:
            stats["caravans_advanced"] += 1
            # Check if arrived
            updated = _get_caravan(caravan["caravan_uuid"])
            if updated and updated.get("status") == "arrived":
                stats["caravans_arrived"] += 1
                process_caravan_arrival(caravan["caravan_uuid"])

    # Degrade bandit activity
    db.execute("""
        UPDATE ext_tr_routes
        SET bandit_activity = MAX(0.0, bandit_activity - 0.01)
        WHERE is_active = 1
    """)
    db.conn.commit()

    log.debug("tr_routes", f"Route tick: {stats}")
    return stats


# ── Internal helpers ─────────────────────────────────────────────────

def _owns_claim(persona_uuid: str, claim_uuid: str) -> bool:
    """Check if a persona owns a claim."""
    state = get_state()
    claims = state.get("persona_claims", {})
    return claims.get(persona_uuid) == claim_uuid or claims.get(claim_uuid) == persona_uuid


def _claim_exists(claim_uuid: str) -> bool:
    """Check if a claim exists."""
    state = get_state()
    all_claims = state.get("all_claims", {})
    return claim_uuid in all_claims


def _calculate_route_distance(segments: list) -> float:
    """Calculate total distance of a route from waypoints."""
    if not segments or len(segments) < 2:
        return 0.0
    total = 0.0
    for i in range(len(segments) - 1):
        a = segments[i]
        b = segments[i + 1]
        if isinstance(a, dict) and isinstance(b, dict):
            dx = b.get("x", 0) - a.get("x", 0)
            dz = b.get("z", 0) - a.get("z", 0)
            total += math.sqrt(dx ** 2 + dz ** 2)
    return total


def _consume_construction_materials(persona_uuid: str, wood: int, stone: int) -> None:
    """Consume wood and stone from persona inventory for construction."""
    # Consume wood from various wood types
    wood_types = ["minecraft:wood", "minecraft:oak_log", "minecraft:spruce_log",
                   "minecraft:birch_log", "minecraft:jungle_log", "minecraft:acacia_log",
                   "minecraft:dark_oak_log"]
    remaining = wood
    for wt in wood_types:
        if remaining <= 0:
            break
        inv = _get_persona_inventory(persona_uuid)
        have = inv.get(wt, 0)
        if have > 0:
            take = min(have, remaining)
            _remove_items_from_persona(persona_uuid, wt, take)
            remaining -= take

    # Consume stone
    stone_types = ["minecraft:stone", "minecraft:cobblestone"]
    remaining = stone
    for st in stone_types:
        if remaining <= 0:
            break
        inv = _get_persona_inventory(persona_uuid)
        have = inv.get(st, 0)
        if have > 0:
            take = min(have, remaining)
            _remove_items_from_persona(persona_uuid, st, take)
            remaining -= take


def _get_persona_inventory(persona_uuid: str) -> dict[str, int]:
    """Get persona inventory from state."""
    state = get_state()
    invs = state.get("persona_inventories", {})
    return invs.get(persona_uuid, {})


def _get_persona_finances(persona_uuid: str) -> dict:
    """Get persona finances from state."""
    state = get_state()
    finances = state.get("persona_finances", {})
    return finances.get(persona_uuid, {})


def _remove_items_from_persona(persona_uuid: str, item_id: str, qty: int) -> bool:
    """Remove items from persona inventory in state."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import _remove_items_from_persona as rem
    return rem(persona_uuid, item_id, qty)


def _add_items_to_persona(persona_uuid: str, item_id: str, qty: int) -> None:
    """Add items to persona inventory in state."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import _add_items_to_persona as add
    add(persona_uuid, item_id, qty)


def _remove_gold_from_persona(persona_uuid: str, amount: float) -> bool:
    """Remove gold from persona in state."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import _remove_gold_from_persona as rem
    return rem(persona_uuid, amount)


def _add_gold_to_persona(persona_uuid: str, amount: float) -> None:
    """Add gold to persona in state."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import _add_gold_to_persona as add
    add(persona_uuid, amount)


def _get_route(route_uuid: str) -> Optional[dict]:
    """Get a route by UUID from DB."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db
    db = get_db()
    cursor = db.execute(
        "SELECT * FROM ext_tr_routes WHERE route_uuid = ?",
        (route_uuid,)
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _get_caravan(caravan_uuid: str) -> Optional[dict]:
    """Get a caravan by UUID from DB."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db
    db = get_db()
    cursor = db.execute(
        "SELECT * FROM ext_tr_caravans WHERE caravan_uuid = ?",
        (caravan_uuid,)
    )
    row = cursor.fetchone()
    return dict(row) if row else None

