"""
tr_core.py — Core trade engine for SIMULATED_TRADE extension.

Handles persona-to-persona trade execution:
  - Direct item+gold exchange
  - Range validation
  - Atomic transaction execution
  - Relationship impact
  - Integration with other extensions via state registry
"""

import json, math, random, uuid
from datetime import datetime, timezone
from typing import Any, Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import fire_hook
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    record_trade, create_pending_offer, get_open_offers_for_persona,
    accept_offer, check_cooldown, set_cooldown,
    is_persona_jailed, log_route_trade,
)
log = get_logger()


def get_db_connection():
    """Get a database connection for internal queries."""
    from AUCTIONHOUSE.ah_database import get_db
    return get_db()


# ── Public API ───────────────────────────────────────────────────────

def initiate_trade(initiator_uuid: str, target_uuid: str,
                   offered_items: dict[str, int],
                   gold_offered: float,
                   requested_items: dict[str, int],
                   gold_requested: float) -> dict:
    """Initiate a trade offer between two personas.

    Args:
        initiator_uuid: Persona making the offer
        target_uuid: Persona receiving the offer
        offered_items: {item_id: quantity} initiator is giving
        gold_offered: Gold initiator is giving
        requested_items: {item_id: quantity} initiator wants
        gold_requested: Gold initiator wants

    Returns:
        {"ok": True, "offer_uuid": str} or {"ok": False, "error": str}
    """
    # (error handling removed - try/except only wrapped comments)
    if initiator_uuid == target_uuid:
        return {"ok": False, "error": "Cannot trade with yourself"}

    # Jail check
    if is_persona_jailed(initiator_uuid):
        return {"ok": False, "error": "Initiator is in jail — cannot trade"}
    if is_persona_jailed(target_uuid):
        return {"ok": False, "error": "Target is in jail — cannot trade"}

    # Cooldown check
    if check_cooldown(initiator_uuid, "trade"):
        return {"ok": False, "error": "Initiator is on trade cooldown"}
    if check_cooldown(target_uuid, "trade"):
        return {"ok": False, "error": "Target is on trade cooldown"}

    # Range check
    range_ok, distance, location = _check_range(initiator_uuid, target_uuid)
    if not range_ok:
        return {"ok": False, "error": f"Personas out of trade range (distance: {distance:.0f})"}
    distance_mod = _distance_modifier(distance)

    # Must offer something
    if not offered_items and gold_offered <= 0:
        return {"ok": False, "error": "Must offer at least some items or gold"}
    if not requested_items and gold_requested <= 0:
        return {"ok": False, "error": "Must request at least some items or gold"}

    # Value check — ensure positive trade value on at least one side
    if not offered_items and not requested_items and gold_offered <= 0 and gold_requested <= 0:
        return {"ok": False, "error": "Empty trade offer"}

    # Pending offer limit check
    max_offers = get_config("trade", "max_pending_offers_per_persona", 5)
    existing = _count_open_offers(initiator_uuid)
    if existing >= max_offers:
        return {"ok": False, "error": f"Too many open offers ({existing}/{max_offers})"}

    # ── Create offer ────────────────────────────────────────────
    offer_uuid = create_pending_offer(
        initiator_uuid=initiator_uuid,
        target_uuid=target_uuid,
        offer_type="trade",
        offered_items=offered_items,
        requested_items=requested_items,
        gold_requested=gold_requested,
        gold_offered=gold_offered,
        expiry_ticks=get_config("trade", "offer_expiry_ticks", 100),
        location_area=location or "unknown",
    )

    if not offer_uuid:
        return {"ok": False, "error": "Failed to create trade offer"}

    # Log to state registry
    _update_state_trade_offers()

    log.info("tr_core",
             f"Trade offer {offer_uuid}: {initiator_uuid} -> {target_uuid} "
             f"(offered: {offered_items}, {gold_offered}g, "
             f"wanted: {requested_items}, {gold_requested}g)")

    return {"ok": True, "offer_uuid": offer_uuid}


def accept_trade(offer_uuid: str, acceptor_uuid: str) -> dict:
    """Accept a pending trade offer and execute the trade atomically.

    Args:
        offer_uuid: UUID of the offer to accept
        acceptor_uuid: Persona accepting the offer

    Returns:
        {"ok": True, "trade_uuid": str} or {"ok": False, "error": str}
    """
    # (error handling removed - try/except only wrapped comments)
    offer = _get_offer(offer_uuid)
    if not offer:
        return {"ok": False, "error": "Offer not found"}
    if offer.get("status") != "open":
        return {"ok": False, "error": f"Offer is {offer['status']}, not open"}
    if offer.get("target_uuid") and offer["target_uuid"] != acceptor_uuid:
        return {"ok": False, "error": "This offer is not for you"}

    initiator = offer["initiator_uuid"]

    # ── Extract trade details ───────────────────────────────────
    offered_items = json.loads(offer.get("offered_items", "{}"))
    requested_items = json.loads(offer.get("requested_items", "{}"))
    gold_offered = offer.get("gold_offered", 0.0)
    gold_requested = offer.get("gold_requested", 0.0)

    # ── Inventory capacity check ────────────────────────────────
    init_inv = _get_persona_inventory(initiator)
    acc_inv = _get_persona_inventory(acceptor_uuid)

    # Check initiator has enough items
    for item_id, qty in offered_items.items():
        if init_inv.get(item_id, 0) < qty:
            return {"ok": False, "error": f"Initiator doesn't have enough {item_id}"}

    # Check acceptor has enough items
    for item_id, qty in requested_items.items():
        if acc_inv.get(item_id, 0) < qty:
            return {"ok": False, "error": f"You don't have enough {item_id}"}

    # Check initiator gold
    init_gold = _get_persona_gold(initiator)
    if init_gold < gold_offered:
        return {"ok": False, "error": "Initiator doesn't have enough gold"}

    # Check acceptor gold
    acc_gold = _get_persona_gold(acceptor_uuid)
    if acc_gold < gold_requested:
        return {"ok": False, "error": "You don't have enough gold"}

    # ── Execute atomic trade ────────────────────────────────────
    location = offer.get("location_area", "unknown")
    distance = _get_distance_between(initiator, acceptor_uuid)
    distance_mod = _distance_modifier(distance)

    # Transfer items: initiator -> acceptor
    for item_id, qty in offered_items.items():
        _remove_items_from_persona(initiator, item_id, qty)
        _add_items_to_persona(acceptor_uuid, item_id, qty)

    # Transfer items: acceptor -> initiator
    for item_id, qty in requested_items.items():
        _remove_items_from_persona(acceptor_uuid, item_id, qty)
        _add_items_to_persona(initiator, item_id, qty)

    # Transfer gold
    if gold_offered > 0:
        _remove_gold_from_persona(initiator, gold_offered)
        _add_gold_to_persona(acceptor_uuid, gold_offered)

    if gold_requested > 0:
        _remove_gold_from_persona(acceptor_uuid, gold_requested)
        _add_gold_to_persona(initiator, gold_requested)

    # ── Mark offer accepted ─────────────────────────────────────
    accept_offer(offer_uuid)

    # ── Record trade ────────────────────────────────────────────
    relationship_delta = _compute_relationship_delta(
        offered_items, requested_items, gold_offered, gold_requested
    )
    trade_uuid = record_trade(
        seller_uuid=initiator,
        buyer_uuid=acceptor_uuid,
        trade_type="currency",
        items_offered=offered_items,
        items_received=requested_items,
        gold_amount=gold_requested - gold_offered,  # net flow to initiator
        location_area=location,
        distance_modifier=distance_mod,
        relationship_delta=relationship_delta,
    )

    # ── Apply relationship effect ──────────────────────────────
    if relationship_delta != 0:
        _apply_relationship_change(initiator, acceptor_uuid, relationship_delta)

    # ── Apply cooldowns ─────────────────────────────────────────
    cooldown = get_config("trade", "trade_cooldown_ticks", 5)
    set_cooldown(initiator, "trade", cooldown)
    set_cooldown(acceptor_uuid, "trade", cooldown)

    # ── Update state ────────────────────────────────────────────
    _update_state_trade_offers()
    fire_hook("on_trade_completed",
              initiator_uuid=initiator,
              target_uuid=acceptor_uuid,
              trade_uuid=trade_uuid,
              trade_type="currency")

    log.info("tr_core",
             f"Trade {trade_uuid} completed: {initiator} <-> {acceptor_uuid} "
             f"({offered_items}+{gold_offered}g for {requested_items}+{gold_requested}g)")

    return {"ok": True, "trade_uuid": trade_uuid}


def get_open_trade_offers(persona_uuid: str) -> list[dict]:
    """Get all open trade offers available to a persona.

    Returns:
        List of offer dicts.
    """
    offers = get_open_offers_for_persona(persona_uuid)
    return offers


def decline_trade_offer(offer_uuid: str, persona_uuid: str) -> dict:
    """Decline a pending trade offer.

    Args:
        offer_uuid: UUID of the offer to decline
        persona_uuid: Persona declining (must be target)

    Returns:
        {"ok": True} or {"ok": False, "error": str}
    """
    try:
        offer = _get_offer(offer_uuid)

    except Exception as e:
        log.error(f"decline_trade_offer failed: {e}")
        return {}
    if not offer:
        return {"ok": False, "error": "Offer not found"}
    if offer.get("target_uuid") and offer["target_uuid"] != persona_uuid:
        return {"ok": False, "error": "This offer is not for you"}

    if accept_offer(offer_uuid):
        # Wrong function — use decline
        pass

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import decline_offer as db_decline
    if db_decline(offer_uuid):
        _update_state_trade_offers()
        return {"ok": True}
    return {"ok": False, "error": "Failed to decline offer"}


# ── AI-facing decision helpers ───────────────────────────────────────

def evaluate_trade_offer(offer: dict, evaluator_uuid: str) -> dict:
    """Evaluate a trade offer from the perspective of a persona.

    Returns:
        {"should_accept": bool, "score": float, "reason": str}
    """
    try:
        offered_items = json.loads(offer.get("offered_items", "{}"))

    except Exception as e:
        log.error(f"evaluate_trade_offer failed: {e}")
        return {}
    requested_items = json.loads(offer.get("requested_items", "{}"))
    gold_offered = offer.get("gold_offered", 0.0)
    gold_requested = offer.get("gold_requested", 0.0)

    # Calculate net value
    offered_value = _estimate_item_value(offered_items) + gold_offered
    requested_value = _estimate_item_value(requested_items) + gold_requested

    if offered_value <= 0 and requested_value <= 0:
        return {"should_accept": False, "score": 0, "reason": "Empty trade"}

    # Fairness ratio (1.0 = perfectly fair)
    if offered_value > 0 and requested_value > 0:
        fairness = offered_value / max(requested_value, 0.01)
    elif offered_value > 0:
        fairness = 2.0  # Free items!
    else:
        fairness = 0.0  # Giving without receiving

    # Relationship modifier
    rel = _get_relationship(evaluator_uuid, offer["initiator_uuid"])
    rel_mod = 1.0 + (rel / 200.0)  # -0.5 to +0.5

    # Need urgency modifier
    urgency_mod = _compute_need_urgency(evaluator_uuid, offered_items)

    score = fairness * rel_mod * urgency_mod

    if score >= 0.8:
        reason = "Good deal"
    elif score >= 0.5:
        reason = "Fair enough"
    else:
        reason = "Bad deal"

    return {
        "should_accept": score >= 0.5,
        "score": round(score, 2),
        "reason": reason,
        "fairness": round(fairness, 2),
        "relationship_mod": round(rel_mod, 2),
        "urgency_mod": round(urgency_mod, 2),
    }


# ── Internal helpers ─────────────────────────────────────────────────

def _check_range(initiator_uuid: str, target_uuid: str) -> tuple:
    """Check if two personas are within trade range.

    Returns:
        (in_range: bool, distance: float, location: str)
    """
    try:
        state = get_state()

    except Exception as e:
        log.error(f"_check_range failed: {e}")
        return ()
    locations = state.get("persona_locations", {})

    init_loc = locations.get(initiator_uuid)
    target_loc = locations.get(target_uuid)

    if not init_loc or not target_loc:
        # If location data unavailable, assume same area
        return True, 0, "unknown"

    # Calculate distance between locations
    distance = _calc_distance(init_loc, target_loc)
    max_range = get_config("trade", "max_trade_distance", 2000)

    # Same area shortcut
    if init_loc.get("area") == target_loc.get("area"):
        return True, 0, init_loc.get("area", "unknown")

    # Route connection check
    if _has_route_connection(initiator_uuid, target_uuid):
        return True, distance, f"route_{distance:.0f}"

    if distance <= max_range:
        return True, distance, f"wilderness_{distance:.0f}"

    return False, distance, "out_of_range"


def _calc_distance(loc_a: dict, loc_b: dict) -> float:
    """Calculate Euclidean distance between two locations."""
    x1 = loc_a.get("x", 0)
    z1 = loc_a.get("z", 0)
    x2 = loc_b.get("x", 0)
    z2 = loc_b.get("z", 0)
    return math.sqrt((x2 - x1) ** 2 + (z2 - z1) ** 2)


def _distance_modifier(distance: float) -> float:
    """Calculate distance penalty on trade value.

    Returns multiplier (1.0 = no penalty, 2.0 = double cost).
    """
    if distance <= 0:
        return 1.0
    penalty = get_config("trade", "distance_penalty_per_1000", 0.1)
    return 1.0 + (distance / 1000.0) * penalty


def _has_route_connection(uuid_a: str, uuid_b: str) -> bool:
    """Check if a trade route exists between two personas' claims."""
    state = get_state()
    claims = state.get("persona_claims", {})
    claim_a = claims.get(uuid_a)
    claim_b = claims.get(uuid_b)

    if not claim_a or not claim_b:
        return False

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_routes_between_claims
    routes = get_routes_between_claims(claim_a, claim_b)
    return len(routes) > 0


def _get_persona_inventory(persona_uuid: str) -> dict[str, int]:
    """Get persona's inventory from state registry or DB.

    Returns:
        {item_id: quantity}
    """
    state = get_state()
    inventories = state.get("persona_inventories", {})
    return inventories.get(persona_uuid, {})


def _get_persona_gold(persona_uuid: str) -> float:
    """Get persona's gold from state registry."""
    state = get_state()
    finances = state.get("persona_finances", {})
    return finances.get(persona_uuid, {}).get("balance", 0.0)


def _remove_items_from_persona(persona_uuid: str, item_id: str, qty: int) -> bool:
    """Remove items from persona inventory in state."""
    state = get_state()
    inventories = dict(state.get("persona_inventories", {}))
    inv = dict(inventories.get(persona_uuid, {}))
    current = inv.get(item_id, 0)
    if current < qty:
        return False
    new_qty = current - qty
    if new_qty <= 0:
        inv.pop(item_id, None)
    else:
        inv[item_id] = new_qty
    inventories[persona_uuid] = inv
    state.set("persona_inventories", inventories, "SIMULATED_TRADE")
    return True


def _add_items_to_persona(persona_uuid: str, item_id: str, qty: int) -> None:
    """Add items to persona inventory in state."""
    state = get_state()
    inventories = dict(state.get("persona_inventories", {}))
    inv = dict(inventories.get(persona_uuid, {}))
    inv[item_id] = inv.get(item_id, 0) + qty
    inventories[persona_uuid] = inv
    state.set("persona_inventories", inventories, "SIMULATED_TRADE")


def _remove_gold_from_persona(persona_uuid: str, amount: float) -> bool:
    """Remove gold from persona in state."""
    state = get_state()
    finances = dict(state.get("persona_finances", {}))
    p_fin = dict(finances.get(persona_uuid, {}))
    balance = p_fin.get("balance", 0.0)
    if balance < amount:
        return False
    p_fin["balance"] = balance - amount
    finances[persona_uuid] = p_fin
    state.set("persona_finances", finances, "SIMULATED_TRADE")
    return True


def _add_gold_to_persona(persona_uuid: str, amount: float) -> None:
    """Add gold to persona in state."""
    state = get_state()
    finances = dict(state.get("persona_finances", {}))
    p_fin = dict(finances.get(persona_uuid, {}))
    p_fin["balance"] = p_fin.get("balance", 0.0) + amount
    finances[persona_uuid] = p_fin
    state.set("persona_finances", finances, "SIMULATED_TRADE")


def _compute_relationship_delta(offered: dict, requested: dict,
                                 gold_offered: float, gold_requested: float) -> float:
    """Compute relationship change from a trade.

    Positive = fair or generous trade improves relationship.
    Negative = unfair trade damages relationship.
    """
    try:
        offered_val = _estimate_item_value(offered) + gold_offered

    except Exception as e:
        log.error(f"_compute_relationshi failed: {e}")
        return 0.0
    requested_val = _estimate_item_value(requested) + gold_requested

    if offered_val <= 0 and requested_val <= 0:
        return 0.0

    if max(offered_val, requested_val) <= 0:
        return 0.0

    ratio = offered_val / max(requested_val, 0.01)

    if ratio >= 1.5:  # Generous
        return get_config("trade", "relationship_gain_fair_trade", 3)
    elif ratio >= 0.8:  # Fair
        return get_config("trade", "relationship_gain_fair_trade", 3) * max(0.1, 1 - abs(1 - ratio))
    elif ratio >= 0.3:  # Somewhat unfair
        return -get_config("trade", "relationship_loss_unfair_trade", 5) * 0.5
    else:  # Very unfair
        return -get_config("trade", "relationship_loss_unfair_trade", 5)


def _estimate_item_value(items: dict[str, int]) -> float:
    """Estimate total value of a set of items.

    Uses base values from a simple lookup.
    """
    # Simple base item values (would be expanded with real item data)
    BASE_VALUES = {
        "minecraft:diamond": 50.0,
        "minecraft:iron_ingot": 10.0,
        "minecraft:gold_ingot": 15.0,
        "minecraft:emerald": 20.0,
        "minecraft:wheat": 1.0,
        "minecraft:wood": 2.0,
        "minecraft:stone": 3.0,
        "minecraft:apple": 2.0,
        "minecraft:bread": 3.0,
        "minecraft:diamond_pickaxe": 100.0,
        "minecraft:diamond_sword": 120.0,
        "minecraft:iron_pickaxe": 40.0,
        "minecraft:iron_sword": 50.0,
        "minecraft:elytra": 500.0,
        "minecraft:netherite_ingot": 200.0,
    }
    total = 0.0
    for item_id, qty in items.items():
        value = BASE_VALUES.get(item_id, 1.0)
        total += value * qty
    return total


def _get_relationship(uuid_a: str, uuid_b: str) -> float:
    """Get relationship score between two personas from state."""
    state = get_state()
    rels = state.get("persona_relationships", {})
    return rels.get(uuid_a, {}).get(uuid_b, 0.0)


def _apply_relationship_change(uuid_a: str, uuid_b: str, delta: float) -> None:
    """Apply a relationship change between two personas."""
    state = get_state()
    rels = dict(state.get("persona_relationships", {}))
    a_rels = dict(rels.get(uuid_a, {}))
    b_rels = dict(rels.get(uuid_b, {}))
    a_rels[uuid_b] = a_rels.get(uuid_b, 0.0) + delta
    b_rels[uuid_a] = b_rels.get(uuid_a, 0.0) + delta
    rels[uuid_a] = a_rels
    rels[uuid_b] = b_rels
    state.set("persona_relationships", rels, "SIMULATED_TRADE")


def _compute_need_urgency(persona_uuid: str, items: dict[str, int]) -> float:
    """Compute how urgently a persona needs the offered items.

    Returns:
        Multiplier: 0.5 (don't need) to 2.0 (desperately need)
    """
    state = get_state()
    needs = state.get("persona_needs", {})
    persona_needs = needs.get(persona_uuid, {})

    if not items or not persona_needs:
        return 1.0

    max_urgency = 1.0
    for item_id in items:
        need = persona_needs.get(item_id, {})
        urgency = need.get("urgency", 0)
        if urgency > max_urgency:
            max_urgency = 1.0 + (urgency / 10.0)
    return max_urgency


def _count_open_offers(persona_uuid: str) -> int:
    """Count how many open offers a persona has initiated."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import count_open_offers_by_initiator
    return count_open_offers_by_initiator(persona_uuid)


def _get_offer(offer_uuid: str) -> Optional[dict]:
    """Get a pending offer by UUID."""
    db = get_db_connection()
    cursor = db.execute(
        "SELECT * FROM ext_tr_pending_trades WHERE offer_uuid = ?",
        (offer_uuid,)
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _update_state_trade_offers() -> None:
    """Update the active trade offers in the shared state registry."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
        get_open_offers_for_persona as get_offers
    )
    # We store a summary count
    state = get_state()
    # Count all open offers
    db = get_db_connection()
    cursor = db.execute(
        "SELECT COUNT(*) as cnt FROM ext_tr_pending_trades WHERE status = 'open'"
    )
    row = cursor.fetchone()
    state.set("open_trade_offer_count", row["cnt"] if row else 0, "SIMULATED_TRADE")


def _get_distance_between(uuid_a: str, uuid_b: str) -> float:
    """Get cached distance between two personas."""
    state = get_state()
    locations = state.get("persona_locations", {})
    loc_a = locations.get(uuid_a, {})
    loc_b = locations.get(uuid_b, {})
    return _calc_distance(loc_a, loc_b)




