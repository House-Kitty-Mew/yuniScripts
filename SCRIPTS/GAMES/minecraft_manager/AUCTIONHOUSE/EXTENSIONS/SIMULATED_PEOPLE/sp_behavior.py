"""
sp_behavior.py — Per-persona decision engine.

Each active persona independently evaluates the AH marketplace on every
simulation tick and decides whether to:

  1. Buy a specific item from listings (player or simulated)
  2. Wait (save money, prices too high)
  3. Sell an owned item (if they have excess stock)
  4. Do nothing (satisfied or broke)

Decisions are based on:
  - AI-powered reasoning (thinking mode) when enabled — full context analysis
  - Fallback: personality archetype traits (spending impulse, price sensitivity)
  - Current financial state (balance, savings goal, debt)
  - Active needs (urgency, max price)
  - Memory (observed prices — won't overpay if they remember a lower price)
  - Active life events (mood override, financial windfall/crisis)
  - World events (regional/global economic conditions)
  - Player bonus (player-listed items get preference)
"""

import random, json, math
from datetime import datetime, timezone
from typing import Optional
from AUCTIONHOUSE.ah_database import get_db as ah_get_db
from AUCTIONHOUSE.ah_logger import get_logger

from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_active_personas, get_needs, get_active_life_events,
    get_price_memories, add_memory, fulfill_need, update_finance,
    record_transaction, get_active_world_events,
    get_personas_by_region,
)

# ── THREAD-enhanced memory (optional, graceful degradation) ─────────
try:
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_memory_thread import (
        thread_recall_memories as _thread_recall_memories,
        is_thread_available as _thread_available,
    )
    _HAS_THREAD = True
except ImportError:
    _HAS_THREAD = False
    log.info('sp_behavior: THREAD memory not available, using legacy memory')
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import (
    ARCHETYPES, deactivate_persona
)

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _get_listings_for_purchase(persona_balance: float,
                                persona_region: str) -> list[dict]:
    """Get active listings that the persona might be interested in.

    Includes both player and simulated listings.
    """
    db = ah_get_db()
    return db.fetch_all("""
        SELECT * FROM auction_listings
        WHERE status = 'active'
          AND (buy_now_price IS NOT NULL OR current_bid IS NOT NULL)
          AND (buy_now_price <= ? OR (buy_now_price IS NULL AND start_price <= ?))
        ORDER BY RANDOM()
        LIMIT 50
    """, (persona_balance * 0.5, persona_balance * 0.5))


def _has_justified_price(persona_uuid: str, item_id: str,
                         current_price: float, price_sensitivity: float) -> bool:
    """Check if a price is acceptable based on memory and sensitivity.

    If the persona remembers buying this item for less, they're less
    likely to pay a higher price.  If they remember it being more
    expensive, they consider it a deal.
    """
    try:
        memories = get_price_memories(persona_uuid, item_id, limit=5)

    except Exception as e:
        log.error(f"_has_justified_price failed: {e}")
        return False
    if not memories:
        return True  # No reference — assume OK

    # Calculate average remembered price
    prices = [m["price"] for m in memories if m["price"] is not None]
    if not prices:
        return True

    avg_memory = sum(prices) / len(prices)

    # Price sensitivity threshold: higher sensitivity = less tolerance for markup
    tolerance = avg_memory * (1 + (1 - price_sensitivity) * 1.5)

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config
    cfg = get_config()
    memory_weight = cfg["price_memory_weight"]

    # Weighted: persona pays attention to memory proportionally to weight
    if current_price > tolerance * (1 + memory_weight):
        return False  # Too expensive based on memory

    return True

def _has_justified_price_thread(persona_uuid: str, item_id: str,
                                current_price: float, price_sensitivity: float) -> bool:
    """THREAD-enhanced price justification using activation-weighted memory.

    Uses hybrid retrieval (lexical + graph activation) to find the most
    relevant price memories. Recent/important memories get higher weight
    in the average calculation via their activation scores.

    Falls back to legacy _has_justified_price if THREAD unavailable.
    """
    if not _HAS_THREAD or not _thread_available():
        return _has_justified_price(persona_uuid, item_id, current_price, price_sensitivity)

    try:
        thread_memories = _thread_recall_memories(
            persona_uuid=persona_uuid,
            query=f"item={item_id} price",
            max_results=10,
            memory_type_filter=None,
        )

        if not thread_memories or len(thread_memories) == 0:
            return _has_justified_price(persona_uuid, item_id, current_price, price_sensitivity)

        # Activation-weighted average price
        total_weight = 0.0
        weighted_sum = 0.0
        for m in thread_memories:
            price = m.get('price')
            if price is not None:
                # Use activation_score as weight (at least 0.1)
                weight = max(0.1, m.get('activation_score', 0.5))
                weighted_sum += price * weight
                total_weight += weight

        if total_weight == 0:
            return True

        avg_memory = weighted_sum / total_weight

        # Price sensitivity threshold: higher sensitivity = less tolerance for markup
        tolerance = avg_memory * (1 + (1 - price_sensitivity) * 1.5)

        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config
        cfg = get_config()
        memory_weight = cfg.get('price_memory_weight', 0.6)

        if current_price > tolerance * (1 + memory_weight):
            return False

        return True

    except Exception:
        # Fallback on any error
        return _has_justified_price(persona_uuid, item_id, current_price, price_sensitivity)




def _mood_multiplier(life_events: list[dict]) -> float:
    """Calculate a spending multiplier from active life events.

    Happy events → spend more.  Stressful/cautious → spend less.
    """
    mult = 1.0
    for evt in life_events:
        mood = evt.get("mood_impact", "neutral")
        if mood == "happy":
            mult *= 1.3
        elif mood == "motivated":
            mult *= 1.15
        elif mood == "stressed":
            mult *= 0.7
        elif mood == "cautious":
            mult *= 0.5
    return mult


def _world_event_multiplier(region: str) -> float:
    """Calculate financial multiplier from active world events."""
    events = get_active_world_events(region)
    mult = 1.0
    for evt in events:
        mult *= evt.get("financial_multiplier", 1.0)
    return mult


# ══════════════════════════════════════════════════════════════════════
# AI-Powered Persona Decision Engine (thinking mode)
# ══════════════════════════════════════════════════════════════════════

_AI_PERSONA_SYSTEM_PROMPT = """You are the decision engine for a simulated Minecraft villager (persona). You receive full context about the persona and must decide what action they take this simulation tick.

Available actions:
- "bought": Purchase an item from the Auction House to fulfill a need
- "saved": Add income to savings, don't spend
- "debt_payment": Pay off existing debt
- "nothing": Do nothing special this tick

Decision rules:
1. If the persona has debt >= 10%% of balance, pay it off first
2. If balance is below savings goal, favor saving
3. If there are urgent needs (urgency >= 7), try to buy the most urgent one
4. Archetype influences: adventurers/vagabonds are impulsive, merchants/miners are thrifty
5. Consider health: low hunger/thirst means the persona may need to rest or forage
6. World events affect income - during recessions, save more
7. Weather extremes cause personas to stay home (no market activity)
8. If no good opportunities, do nothing and save income

Your response must be valid JSON only. No markdown, no extra text."""


def _ai_build_persona_context(persona: dict) -> str:
    """Build a detailed context prompt for the AI about this persona.

    Includes: profile, finances, health, needs, life events, memories,
    current area, weather, world events, and active market listings.
    """
    puid = persona["persona_uuid"]
    parts = []

    # Profile
    parts.append(f"Persona: {persona.get('name', '?')} ({persona.get('archetype', '?')})")
    parts.append(f"Wealth Tier: {persona.get('wealth_tier', '?')}")
    parts.append(f"Region: {persona.get('region', '?')}")
    parts.append(f"Balance: {persona.get('balance', 0):.1f}em")
    parts.append(f"Savings Goal: {persona.get('savings_goal', 0):.1f}em")
    parts.append(f"Debt: {persona.get('debt', 0):.1f}em")
    parts.append(f"Income/Tick: {persona.get('income_per_tick', 0):.1f}em")

    # Health
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
        h = get_persona_health(puid)
        if h:
            parts.append(f"Health: food={h['food']}/100, hydration={h['hydration']}/100, energy={h['energy']}/100, temp={h['temperature']}")
    except Exception:
        pass

    # Needs
    needs = get_needs(puid, only_unfulfilled=True)
    if needs:
        parts.append("Active Needs:")
        for n in needs[:5]:
            parts.append(f"  - {n['item_id']} (urgency={n['urgency']}, max_price={n['max_price']}em, reason={n.get('reason','')})")

    # Life events
    try:
        events = get_active_life_events(puid)
        if events:
            parts.append("Active Life Events:")
            for evt in events[:3]:
                parts.append(f"  - {evt.get('event_type','')}: {evt.get('description','')} (mood={evt.get('mood_impact','neutral')})")
    except Exception:
        pass

    # World events
    try:
        w_events = get_active_world_events(persona.get('region', 'overworld'))
        if w_events:
            parts.append("World Events:")
            for we in w_events[:3]:
                parts.append(f"  - {we.get('name','')}: fin_mult={we.get('financial_multiplier',1.0)}, income_mult={we.get('income_multiplier',1.0)}")
    except Exception:
        pass

    # Current area
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area
        area = get_persona_area(puid)
        if area:
            parts.append(f"Current Area: {area['name']} ({area['biome_type']})")
            # Weather
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import get_area_weather
                w = get_area_weather(area['area_uuid'])
                if w:
                    parts.append(f"Weather: {w['temperature']}C, humidity={w['humidity']}%, rain={'yes' if w.get('is_raining') else 'no'}, wind={w.get('wind_speed',0)}m/s")
            except Exception:
                pass
    except Exception:
        pass

    # Active market listings (affordable)
    cfg = get_config()
    if persona.get('balance', 0) >= cfg.get('min_balance_for_purchase', 5):
        try:
            from AUCTIONHOUSE.ah_database import get_db as ah_db
            listings = ah_db().fetch_all("""
                SELECT item_id, buy_now_price, current_bid, start_price, seller_name
                FROM auction_listings
                WHERE status = 'active' AND (buy_now_price IS NOT NULL OR current_bid IS NOT NULL)
                  AND (buy_now_price <= ? OR current_bid <= ? OR start_price <= ?)
                ORDER BY buy_now_price ASC, start_price ASC LIMIT 10
            """, (persona.get('balance', 0), persona.get('balance', 0), persona.get('balance', 0)))
            if listings:
                parts.append("Available Listings (affordable):")
                for li in listings[:5]:
                    price = li['buy_now_price'] or li['current_bid'] or li['start_price']
                    parts.append(f"  - {li['item_id']} @ {price}em by {li['seller_name']}")
        except Exception:
            pass

    return "\n".join(parts)


def _ai_process_persona(persona: dict) -> Optional[dict]:
    """Use DeepSeek AI (thinking mode) to decide what a persona does this tick.

    Returns the same action dict as process_persona().
    Falls back to procedural logic on API failure.
    """
    from AUCTIONHOUSE.ah_ai_engine import _call_deepseek

    try:
        context = _ai_build_persona_context(persona)
        prompt = f"Decide what {persona.get('name','?')} does this tick:\n\n{context}\n\nRespond with a JSON action."

        response = _call_deepseek(prompt, system_prompt=_AI_PERSONA_SYSTEM_PROMPT)
        if response is not None:
            log.info("sp_behavior", f"AI decision for {persona.get('name','?')}: {json.dumps(response)[:200]}")
            return response
    except Exception as e:
        log.warn("sp_behavior", f"AI decision failed for {persona.get('name','?')}: {e}")

    return None  # Signal fallback


# ══════════════════════════════════════════════════════════════════════
# Per-persona decision
# ══════════════════════════════════════════════════════════════════════

def process_persona(persona: dict) -> dict:
    """Run one decision cycle for a single persona.

    Uses AI (thinking mode) by default with procedural fallback.
    If AI is disabled or the API is unavailable, uses the archetype-based
    procedural decision engine.

    Args:
        persona: Persona dict from get_active_personas()

    Returns:
        Dict describing what the persona did:
          {"action": "bought"|"waited"|"saved"|"nothing", "detail": ...}
    """
    cfg = get_config()
    archetype = persona["archetype"]
    traits_json = persona.get("personality_traits", "{}")
    traits = json.loads(traits_json) if isinstance(traits_json, str) else traits_json
    archer_data = ARCHETYPES.get(archetype, ARCHETYPES["vagabond"])

    balance = persona.get("balance", 0) or 0
    savings_goal = persona.get("savings_goal", 0) or 0
    impulse = archer_data["spending_impulse"]
    price_sens = archer_data["price_sensitivity"]
    needs = get_needs(persona["persona_uuid"], only_unfulfilled=True)
    life_events = get_active_life_events(persona["persona_uuid"])

    mood = _mood_multiplier(life_events)
    world_mult = _world_event_multiplier(persona["region"])
    effective_balance = balance * world_mult

    # ── AI-Powered Decision (ON by default, falls back to procedural) ──
    try:
        if cfg.get("ai_persona_decision", True):  # Default: ON
            ai_result = _ai_process_persona(persona)
            if ai_result:
                action = ai_result.get("action", "nothing")
                if action == "bought":
                    item_to_buy = ai_result.get("item_to_buy", "")
                    max_price = ai_result.get("max_price", 0)
                    if item_to_buy and max_price > 0:
                        listing = _find_best_listing(item_to_buy, max_price, effective_balance * 0.8)
                        if listing:
                            price = listing["buy_now_price"] or listing["current_bid"] or listing["start_price"]
                            _execute_purchase(persona, listing, price, None,
                                              reason=f"AI: {ai_result.get('reasoning', '')[:80]}")
                            return {"action": "bought", "item": item_to_buy, "price": price, "reason": "ai_decision"}
                elif action == "debt_payment":
                    debt = persona.get("debt", 0) or 0
                    payment = min(balance * 0.1, debt)
                    if payment > 0:
                        update_finance(persona["persona_uuid"], -payment, reason="debt_payment")
                        return {"action": "debt_payment", "amount": payment}
                elif action == "saved":
                    income = persona.get("income_per_tick", 0) or 0
                    income_boost = income * world_mult
                    if income_boost > 0:
                        update_finance(persona["persona_uuid"], income_boost, reason="income")
                    return {"action": "saved", "amount": income_boost}
                return {"action": "nothing"}
    except Exception as e:
        log.debug("sp_behavior", f"AI decision failed for {persona.get('name','?')}, falling back: {e}")

    # ── FALLBACK: Procedural decision engine ──────────────────────

    # 1. Pay off debt first (if any)
    debt = persona.get("debt", 0) or 0
    if debt > 0 and balance >= debt * 0.1:
        debt_payment = min(balance * 0.1, debt)
        update_finance(persona["persona_uuid"], -debt_payment, reason="debt_payment")
        add_memory(persona["persona_uuid"], "life_event", detail={
            "event": "debt_payment", "amount": debt_payment,
            "new_debt": debt - debt_payment
        })
        return {"action": "debt_payment", "amount": debt_payment}

    # 2. Save if under savings goal
    if effective_balance < savings_goal * 0.5 and random.random() < 0.4:
        income = persona.get("income_per_tick", 0) or 0
        income_boost = income * world_mult
        if income_boost > 0:
            update_finance(persona["persona_uuid"], income_boost, reason="income")
        return {"action": "saved", "balance": effective_balance + income_boost}

    # 3. Check urgent needs (urgency >= 7)
    urgent_needs = [n for n in needs if n["urgency"] >= 7]
    if urgent_needs:
        need = urgent_needs[0]
        item_id = need["item_id"]
        max_price = need["max_price"] * mood

        listing = _find_best_listing(item_id, max_price, effective_balance * 0.8)
        if listing:
            price = listing["buy_now_price"] or listing["current_bid"] or listing["start_price"]
            if price <= effective_balance * 0.8 and _has_justified_price_thread(
                    persona["persona_uuid"], item_id, price, price_sens):
                _execute_purchase(persona, listing, price, need,
                                  reason=f"Urgent: {need.get('reason', '')}")
                return {"action": "bought", "item": item_id, "price": price, "reason": "urgent_need"}

        # Couldn't find or afford — add memory of frustration
        add_memory(persona["persona_uuid"], "missed_deal", item_id=item_id,
                   price=max_price,
                   detail={"reason": "could_not_find", "urgency": need["urgency"]},
                   emotional_weight=need["urgency"])

        # ── Post urgent need to player board ─────────────────
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import post_board_need
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import get_item_def
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim
            sim_id = market_to_sim(item_id)
            item_def = get_item_def(sim_id)
            if item_def:
                display_name = item_def.get("name", item_id.split(":")[-1].replace("_", " ").title())
            else:
                display_name = item_id.split(":")[-1].replace("_", " ").title()
            post_board_need(
                persona_uuid=persona["persona_uuid"],
                persona_name=persona.get("name", "Someone"),
                item_id=item_id,
                quantity=need.get("desired_quantity", 1),
                max_price=max_price,
                urgency=need["urgency"],
                reason=need.get("reason", "needed urgently"))
        except Exception:
            pass

    # 4. Roll for impulse buy (based on archetype impulse)
    impulse_chance = impulse * mood * world_mult * 0.3
    if random.random() < impulse_chance and effective_balance >= cfg["min_balance_for_purchase"]:
        listings = _get_listings_for_purchase(effective_balance, persona["region"])
        if listings:
            listing = random.choice(listings)
            price = listing["buy_now_price"] or listing["current_bid"] or listing["start_price"]
            item_id = listing["item_id"].lower()
            prefs = archer_data["item_preferences"]
            pref_mult = 1.5 if any(p in item_id for p in prefs) else 1.0

            if price * pref_mult <= effective_balance * 0.5:
                if _has_justified_price_thread(persona["persona_uuid"], item_id, price, price_sens):
                    _execute_purchase(persona, listing, price, None,
                                      reason="Impulse buy!")
                    return {"action": "bought", "item": item_id, "price": price, "reason": "impulse"}

    # 5. Check non-urgent needs
    for need in needs:
        if need["urgency"] >= 4:
            item_id = need["item_id"]
            max_price = need["max_price"] * mood

            listing = _find_best_listing(item_id, max_price, effective_balance * 0.5)
            if listing:
                price = listing["buy_now_price"] or listing["current_bid"] or listing["start_price"]
                if price <= effective_balance * 0.5 and _has_justified_price_thread(
                        persona["persona_uuid"], item_id, price, price_sens):
                    _execute_purchase(persona, listing, price, need,
                                      reason=need.get("reason", ""))
                    return {"action": "bought", "item": item_id, "price": price, "reason": "need"}

    # 6. Nothing — credit income and save
    income = persona.get("income_per_tick", 0) or 0
    income_boost = income * world_mult
    if income_boost > 0:
        update_finance(persona["persona_uuid"], income_boost, reason="income")

    deactivation_chance = cfg.get("persona_deactivation_rate", 0.15)
    if random.random() < deactivation_chance * 0.1:
        deactivate_persona(persona["persona_uuid"])

    return {"action": "nothing", "saved": income_boost}


def _find_best_listing(item_id: str, max_price: float, max_budget: float
                       ) -> Optional[dict]:
    """Find the best listing for a specific item within budget.

    Prefers player listings over simulated ones.
    """
    db = ah_get_db()
    effective_max = min(max_price, max_budget)

    # Try player listings first
    player = db.fetch_one("""
        SELECT *, 'player' as source FROM auction_listings
        WHERE status = 'active'
          AND item_id = ?
          AND (buy_now_price IS NOT NULL OR current_bid IS NOT NULL)
          AND (buy_now_price <= ? OR current_bid <= ? OR start_price <= ?)
          AND is_simulated = 0
        ORDER BY buy_now_price ASC, start_price ASC
        LIMIT 1
    """, (item_id, effective_max, effective_max, effective_max))

    if player:
        return player

    # Fall back to simulated
    sim = db.fetch_one("""
        SELECT *, 'simulated' as source FROM auction_listings
        WHERE status = 'active'
          AND item_id = ?
          AND (buy_now_price IS NOT NULL OR current_bid IS NOT NULL)
          AND (buy_now_price <= ? OR current_bid <= ? OR start_price <= ?)
          AND is_simulated = 1
        ORDER BY buy_now_price ASC, start_price ASC
        LIMIT 1
    """, (item_id, effective_max, effective_max, effective_max))

    return sim


def _execute_purchase(persona: dict, listing: dict, price: float,
                       need: Optional[dict], reason: str = ""):
    """Execute a purchase for a persona.

    This records the transaction in the SP system.  It does NOT modify
    the actual AH listing or wallet — the main AH core handles that.
    Instead, this marks the persona's intent and adjusts their internal
    finances/needs/memory.

    Args:
        persona: The persona dict
        listing: The listing dict being purchased
        price: The purchase price
        need: The need being fulfilled (or None for impulse buys)
        reason: Why they bought it
    """
    try:
        persona_uuid = persona["persona_uuid"]

    except Exception as e:
        log.error(f"_execute_purchase failed: {e}")
        return None
    item_id = listing["item_id"]
    quantity = 1
    listing_uuid = listing["listing_uuid"]

    # Deduct from persona's simulated balance
    new_balance = update_finance(persona_uuid, -price, reason="purchase")
    if new_balance is not None:
        # Record the transaction in SP log
        record_transaction(persona_uuid, listing_uuid, "buy", item_id, quantity, price, reason)

        # Add memory of the purchase
        emotional_weight = 6 if need and need["urgency"] >= 7 else 3
        add_memory(persona_uuid, "purchase", item_id, price,
                   detail={"reason": reason, "listing_uuid": listing_uuid},
                   emotional_weight=emotional_weight)

        # Fulfill the need if this matches one
        if need:
            fulfill_need(need["id"], quantity)
        else:
            # Impulse buy — add a "regret" or "satisfaction" memory based on archetype
            if random.random() < 0.3:
                add_memory(persona_uuid, "missed_deal", item_id, price,
                           detail={"reason": "impulse_regret"},
                           emotional_weight=2)

        log.info("sp_behavior",
                 f"{persona['name']} bought {item_id} for {price}em ({reason})",
                 {"persona_uuid": persona_uuid, "listing_uuid": listing_uuid})


# ══════════════════════════════════════════════════════════════════════
# Bulk tick
# ══════════════════════════════════════════════════════════════════════

def run_persona_tick() -> dict:
    """Run one simulation tick for ALL active personas.

    Each persona gets an independent decision cycle.  Failures in one
    persona never affect others.

    Returns:
        Dict with summary: {"processed": N, "actions": {...}}
    """
    cfg = get_config()
    max_active = cfg.get("max_active_personas", 50)

    personas = get_active_personas()
    # Limit to configured max
    if len(personas) > max_active:
        personas = random.sample(personas, max_active)

    results = {"processed": len(personas),
               "bought": 0, "saved": 0, "nothing": 0, "debt_payment": 0}

    for persona in personas:
        try:
            result = process_persona(persona)
            action = result.get("action", "nothing")
            if action in results:
                results[action] += 1
            else:
                results[action] = 1
        except Exception as e:
            log.error("sp_behavior",
                      f"Error processing {persona.get('name', '?')}: {e}")

    # Apply passive income to all active personas
    for persona in personas:
        try:
            income = persona.get("income_per_tick", 0) or 0
            if income > 0:
                events = get_active_world_events(persona["region"])
                mult = 1.0
                for evt in events:
                    mult *= evt.get("income_multiplier", 1.0)
                adjusted = income * mult
                if adjusted > 0:
                    update_finance(persona["persona_uuid"], adjusted, reason="income")
        except Exception:
            pass

    log.info("sp_behavior",
             f"Tick complete: {results['processed']} personas, "
             f"{results['bought']} bought, {results['saved']} saved, "
             f"{results['nothing']} idle")


    # ── THREAD memory maintenance (non-blocking) ──────────────
    try:
        if _HAS_THREAD and _thread_available():
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_memory_thread import thread_run_reflect
            thread_run_reflect()
    except Exception:
        pass  # THREAD maintenance is non-critical

    return results

