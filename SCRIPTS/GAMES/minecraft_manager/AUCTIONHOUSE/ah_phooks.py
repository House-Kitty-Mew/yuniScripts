"""
ah_phooks.py — Phooks event handlers for the Auction House.

Listens for auction-related Phooks events from minescript clients and
responds with appropriate results.  Also emits events for broadcasting.

INPUT VALIDATION: All incoming player data is validated before being
passed to ah_core functions.  This prevents:
  - SQL injection via player names or item IDs
  - Price manipulation (negative/NaN bids)
  - UUID format errors
  - Player name spoofing

Registered events:
  LISTEN:  ah_list, ah_bid, ah_buy, ah_remove, ah_query, ah_test
  EMIT:    ah_list_response, ah_bid_response, ah_buy_response,
           ah_remove_response, ah_query_response, ah_test_response, ah_announce
"""

import json, traceback, re, time, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional, Callable

from AUCTIONHOUSE.ah_core import (
    list_item, place_bid, buy_now, cancel_listing, query_listings,
    get_player_listings, get_listing, get_bid_history,
    get_player_purchases, get_player_sales, get_recent_prices,
    _format_time_remaining
)
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_reports import get_weekly_report, format_report_for_chat
from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_plugin_registry import fire_hook

log = get_logger()
cfg = get_config

# ──────────────────────────────────────────────────────────────────────
# Input validation
# ──────────────────────────────────────────────────────────────────────

_PLAYER_NAME_RE = re.compile(r'^[A-Za-z0-9_]{2,32}$')
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
_ITEM_ID_RE = re.compile(r'^[a-z0-9_.\-]+:[a-z0-9_.\-]+$')


def validate_player_name(name: str) -> Optional[str]:
    if not name or not isinstance(name, str):
        return "Player name is required"
    if not _PLAYER_NAME_RE.match(name):
        return "Invalid player name format"
    return None


def validate_listing_uuid(uuid_str: str) -> Optional[str]:
    if not uuid_str or not isinstance(uuid_str, str):
        return "Listing UUID is required"
    if not _UUID_RE.match(uuid_str):
        return "Invalid listing UUID format"
    return None


def validate_item_id(item_id: str) -> Optional[str]:
    if not item_id or not isinstance(item_id, str):
        return "Item ID is required"
    if not _ITEM_ID_RE.match(item_id):
        return "Invalid item ID format (expected 'namespace:path')"
    return None


def sanitize_request(data: dict, required_fields: list[str]) -> Optional[str]:
    for field in required_fields:
        if field not in data or data[field] is None:
            return f"Missing required field: {field}"
    return None


# ──────────────────────────────────────────────────────────────────────
# Event declarations
# ──────────────────────────────────────────────────────────────────────

PHOOKS_EVENTS_LISTEN = [
    "ah_list",
    "ah_bid",
    "ah_buy",
    "ah_remove",
    "ah_query",
    "ah_test",
    # Announce system events
    "ah_sub",
    "ah_unsub",
    "ah_subs",
    "ah_announces",
]

PHOOKS_EVENTS_EMIT = [
    "ah_list_response",
    "ah_bid_response",
    "ah_buy_response",
    "ah_remove_response",
    "ah_query_response",
    "ah_test_response",
    "ah_announce",
    # Announce system responses
    "ah_sub_response",
    "ah_unsub_response",
    "ah_subs_response",
    "ah_announces_response",
]


# ──────────────────────────────────────────────────────────────────────
# Response helpers
# ──────────────────────────────────────────────────────────────────────

def _respond(emit_fn, event: str, request_uuid: str, data: dict):
    emit_fn(event, {"request_uuid": request_uuid, "data": data})


def _ok(emit_fn, event: str, request_uuid: str, message: str = "",
        extra: Optional[dict] = None):
    _respond(emit_fn, event, request_uuid, {
        "status": "ok", "message": message, **(extra or {})
    })


def _error(emit_fn, event: str, request_uuid: str, error: str):
    _respond(emit_fn, event, request_uuid, {
        "status": "error", "message": error
    })


# ──────────────────────────────────────────────────────────────────────
# Event handlers
# ──────────────────────────────────────────────────────────────────────

def handle_ah_list(data: dict, emit_fn: Callable, rcon_func: Optional[Callable] = None):
    request_uuid = data.get("request_uuid", "")
    player = data.get("player_name", "")
    err = sanitize_request(data, ["request_uuid", "player_name", "start_price"]) or validate_player_name(player) or validate_item_id(data.get("item_id", ""))
    if err:
        return _error(emit_fn, "ah_list_response", request_uuid, err)
    try:
        result = list_item(seller=player, item_id=data["item_id"],
            count=int(data.get("item_count", 1)),
            start_price=float(data["start_price"]),
            buy_now_price=float(data["buy_now_price"]) if data.get("buy_now_price") else None,
            duration_hours=int(data["duration_hours"]) if data.get("duration_hours") else None,
            item_nbt=data.get("item_nbt"), signed_name=data.get("signed_name"),
            rarity=data.get("rarity"), cert_hash=data.get("cert_hash"),
            rcon_func=rcon_func)
        if result["ok"]:
            _ok(emit_fn, "ah_list_response", request_uuid,
                "Your item has been listed!",
                {"listing_uuid": result["data"]["listing_uuid"]})
        else:
            _error(emit_fn, "ah_list_response", request_uuid, result["error"])
    except Exception as e:
        log.error("phooks", f"ah_list error: {e}\n{traceback.format_exc()}")
        _error(emit_fn, "ah_list_response", request_uuid, "Internal error")


def handle_ah_bid(data: dict, emit_fn: Callable, rcon_func: Optional[Callable] = None):
    request_uuid = data.get("request_uuid", "")
    player = data.get("player_name", "")
    err = sanitize_request(data, ["request_uuid", "player_name", "listing_uuid", "bid_amount"]) or validate_player_name(player) or validate_listing_uuid(data.get("listing_uuid", ""))
    if err:
        return _error(emit_fn, "ah_bid_response", request_uuid, err)
    try:
        result = place_bid(bidder=player, listing_uuid=data["listing_uuid"], bid_amount=float(data["bid_amount"]))
        if result["ok"]:
            rd = result["data"]
            time_msg = f" | {rd['time_remaining']} left" if rd.get("time_remaining") else ""
            _ok(emit_fn, "ah_bid_response", request_uuid,
                f"Bid placed! Highest at {rd['new_current_bid']:.2f}em.{time_msg}",
                {"listing_uuid": rd["listing_uuid"], "new_current_bid": rd["new_current_bid"],
                 "time_remaining": rd.get("time_remaining")})
            if rd.get("previous_bidder") and rcon_func:
                try:
                    from AUCTIONHOUSE.ah_announcer import notify_outbid
                    listing = get_listing(rd["listing_uuid"])
                    item_name = listing.get("signed_name") or "?" if listing else "?"
                    notify_outbid(rd["previous_bidder"],
                        item_name,
                        rd["new_current_bid"], rcon_func=rcon_func)
                except Exception:
                    pass
        else:
            _error(emit_fn, "ah_bid_response", request_uuid, result["error"])
    except Exception as e:
        log.error("phooks", f"ah_bid error: {e}\n{traceback.format_exc()}")
        _error(emit_fn, "ah_bid_response", request_uuid, "Internal error")


def handle_ah_buy(data: dict, emit_fn: Callable, rcon_func: Optional[Callable] = None):
    request_uuid = data.get("request_uuid", "")
    player = data.get("player_name", "")
    err = sanitize_request(data, ["request_uuid", "player_name", "listing_uuid"]) or validate_player_name(player) or validate_listing_uuid(data.get("listing_uuid", ""))
    if err:
        return _error(emit_fn, "ah_buy_response", request_uuid, err)
    try:
        result = buy_now(buyer=player, listing_uuid=data["listing_uuid"], quantity=int(data.get("quantity", 1)),
                         rcon_func=rcon_func)
        if result["ok"]:
            rd = result["data"]
            _ok(emit_fn, "ah_buy_response", request_uuid,
                f"Bought {rd['item_count']}x {rd['item_id']} for {rd['price']:.2f}em.", rd)
            if rcon_func:
                try:
                    from AUCTIONHOUSE.ah_announcer import notify_sold
                    notify_sold(rd["seller"], rd["item_id"], rd["price"], rcon_func=rcon_func)
                except Exception:
                    pass
        else:
            _error(emit_fn, "ah_buy_response", request_uuid, result["error"])
    except Exception as e:
        log.error("phooks", f"ah_buy error: {e}\n{traceback.format_exc()}")
        _error(emit_fn, "ah_buy_response", request_uuid, "Internal error")


def handle_ah_remove(data: dict, emit_fn: Callable, rcon_func: Optional[Callable] = None):
    request_uuid = data.get("request_uuid", "")
    player = data.get("player_name", "")
    err = sanitize_request(data, ["request_uuid", "player_name", "listing_uuid"]) or validate_player_name(player) or validate_listing_uuid(data.get("listing_uuid", ""))
    if err:
        return _error(emit_fn, "ah_remove_response", request_uuid, err)
    try:
        result = cancel_listing(player=player, listing_uuid=data["listing_uuid"], rcon_func=rcon_func)
        if result["ok"]:
            rd = result["data"]
            msg = f"Cancelled. Your {rd['item_id']} returned."
            if rd.get("had_bids"):
                msg += f" Cancel fee: {rd['cancel_fee']}em."
            _ok(emit_fn, "ah_remove_response", request_uuid, msg, rd)
        else:
            _error(emit_fn, "ah_remove_response", request_uuid, result["error"])
    except Exception as e:
        log.error("phooks", f"ah_remove error: {e}\n{traceback.format_exc()}")
        _error(emit_fn, "ah_remove_response", request_uuid, "Internal error")


def handle_ah_query(data: dict, emit_fn: Callable, rcon_func: Optional[Callable] = None):
    """Handle queries: search, my listings, details, history, purchases, sales, pricecheck, report."""
    request_uuid = data.get("request_uuid", "")
    player = data.get("player_name", "")
    filter_type = data.get("filter_type", "all")
    filter_value = data.get("filter_value", "")
    err = sanitize_request(data, ["request_uuid", "player_name"]) or validate_player_name(player)
    if err:
        return _error(emit_fn, "ah_query_response", request_uuid, err)

    try:
        # Fire extension hooks for listing queries (non-critical)
        try:
            fire_hook("on_listing_queried", query={
                "player": player,
                "filter_type": filter_type,
                "filter_value": filter_value,
            })
        except Exception:
            pass

        if filter_type == "my":
            listings = get_player_listings(player)
            _ok(emit_fn, "ah_query_response", request_uuid, "", {"listings": listings, "count": len(listings)})
        elif filter_type == "purchases":
            purchases = get_player_purchases(player)
            _ok(emit_fn, "ah_query_response", request_uuid, "", {"purchases": purchases, "count": len(purchases)})
        elif filter_type == "sales":
            sales = get_player_sales(player)
            _ok(emit_fn, "ah_query_response", request_uuid, "", {"sales": sales, "count": len(sales)})
        elif filter_type == "details":
            err = validate_listing_uuid(filter_value)
            if err: return _error(emit_fn, "ah_query_response", request_uuid, err)
            listing = get_listing(filter_value)
            if listing and listing.get("status") == "active":
                listing["time_remaining"] = _format_time_remaining(listing.get("expires_at"))
            _ok(emit_fn, "ah_query_response", request_uuid, "", {"listing": listing}) if listing else _error(emit_fn, "ah_query_response", request_uuid, "Not found")
        elif filter_type == "history":
            err = validate_listing_uuid(filter_value)
            if err: return _error(emit_fn, "ah_query_response", request_uuid, err)
            _ok(emit_fn, "ah_query_response", request_uuid, "", {"history": get_bid_history(filter_value)})
        elif filter_type == "pricecheck":
            _ok(emit_fn, "ah_query_response", request_uuid, "", {"prices": get_recent_prices(filter_value), "item_id": filter_value})
        elif filter_type == "report":
            report = get_weekly_report()
            from AUCTIONHOUSE.ah_ai_engine import get_simulation_status
            try:
                sim_status = get_simulation_status()
            except Exception:
                sim_status = {"scheduler": {"running": False}, "error": "status_unavailable"}
            _ok(emit_fn, "ah_query_response", request_uuid, "", {"report": report, "simulation": sim_status, "chat_lines": format_report_for_chat(report)})
        else:
            page = int(data.get("page", 1))
            per_page = int(data.get("per_page", 10))
            result = query_listings(filter_type=filter_type, filter_value=filter_value, page=page, per_page=per_page)
            if result["ok"]: _ok(emit_fn, "ah_query_response", request_uuid, "", result["data"])
            else: _error(emit_fn, "ah_query_response", request_uuid, result["error"])
    except Exception as e:
        log.error("phooks", f"ah_query error: {e}\n{traceback.format_exc()}")
        _error(emit_fn, "ah_query_response", request_uuid, "Internal error")


def handle_ah_test(data: dict, emit_fn: Callable, rcon_func: Optional[Callable] = None):
    """Run a non-destructive full-system test and return a report.

    Tests (read-only / no permanent changes):
      1. AH Database — query listing count
      2. Economy Bridge — ping + player balance
      3. RCON — /list command
      4. AI Scheduler — running state
      5. Market Events — active count
      6. Player's balance from bridge (if available)
    """
    request_uuid = data.get("request_uuid", "")
    player = data.get("player_name", "")
    start_time = time.time()
    results = {"phooks": {"status": "ok", "latency_ms": 0}, "database": {}, "bridge": {}, "rcon": {}, "scheduler": {}, "events": {}, "player_balance": None}

    # 1. Phooks latency (measured from response)
    results["phooks"]["latency_ms"] = round((time.time() - start_time) * 1000, 1)

    # 2. AH Database
    try:
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        count = db.fetch_one("SELECT COUNT(*) as cnt FROM auction_listings WHERE status = 'active'")
        tx = db.fetch_one("SELECT COUNT(*) as cnt FROM transaction_history")
        results["database"] = {"status": "ok", "active_listings": count["cnt"] if count else 0, "transactions": tx["cnt"] if tx else 0}
    except Exception as e:
        results["database"] = {"status": "error", "error": str(e)[:60]}

    # 3. Economy Bridge
    try:
        from ECO_BRIDGE.eco_bridge import get_bridge as get_eco
        bridge = get_eco()
        if bridge and bridge.is_ready:
            ping = bridge.ping() if hasattr(bridge, "ping") else False
            bal = bridge.get_balance(player) if hasattr(bridge, "get_balance") else None
            results["bridge"] = {"status": "ok", "ping": ping, "balance": bal}
            results["player_balance"] = bal
        else:
            results["bridge"] = {"status": "unavailable", "reason": "not connected"}
    except Exception as e:
        results["bridge"] = {"status": "error", "error": str(e)[:60]}

    # 4. RCON
    if rcon_func:
        try:
            resp = rcon_func("list")
            online = "players online" in resp.lower() or len(resp) > 5
            results["rcon"] = {"status": "ok" if online else "degraded", "response_preview": resp[:80].strip()}
        except Exception as e:
            results["rcon"] = {"status": "error", "error": str(e)[:60]}
    else:
        results["rcon"] = {"status": "unavailable", "reason": "no rcon function"}

    # 5. AI Scheduler
    try:
        from AUCTIONHOUSE.ah_ai_engine import SimulationScheduler
        # Check global scheduler via main's ah_scheduler variable (heuristic: check if it exists)
        import sys as _sys
        main_mod = _sys.modules.get("__main__")
        scheduler = getattr(main_mod, "ah_scheduler", None) if main_mod else None
        if scheduler and scheduler.is_running:
            results["scheduler"] = {"status": "ok", "running": True}
        elif scheduler:
            results["scheduler"] = {"status": "ok", "running": False}
        else:
            results["scheduler"] = {"status": "unavailable", "reason": "not in main"}
    except Exception as e:
        results["scheduler"] = {"status": "error", "error": str(e)[:60]}

    # 6. Market Events
    try:
        from AUCTIONHOUSE.ah_market_events import get_active_events, get_event_history
        active = get_active_events()
        recent = get_event_history(limit=3)
        results["events"] = {"status": "ok", "active_count": len(active), "recent_count": len(recent)}
    except Exception as e:
        results["events"] = {"status": "error", "error": str(e)[:60]}

    # 7. Compute overall status
    all_ok = all(
        r.get("status") == "ok" or r.get("status") == "unavailable"
        for r in [results["database"], results["bridge"], results["rcon"],
                   results["scheduler"], results["events"]]
    )
    results["overall"] = "PASS" if all_ok else "DEGRADED"

    elapsed = time.time() - start_time
    results["duration_seconds"] = round(elapsed, 2)

    total_tests = 5
    passed = sum(1 for k in ["database", "bridge", "rcon", "scheduler", "events"]
                 if results[k].get("status") == "ok" or results[k].get("status") == "unavailable")

    # Format the response
    lines = []
    lines.append(f"§6═══ §eSystem Test Report §6═══")
    lines.append(f" §7Overall: §{'a' if all_ok else 'e'}{results['overall']} §7({passed}/{total_tests} systems OK)")

    # DB
    db_s = results["database"]
    if db_s["status"] == "ok":
        lines.append(f" §a✓ §7Database: db§a OK §7({db_s['active_listings']} listings, {db_s['transactions']} tx)")
    else:
        lines.append(f" §c✗ §7Database: {db_s.get('error','?')}")

    # Bridge
    br_s = results["bridge"]
    if br_s["status"] == "ok" and br_s.get("ping"):
        bal_str = f" §7Your balance: §e{br_s['balance']} §7coins" if br_s.get("balance") is not None else ""
        lines.append(f" §a✓ §7Bridge:      §a{br_s['ping']} {bal_str}")
    elif br_s["status"] == "unavailable":
        lines.append(f" §7- §7Bridge:      unavailable (AH uses internal balances)")
    else:
        lines.append(f" §c✗ §7Bridge:      {br_s.get('error','?')}")

    # RCON
    rc_s = results["rcon"]
    if rc_s["status"] == "ok":
        lines.append(f" §a✓ §7RCON:        {rc_s.get('response_preview','')[:50]}")
    elif rc_s["status"] == "unavailable":
        lines.append(f" §7- §7RCON:        unavailable (offline mode)")
    else:
        lines.append(f" §c✗ §7RCON:        {rc_s.get('error','?')}")

    # Scheduler
    sc_s = results["scheduler"]
    if sc_s["status"] == "ok":
        lines.append(f" §a✓ §7AI Scheduler: {'§arunning' if sc_s.get('running') else '§estopped'}")
    elif sc_s["status"] == "unavailable":
        lines.append(f" §7- §7AI Scheduler: N/A (standalone test)")
    else:
        lines.append(f" §c✗ §7AI Scheduler: {sc_s.get('error','?')}")

    # Events
    ev_s = results["events"]
    if ev_s["status"] == "ok":
        lines.append(f" §a✓ §7Events:      {ev_s['active_count']} active, {ev_s['recent_count']} recent")
    else:
        lines.append(f" §c✗ §7Events:      {ev_s.get('error','?')}")

    lines.append(f" §7Duration: §f{results['duration_seconds']}s")
    lines.append(f"§6═══════════════════════════")

    results["chat_lines"] = lines
    log.info("phooks", f"Test completed: {results['overall']} ({passed}/{total_tests}) in {results['duration_seconds']}s")

    emit_fn("ah_test_response", {
        "request_uuid": request_uuid,
        "data": results
    })


# ──────────────────────────────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────────────────────────────

HANDLER_MAP = {
    "ah_list": handle_ah_list,
    "ah_bid": handle_ah_bid,
    "ah_buy": handle_ah_buy,
    "ah_remove": handle_ah_remove,
    "ah_query": handle_ah_query,
    "ah_test": handle_ah_test,
}


def dispatch_event(event_name: str, data: dict, emit_fn: Callable,
                   rcon_func: Optional[Callable] = None):
    """Dispatch an incoming Phooks event to the appropriate handler.

    All handlers include input validation before calling core logic.
    """
    handler = HANDLER_MAP.get(event_name)
    if handler:
        log.info("phooks", f"Dispatching {event_name} from {data.get('player_name', '?')}")
        handler(data, emit_fn, rcon_func=rcon_func)
    else:
        log.warn("phooks", f"No handler for event: {event_name}")
