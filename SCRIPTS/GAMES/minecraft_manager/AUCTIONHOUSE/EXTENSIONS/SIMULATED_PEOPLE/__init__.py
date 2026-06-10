"""
SIMULATED_PEOPLE — AH Extension: Individual persona market simulation.

Adds hundreds of simulated personas, each with:
  - Unique personality, job, wealth, and region
  - Personal finances, needs, and memory
  - Independent decision-making on every tick
  - Life events and world events that affect behavior

Architecture:
  sp_database.py     — Schema & queries (tables: ext_sp_*)
  sp_profile.py      — Persona generation
  sp_behavior.py     — Decision engine (core tick)
  sp_world_events.py — World events & personal events
  sp_database.py     — Schema & queries (also handles memory/sp_memory_thread.py)

Safety:
  - All hooks are wrapped in try/except by the registry
  - Uses own tables (ext_sp_*) — never modifies core AH data
  - If disabled/removed, the main AH runs unchanged
"""

import json, os, threading, random
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from AUCTIONHOUSE.ah_logger import get_logger
from engine.config_loader import get_config_path, load_config, save_config

log = get_logger()

# ── Config ───────────────────────────────────────────────────────────

_CONFIG = {}
_CONFIG_LOCK = threading.Lock()

_DEFAULT_CONFIG = {
    "enabled": True,
    "tick_interval_minutes": 60,
    "max_active_personas": 50,
    # ── Persona tier (auto-configures pool and max_active) ──────────────
    #   "ultra_data_saver"  → max_active=3,   pool=10   (🟢 1-3 personas)
    #   "data_saver"        → max_active=7,   pool=25   (🟢 4-7)
    #   "normal"            → max_active=20,  pool=50   (🟡 8-20)
    #   "above_normal"      → max_active=50,  pool=200  (🟠 21-50  DEFAULT)
    #   "burn_my_tokens"    → max_active=200, pool=500  (🔴 51-200)
    "persona_tier": "data_saver",
    "persona_pool_size": 25,
    "new_persona_spawn_chance": 0.1,
    "persona_activation_rate": 0.6,
    "persona_deactivation_rate": 0.15,
    "min_balance_for_purchase": 5,
    "memory_retention_days": 90,
    "max_needs_per_persona": 5,
    "need_refresh_interval_ticks": 3,
    "life_event_chance_per_tick": 0.25,
    "max_active_life_events": 2,
    "personal_event_pool_size": 30,
    "world_event_chance_per_tick": 0.05,
    "player_item_buy_bonus": 3.0,
    "price_memory_weight": 0.6,
    "debug_logging": False,
    "ai_persona_decision": True,
}


def load_config():
    """Load config from our config.json, merged over defaults."""
    global _CONFIG
    cfg = dict(_DEFAULT_CONFIG)

    config_path = Path(__file__).parent / "config.json"
    data_path = get_config_path("simulated_people")
    
    # Migrate from legacy inline config.json to centralized DATA/
    if config_path.exists() and not data_path.exists():
        try:
            import shutil
            shutil.copy2(str(config_path), str(data_path))
            log.info("sp_config", f"Migrated config to {data_path}")
        except Exception:
            pass

    # Try centralized config first, fall back to inline
    if data_path.exists():
        try:
            with open(data_path) as f:
                override = json.load(f)
            cfg.update(override)
            
            # ── Apply persona_tier if set (overrides max_active_personas & persona_pool_size) ──
            _tier = cfg.get("persona_tier", "data_saver")
            _TIER_MAP = {
                "ultra_data_saver": {"max_active": 3,  "pool": 10},
                "data_saver":       {"max_active": 7,  "pool": 25},
                "normal":           {"max_active": 20, "pool": 50},
                "above_normal":     {"max_active": 50, "pool": 200},
                "burn_my_tokens":   {"max_active": 200,"pool": 500},
            }
            if _tier in _TIER_MAP:
                cfg["max_active_personas"] = _TIER_MAP[_tier]["max_active"]
                cfg["persona_pool_size"] = _TIER_MAP[_tier]["pool"]
        except (json.JSONDecodeError, OSError) as e:
            log.warn("sp_extension", f"Could not load config.json: {e}")

    _CONFIG = cfg


def get_config() -> dict:
    """Return the current extension configuration."""
    global _CONFIG
    if not _CONFIG:
        with _CONFIG_LOCK:
            if not _CONFIG:
                load_config()
    return _CONFIG


def reload_config() -> dict:
    """Force reload configuration from disk. E6: runtime config reload."""
    global _CONFIG
    with _CONFIG_LOCK:
        _CONFIG = {}
        load_config()
    log.info("sp_extension", "Configuration reloaded from disk")
    return get_config()


# ── Tick Counter ─────────────────────────────────────────────────────

_tick_count = 0
_tick_lock = threading.Lock()


def _next_tick() -> int:
    global _tick_count
    with _tick_lock:
        _tick_count += 1
        return _tick_count


# ══════════════════════════════════════════════════════════════════════
# Hook implementations
# ══════════════════════════════════════════════════════════════════════

def _on_simulation_cycle_start(**kwargs) -> dict:
    """Called before the main AI simulation cycle.

    We run the persona tick here so the market state is updated before
    the AI makes decisions.
    """
    cfg = get_config()
    if not cfg.get("enabled", True):
        return {"status": "disabled"}

    tick = _next_tick()

    # Import here to avoid circular imports at module level
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import run_persona_tick
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world_events import run_world_event_tick
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import cleanup_database
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import (
        spawn_initial_population, generate_persona, pick_random_inactive,
        activate_persona, get_persona_count
    )

    # 1. Ensure world is seeded (one-time)
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import (
        generate_world, get_area_count
    )
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import (
        best_skill, skill_to_action, improve_skill
    )
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import process_health_tick
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import add_life_event
    world_count = get_area_count()
    if not world_count or sum(world_count.values()) < 7:
        areas = generate_world()
        log.info("sp_extension", f"World generated: {areas} areas")

    # 2. Ensure population is seeded
    pop = get_persona_count()
    if pop["total"] < cfg["persona_pool_size"]:
        spawn_initial_population(cfg["persona_pool_size"])

    # 2. Maybe spawn new personas
    if random.random() < cfg.get("new_persona_spawn_chance", 0.1):
        try:
            generate_persona()
        except Exception as e:
            log.debug("sp_extension", f"Spawn new persona failed: {e}")

    # 3. Maybe reactivate some inactive personas
    if pop["active"] < cfg.get("max_active_personas", 50):
        inactive = pick_random_inactive()
        if inactive and random.random() < cfg.get("persona_activation_rate", 0.6):
            try:
                activate_persona(inactive["persona_uuid"])
            except Exception as e:
                log.debug("sp_extension", f"Reactivate persona failed: {e}")

    # 4. Update weather for all areas
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import (
        update_weather, init_weather
    )
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as _spdb
    # Ensure weather is seeded (check if any area has weather data)
    _any_weather = _spdb().fetch_one("SELECT COUNT(*) as c FROM ext_sp_weather WHERE updated_at != ''")
    if not _any_weather or _any_weather["c"] == 0:
        init_weather()
    weather_result = update_weather()

    # 5. Run world event tick (start/end events, generate life events)
    world_result = run_world_event_tick()

    # 6. Process persona quests (adventures)
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_weather import process_quests
    quest_result = process_quests()

    # 7. Run persona movement tick (each persona may move)
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_movement import process_movement_tick
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_active_personas
    active_personas = get_active_personas()
    moved_count = 0
    for persona in active_personas:
        try:
            reason = process_movement_tick(persona)
            if reason:
                moved_count += 1
                if cfg.get("debug_logging"):
                    log.info("sp_extension",
                             f"{persona['name']} moved: {reason}")
        except Exception as e:
            log.debug("sp_extension", f"Move error for {persona.get('name','?')}: {e}")

    # 8. Run persona behavior tick (each persona decides to buy/save/wait)
    behavior_result = run_persona_tick()

    # 9. Process health decay for all active personas
    health_result = process_health_tick()
    for p in health_result.get("deaths", []):
        log.info("sp_extension", f"PERSONA DIED: {p['name']} ({p['cause']})")

    # 9b. Process ecosystem tick (plants, animals, density)
    ecosystem_result = {}
    craft_result = {'completed': 0}
    _any_nodes = None
    if cfg.get("ecosystem_enabled", True):
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import (
                init_ecosystem, process_ecosystem_tick
            )
            # Seed ecosystem if not yet initialized
            _any_eco = False
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as _spdb2
            eco_check = _spdb2().fetch_one(
                "SELECT COUNT(*) as c FROM ext_sp_ecosystem")
            if eco_check and eco_check["c"] > 0:
                _any_eco = True

            if not _any_eco:
                init_ecosystem()

            ecosystem_result = process_ecosystem_tick(tick)

            # Check animal threats for all active personas
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_ecosystem import persona_threat_check
            for persona in active_personas:
                try:
                    threat = persona_threat_check(persona["persona_uuid"])
                    if threat.get("threat"):
                        log.info("sp_extension",
                                 f"{persona['name']} was attacked by a {threat['species']}! "
                                 f"({threat.get('damage', 0):.0f} damage)")
                        add_life_event(
                            persona["persona_uuid"], "animal_attack",
                            f"Attacked by a {threat['species']} in {persona.get('area','?')}",
                            financial_impact=0, mood_impact="stressed", duration_hours=48)
                except Exception as e:
                    log.debug("sp_extension", f"Threat check failed for {persona.get('name','?')}: {e}")

            if cfg.get("ecosystem_debug"):
                log.info("sp_extension",
                         f"Ecosystem tick #{tick}: "
                         f"plants={ecosystem_result.get('plants', {})} "
                         f"animals={ecosystem_result.get('animals', {})} "
                         f"density={ecosystem_result.get('density', {})}")

            # 9c. Resource & crafting processing
            # Seed resource nodes if not yet initialized
            _any_nodes = _spdb2().fetch_one(
                "SELECT COUNT(*) as c FROM ext_sp_resource_nodes")
            if not _any_nodes or _any_nodes["c"] == 0:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_resources import spawn_resource_nodes
                spawn_resource_nodes()

            # Process resource node regrowth (every 6 ticks = daily)
            if tick % 6 == 0:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_resources import process_node_regrowth
                regrowth = process_node_regrowth()
                if regrowth.get("nodes_regrown", 0) > 0 and cfg.get("debug_logging"):
                    log.info("sp_extension",
                             f"Resource regrowth: {regrowth['nodes_regrown']} nodes")

            # Process crafting queue (every tick)
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_crafting import process_crafting_queue
            craft_result = process_crafting_queue()
            if craft_result.get("completed", 0) > 0:
                log.info("sp_extension",
                         f"{craft_result['completed']} crafting jobs completed")

            # Inventory decay (every 6 ticks = daily)
            if tick % 6 == 0:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import process_inventory_decay
                decay = process_inventory_decay()
                if decay.get("destroyed", 0) > 0 and cfg.get("debug_logging"):
                    log.info("sp_extension",
                             f"Inventory decay: {decay['destroyed']} items spoiled")

            # Auto-consume for thirst (every tick)
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import auto_consume
            for persona in active_personas:
                try:
                    auto_consume(persona["persona_uuid"])
                except Exception:
                    pass

        except Exception as e:
            log.warn("sp_extension", f"Ecosystem/resource tick error: {e}")

    # 9d. Process wounds (bleeding, infection, healing)
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_combat import process_wounds_tick
        wound_result = process_wounds_tick()
        if wound_result.get("deaths", 0) > 0:
            log.info("sp_extension",
                     f"{wound_result['deaths']} personas died from wound infections")
        if wound_result.get("healed", 0) > 0 and cfg.get("debug_logging"):
            log.info("sp_extension",
                     f"{wound_result['healed']} wounds healed naturally")
    except Exception as e:
        log.debug("sp_extension", f"Wound processing error: {e}")

    # 9e. Process factions, claims, and wars (daily = every 6 ticks)
    if cfg.get("factions_enabled", True) and tick % 6 == 0:
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_factions import process_guild_tick
            guild_result = process_guild_tick()
            if guild_result.get("morale_shifts", 0) > 0 and cfg.get("debug_logging"):
                log.info("sp_extension",
                         f"Guild tick: {guild_result['processed']} guilds processed")
        except Exception as e:
            log.debug("sp_extension", f"Guild tick error: {e}")

        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_claims import (
                process_claims_tick, detect_settlements
            )
            claim_result = process_claims_tick()
            if claim_result.get("claims_lost", 0) > 0 and cfg.get("debug_logging"):
                log.info("sp_extension",
                         f"{claim_result['claims_lost']} claims expired")

            settlements = detect_settlements()
            if settlements and cfg.get("debug_logging"):
                log.info("sp_extension",
                         f"{len(settlements)} active settlements detected")
        except Exception as e:
            log.debug("sp_extension", f"Claims tick error: {e}")

        if cfg.get("wars_enabled", True):
            try:
                from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_wars import process_wars_tick
                war_result = process_wars_tick()
                if war_result.get("surrender", {}).get("surrendered", 0) > 0:
                    log.info("sp_extension",
                             f"{war_result['surrender']['surrendered']} guilds surrendered")
            except Exception as e:
                log.debug("sp_extension", f"War tick error: {e}")

    # 10. Cleanup old data
    if tick % 5 == 0:  # Every 5 ticks
        cleanup_database()

    if cfg.get("debug_logging"):
        log.info("sp_extension",
                 f"Tick #{tick}: {behavior_result['processed']} personas processed, "
                 f"{behavior_result['bought']} bought, "
                 f"world events: {world_result}")

    # ── Write to shared extension state (Phase 2C step 1) ────────
    from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
    _state = get_state()
    _state.set("active_personas", active_personas, "SIMULATED_PEOPLE")
    _state.set("persona_count", pop, "SIMULATED_PEOPLE")
    _state.set("weather", weather_result, "SIMULATED_PEOPLE")
    _state.set("world_events", world_result, "SIMULATED_PEOPLE")
    _state.set("moved_count", moved_count, "SIMULATED_PEOPLE")

    return {
        "tick": tick,
        "personas": behavior_result["processed"],
        "bought": behavior_result["bought"],
        "moved": moved_count,
        "deaths": len(health_result.get("deaths", [])),
        "critical": len(health_result.get("critical", [])),
        "world_events": world_result,
        "weather": weather_result,
        "quests": quest_result.get("quests_started", 0),
        "ecosystem": {
            "plants": ecosystem_result.get("plants", {}).get("areas_processed", 0),
            "animals": ecosystem_result.get("animals", {}).get("processed", 0),
            "animal_moves": ecosystem_result.get("animals", {}).get("moved", 0),
            "animal_deaths": ecosystem_result.get("animals", {}).get("died", 0),
            "density": ecosystem_result.get("density", {}).get("total_delta", 0),
        } if ecosystem_result else {},
        "crafting": craft_result.get("completed", 0),
        "resource_nodes": _any_nodes.get("c", 0) if _any_nodes else 0,
    }

def _on_listing_created(**kwargs) -> dict:
    """Called when a player creates a listing.

    We check if any persona needs this item and adjust their urgency.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"_on_listing_created failed: {e}")
        return {}
    if not cfg.get("enabled", True):
        return {"status": "disabled"}

    listing = kwargs.get("listing", {})
    if not listing:
        return {"error": "no listing data"}

    item_id = listing.get("item_id", "")
    if not item_id:
        return {"error": "no item id"}

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
        get_all_active_needs, add_memory, get_db as sp_get_db
    )

    # Find all personas that need this item
    needs = get_all_active_needs()
    matching = [n for n in needs if n["item_id"] == item_id]

    if matching:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_behavior import ARCHETYPES

        for need in matching:
            persona_uuid = need["persona_uuid"]
            price = listing.get("start_price", 0) or listing.get("buy_now_price", 0)

            # Increase urgency slightly — the item is now available
            db = sp_get_db()
            db.execute("""
                UPDATE ext_sp_needs SET urgency = MIN(10, urgency + 1)
                WHERE id = ? AND urgency < 10
            """, (need["id"],))

            # Add memory that this item was spotted
            add_memory(persona_uuid, "observed_price", item_id, price,
                       detail={"listing_uuid": listing.get("listing_uuid", ""),
                               "seller": listing.get("seller_name", "")},
                       emotional_weight=3)

        log.info("sp_extension",
                 f"Listing {item_id} matches {len(matching)} persona needs")

    return {"matching_needs": len(matching)}


def _on_listing_queried(**kwargs) -> dict:
    """Called when listings are queried.

    Annotates results with persona need counts for the UI to show badges.
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"_on_listing_queried failed: {e}")
        return {}
    if not cfg.get("enabled", True):
        return {}

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import count_matching_needs

    query = kwargs.get("query", {})
    filter_type = query.get("filter_type", "all")
    filter_value = query.get("filter_value", "")

    # Only annotate item searches or all listings
    if filter_type == "item" and filter_value:
        need_count = count_matching_needs(filter_value)
        if need_count > 0:
            return {"needs_count": need_count}

    return {}


def _on_purchase(**kwargs) -> dict:
    """Called when an item is purchased by a real player.

    Two things happen:
      1. Random personas observe the transaction (price memory)
      2. If a SIMULATED persona bought from a REAL player, send an
         AI-generated message from the persona to the player via tellraw
    """
    try:
        cfg = get_config()

    except Exception as e:
        log.error(f"_on_purchase failed: {e}")
        return {}
    if not cfg.get("enabled", True):
        return {}

    transaction = kwargs.get("transaction", {})
    item_id = transaction.get("item_id", "")
    price = transaction.get("price", 0)
    buyer = transaction.get("buyer", "")
    seller = transaction.get("seller", "")
    listing_uuid = transaction.get("listing_uuid", "")
    rcon_func = kwargs.get("rcon_func")

    if item_id and price > 0:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db, add_memory
        db = sp_get_db()

        # 1. Random personas observe the price
        observers = db.fetch_all("""
            SELECT persona_uuid FROM ext_sp_profiles
            WHERE active = 1 ORDER BY RANDOM() LIMIT 10
        """)
        for p in observers:
            add_memory(p["persona_uuid"], "observed_price", item_id, price,
                       detail={"source": "market_purchase"},
                       emotional_weight=2)

        # 2. If a persona bought from a real player, send AI message
        persona_buyer = db.fetch_one("""
            SELECT * FROM ext_sp_profiles WHERE name = ? AND active = 1
        """, (buyer,))
        if persona_buyer:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_messaging import send_purchase_message
            import uuid as _uuid
            tx_uuid = str(_uuid.uuid4())
            send_purchase_message(
                persona=dict(persona_buyer),
                item_id=item_id,
                price=price,
                player_name=seller,
                listing_uuid=listing_uuid,
                transaction_uuid=tx_uuid,
                rcon_func=rcon_func,
            )
            return {"message_sent": True, "persona": persona_buyer["name"],
                    "item": item_id, "to_player": seller}

    return {}


def _on_cancel(**kwargs) -> dict:
    """Called when a listing is cancelled.

    We notify personas that had their eye on this item.
    """
    cfg = get_config()
    if not cfg.get("enabled", True):
        return {}

    listing_uuid = kwargs.get("listing_uuid", "")
    item_id = kwargs.get("item_id", "")
    seller = kwargs.get("seller", "")

    if listing_uuid and item_id:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db
        db = sp_get_db()
        # Find personas that had observed this listing
        import json as _json
        memories = db.fetch_all("""
            SELECT * FROM ext_sp_memory
            WHERE item_id = ? AND memory_type = 'observed_price'
              AND detail LIKE ?
            LIMIT 20
        """, (item_id, f"%{listing_uuid[:16]}%"))

        for mem in memories:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import add_memory
            add_memory(mem["persona_uuid"], "missed_deal", item_id,
                       mem.get("price"),
                       detail={"reason": "listing_cancelled", "seller": seller},
                       emotional_weight=4)

    return {}


# ══════════════════════════════════════════════════════════════════════
# Extension Entry Point
# ══════════════════════════════════════════════════════════════════════

def on_load(registry):
    """Called by ah_plugin_registry when discovering extensions.

    Args:
        registry: The _HookRegistry instance from ah_plugin_registry
    """
    cfg = get_config()
    if not cfg.get("enabled", True):
        log.info("sp_extension", "Simulated People extension is disabled in config — skipping load")
        return

    # 1. Ensure database schema exists
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema
    ensure_schema()

    # 1c. Initialize THREAD-enhanced memory bridge (graceful if unavailable)
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_memory_thread import (
            get_persona_thread, is_thread_available
        )
        pt = get_persona_thread()
        if pt and pt.initialized:
            log.info('sp_extension', 'THREAD memory bridge initialized for persona memories')
        elif not is_thread_available():
            log.info('sp_extension', 'THREAD memory bridge unavailable — using legacy SQL memory')
    except Exception as e:
        log.warn('sp_extension', f'THREAD bridge init skipped: {e}')

    # 1a. Ensure item cache + trash tables
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import ensure_all_tables
    ensure_all_tables()

    # 1b. Seed item definitions (safe to call repeatedly)
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import seed_item_defs
    seed_item_defs()

    # 2. Register hooks with priority=10 (runs before SHM at priority=20)
    #    This fixes GAP #3 — explicit tick ordering: sp_combat wounds process first
    registry.register("on_simulation_cycle_start", "SIMULATED_PEOPLE",
                      _on_simulation_cycle_start, priority=10)
    registry.register("on_listing_created", "SIMULATED_PEOPLE", _on_listing_created)
    registry.register("on_listing_queried", "SIMULATED_PEOPLE", _on_listing_queried)
    registry.register("on_purchase", "SIMULATED_PEOPLE", _on_purchase)
    registry.register("on_cancel", "SIMULATED_PEOPLE", _on_cancel)

    log.info("sp_extension",
             f"Loaded: persona pool={cfg['persona_pool_size']}, "
             f"max active={cfg['max_active_personas']}, "
             f"tick={cfg['tick_interval_minutes']}min")

