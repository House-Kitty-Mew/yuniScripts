"""
tr_ai_engine.py — AI trade behavior decisions for SIMULATED_TRADE.

Persona AI decision-making for trade actions:
  - Evaluate whether to trade, barter, or wait
  - Choose trade partners
  - Determine fair prices and offers
  - Decide banditry targets (for criminal personas)
  - Integrate with tick system
"""

import json, random, math
from typing import Any, Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import fire_hook
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import (
    initiate_trade, evaluate_trade_offer,
    get_open_trade_offers,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_barter import (
    propose_barter, evaluate_barter_offer,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_routes import (
    construct_route, dispatch_caravan, upgrade_road, perform_maintenance,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_banditry import (
    attempt_banditry, get_banditry_risk,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_reputation import (
    get_persona_reputation, is_guard_hostile,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    is_persona_jailed, get_open_offers_for_persona,
    get_routes_for_persona, get_reputation,
)

log = get_logger()


def make_trade_decisions(cycle_data: dict) -> list[dict]:
    """Make trade decisions for all active personas for this tick.

    This is the main AI entry point called each simulation cycle.

    Args:
        cycle_data: Dict with current world state
            Must contain 'active_personas' list

    Returns:
        List of action results.
    """
    try:
        active_personas = cycle_data.get("active_personas", [])

    except Exception as e:
        log.error(f"make_trade_decisions failed: {e}")
        return []
    if not active_personas:
        return []

    results = []
    for persona in active_personas:
        persona_uuid = persona.get("uuid") or persona.get("id") or persona.get("persona_uuid")
        if not persona_uuid:
            continue

        action = _decide_persona_action(persona_uuid, cycle_data)
        if action:
            result = _execute_action(persona_uuid, action)
            results.append({"persona": persona_uuid, "action": action, "result": result})

    if results:
        log.info("tr_ai_engine", f"AI decisions made for {len(results)}/{len(active_personas)} personas")

    return results


def _decide_persona_action(persona_uuid: str, cycle_data: dict) -> Optional[dict]:
    """Decide what trade action a persona should take.

    Decision tree:
    1. If in jail -> skip
    2. If on cooldown -> skip
    3. Check open offers -> accept good ones
    4. Check inventory surplus -> try to sell
    5. Check needs -> try to buy/barter
    6. Check criminal tendency -> consider banditry
    7. Check routes -> maintain or send caravan

    Returns:
        Action dict or None.
    """
    # Skip jailed personas
    if is_persona_jailed(persona_uuid):
        return None

    rep = get_persona_reputation(persona_uuid)
    personality = _get_personality(persona_uuid)
    location = cycle_data.get("persona_locations", {}).get(persona_uuid, {})
    inventories = cycle_data.get("persona_inventories", {}).get(persona_uuid, {})
    needs = cycle_data.get("persona_needs", {}).get(persona_uuid, {})

    # 1. Check pending offers (respond to received offers)
    offers = get_open_offers_for_persona(persona_uuid)
    for offer in offers:
        if offer.get("initiator_uuid") == persona_uuid:
            continue
        if offer.get("offer_type") == "barter":
            evaluation = evaluate_barter_offer(offer, persona_uuid)
        else:
            evaluation = evaluate_trade_offer(offer, persona_uuid)

        if evaluation.get("should_accept"):
            return {
                "type": "accept_offer",
                "offer_uuid": offer["offer_uuid"],
                "evaluation": evaluation,
            }

    # 2. Check if has surplus to offer
    surplus = _find_surplus_items(inventories, personality)
    if surplus and random.random() < personality.get("trade_willingness", 0.5):
        # Find a trading partner
        partner = _find_trade_partner(persona_uuid, cycle_data, surplus)
        if partner:
            # Figure out what we want
            wanted = _find_wanted_items(persona_uuid, needs, inventories, personality)
            if wanted:
                return {
                    "type": "initiate_trade",
                    "target_uuid": partner,
                    "offered_items": surplus,
                    "requested_items": wanted,
                    "gold_offered": 0.0,
                    "gold_requested": 0.0,
                }

    # 3. Check if we have needs to fill
    if needs and random.random() < personality.get("need_urgency_factor", 0.3):
        # Try to buy or barter for needed items
        needed_items = {k: v.get("quantity", 1) for k, v in needs.items()
                        if v.get("urgency", 0) > 5}
        if needed_items:
            return {
                "type": "fulfill_needs",
                "needed_items": needed_items,
            }

    # 4. Criminal personas consider banditry
    criminal_tendency = personality.get("criminal_tendency", 0.0)
    if criminal_tendency > 0.6 and random.random() < criminal_tendency:
        rep_data = get_reputation(persona_uuid)
        if rep_data and not is_guard_hostile(persona_uuid):
            target_route = _find_banditry_target(persona_uuid, cycle_data)
            if target_route:
                risk = get_banditry_risk(target_route, persona_uuid)
                if risk.get("victory_chance", 0) > 0.4:
                    return {
                        "type": "banditry",
                        "route_uuid": target_route,
                        "risk": risk,
                    }

    # 5. Route owners maintain or send caravans
    routes = get_routes_for_persona(persona_uuid)
    if routes:
        return {
            "type": "manage_routes",
            "routes": routes,
        }

    return None  # No action this tick


def _execute_action(persona_uuid: str, action: dict) -> dict:
    """Execute a decided action.

    Args:
        persona_uuid: The persona
        action: Action dict from _decide_persona_action

    Returns:
        Result of the action.
    """
    try:
        action_type = action.get("type")

    except Exception as e:
        log.error(f"_execute_action failed: {e}")
        return {}

    if action_type == "accept_offer":
        offer_uuid = action["offer_uuid"]
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import accept_trade
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_barter import accept_barter

        # Check offer type
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db
        db = get_db()
        cursor = db.execute(
            "SELECT offer_type FROM ext_tr_pending_trades WHERE offer_uuid = ?",
            (offer_uuid,)
        )
        row = cursor.fetchone()
        if row and row["offer_type"] == "barter":
            return accept_barter(offer_uuid, persona_uuid)
        else:
            return accept_trade(offer_uuid, persona_uuid)

    elif action_type == "initiate_trade":
        return initiate_trade(
            initiator_uuid=persona_uuid,
            target_uuid=action["target_uuid"],
            offered_items=action.get("offered_items", {}),
            gold_offered=action.get("gold_offered", 0.0),
            requested_items=action.get("requested_items", {}),
            gold_requested=action.get("gold_requested", 0.0),
        )

    elif action_type == "initiate_barter":
        return propose_barter(
            initiator_uuid=persona_uuid,
            target_uuid=action["target_uuid"],
            offered_items=action.get("offered_items", {}),
            requested_items=action.get("requested_items", {}),
        )

    elif action_type == "banditry":
        return attempt_banditry(
            attacker_uuid=persona_uuid,
            route_uuid=action["route_uuid"],
        )

    elif action_type == "manage_routes":
        routes = action.get("routes", [])
        results = []
        for route in routes:
            if random.random() < 0.1:  # 10% chance per route per tick
                result = perform_maintenance(route["route_uuid"], persona_uuid)
                results.append({"action": "maintain", "route": route["route_uuid"], "result": result})
        return {"actions_taken": results}

    elif action_type == "fulfill_needs":
        # Try to find best way to fulfill needs
        needed = action.get("needed_items", {})
        return {"note": "needs_identified", "items": needed}

    return {"error": f"Unknown action type: {action_type}"}


def _find_surplus_items(inventory: dict, personality: dict) -> dict[str, int]:
    """Find items in inventory that the persona can spare.

    Returns:
        {item_id: quantity} of surplus items.
    """
    surplus = {}
    for item_id, qty in inventory.items():
        if qty > 1 and random.random() < personality.get("generosity", 0.3):
            # Offer up to half of surplus
            offer_qty = max(1, qty // 2)
            surplus[item_id] = offer_qty
    return surplus


def _find_wanted_items(persona_uuid: str, needs: dict,
                        inventory: dict, personality: dict) -> dict[str, int]:
    """Find items the persona wants to acquire.

    Returns:
        {item_id: quantity} of wanted items.
    """
    wanted = {}
    for item_id, need in needs.items():
        urgency = need.get("urgency", 0)
        if urgency > 5:  # Only pursue urgent needs
            qty_needed = need.get("quantity", 1) - need.get("quantity_obtained", 0)
            have = inventory.get(item_id, 0)
            want = max(1, qty_needed - have)
            wanted[item_id] = want
    return wanted


def _find_trade_partner(persona_uuid: str, cycle_data: dict,
                         surplus: dict[str, int]) -> Optional[str]:
    """Find the best persona to trade with.

    Considers:
    - Range/distance
    - Relationship
    - Who needs our surplus items

    Returns:
        Persona UUID, or None.
    """
    try:
        locations = cycle_data.get("persona_locations", {})

    except Exception as e:
        log.error(f"_find_trade_partner failed: {e}")
        return None
    needs = cycle_data.get("persona_needs", {})
    relationships = cycle_data.get("persona_relationships", {})

    my_loc = locations.get(persona_uuid, {})
    if not my_loc:
        return None

    candidates = []
    for other_uuid, other_loc in locations.items():
        if other_uuid == persona_uuid:
            continue

        # Check range
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import _calc_distance
        dist = _calc_distance(my_loc, other_loc)
        max_range = get_config("trade", "max_trade_distance", 2000)
        if dist > max_range:
            continue

        # Check if they need our surplus
        their_needs = needs.get(other_uuid, {})
        need_score = 0
        for item_id in surplus:
            need_urgency = their_needs.get(item_id, {}).get("urgency", 0)
            need_score += need_urgency

        if need_score == 0:
            continue

        # Relationship modifier
        rel = relationships.get(persona_uuid, {}).get(other_uuid, 0)

        candidates.append({
            "uuid": other_uuid,
            "score": need_score + max(0, rel) / 10.0 - dist / 1000.0,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]["uuid"]


def _find_banditry_target(persona_uuid: str, cycle_data: dict) -> Optional[str]:
    """Find a good banditry target route.

    Returns:
        Route UUID, or None.
    """
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db

    except Exception as e:
        log.error(f"_find_banditry_targe failed: {e}")
        return None
    db = get_db()

    # Find routes with active caravans and low defense
    cursor = db.execute("""
        SELECT r.route_uuid, r.guard_combat_power, r.bandit_activity,
               r.trade_volume
        FROM ext_tr_routes r
        WHERE r.is_active = 1
          AND r.bandit_activity < 0.5  -- Not already heavily bandit-invested
        ORDER BY r.trade_volume DESC
        LIMIT 5
    """)

    routes = [dict(row) for row in cursor.fetchall()]
    if not routes:
        return None

    # Pick best target (high value, low defense)
    combat_skill = _get_combat_skill(persona_uuid)
    scored = []
    for route in routes:
        defense = route["guard_combat_power"] * (1 + route["bandit_activity"] * 2)
        if combat_skill * 1.3 > defense * 0.8:  # Wimmable
            score = route["trade_volume"] / max(defense, 1.0)
            scored.append((score, route["route_uuid"]))

    if not scored:
        return None

    scored.sort(reverse=True)
    return scored[0][1]


def _get_personality(persona_uuid: str) -> dict:
    """Get a persona's personality traits."""
    state = get_state()
    personas = state.get("persona_profiles", {})
    profile = personas.get(persona_uuid, {})

    traits_str = profile.get("personality_traits", "{}")
    if isinstance(traits_str, str):
        try:
            traits = json.loads(traits_str)
        except (json.JSONDecodeError, TypeError):
            traits = {}
    else:
        traits = traits_str or {}

    return {
        "trade_willingness": traits.get("trade_willingness", 0.5),
        "need_urgency_factor": traits.get("need_urgency_factor", 0.3),
        "criminal_tendency": traits.get("criminal_tendency", 0.0),
        "generosity": traits.get("generosity", 0.3),
        "risk_tolerance": traits.get("risk_tolerance", 0.3),
        "patience": traits.get("patience", 0.5),
    }


def _get_combat_skill(persona_uuid: str) -> float:
    """Get combat skill from state."""
    state = get_state()
    skills = state.get("persona_skills", {})
    return skills.get(persona_uuid, {}).get("combat", 10.0)


# ── Tick processing entry point ──────────────────────────────────────

def before_tick(**kwargs) -> None:
    """Called on_simulation_cycle_start.

    Prepares AI decisions for this tick.
    """
    cycle_data = kwargs.get("cycle_data") or {}
    state = get_state()

    # Collect current state
    cycle_data["active_personas"] = state.get("active_personas", [])
    cycle_data["persona_locations"] = state.get("persona_locations", {})
    cycle_data["persona_inventories"] = state.get("persona_inventories", {})
    cycle_data["persona_needs"] = state.get("persona_needs", {})
    cycle_data["persona_relationships"] = state.get("persona_relationships", {})

    # Store for after_tick
    state.set("cycle_data", cycle_data, "SIMULATED_TRADE")


def after_tick(**kwargs) -> None:
    """Called on_simulation_cycle_end.

    Processes AI trade decisions and applies results.
    """
    state = get_state()
    cycle_data = state.get("cycle_data", {})

    if not cycle_data:
        return

    decisions = make_trade_decisions(cycle_data)
    state.set("last_trade_decisions", decisions, "SIMULATED_TRADE")
    state.set("decision_count", len(decisions), "SIMULATED_TRADE")

    fire_hook("on_ai_trade_decisions_completed",
              decision_count=len(decisions))

    log.info("tr_ai_engine", f"AI trade decisions applied: {len(decisions)} actions taken")

