"""
ah_ai_engine.py — DeepSeek AI integration & market simulation loop.

The heart of the Auction House's dynamic economy.  Runs on a configurable
timer (default: every 6 hours) and performs:

  1. Gather market context (listings, prices, events, notes)
  2. Build a structured prompt for DeepSeek
  3. Call the DeepSeek API with retry logic
  4. Parse the AI's JSON response
  5. Apply price adjustments, stock changes, events, rare items
  6. Generate price snapshots and store AI notes

The AI prompt includes detailed context about the Minecraft market, rarity
system, enchantment pools, lore generation rules, and event frequency caps.
"""

import json, time, threading, os, traceback
import urllib.request, urllib.error
from datetime import datetime, timezone
from typing import Optional, Callable

from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_price_history import (
    get_market_summary, get_stale_listings, get_simulated_inventory_state, take_snapshot
)
from AUCTIONHOUSE.ah_market_events import (
    start_event, end_event, get_active_events, check_event_progress,
    get_event_summary_for_prompt
)
from AUCTIONHOUSE.ah_core import list_item
from AUCTIONHOUSE.ah_helper_db import add_note, get_notes_for_prompt, clean_expired_notes
from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_plugin_registry import fire_hook

log = get_logger()
cfg = get_config

# ──────────────────────────────────────────────────────────────────────
# Prompt template
# ──────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are the Auction House AI for a Minecraft server. Your role is to simulate a living, breathing economy. You manage common items in the simulated inventory, generate rare items occasionally, trigger market events for flavor, and keep the market feeling alive.

You have access to:
1. Current market prices and trends
2. Player listing statistics
3. Active market events
4. Transaction history
5. Your own previous notes

You must respond ONLY with valid JSON that matches the expected action schema. Do NOT include any markdown, code fences, or explanatory text outside the JSON."""

_USER_PROMPT_TEMPLATE = """=== MARKET STATUS ===
Current Date: {datetime}
Active Listings: {total_active_listings}
    - Player items: {player_listings_count}
    - Simulated items: {sim_listings_count}
Transactions (last 24h): {transactions_24h}
Total Volume (last 24h): {volume_24h} emeralds

=== PRICE SNAPSHOTS ===
{price_snapshots}

=== ACTIVE MARKET EVENTS ===
{active_events}

=== SIMULATED INVENTORY ===
{sim_inventory}

=== STALE LISTINGS ===
{stale_listings}

=== PREVIOUS AI NOTES ===
{previous_notes}

=== AI TASK ===
Please analyze the market and produce a JSON response with:

1. "market_assessment": Brief 1-2 sentence summary of market health
2. "price_adjustments": Array of {{item_id, new_base_price, reason}} for items whose prices should change
3. "stock_adjustments": Array of {{item_id, action: "buy"|"sell", quantity, price}} for simulated trades
4. "events": Array of {{action: "start"|"end"|"continue", event_name, ...}} or empty array.
   - Only start events that feel organic and fun.
   - Events should have hidden goals (e.g. "sell X units of Y to end event").
5. "rare_items_to_list": Array of rare items to generate (max 2 per cycle, usually 0).
   Each has: {{item_id, count, price, enchantments[], lore[], rarity_tier, durability}}
   - Ultra-rare (elytra): ~1% chance per cycle
   - Rare (enchanted gear): ~5% chance per cycle
   - Uncommon (good tools): ~15% chance per cycle
   - Most cycles: [] empty array
6. "stale_recommendations": Array of {{listing_uuid, recommendation: "lower_price"|"remove", suggested_price}}
7. "notes": Array of {{category, content}} for your helper database
8. "announcement": String or null — market announcement to broadcast

=== EVENT FREQUENCY RULES ===
Events have 4 rarity tiers. Respect these frequency caps:
- Small events: ~1 per 2-4 hours. 1.1-1.5x price, 1-2 items, 1-4h duration.
- Medium events: ~1 per 1-3 days. 1.5-2.5x price, 2-4 items, 6-24h duration.
- Rare events: ~1 per 1-3 weeks. 2.0-4.0x price, 3-6 items, 24-72h duration.
- Major events: ~1 per 1-3 months. 3.0-6.0x price, 4-8 items, 48-168h duration.

- Do NOT trigger the same rarity tier two cycles in a row.
- Small events can overlap with Medium events.
- Major events need >=1 week of build-up notes in your AI helper DB first.
- Between Major events: minimum 30-day cooldown.

=== ITEM ENCHANTMENT POOL ===
Normal enchants: sharpness, protection, efficiency, unbreaking, fortune, looting, power, fire_aspect, thorns, feather_falling, respiration, aqua_affinity, depth_strider, swift_sneak, silk_touch, mending, soul_speed

Rarity enchantment rules:
- Common items: no enchants or 1 low-level enchant
- Uncommon: 1-2 enchants (max level 3)
- Rare: 2-3 enchants (max level 4)
- Epic: 3-4 enchants (can have level 5)
- Legendary: 3-5 enchants (any level)
- Mythic: 4-6 enchants (any level, can have incompatible combos via "curse" bypass)
- Cosmic Perfection: 5-7 enchants (all max level, can break normal rules)

=== LORE GENERATION RULES ===
Every simulated rare item should have flavor lore text (1-3 lines) that:
- Fits the Minecraft universe
- References the item's enchantments or purpose
- Is creative and immersive
- Uses the item's rarity tier in tone

=== CRITICAL RULES ===
- Do NOT override player-listed prices directly. Only suggest price changes to player items via "stale_recommendations" with a "lower_price" suggestion.
- Simulated items can be priced freely but must be reasonable.
- Events should be FUN and create interesting gameplay moments.
- Never generate the same rare item twice in a row.
- Keep the economy stable.
- Write at least one note per cycle to track your reasoning.

=== RESPONSE FORMAT (JSON ONLY) ===
{{
  "market_assessment": "...",
  "price_adjustments": [{{"item_id": "...", "new_base_price": 0.0, "reason": "..."}}],
  "stock_adjustments": [{{"item_id": "...", "action": "buy|sell", "quantity": 0, "price": 0.0}}],
  "events": [],
  "rare_items_to_list": [],
  "stale_recommendations": [],
  "notes": [{{"category": "observation", "content": "..."}}],
  "announcement": null
}}"""


# ──────────────────────────────────────────────────────────────────────
# DeepSeek API call
# ──────────────────────────────────────────────────────────────────────

def _resolve_model(config) -> str:
    """Resolve the actual model name based on config tier and thinking mode.

    Rules:
      - Tier "flash" + non-thinking → "deepseek-v4-flash" (fast, cheap)
      - Tier "flash" + thinking    → "deepseek-v4-flash" (same model, thinking param toggles)
      - Tier "pro"  + non-thinking → "deepseek-v4-pro"  (powerful, no thinking)
      - Tier "pro"  + thinking    → "deepseek-v4-pro"   (powerful + thinking)

    Returns:
        Model name string for the API payload.
    """
    tier = config.deepseek_model_tier if hasattr(config, "deepseek_model_tier") else "flash"
    if tier == "pro":
        return "deepseek-v4-pro"
    return "deepseek-v4-flash"


def _call_deepseek(prompt: str, system_prompt: str = _SYSTEM_PROMPT) -> Optional[dict]:
    """Call the DeepSeek chat completion API with Flash/Pro, thinking mode, and context caching.

    Args:
        prompt: The user prompt text
        system_prompt: The system prompt text

    Returns:
        Parsed JSON response dict, or None on failure
    """
    config = cfg()

    if not config.deepseek_api_key:
        log.error("ai_engine", "DeepSeek API key not configured. Set deepseek_api_key in config.")
        return None

    # ── Resolve model name from tier ────────────────────────────
    model = _resolve_model(config)
    thinking_enabled = config.deepseek_thinking_mode if hasattr(config, "deepseek_thinking_mode") else False
    cache_enabled = config.deepseek_enable_context_cache if hasattr(config, "deepseek_enable_context_cache") else False

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.deepseek_api_key}",
    }

    # ── Build messages ──────────────────────────────────────────
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    # ── Context caching: mark system prompt as cacheable ────────
    # DeepSeek supports automatic context caching.  When the system
    # prompt is static across calls, the cache hit rate is very high,
    # dropping input token cost from $0.14→$0.0028 per 1M (97% off).
    # The API automatically caches repeated prefix content, so we
    # just need to keep the system prompt stable.
    if cache_enabled:
        # Mark system as cacheable (Anthropic-compatible hint)
        # DeepSeek also supports native cache control through the API
        messages[0]["cache_control"] = {"type": "ephemeral"}

    # ── Build payload ───────────────────────────────────────────
    payload = {
        "model": model,
        "messages": messages,
        "temperature": config.deepseek_temperature,
        "max_tokens": config.deepseek_max_tokens,
    }

    # ── Thinking mode (only for Flash — Pro always thinks) ─────
    # deepseek-v4-flash: thinking = True enables Chain-of-Thought reasoning
    # Default is non-thinking (faster, cheaper, sufficient for market analysis)
    if thinking_enabled or config.deepseek_model_tier == "pro":
        payload["thinking"] = {"type": "enabled"}
        log.info("ai_engine", f"Thinking mode enabled ({model})")
    else:
        payload["thinking"] = {"type": "disabled"}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=config.deepseek_timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)

            # Log cache status if available (DeepSeek returns in response headers/body)
            usage = result.get("usage", {})
            if cache_enabled and usage:
                cache_hit = usage.get("prompt_cache_hit_tokens", 0) or usage.get("cached_input_tokens", 0)
                total_input = usage.get("prompt_tokens", 0)
                if cache_hit > 0 and total_input > 0:
                    log.info("ai_engine",
                             f"Context cache: {cache_hit}/{total_input} tokens cached "
                             f"({cache_hit/total_input*100:.0f}% hit rate)")

            # Extract the response text
            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0].get("message", {}).get("content", "")
                # Try to find JSON in the response (handle AI that wraps in markdown)
                content = _extract_json(content)
                if content:
                    return json.loads(content)
                else:
                    log.error("ai_engine", "No valid JSON found in DeepSeek response",
                              {"response_preview": body[:500]})
                    return None
            else:
                log.error("ai_engine", "Unexpected DeepSeek response structure",
                          {"response_preview": body[:500]})
                return None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log.error("ai_engine", f"DeepSeek API HTTP {e.code}: {error_body[:300]}")
        return None
    except urllib.error.URLError as e:
        log.error("ai_engine", f"DeepSeek API connection error: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        log.error("ai_engine", f"DeepSeek JSON parse error: {e}")
        return None
    except Exception as e:
        log.error("ai_engine", f"DeepSeek API unexpected error: {e}")
        return None


def _extract_json(text: str) -> Optional[str]:
    """Try to extract a JSON object from text that may contain markdown or extra content.

    Handles:
      - ```json ... ``` code fences
      - Leading/trailing non-JSON text
      - Raw JSON with no wrapping
    """
    # Try to find JSON block in code fences
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()

    # Find first { and last }
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        return text[brace_start:brace_end + 1]

    return None


# ──────────────────────────────────────────────────────────────────────
# Retry logic
# ──────────────────────────────────────────────────────────────────────

def _call_with_retry(prompt: str) -> Optional[dict]:
    """Call DeepSeek API with exponential backoff retry.

    Returns:
        Parsed JSON response dict, or None after all retries fail
    """
    config = cfg()
    last_error = None

    for attempt in range(1, config.simulation_retry_count + 1):
        log.info("ai_engine", f"DeepSeek API call attempt {attempt}/{config.simulation_retry_count}")
        result = _call_deepseek(prompt)

        if result is not None:
            # Validate basic structure
            if isinstance(result, dict) and "market_assessment" in result:
                return result
            # If it parsed but lacks expected keys, do one JSON fix retry
            if attempt < config.simulation_retry_count:
                log.warn("ai_engine", "AI response lacks expected keys, requesting JSON fix")
                fix_prompt = prompt + "\n\nYour previous response was missing required fields (market_assessment, price_adjustments, etc.). Please respond with ONLY valid JSON matching the schema."
                result = _call_deepseek(fix_prompt)
                if result and isinstance(result, dict):
                    return result

        # Exponential backoff
        if attempt < config.simulation_retry_count:
            delay = config.simulation_retry_delay_base * (2 ** (attempt - 1))
            log.info("ai_engine", f"Retrying in {delay}s...")
            time.sleep(delay)

    log.error("ai_engine", f"All {config.simulation_retry_count} DeepSeek API retries failed")
    return None


# ──────────────────────────────────────────────────────────────────────
# Applying AI decisions
# ──────────────────────────────────────────────────────────────────────

def _apply_price_adjustments(adjustments: list[dict]):
    """Apply price adjustment suggestions from the AI to simulated_inventory."""
    if not adjustments:
        return
    db = get_db()
    for adj in adjustments:
        item_id = adj.get("item_id", "")
        new_price = adj.get("new_base_price")
        reason = adj.get("reason", "No reason given")

        if not item_id or new_price is None:
            continue

        config = cfg()
        new_price = max(config.sim_price_min, min(new_price, config.sim_price_max))

        existing = db.fetch_one(
            "SELECT base_price, current_price FROM simulated_inventory WHERE item_id = ?",
            (item_id,)
        )
        if existing:
            old_price = existing["base_price"]
            db.execute(
                "UPDATE simulated_inventory SET base_price = ?, current_price = ?, last_updated = ? WHERE item_id = ?",
                (new_price, new_price, datetime.now(timezone.utc).isoformat(), item_id)
            )
            log.info("ai_engine", f"Price adjusted: {item_id} {old_price} -> {new_price}em ({reason})",
                     {"old": old_price, "new": new_price, "reason": reason})


def _apply_stock_adjustments(adjustments: list[dict]):
    """Apply simulated buy/sell trades from the AI."""
    if not adjustments:
        return
    db = get_db()
    for adj in adjustments:
        item_id = adj.get("item_id", "")
        action = adj.get("action", "")
        quantity = adj.get("quantity", 0)
        price = adj.get("price", 0.0)

        if not item_id or not action or quantity <= 0:
            continue

        stock = db.fetch_one(
            "SELECT current_stock, max_stock FROM simulated_inventory WHERE item_id = ?",
            (item_id,)
        )
        if not stock:
            continue

        if action == "buy":
            # AI is buying stock (adding to inventory)
            new_stock = min(stock["current_stock"] + quantity, stock["max_stock"])
            db.execute(
                "UPDATE simulated_inventory SET current_stock = ?, last_updated = ? WHERE item_id = ?",
                (new_stock, datetime.now(timezone.utc).isoformat(), item_id)
            )
            log.info("ai_engine", f"Stock buy: +{quantity} {item_id} ({stock['current_stock']} -> {new_stock}) @ {price}em each")
        elif action == "sell":
            # AI is selling stock (removing from inventory, simulating market activity)
            new_stock = max(0, stock["current_stock"] - quantity)
            db.execute(
                "UPDATE simulated_inventory SET current_stock = ?, last_updated = ? WHERE item_id = ?",
                (new_stock, datetime.now(timezone.utc).isoformat(), item_id)
            )
            log.info("ai_engine", f"Stock sell: -{quantity} {item_id} ({stock['current_stock']} -> {new_stock}) @ {price}em each")


def _apply_events(events: list[dict]):
    """Start or end market events based on AI decisions."""
    if not events:
        return
    for evt in events:
        action = evt.get("action", "")
        if action == "start":
            result = start_event(
                event_name=evt.get("event_name", "unknown"),
                event_title=evt.get("event_title", evt.get("event_name", "Unknown Event")),
                event_flavor=evt.get("event_flavor", ""),
                event_type=evt.get("event_type", "seasonal"),
                rarity_tier=evt.get("rarity_tier", "small"),
                affected_items=evt.get("affected_items", []),
                price_multiplier=evt.get("price_multiplier", 1.5),
                demand_boost=evt.get("demand_boost", 1.0),
                duration_seconds=evt.get("duration_seconds"),
                goal_count=evt.get("goal_count"),
            )
            if result.get("ok"):
                log.info("ai_engine", f"AI started event: {evt.get('event_name')}")
            else:
                log.warn("ai_engine", f"AI could not start event: {result.get('error')}")

        elif action == "end":
            event_name = evt.get("event_name", "")
            # Find active event by name
            db = get_db()
            active = db.fetch_all(
                "SELECT * FROM market_events WHERE event_name = ? AND is_active = 1",
                (event_name,)
            )
            for event in active:
                end_event(event["event_uuid"], reason="ai_decision")
                log.info("ai_engine", f"AI ended event: {event_name}")


def _apply_rare_items(items: list[dict]):
    """ Apply Rare Items.
    """
    if not items:
        return
    from AUCTIONHOUSE.ah_item_gen import generate_simulated_item

    for item_data in items:
        item_id = item_data.get("item_id", "")
        if not item_id:
            continue

        # Use the AI's specifications for enchantments, lore, etc.
        # Fall back to procedural generation if AI didn't provide details
        enchants = item_data.get("enchantments", [])
        lore = item_data.get("lore", [])
        price = item_data.get("price", 10.0)
        rarity_tier = item_data.get("rarity_tier", "Rare")

        # Generate the item with possible override
        try:
            gen_item = generate_simulated_item(item_id)
            # Override with AI's specifics
            if enchants:
                gen_item["enchantments"] = enchants
            if lore:
                gen_item["lore"] = lore
            if price:
                gen_item["price"] = price

            result = list_item(
                seller="Auction House",
                item_id=item_id,
                count=item_data.get("count", 1),
                start_price=gen_item["price"],
                buy_now_price=gen_item["price"],
                is_simulated=True,
                signed_name=gen_item["signed_name"],
                sim_lore=json.dumps(gen_item["lore"]),
                sim_enchantments=gen_item["enchantments"],
                sim_durability=gen_item.get("durability"),
                sim_source_event="ai_generation",
            )
            if result.get("ok"):
                log.info("ai_engine", f"Rare item listed: {gen_item['signed_name']} ({rarity_tier}) @ {gen_item['price']}em")
            else:
                log.warn("ai_engine", f"Failed to list rare item: {result.get('error')}")
        except Exception as e:
            log.error("ai_engine", f"Error generating rare item: {e}")


def _apply_stale_recommendations(recommendations: list[dict]):
    """ Apply Stale Recommendations.
    """
    if not recommendations:
        return
    for rec in recommendations:
        listing_uuid = rec.get("listing_uuid", "")
        recommendation = rec.get("recommendation", "lower_price")
        suggested_price = rec.get("suggested_price")

        note_content = f"Stale listing {listing_uuid}: {recommendation}"
        if suggested_price:
            note_content += f" (suggested price: {suggested_price:.2f}em)"

        add_note(
            category="item_opinion",
            content=note_content,
            reasoning=rec.get("reason", "AI identified as stale"),
            related_item_id=listing_uuid,
            importance=3,
            expires_in_days=7,
        )


def _apply_notes(notes: list[dict]):
    """Store AI notes to the helper database.

    Fallback category mapping for any categories the AI creates that
    don't match our valid list (the AI is creative — "strategy" maps
    to "recommendation", "trend" maps to "observation", etc.).
    """
    _CATEGORY_FALLBACKS = {
        "strategy": "recommendation",
        "trend": "observation",
        "analysis": "price_reasoning",
        "forecast": "market_health",
        "alert": "recommendation",
        "suggestion": "recommendation",
        "feedback": "observation",
    }
    # Import from canonical source to prevent drift (BUG FIX M3)
    from AUCTIONHOUSE.ah_helper_db import VALID_CATEGORIES as _VALID_CATEGORIES

    if not notes:
        return
    for note in notes:
        raw_category = note.get("category", "observation")
        # Map fallback categories, then validate
        category = _CATEGORY_FALLBACKS.get(raw_category, raw_category)
        if category not in _VALID_CATEGORIES:
            category = "observation"  # Ultimate fallback
        content = note.get("content", "")
        reasoning = note.get("reasoning")

        if not content:
            continue

        add_note(
            category=category,
            content=content,
            reasoning=reasoning,
            importance=note.get("importance", 1),
            expires_in_days=note.get("expires_in_days", 30),
        )


# ──────────────────────────────────────────────────────────────────────
# Main simulation cycle
# ──────────────────────────────────────────────────────────────────────

def run_simulation_cycle(announce_fn: Optional[Callable] = None,
                         rcon_func: Optional[Callable] = None) -> bool:
    """Execute one complete AI simulation cycle.

    Args:
        announce_fn: Optional function to call for announcements.
                     Signature: announce_fn(message_type, data)
        rcon_func: Optional RCON command function for in-game broadcasts

    Returns:
        True if the cycle completed successfully
    """
    log.info("ai_engine", "Starting simulation cycle...")
    start_time = time.time()

    # ── Fire extension hooks (on_simulation_cycle_start) ─────────────
    from EXTENSIONS.state_registry import clear_state
    clear_state()
    try:
        fire_hook("on_simulation_cycle_start")
    except Exception:
        pass  # Extensions are non-critical — don't block the AI core

    try:
        # 1. Gather context
        summary = get_market_summary()
        stale = get_stale_listings()
        sim_inv = get_simulated_inventory_state()
        active_events_str = get_event_summary_for_prompt()
        notes = get_notes_for_prompt(limit=30)

        # Format stale listings for prompt
        stale_lines = []
        for s in stale[:10]:  # Max 10 stale listings to keep prompt size manageable
            price = s.get("current_bid") or s.get("start_price", 0)
            stale_lines.append(
                f"  {s['listing_uuid'][:8]} | {s['seller_name']:16s} | {s['item_id']:30s} | "
                f"{price:>6.2f}em | listed: {s['listed_at'][:16]}"
            )
        stale_str = "\n".join(stale_lines) if stale_lines else "  No stale listings."

        # Format sim inventory for prompt
        inv_lines = []
        for item in sim_inv:
            price = item.get("current_price") or item.get("base_price", 0)
            inv_lines.append(
                f"  {item['item_id']:30s} | stock={item['current_stock']:>5d}/{item['max_stock']:>5d} | "
                f"base={item['base_price']:>6.2f} | current={price:>6.2f}em | "
                f"trend={'↑' if item['trend_direction'] == 1 else '↓' if item['trend_direction'] == -1 else '→'}"
            )
        inv_str = "\n".join(inv_lines)

        # Format notes for prompt
        notes_lines = []
        for n in notes[:15]:
            notes_lines.append(f"  [{n['category']:20s}] [{n['importance']}] {n['content'][:120]}")
        notes_str = "\n".join(notes_lines) if notes_lines else "  No previous notes."

        # 2. Build prompt
        prompt = _USER_PROMPT_TEMPLATE.format(
            datetime=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            total_active_listings=summary["total_active_listings"],
            player_listings_count=summary["player_listings_count"],
            sim_listings_count=summary["sim_listings_count"],
            transactions_24h=summary["transactions_24h"],
            volume_24h=summary["volume_24h"],
            price_snapshots=summary["price_snapshots_formatted"][:3000],  # Cap length
            active_events=active_events_str,
            sim_inventory=inv_str[:2000],  # Cap length
            stale_listings=stale_str[:2000],
            previous_notes=notes_str[:2000],
        )

        # 3. Call DeepSeek API
        log.info("ai_engine", f"Prompt built ({len(prompt)} chars), calling DeepSeek API...")
        ai_response = _call_with_retry(prompt)

        if ai_response is None:
            log.error("ai_engine", "Simulation cycle failed — no valid AI response")
            # Still take a snapshot so the log shows the failure
            take_snapshot()
            return False

        # 4. Parse and apply AI decisions
        assessment = ai_response.get("market_assessment", "No assessment provided.")
        log.info("ai_engine", f"AI assessment: {assessment}")

        _apply_price_adjustments(ai_response.get("price_adjustments", []))
        _apply_stock_adjustments(ai_response.get("stock_adjustments", []))
        _apply_events(ai_response.get("events", []))
        _apply_rare_items(ai_response.get("rare_items_to_list", []))
        _apply_stale_recommendations(ai_response.get("stale_recommendations", []))
        _apply_notes(ai_response.get("notes", []))

        # 5. Check for completed events
        completed_events = check_event_progress()
        for ce in completed_events:
            log.info("ai_engine", f"Event completed: {ce['event_name']} ({ce['reason']})")

        # 6. Store the AI market assessment as a note
        if assessment:
            _apply_notes([{
                "category": "market_health",
                "content": f"AI Market Assessment: {assessment}",
                "reasoning": "Generated by DeepSeek AI during market simulation cycle",
                "importance": 5,
                "expires_in_days": 14,
            }])

        # 7. Take price snapshot
        take_snapshot()

        # 8. Process expired auctions — notify winners/sellers in-game
        from AUCTIONHOUSE.ah_core import expire_listings
        expired = expire_listings(rcon_func=rcon_func)
        if expired:
            log.info("ai_engine", f"Processed {len(expired)} expired listings")

        # 9. Clean expired notes
        clean_expired_notes()

        duration_ms = (time.time() - start_time) * 1000
        log.ai_simulation(prompt, json.dumps(ai_response), duration_ms, {
            "cycle_completed": True,
            "adjustments": len(ai_response.get("price_adjustments", [])),
            "events": len(ai_response.get("events", [])),
            "rare_items": len(ai_response.get("rare_items_to_list", [])),
        })

        # 8. Announcement
        announcement = ai_response.get("announcement")
        if announcement and rcon_func:
            try:
                from AUCTIONHOUSE.ah_announcer import broadcast
                broadcast(announcement, color="gold", rcon_func=rcon_func)
            except Exception as e:
                log.warn("ai_engine", f"Failed to broadcast AI announcement: {e}")

        # ── Fire extension hooks (on_simulation_cycle_end) ─────────────
        try:
            fire_hook("on_simulation_cycle_end", cycle_start_time=start_time)
        except Exception:
            pass  # Extensions are non-critical — don't block the AI core

        log.info("ai_engine", f"Simulation cycle complete ({duration_ms:.0f}ms)")
        return True

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log.error("ai_engine", f"Simulation cycle crashed: {e}\n{traceback.format_exc()}")
        return False


# ──────────────────────────────────────────────────────────────────────
# Timer / scheduler
# ──────────────────────────────────────────────────────────────────────

class SimulationScheduler:
    """Manages the periodic simulation timer in a background thread."""

    def __init__(self, announce_fn: Optional[Callable] = None,
                 rcon_func: Optional[Callable] = None):
        self._announce_fn = announce_fn
        self._rcon_func = rcon_func
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self._lock = threading.Lock()

    def _cycle_wrapper(self):
        """Run a simulation cycle and reschedule."""
        if not self._running:
            return
        try:
            run_simulation_cycle(
                announce_fn=self._announce_fn,
                rcon_func=self._rcon_func
            )
        except Exception:
            log.error("scheduler", f"Unhandled error in simulation cycle:\n{traceback.format_exc()}")
        finally:
            if self._running:
                self._schedule_next()

    def _schedule_next(self):
        """Schedule the next simulation cycle."""
        config = cfg()
        interval = config.simulation_interval_minutes * 60  # Convert to seconds
        self._timer = threading.Timer(interval, self._cycle_wrapper)
        self._timer.daemon = True
        self._timer.start()
        log.info("scheduler", f"Next simulation cycle in {config.simulation_interval_minutes} minutes")

    def start(self):
        """Start the simulation scheduler."""
        config = cfg()
        if not config.simulation_enabled:
            log.info("scheduler", "Simulation is disabled in config. Not starting.")
            return

        with self._lock:
            if self._running:
                log.warn("scheduler", "Scheduler is already running")
                return
            self._running = True
            log.info("scheduler", f"Simulation scheduler started (interval: {config.simulation_interval_minutes}min)")

            # Run first cycle after a short delay (don't block startup)
            self._timer = threading.Timer(5.0, self._cycle_wrapper)
            self._timer.daemon = True
            self._timer.start()

    def stop(self):
        """Stop the simulation scheduler."""
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
            log.info("scheduler", "Simulation scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._running


# ──────────────────────────────────────────────────────────────────────
# Convenience
# ──────────────────────────────────────────────────────────────────────

_scheduler: Optional[SimulationScheduler] = None
_last_simulation_result = None  # Stores the last run's result dict
_last_simulation_time = None    # ISO timestamp of last run


def get_simulation_status() -> dict:
    """Get full simulation status: scheduler state, last run, market, events, personas.

    Returns a dict suitable for the report command in the minescript client.
    """
    global _scheduler, _last_simulation_result, _last_simulation_time

    # Scheduler status
    sched_running = _scheduler.is_running if _scheduler else False

    # Market summary
    summary = None
    try:
        summary = get_market_summary()
    except Exception:
        pass

    # Active events
    events = []
    try:
        events = get_active_events()
    except Exception:
        pass

    # AI notes
    notes = []
    try:
        notes = get_notes_for_prompt(limit=10, min_importance=2)
    except Exception:
        pass

    # Recent transactions (last 24h)
    recent_tx_count = 0
    recent_tx_volume = 0.0
    if summary:
        recent_tx_count = summary.get("transactions_24h", 0)
        recent_tx_volume = summary.get("volume_24h", 0.0)

    # Persona count
    persona_count = 0
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db
        spdb = sp_get_db()
        persona_count = spdb.fetch_one("SELECT COUNT(*) as c FROM ext_sp_profiles WHERE active = 1")
        if persona_count:
            persona_count = persona_count["c"]
    except Exception:
        persona_count = -1  # Extension not loaded

    # Board open needs count
    board_count = 0
    try:
        board_count = spdb.fetch_one("SELECT COUNT(*) as c FROM ext_sp_board WHERE status = 'open'")
        if board_count:
            board_count = board_count["c"]
    except Exception:
        pass

    # Config info
    from AUCTIONHOUSE.ah_config import cfg
    config = cfg()
    tier = getattr(config, 'persona_tier', '?')

    return {
        "scheduler": {
            "running": sched_running,
            "interval_minutes": config.simulation_interval_minutes if hasattr(config, 'simulation_interval_minutes') else 360,
            "enabled": config.simulation_enabled if hasattr(config, 'simulation_enabled') else True,
        },
        "last_run": {
            "time": _last_simulation_time,
            "result": _last_simulation_result,
        },
        "market": {
            "active_listings": summary["total_active_listings"] if summary else 0,
            "player_listings": summary["player_listings_count"] if summary else 0,
            "sim_listings": summary["sim_listings_count"] if summary else 0,
            "tx_24h": recent_tx_count,
            "volume_24h": recent_tx_volume,
        },
        "events": [{"name": e["event_name"], "type": e["event_type"], "active": e["is_active"]} for e in events[:5]],
        "notes": [{"category": n["category"], "content": n["content"][:100]} for n in notes[:5]],
        "personas": {
            "active_count": persona_count,
            "tier": tier,
        },
        "board": {
            "open_needs": board_count,
        },
    }


def set_last_simulation_result(result: dict):
    """Store the result of a simulation cycle for status/report queries."""
    global _last_simulation_result, _last_simulation_time
    _last_simulation_result = result
    from datetime import datetime, timezone
    _last_simulation_time = datetime.now(timezone.utc).isoformat()



def start_scheduler(announce_fn: Optional[Callable] = None,
                    rcon_func: Optional[Callable] = None):
    """Start the global simulation scheduler.

    Args:
        announce_fn: Optional announcement callback
        rcon_func: Optional RCON function
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = SimulationScheduler(announce_fn=announce_fn, rcon_func=rcon_func)
    _scheduler.start()
    return _scheduler


def stop_scheduler():
    """Stop the global simulation scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None


def force_run(announce_fn: Optional[Callable] = None,
              rcon_func: Optional[Callable] = None) -> bool:
    """Force-run a simulation cycle immediately.

    Returns:
        True if successful
    """
    return run_simulation_cycle(announce_fn=announce_fn, rcon_func=rcon_func)
