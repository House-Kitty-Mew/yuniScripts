"""
SIMULATED_TRADE — AH Extension: Trade, Barter, Routes, Banditry & Economy.

Adds a complete tick-based merchant/trade economy to the Simulated People
ecosystem.  Personas can trade directly, build trade routes between claims,
engage in banditry on trade routes, and experience world events that cause
resource scarcity.

See DESIGN.md for full specification.

Extension lifecycle:
  on_load(registry) -> init DB, register hooks
  on_simulation_cycle_start -> tr_ai_engine.before_tick
  tick processing -> tr_routes, tr_banditry, tr_world_events, tr_economy
  on_simulation_cycle_end -> tr_ai_engine.after_tick
"""

import json, threading
from pathlib import Path
from typing import Any

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import VALID_HOOKS, _HookRegistry
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state

log = get_logger()

_EXT_NAME = "SIMULATED_TRADE"
_initialized = False
_init_lock = threading.Lock()


def on_load(registry: _HookRegistry) -> dict:
    """Extension entry point. Called by ah_plugin_registry on startup.

    Args:
        registry: The central hook registry instance.

    Returns:
        {"ok": True, "extension": _EXT_NAME} or {"ok": False, "error": str}
    """
    global _initialized

    with _init_lock:
        if _initialized:
            return {"ok": True, "extension": _EXT_NAME, "note": "already initialized"}

        try:
            # 1. Load config
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import load_config
            config = load_config()
            log.info("SIMULATED_TRADE", "Config loaded successfully")

            # 2. Initialize database tables
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import init_database
            if not init_database():
                return {"ok": False, "error": "Database initialization failed"}

            # 3. Initialize world event resource states
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_world_events import (
                initialize_resource_states
            )
            initialize_resource_states()

            # 4. Initialize economy
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_economy import (
                initialize_economy
            )
            initialize_economy()

            # 5. Register hooks
            _register_hooks(registry)

            _initialized = True
            log.info("SIMULATED_TRADE", "Extension loaded and initialized")

            return {"ok": True, "extension": _EXT_NAME}

        except Exception as e:
            log.error("SIMULATED_TRADE", f"Extension load failed: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}


def _register_hooks(registry: _HookRegistry) -> None:
    """Register all trade extension hooks."""

    # ── Simulation cycle hooks ──────────────────────────────────
    registry.register("on_simulation_cycle_start", _EXT_NAME, _on_cycle_start)
    registry.register("on_simulation_cycle_end", _EXT_NAME, _on_cycle_end)

    # ── Persona hooks ───────────────────────────────────────────
    registry.register("on_persona_activated", _EXT_NAME, _on_persona_activated)
    registry.register("on_persona_deactivated", _EXT_NAME, _on_persona_deactivated)

    # ── Trade hooks ─────────────────────────────────────────────
    registry.register("on_trade_completed", _EXT_NAME, _on_trade_completed)
    registry.register("on_route_constructed", _EXT_NAME, _on_route_constructed)
    registry.register("on_banditry_completed", _EXT_NAME, _on_banditry_completed)
    registry.register("on_ai_trade_decisions_completed", _EXT_NAME, _on_ai_decisions_completed)

    # ── World event hooks ───────────────────────────────────────
    registry.register("on_world_event", _EXT_NAME, _on_world_event_triggered)

    log.info("SIMULATED_TRADE", f"Registered {8} hooks")


# ── Hook callback implementations ────────────────────────────────────

def _on_cycle_start(**kwargs) -> dict:
    """Called at the start of each simulation cycle.

    Runs pre-tick processing for routes, banditry, world events, economy.
    """
    try:
        # Process route caravans and maintenance
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_routes import process_routes_tick
        route_stats = process_routes_tick()

        # Process world events and resource regeneration
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_world_events import (
            check_and_trigger_events,
            process_world_events_tick,
        )
        event_trigger = check_and_trigger_events()
        event_stats = process_world_events_tick()

        # Process reputation decay
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_reputation import (
            process_reputation_tick
        )
        rep_stats = process_reputation_tick()

        # Process economy tick
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_economy import (
            process_economy_tick
        )
        eco_stats = process_economy_tick()

        # Guard patrol
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_banditry import (
            process_guard_response
        )
        guard_stats = process_guard_response()

        # Fire AI trade decisions hook
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_ai_engine import before_tick
        before_tick(**kwargs)

        return {
            "routes": route_stats,
            "events_triggered": event_trigger.get("triggered", False),
            "events": event_stats,
            "reputation": rep_stats,
            "economy": eco_stats,
            "guards": guard_stats,
        }
    except Exception as e:
        log.error("SIMULATED_TRADE", f"Cycle start error: {e}")
        return {"error": str(e)}


def _on_cycle_end(**kwargs) -> dict:
    """Called at the end of each simulation cycle.

    Applies AI trade decisions and updates state registry.
    """
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_ai_engine import after_tick
        after_tick(**kwargs)
        return {"ok": True}
    except Exception as e:
        log.error("SIMULATED_TRADE", f"Cycle end error: {e}")
        return {"error": str(e)}


def _on_persona_activated(**kwargs) -> dict:
    """Called when a persona is activated.

    Initializes reputation tracking for the persona.
    """
    persona_uuid = kwargs.get("persona_uuid")
    if not persona_uuid:
        return {"error": "No persona_uuid provided"}

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_reputation import (
        init_persona_reputation
    )
    result = init_persona_reputation(persona_uuid)
    return result


def _on_persona_deactivated(**kwargs) -> dict:
    """Called when a persona is deactivated.

    Cleans up any pending trade offers from the persona.
    """
    persona_uuid = kwargs.get("persona_uuid")
    if not persona_uuid:
        return {"error": "No persona_uuid provided"}

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db
    db = get_db()
    db.execute("""
        UPDATE ext_tr_pending_trades
        SET status = 'expired'
        WHERE (initiator_uuid = ? OR target_uuid = ?) AND status = 'open'
    """, (persona_uuid, persona_uuid))
    db.conn.commit()

    return {"ok": True, "expired_offers": True}


def _on_trade_completed(**kwargs) -> dict:
    """Called when a trade is completed.

    Updates economy supply/demand and state registry.
    """
    trade_uuid = kwargs.get("trade_uuid")
    trade_type = kwargs.get("trade_type", "currency")

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_economy import (
        process_trade_economy_impact
    )

    if trade_uuid:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db
        db = get_db()
        cursor = db.execute(
            "SELECT * FROM ext_tr_trades WHERE trade_uuid = ?",
            (trade_uuid,)
        )
        row = cursor.fetchone()
        if row:
            trade = dict(row)
            offered = json.loads(trade.get("items_offered", "{}"))
            received = json.loads(trade.get("items_received", "{}"))
            region = trade.get("location_area", "unknown")

            process_trade_economy_impact(trade_type, offered, received, region)

    return {"ok": True}


def _on_route_constructed(**kwargs) -> dict:
    """Called when a trade route is constructed.

    Updates state registry.
    """
    route_uuid = kwargs.get("route_uuid")
    persona_uuid = kwargs.get("persona_uuid")

    state = get_state()
    route_count = state.get("route_count", 0)
    state.set("route_count", route_count + 1, "SIMULATED_TRADE")
    state.set("last_route_constructed", route_uuid, "SIMULATED_TRADE")

    return {"ok": True}


def _on_banditry_completed(**kwargs) -> dict:
    """Called when a banditry incident is completed.

    Updates economy with stolen goods impact.
    """
    loot_value = kwargs.get("loot_value", 0.0)
    route_uuid = kwargs.get("route_uuid")

    if route_uuid and loot_value > 0:
        # Record economy impact of lost goods
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_db
        db = get_db()
        cursor = db.execute(
            "SELECT * FROM ext_tr_routes WHERE route_uuid = ?",
            (route_uuid,)
        )
        row = cursor.fetchone()
        if row:
            route = dict(row)
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_economy import adjust_supply
            # Goods stolen = supply loss at destination
            adjust_supply("looted_goods", route.get("from_claim_uuid", "unknown"),
                          -loot_value / 100.0)

    return {"ok": True}


def _on_ai_decisions_completed(**kwargs) -> dict:
    """Called after AI trade decisions are applied."""
    return {"ok": True}


def _on_world_event_triggered(**kwargs) -> dict:
    """Called when a world event is triggered."""
    return {"ok": True}
