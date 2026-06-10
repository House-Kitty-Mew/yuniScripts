"""
tr_barter.py — Barter system for SIMULATED_TRADE extension.

Handles item-for-item exchanges without gold:
  - Value calculation with barter skill influence
  - Tolerance-based acceptance
  - AI decision making for barter proposals
  - Integration with trade engine
"""

import json, random, math
from typing import Any, Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import fire_hook
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    record_trade, create_pending_offer, check_cooldown,
    set_cooldown, is_persona_jailed,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import (
    _get_persona_inventory, _get_persona_gold,
    _remove_items_from_persona, _add_items_to_persona,
    _apply_relationship_change, _estimate_item_value,
    _check_range, _distance_modifier, get_db_connection,
)

log = get_logger()


def propose_barter(initiator_uuid: str, target_uuid: str,
                   offered_items: dict[str, int],
                   requested_items: dict[str, int]) -> dict:
    """Propose a barter exchange (no gold involved).

    Args:
        initiator_uuid: Persona making the barter offer
        target_uuid: Persona receiving the offer
        offered_items: {item_id: quantity} initiator is offering
        requested_items: {item_id: quantity} initiator wants

    Returns:
        {"ok": True, "offer_uuid": str} or {"ok": False, "error": str}
    """
    # ── Validation ──────────────────────────────────────────────
    if initiator_uuid == target_uuid:
        return {"ok": False, "error": "Cannot barter with yourself"}

    if is_persona_jailed(initiator_uuid):
        return {"ok": False, "error": "Initiator is in jail"}
    if is_persona_jailed(target_uuid):
        return {"ok": False, "error": "Target is in jail"}

    if check_cooldown(initiator_uuid, "barter"):
        return {"ok": False, "error": "Initiator is on barter cooldown"}
    if check_cooldown(target_uuid, "barter"):
        return {"ok": False, "error": "Target is on barter cooldown"}

    if not offered_items:
        return {"ok": False, "error": "Must offer items to barter"}
    if not requested_items:
        return {"ok": False, "error": "Must request items to barter"}

    range_ok, distance, location = _check_range(initiator_uuid, target_uuid)
    if not range_ok:
        return {"ok": False, "error": f"Personas out of range (distance: {distance:.0f})"}

    # ── Create barter offer ─────────────────────────────────────
    offer_uuid = create_pending_offer(
        initiator_uuid=initiator_uuid,
        target_uuid=target_uuid,
        offer_type="barter",
        offered_items=offered_items,
        requested_items=requested_items,
        gold_requested=0.0,
        gold_offered=0.0,
        expiry_ticks=get_config("trade", "offer_expiry_ticks", 100),
        location_area=location or "unknown",
    )

    if not offer_uuid:
        return {"ok": False, "error": "Failed to create barter offer"}

    log.info("tr_barter",
             f"Barter offer {offer_uuid}: {initiator_uuid} offers "
             f"{offered_items} for {requested_items}")

    return {"ok": True, "offer_uuid": offer_uuid}


def accept_barter(offer_uuid: str, acceptor_uuid: str) -> dict:
    """Accept a barter offer and execute the exchange atomically.

    Args:
        offer_uuid: UUID of the barter offer
        acceptor_uuid: Persona accepting

    Returns:
        {"ok": True, "trade_uuid": str} or {"ok": False, "error": str}
    """
    try:
        offer = _get_barter_offer(offer_uuid)

    except Exception as e:
        log.error(f"accept_barter failed: {e}")
        return {}
    if not offer:
        return {"ok": False, "error": "Offer not found"}
    if offer.get("status") != "open":
        return {"ok": False, "error": f"Offer is {offer['status']}"}
    if offer.get("target_uuid") and offer["target_uuid"] != acceptor_uuid:
        return {"ok": False, "error": "This barter is not for you"}

    initiator = offer["initiator_uuid"]
    offered_items = json.loads(offer.get("offered_items", "{}"))
    requested_items = json.loads(offer.get("requested_items", "{}"))

    # ── Barter fairness check ───────────────────────────────────
    init_barter_skill = _get_barter_skill(initiator)
    acc_barter_skill = _get_barter_skill(acceptor_uuid)
    tolerance = get_config("trade", "barter_tolerance_default", 0.15)
    skill_impact = get_config("trade", "barter_skill_impact", 0.005)

    # Value calculation with skill modifiers
    init_mod = 1.0 + (init_barter_skill - acc_barter_skill) * skill_impact
    acc_mod = 1.0 + (acc_barter_skill - init_barter_skill) * skill_impact

    offered_val = _estimate_item_value(offered_items) * init_mod
    requested_val = _estimate_item_value(requested_items) * acc_mod

    # Check fairness tolerance
    max_val = max(offered_val, requested_val)
    if max_val <= 0:
        return {"ok": False, "error": "Zero-value barter not allowed"}

    is_fair = abs(offered_val - requested_val) <= tolerance * max_val

    if not is_fair:
        return {
            "ok": False,
            "error": f"Barter too unbalanced: offered={offered_val:.1f}, requested={requested_val:.1f} (max diff {tolerance*100:.0f}%)",
            "offered_value": round(offered_val, 2),
            "requested_value": round(requested_val, 2),
            "max_tolerance": round(tolerance * 100, 0),
        }

    # ── Inventory checks ────────────────────────────────────────
    init_inv = _get_persona_inventory(initiator)
    acc_inv = _get_persona_inventory(acceptor_uuid)

    for item_id, qty in offered_items.items():
        if init_inv.get(item_id, 0) < qty:
            return {"ok": False, "error": f"Initiator doesn't have enough {item_id}"}

    for item_id, qty in requested_items.items():
        if acc_inv.get(item_id, 0) < qty:
            return {"ok": False, "error": f"You don't have enough {item_id}"}

    # ── Execute barter ──────────────────────────────────────────
    location = offer.get("location_area", "unknown")

    # Transfer items: initiator -> acceptor
    for item_id, qty in offered_items.items():
        _remove_items_from_persona(initiator, item_id, qty)
        _add_items_to_persona(acceptor_uuid, item_id, qty)

    # Transfer items: acceptor -> initiator
    for item_id, qty in requested_items.items():
        _remove_items_from_persona(acceptor_uuid, item_id, qty)
        _add_items_to_persona(initiator, item_id, qty)

    # ── Mark offer accepted ─────────────────────────────────────
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import accept_offer as db_accept
    db_accept(offer_uuid)

    # ── Record trade ────────────────────────────────────────────
    relationship_delta = 3.0 if is_fair else -2.0  # fair barter always good
    trade_uuid = record_trade(
        seller_uuid=initiator,
        buyer_uuid=acceptor_uuid,
        trade_type="barter",
        items_offered=offered_items,
        items_received=requested_items,
        gold_amount=0.0,
        location_area=location,
        barter_skill_used=(init_barter_skill + acc_barter_skill) / 2.0,
        relationship_delta=relationship_delta,
    )

    # ── Apply relationship change ──────────────────────────────
    if relationship_delta != 0:
        _apply_relationship_change(initiator, acceptor_uuid, relationship_delta)

    # ── Apply cooldowns ─────────────────────────────────────────
    cooldown = get_config("trade", "trade_cooldown_ticks", 5)
    set_cooldown(initiator, "barter", cooldown)
    set_cooldown(acceptor_uuid, "barter", cooldown)

    # ── Fire hooks ─────────────────────────────────────────────
    fire_hook("on_trade_completed",
              initiator_uuid=initiator,
              target_uuid=acceptor_uuid,
              trade_uuid=trade_uuid,
              trade_type="barter")

    log.info("tr_barter",
             f"Barter {trade_uuid}: {initiator} gave {offered_items}, "
             f"got {requested_items} from {acceptor_uuid} (fair={is_fair})")

    return {"ok": True, "trade_uuid": trade_uuid}


def evaluate_barter_offer(offer: dict, evaluator_uuid: str) -> dict:
    """Evaluate a barter offer from the perspective of a persona.

    Returns:
        {"should_accept": bool, "score": float, "reason": str, ...}
    """
    try:
        offered_items = json.loads(offer.get("offered_items", "{}"))

    except Exception as e:
        log.error(f"evaluate_barter_offe failed: {e}")
        return {}
    requested_items = json.loads(offer.get("requested_items", "{}"))
    initiator = offer["initiator_uuid"]
    init_skill = _get_barter_skill(initiator)
    eval_skill = _get_barter_skill(evaluator_uuid)
    skill_impact = get_config("trade", "barter_skill_impact", 0.005)

    init_mod = 1.0 + (init_skill - eval_skill) * skill_impact
    eval_mod = 1.0 + (eval_skill - init_skill) * skill_impact

    offered_val = _estimate_item_value(offered_items) * eval_mod
    requested_val = _estimate_item_value(requested_items) * init_mod

    max_val = max(offered_val, requested_val)
    if max_val <= 0:
        return {"should_accept": False, "score": 0, "reason": "Zero value items"}

    tolerance = get_config("trade", "barter_tolerance_default", 0.15)
    fairness = offered_val / max(requested_val, 0.01)
    is_fair = abs(offered_val - requested_val) <= tolerance * max_val

    # Relationship modifier
    rel = _get_relationship(evaluator_uuid, initiator)
    rel_mod = 1.0 + (rel / 200.0)

    # Need urgency for items being offered to us
    urgency_mod = _compute_need_urgency(evaluator_uuid, offered_items)

    # Desire for items we're giving away (low desire = more willing)
    desire_mod = _compute_desire_to_keep(evaluator_uuid, requested_items)

    score = fairness * rel_mod * urgency_mod / max(desire_mod, 0.5)

    if is_fair and score >= 0.8:
        reason = "Good barter deal"
    elif is_fair:
        reason = "Fair exchange"
    else:
        reason = "Unbalanced trade"

    return {
        "should_accept": is_fair and score >= 0.5,
        "score": round(score, 2),
        "reason": reason,
        "fairness": round(fairness, 2),
        "offered_value": round(offered_val, 2),
        "requested_value": round(requested_val, 2),
        "relationship_mod": round(rel_mod, 2),
        "urgency_mod": round(urgency_mod, 2),
    }


def _get_barter_skill(persona_uuid: str) -> float:
    """Get a persona's barter skill from state registry."""
    state = get_state()
    skills = state.get("persona_skills", {})
    p_skills = skills.get(persona_uuid, {})
    return p_skills.get("barter", 25.0)  # Default 25 if not found


def _get_relationship(uuid_a: str, uuid_b: str) -> float:
    """Get relationship score between two personas."""
    state = get_state()
    rels = state.get("persona_relationships", {})
    return rels.get(uuid_a, {}).get(uuid_b, 0.0)


def _compute_need_urgency(persona_uuid: str, items: dict[str, int]) -> float:
    """Compute how urgently a persona needs offered items."""
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


def _compute_desire_to_keep(persona_uuid: str, items: dict[str, int]) -> float:
    """Compute how much a persona wants to keep the requested items.

    Returns multiplier: <1 = willing to give, >1 = reluctant.
    """
    state = get_state()
    needs = state.get("persona_needs", {})
    persona_needs = needs.get(persona_uuid, {})
    if not items or not persona_needs:
        return 1.0
    max_desire = 0.5
    for item_id in items:
        need = persona_needs.get(item_id, {})
        urgency = need.get("urgency", 0)
        if urgency > 0:
            max_desire = 0.5 + (urgency / 5.0)
    return max_desire


def _get_barter_offer(offer_uuid: str) -> Optional[dict]:
    """Get a barter offer by UUID."""
    db = get_db_connection()
    cursor = db.execute(
        "SELECT * FROM ext_tr_pending_trades WHERE offer_uuid = ? AND offer_type = 'barter'",
        (offer_uuid,)
    )
    row = cursor.fetchone()
    return dict(row) if row else None

