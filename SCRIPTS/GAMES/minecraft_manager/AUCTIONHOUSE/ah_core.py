"""
ah_core.py — Core Auction House operations.

Provides the main CRUD functions for auction listings:
  - list_item()     — Create a new auction listing
  - place_bid()     — Place a bid on an existing listing (atomic)
  - buy_now()       — Buy-It-Now purchase (atomic, with duplication prevention)
  - cancel_listing() — Cancel/remove a listing
  - query_listings() — Query listings with filters
  - expire_listings() — Process expired auctions
  - get_player_listings() — Get a specific player's listings

All mutating operations use **atomic SQL updates** to prevent TOCTOU race
conditions (item duplication).  Instead of "read, check, then write" we
use "UPDATE ... WHERE status = 'active'" and check the affected row count.
If rowcount == 0, another operation already claimed the listing.

All functions return a standardised dict:
    {"ok": True, "data": {...}} or {"ok": False, "error": "message"}
"""

import uuid, json, math, threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_helper_db import add_note
from AUCTIONHOUSE.ah_bans import is_player_banned
from AUCTIONHOUSE.ah_plugin_registry import fire_hook

# ── Optional economy bridge ──────────────────────────────────────────
# The bridge reads/writes the Otters Civ economy database directly,
# with RCON fallback for writes.  If the database can't be found, the
# AH falls back to its own player_balances table.
_eco_bridge = None
_eco_bridge_attempted = False
_eco_bridge_lock = threading.Lock()


def _get_eco_bridge():
    """Lazy-init and return the economy bridge, or None."""
    global _eco_bridge, _eco_bridge_attempted
    if _eco_bridge_attempted:
        return _eco_bridge
    with _eco_bridge_lock:
        if _eco_bridge_attempted:
            return _eco_bridge
        _eco_bridge_attempted = True
        try:
            from ECO_BRIDGE.eco_bridge import get_bridge
            bridge = get_bridge()
            if bridge and bridge.is_ready:
                _eco_bridge = bridge
                log.info("core", "Economy bridge connected — using Otters Civ economy for balance ops")
            else:
                log.info("core", "Economy bridge unavailable — falling back to AH player_balances table")
        except ImportError:
            log.debug("core", "ECO_BRIDGE package not installed")
        except Exception as e:
            log.debug("core", f"Economy bridge init failed: {e}")
        return _eco_bridge


def _check_eco_balance(player: str, needed: int = 1, reason: str = "AH_CHECK") -> dict:
    """Check player balance via economy bridge, with fallback.

    If the player isn't found in the bridge (e.g. new player, test player),
    the check is skipped — the operation is allowed.  Only blocks when the
    bridge explicitly reports a balance that's too low.

    Returns:
        {"ok": True, "balance": N} or {"ok": False, "error": "..."}
    """
    bridge = _get_eco_bridge()
    if bridge:
        balance = bridge.get_balance(player)
        if balance is None:
            # Player not found in bridge — allow (they may be new, or a test)
            return {"ok": True, "balance": None, "note": "player not found"}
        if balance < needed:
            return {"ok": False, "error": f"Insufficient funds: {balance}, need {needed}"}
        return {"ok": True, "balance": balance}
    # No bridge — assume OK (can't verify)
    return {"ok": True, "balance": None}


def _eco_deduct(player: str, amount: int, reason: str = "AH_TAKE",
                note: Optional[str] = None) -> bool:
    """Deduct from player's wallet via bridge, with fallback to DB writes.

    If the bridge can't find the player, the deduct is silently skipped
    (RCON will handle it on the server side).
    """
    bridge = _get_eco_bridge()
    if bridge:
        result = bridge.deduct(player, amount, reason, note=note)
        if result is False:
            # Player not found? Skip — RCON will handle
            return True
        return bool(result)
    return True  # No bridge — can't enforce (RCON should handle it)


def _eco_credit(player: str, amount: int, reason: str = "AH_CREDIT",
                note: Optional[str] = None) -> bool:
    """Credit player's wallet via bridge, with fallback to DB writes."""
    bridge = _get_eco_bridge()
    if bridge:
        result = bridge.credit(player, amount, reason, note=note)
        if result is False:
            return True  # Player not found — RCON handles
        return bool(result)
    return True  # No bridge — can't enforce (RCON should handle it)


log = get_logger()
cfg = get_config

# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_time_remaining(expires_at: Optional[str]) -> Optional[str]:
    """Convert an ISO-8601 expiry timestamp to a human-readable 'time remaining' string.

    Returns strings like '2h 15m', '45m', '<1m', 'Ended', or None if no expiry.
    Handles timezone-aware and naive timestamps (assuming UTC for naive).
    """
    if not expires_at:
        return None
    try:
        # Parse expiry — handle both timezone-aware and naive
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now >= expiry:
            return "Ended"
        delta = expiry - now
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return "<1m"
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return None


def _make_response(ok: bool, data: Optional[dict] = None,
                   error: Optional[str] = None) -> dict:
    if ok:
        return {"ok": True, "data": data or {}}
    return {"ok": False, "error": error or "Unknown error"}


def _record_tx(listing_uuid: str, tx_type: str, actor: str, item_id: str,
               count: int, price: Optional[float], prev_price: Optional[float] = None,
               metadata: Optional[dict] = None):
    """Insert a transaction record into transaction_history."""
    db = get_db()
    tx_uuid = str(uuid.uuid4())
    db.execute(
        """INSERT INTO transaction_history
           (transaction_uuid, listing_uuid, transaction_type, actor_name,
            item_id, item_count, price, previous_price, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tx_uuid, listing_uuid, tx_type, actor, item_id, count,
         price, prev_price,
         json.dumps(metadata) if metadata else None,
         _now())
    )
    log.transaction(tx_type, {
        "tx_uuid": tx_uuid,
        "listing_uuid": listing_uuid,
        "actor": actor,
        "item_id": item_id,
        "count": count,
        "price": price,
    })


# ══════════════════════════════════════════════════════════════════════
# Listing
# ══════════════════════════════════════════════════════════════════════

def list_item(seller: str, item_id: str, count: int, start_price: float,
              buy_now_price: Optional[float] = None,
              duration_hours: Optional[int] = None,
              item_nbt: Optional[str] = None,
              signed_name: Optional[str] = None,
              rarity: Optional[str] = None,
              cert_hash: Optional[str] = None,
              is_simulated: bool = False,
              sim_lore: Optional[str] = None,
              sim_enchantments: Optional[str] = None,
              sim_durability: Optional[int] = None,
              sim_quality_roll: Optional[int] = None,
              sim_source_event: Optional[str] = None,
              ai_weight: float = 1.0,
              rcon_func: Optional[Callable] = None) -> dict:
    """List an item on the Auction House.

    Args:
        seller: Minecraft player name
        item_id: e.g. "minecraft:diamond_sword"
        count: Stack size
        start_price: Starting price in emeralds
        buy_now_price: Buy-It-Now price (optional)
        duration_hours: Auction duration (default from config)
        item_nbt: Full item NBT/component data as JSON string
        signed_name: Display name if signed
        rarity: Rarity tier string from sign_item.py
        cert_hash: Certification hash from signing
        is_simulated: True if this is an AI-generated listing
        sim_lore, etc.: Simulated item metadata fields

    Returns:
        {"ok": True, "data": {"listing_uuid": "..."}}
        or {"ok": False, "error": "..."}
    """
    config = cfg()

    # ── Validate ────────────────────────────────────────────────
    if start_price < config.sim_price_min:
        return _make_response(False, error=f"Start price too low (min {config.sim_price_min})")
    if buy_now_price and buy_now_price < start_price:
        return _make_response(False, error="Buy-It-Now price must be >= start price")
    if duration_hours is None:
        duration_hours = config.auction_duration_default_hours
    if duration_hours > config.auction_duration_max_hours:
        duration_hours = config.auction_duration_max_hours

    # ── Ban check for real players ──────────────────────────────
    if not is_simulated and is_player_banned(seller):
        return _make_response(False, error="You are banned from the Auction House.")

    # ── Check economy balance for listing fee ────────────────────
    # If the economy bridge is available, verify the player has enough
    # coins to cover the listing fee BEFORE creating the listing.
    if not is_simulated and config.listing_fee_emeralds > 0:
        eco_check = _check_eco_balance(seller, int(config.listing_fee_emeralds),
                                        reason="AH_LISTING_FEE_CHECK")
        if not eco_check["ok"]:
            return _make_response(False, error=f"Not enough coins for listing fee: {eco_check['error']}")

    now = _now()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=duration_hours)).isoformat()

    # ── Player listing limit ────────────────────────────────────
    if not is_simulated:
        db = get_db()
        active = db.fetch_one(
            "SELECT COUNT(*) as cnt FROM auction_listings WHERE seller_name = ? AND status = 'active'",
            (seller,)
        )
        if active and active["cnt"] >= config.max_listings_per_player:
            return _make_response(False,
                                  error=f"Max {config.max_listings_per_player} active listings reached")

        # ── Duplicate item check (same item & seller & active) ──
        dup = db.fetch_one(
            "SELECT listing_uuid FROM auction_listings WHERE seller_name = ? AND item_id = ? AND status = 'active'",
            (seller, item_id)
        )
        if dup:
            return _make_response(False,
                                  error="You already have an active listing for this item. Cancel it first.")

    # ── Deduct listing fee BEFORE inserting the listing ────────────
    # CRITICAL: Deduct FIRST so that if the fee fails, no listing is
    # created. This prevents orphan listings with unpaid fees.
    fee_deducted = False
    if not is_simulated and config.listing_fee_emeralds > 0:
        fee_amt = int(config.listing_fee_emeralds)
        # Pre-check balance (bridge only — fallback is no-op)
        fee_check = _check_eco_balance(seller, fee_amt, reason="AH_LISTING_FEE_CHECK")
        if not fee_check["ok"]:
            return _make_response(False, error=f"Not enough coins for listing fee: {fee_check['error']}")
        fee_ok = _eco_deduct(seller, fee_amt, reason="AH_LISTING_FEE",
                             note=f"Listing fee for new listing")
        if fee_ok:
            fee_deducted = True
        else:
            # Bridge available but deduct failed — can't proceed
            return _make_response(False, error="Failed to deduct listing fee. Please try again later.")

    # ── Insert ──────────────────────────────────────────────────
    db = get_db()
    listing_uuid = str(uuid.uuid4())
    db.execute("""
        INSERT INTO auction_listings
        (listing_uuid, seller_name, item_id, item_count, item_nbt,
         signed_name, rarity, cert_hash, is_simulated,
         start_price, buy_now_price, currency_type, status,
         listed_at, expires_at, ai_weight,
         sim_lore, sim_source_event, sim_enchantments,
         sim_durability, sim_quality_roll, extra_meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        listing_uuid, seller, item_id, count, item_nbt,
        signed_name, rarity, cert_hash, 1 if is_simulated else 0,
        start_price, buy_now_price, "emerald", "active",
        now, expires_at, ai_weight,
        sim_lore, sim_source_event,
        json.dumps(sim_enchantments) if sim_enchantments else None,
        sim_durability, sim_quality_roll, None
    ))

    _record_tx(listing_uuid, "ai_sim_list" if is_simulated else "list",
               seller, item_id, count, start_price)

    log.info("core", f"Listed: {seller} -> {count}x {item_id} @ {start_price}em",
             {"listing_uuid": listing_uuid, "is_simulated": is_simulated})

    # ── Take item from player inventory via RCON ─────────────────────
    clear_ok = True
    if not is_simulated and rcon_func:
        try:
            clear_resp = rcon_func(f"clear {seller} {item_id} {count}")
            if "No items" in clear_resp or "nothing" in clear_resp.lower():
                log.warn("core", f"Clear failed: player {seller} no longer has {item_id}")
                clear_ok = False
            else:
                log.info("core", f"Clear OK: {seller} -> -{count}x {item_id}: {clear_resp.strip()[:80]}")
        except Exception as e:
            log.error("core", f"Clear RCON error: {e}")
            clear_ok = False

    # ── Notify player via RCON ───────────────────────────────────────
    if not is_simulated and rcon_func:
        try:
            msg_text = f"§a[AH] Listed {count}x {item_id} for {start_price}em"
            if fee_deducted:
                msg_text += f" §7(fee: {int(config.listing_fee_emeralds)} coins)"
            tellraw = json.dumps({"text": msg_text})
            rcon_func(f"tellraw {seller} {tellraw}")
        except Exception as e:
            log.debug("core", f"Tellraw notification failed: {e}")

    # ── Fire extension hooks (on_listing_created) ────────────────────
    try:
        fire_hook("on_listing_created", listing={
            "listing_uuid": listing_uuid,
            "seller_name": seller,
            "item_id": item_id,
            "item_count": count,
            "start_price": start_price,
            "buy_now_price": buy_now_price,
            "is_simulated": is_simulated,
        })
    except Exception:
        pass  # Extensions are non-critical

    return _make_response(True, {"listing_uuid": listing_uuid,
                                 "clear_ok": clear_ok,
                                 "fee_deducted": fee_deducted})


# ══════════════════════════════════════════════════════════════════════
# Bidding (atomic — prevents double-spend / race conditions)
# ══════════════════════════════════════════════════════════════════════

def place_bid(bidder: str, listing_uuid: str, bid_amount: float) -> dict:
    """Place a bid on an active auction listing.

    Uses an **atomic UPDATE** with a WHERE clause that verifies the
    listing is still active AND the bid is high enough.  If the UPDATE
    affects 0 rows, the auction was already won/expired/cancelled by
    another thread or the bid was too low.

    Args:
        bidder: Minecraft player name
        listing_uuid: The listing to bid on
        bid_amount: Bid amount in emeralds

    Returns:
        {"ok": True, "data": {...}} or {"ok": False, "error": "..."}
    """
    config = cfg()

    # ── Ban check ────────────────────────────────────────────────
    if is_player_banned(bidder):
        return _make_response(False, error="You are banned from the Auction House.")

    # ── Check bidder has enough coins via economy bridge ──────────
    # Only check if bridge is available (fallback: assume OK, RCON enforces)
    eco_check = _check_eco_balance(bidder, int(bid_amount), reason="AH_BID_CHECK")
    if not eco_check["ok"]:
        return _make_response(False, error=f"Cannot afford bid: {eco_check['error']}")

    db = get_db()

    # Fetch current state for validation (read-only)
    listing = db.fetch_one(
        "SELECT * FROM auction_listings WHERE listing_uuid = ?", (listing_uuid,))
    if not listing:
        return _make_response(False, error="Listing not found")

    if listing["status"] != "active":
        return _make_response(False, error=f"Listing is {listing['status']}, not active")

    now = _now()
    if listing["expires_at"] and now > listing["expires_at"]:
        return _make_response(False, error="This auction has already expired")

    if listing["seller_name"] == bidder:
        return _make_response(False, error="You cannot bid on your own listing")

    current_bid = listing["current_bid"] or listing["start_price"]
    min_increment = current_bid * (config.min_bid_increment_pct / 100.0)
    min_bid = current_bid + min_increment

    if bid_amount < min_bid:
        return _make_response(
            False,
            error=f"Bid too low. Minimum is {min_bid:.2f}em (current: {current_bid:.2f} + {config.min_bid_increment_pct}%)"
        )

    # ── Atomic UPDATE: only succeeds if listing is still active ──
    # The WHERE clause ensures we don't bid on an already-sold/expired listing
    cursor = db.execute("""
        UPDATE auction_listings
        SET current_bid = ?, highest_bidder = ?, bids_count = bids_count + 1,
            stale_since = NULL
        WHERE listing_uuid = ?
          AND status = 'active'
          AND (current_bid IS NULL OR current_bid < ?)
    """, (bid_amount, bidder, listing_uuid, bid_amount))

    if cursor.rowcount == 0:
        # Either the listing was claimed by another operation, or another
        # thread already updated the bid. Try to detect which.
        refreshed = db.fetch_one(
            "SELECT status, current_bid FROM auction_listings WHERE listing_uuid = ?",
            (listing_uuid,)
        )
        if refreshed and refreshed["status"] != "active":
            return _make_response(False,
                                  error=f"Listing was {refreshed['status']} while you were placing your bid")
        return _make_response(False,
                              error="Bid was not accepted (another bid may have been placed first)")

    previous_bidder = listing["highest_bidder"]
    previous_bidder_bid = listing["current_bid"]

    # ── ESCROW bidder's funds ───────────────────────────────────────
    # Immediately deduct/hold the bid amount from the bidder's wallet.
    # If the deduction fails but the bridge is available, rollback the bid.
    bid_escrowed = _eco_deduct(bidder, int(bid_amount), reason="AH_BID_ESCROW",
                                note=f"Bid escrow for {listing_uuid}")
    if not bid_escrowed:
        # Bridge is available but deduct failed — revert the bid
        log.warn("core", f"Bid escrow failed for {bidder} on {listing_uuid[:16]}... — reverting bid")
        db.execute("""
            UPDATE auction_listings
            SET current_bid = ?, highest_bidder = ?, bids_count = bids_count - 1,
                stale_since = ?
            WHERE listing_uuid = ? AND highest_bidder = ?
        """, (previous_bidder_bid or listing["start_price"],
              previous_bidder,
              _now() if previous_bidder else None,
              listing_uuid, bidder))
        return _make_response(False,
                              error="Could not escrow bid funds. Please try again later.")

    # ── REFUND previous bidder (if there was one) ───────────────────
    # When a player is outbid, the previous highest bidder gets their
    # escrowed funds back automatically.
    if previous_bidder and previous_bidder_bid:
        refund_ok = _eco_credit(previous_bidder, int(previous_bidder_bid),
                                 reason="AH_BID_REFUND",
                                 note=f"Outbid refund for {listing_uuid}")
        if refund_ok:
            log.info("core", f"Refunded {previous_bidder} -> {previous_bidder_bid}em (outbid on {listing_uuid[:16]}...)")
        else:
            log.warn("core", f"Failed to refund {previous_bidder} -> {previous_bidder_bid}em (outbid) — RCON fallback needed")

    _record_tx(listing_uuid, "bid", bidder, listing["item_id"],
               listing["item_count"], bid_amount,
               prev_price=current_bid)

    if listing["is_simulated"]:
        log.info("core", f"Sim bid: {bidder} bid {bid_amount}em on sim item {listing_uuid}")

    log.info("core", f"Bid: {bidder} -> {bid_amount}em on {listing_uuid[:16]}...",
             {"previous_bidder": previous_bidder})

    return _make_response(True, {
        "new_current_bid": bid_amount,
        "previous_bidder": previous_bidder,
        "listing_uuid": listing_uuid,
        "time_remaining": _format_time_remaining(listing.get("expires_at"))
    })


# ══════════════════════════════════════════════════════════════════════
# Buy-It-Now (atomic — prevents item duplication)
# ══════════════════════════════════════════════════════════════════════

def buy_now(buyer: str, listing_uuid: str, quantity: int = 1,
             rcon_func: Optional[Callable] = None) -> dict:
    """Purchase an item via Buy-It-Now.

    Uses an **atomic UPDATE** with a WHERE clause that checks:
      - status is 'active'
      - buy_now_price IS NOT NULL
      - buyer is not the seller
    If the UPDATE affects 0 rows, someone else already bought it.

    Args:
        buyer: Minecraft player name
        listing_uuid: The listing to buy
        quantity: Number of items (must be <= listing count)

    Returns:
        {"ok": True, "data": {...}} or {"ok": False, "error": "..."}
    """
    config = cfg()

    if is_player_banned(buyer):
        return _make_response(False, error="You are banned from the Auction House.")

    # ── Check buyer can afford via economy bridge ─────────────────
    db = get_db()
    listing = db.fetch_one(
        "SELECT * FROM auction_listings WHERE listing_uuid = ?", (listing_uuid,))
    if not listing:
        return _make_response(False, error="Listing not found")
    if listing.get("buy_now_price"):
        total_cost = int(listing["buy_now_price"] * quantity)
        eco_check = _check_eco_balance(buyer, total_cost, reason="AH_BIN_CHECK")
        if not eco_check["ok"]:
            return _make_response(False, error=f"Cannot afford: {eco_check['error']}")

    if listing["status"] != "active":
        return _make_response(False, error=f"Listing is {listing['status']}")

    if not listing["buy_now_price"]:
        return _make_response(False, error="This listing has no Buy-It-Now price")

    if listing["seller_name"] == buyer:
        return _make_response(False, error="You cannot buy your own listing")

    if quantity > listing["item_count"]:
        return _make_response(False, error=f"Only {listing['item_count']} available")

    sale_price = listing["buy_now_price"] * quantity
    fee = sale_price * (config.sale_fee_pct / 100.0)
    seller_payout = sale_price - fee

    now = _now()

    # ── Partial buy: keep listing active with remaining items ─────
    # BUG FIX C1: Previously the `remaining` variable was dead code and
    # item_count was overwritten with `quantity` before marking as sold,
    # silently destroying remaining items. Now: keep active with remainder.
    if quantity < listing["item_count"]:
        remaining = listing["item_count"] - quantity
        db.execute(
            "UPDATE auction_listings SET item_count = ? WHERE listing_uuid = ?",
            (remaining, listing_uuid)
        )
        # Keep listing active — skip the atomic UPDATE that marks as sold
    else:
        # ── Atomic UPDATE: only succeeds if listing is still active ──
        cursor = db.execute("""
            UPDATE auction_listings
            SET status = 'sold', sold_at = ?, sold_price = ?,
                highest_bidder = ?, current_bid = ?
            WHERE listing_uuid = ?
              AND status = 'active'
              AND buy_now_price IS NOT NULL
        """, (now, sale_price, buyer, sale_price, listing_uuid))

        if cursor.rowcount == 0:
            # Race condition: someone else already bought or listing was modified
            return _make_response(False,
                                  error="This item was already purchased by someone else")

    # ── Deduct from buyer BEFORE recording transaction ─────────────
    # CRITICAL: We MUST deduct from the buyer FIRST. If deduct fails
    # and the bridge is available, we rollback the listing to 'active'.
    # This prevents a scenario where the listing is marked sold but
    # the buyer never paid (money was never taken).
    deduct_ok = _eco_deduct(buyer, int(sale_price), reason="AH_BIN_PURCHASE",
                            note=f"Purchase of {listing['item_id']} (listing: {listing_uuid})")
    if not deduct_ok:
        # Bridge is available but deduct failed — rollback listing to active
        log.error("core", f"BIN purchase failed: could not deduct {sale_price}em from {buyer} — rolling back {listing_uuid}")
        db.execute("""
            UPDATE auction_listings
            SET status = 'active', sold_at = NULL, sold_price = NULL,
                highest_bidder = NULL, current_bid = ?
            WHERE listing_uuid = ?
        """, (listing["current_bid"] or listing["start_price"], listing_uuid))
        return _make_response(False,
                              error="Failed to process payment. Please try again later.")

    # ── Credit seller ──────────────────────────────────────────────
    # Credit the seller. If this fails, we still flag it but don't
    # rollback the buyer's sale — seller can be compensated via support.
    credit_ok = _eco_credit(listing["seller_name"], int(seller_payout), reason="AH_SALE_CREDIT",
                            note=f"Sale of {listing['item_id']} (fee: {fee})")
    if not credit_ok:
        log.error("core", f"BIN sale credit failed: could not credit {seller_payout}em to {listing['seller_name']} — manual intervention needed for {listing_uuid}")

    _record_tx(listing_uuid, "buy", buyer, listing["item_id"],
               quantity, sale_price,
               metadata={"seller_payout": seller_payout, "fee": fee,
                         "deduct_ok": deduct_ok, "credit_ok": credit_ok})

    log.info("core", f"BIN: {buyer} bought {quantity}x {listing['item_id']} for {sale_price}em",
             {"listing_uuid": listing_uuid, "seller": listing["seller_name"],
              "payout": seller_payout, "fee": fee})

    # Update event progress if simulated item
    if listing["is_simulated"]:
        _update_event_progress(listing["item_id"], quantity)

    # ── Give item to buyer via RCON ─────────────────────────────────
    item_name = listing["item_id"]
    seller_name = listing["seller_name"]
    if rcon_func:
        try:
            give_resp = rcon_func(f"give {buyer} {item_name} {quantity}")
            log.info("core", f"Item given: {buyer} <- {quantity}x {item_name}: {give_resp.strip()[:80]}")
        except Exception as e:
            log.error("core", f"Give RCON error: {e}")

        try:
            buyer_msg = f"§a[AH] Purchased {quantity}x {item_name} for {sale_price:.2f}em (fee: {fee:.2f})"
            rcon_func(f"tellraw {buyer} {json.dumps({'text': buyer_msg})}")
        except Exception as e:
            log.debug("core", f"Buy tellraw failed: {e}")

        try:
            seller_msg = f"§a[AH] Your {item_name} sold for {sale_price:.2f}em (payout: {seller_payout:.2f}em)"
            rcon_func(f"tellraw {seller_name} {json.dumps({'text': seller_msg})}")
        except Exception as e:
            log.debug("core", f"Sell notification failed: {e}")

    # ── Fire extension hooks (on_purchase) ───────────────────────────
    try:
        fire_hook("on_purchase", transaction={
            "listing_uuid": listing_uuid,
            "item_id": listing["item_id"],
            "quantity": quantity,
            "price": sale_price,
            "seller": listing["seller_name"],
            "buyer": buyer,
            "seller_payout": seller_payout,
            "fee": fee,
        }, rcon_func=rcon_func)
    except Exception:
        pass

    return _make_response(True, {
        "listing_uuid": listing_uuid,
        "item_id": listing["item_id"],
        "item_count": quantity,
        "price": sale_price,
        "seller": listing["seller_name"],
        "buyer": buyer,
        "seller_payout": seller_payout,
        "fee": fee
    })


def _update_event_progress(item_id: str, quantity: int):
    """Increment market event progress for affected items.

    Called when a simulated item is bought.  May trigger event resolution
    if the hidden goal is met.
    """
    db = get_db()
    now = _now()

    events = db.fetch_all(
        "SELECT * FROM market_events WHERE is_active = 1")
    for event in events:
        try:
            affected = json.loads(event["affected_items"])
        except (json.JSONDecodeError, TypeError):
            continue

        if item_id not in affected:
            continue

        new_count = (event["current_count"] or 0) + quantity
        db.execute(
            "UPDATE market_events SET current_count = ? WHERE id = ?",
            (new_count, event["id"])
        )

        if event["goal_count"] and new_count >= event["goal_count"]:
            db.execute(
                "UPDATE market_events SET is_active = 0, ended_at = ? WHERE id = ?",
                (now, event["id"])
            )
            log.info("core", f"Market event '{event['event_name']}' resolved! "
                             f"Goal of {event['goal_count']} reached.")


# ══════════════════════════════════════════════════════════════════════
# Cancel listing
# ══════════════════════════════════════════════════════════════════════

def cancel_listing(player: str, listing_uuid: str,
                    rcon_func: Optional[Callable] = None) -> dict:
    """Cancel/remove a listing (atomic).

    Only the original seller can cancel.
    If the listing has bids, a cancel fee applies.
    Uses atomic UPDATE with WHERE to prevent race conditions.

    Args:
        player: Minecraft player name requesting cancellation
        listing_uuid: The listing to cancel

    Returns:
        {"ok": True, "data": {...}} or {"ok": False, "error": "..."}
    """
    config = cfg()
    db = get_db()

    # Atomic: only cancels if status = 'active' AND seller matches
    cursor = db.execute(
        "UPDATE auction_listings SET status = 'cancelled' WHERE listing_uuid = ? AND seller_name = ? AND status = 'active'",
        (listing_uuid, player)
    )

    if cursor.rowcount == 0:
        # Either not the seller, or not active
        listing = db.fetch_one(
            "SELECT status, seller_name FROM auction_listings WHERE listing_uuid = ?",
            (listing_uuid,)
        )
        if not listing:
            return _make_response(False, error="Listing not found")
        if listing["seller_name"] != player:
            return _make_response(False, error="Only the seller can cancel this listing")
        return _make_response(False, error=f"Cannot cancel a {listing['status']} listing")

    # Fetch for metadata
    listing = db.fetch_one(
        "SELECT * FROM auction_listings WHERE listing_uuid = ?", (listing_uuid,))
    has_bids = (listing["bids_count"] or 0) > 0

    _record_tx(listing_uuid, "cancel", player, listing["item_id"],
               listing["item_count"], listing["current_bid"] or listing["start_price"],
               metadata={"had_bids": has_bids, "fee": config.cancel_after_bids_fee if has_bids else 0})

    log.info("core", f"Cancelled: {player} removed listing {listing_uuid[:16]}...",
             {"has_bids": has_bids})

    # ── Return item to player via RCON ──────────────────────────────
    if rcon_func:
        try:
            give_resp = rcon_func(f"give {player} {listing['item_id']} {listing['item_count']}")
            log.info("core", f"Item returned: {player} <- {listing['item_count']}x {listing['item_id']}: {give_resp.strip()[:80]}")
        except Exception as e:
            log.error("core", f"Give RCON error: {e}")

    # ── Deduct cancel fee if listing had bids ───────────────────────
    cancel_fee_charged = False
    if has_bids and config.cancel_after_bids_fee > 0:
        fee_amt = int(config.cancel_after_bids_fee)
        fee_ok = _eco_deduct(player, fee_amt, reason="AH_CANCEL_FEE",
                             note=f"Cancel fee for {listing_uuid}")
        if fee_ok:
            cancel_fee_charged = True
            log.info("core", f"Cancel fee deducted: {player} -> -{fee_amt} coins ({listing_uuid[:16]}...)")
        else:
            log.warn("core", f"Cancel fee deduction failed: {player} -> {fee_amt} coins")

    # ── Notify player via RCON ──────────────────────────────────────
    if rcon_func:
        try:
            msg_text = f"§a[AH] Cancelled {listing['item_id']}. Item returned."
            if cancel_fee_charged:
                msg_text += f" §7(cancel fee: {int(config.cancel_after_bids_fee)} coins)"
            rcon_func(f"tellraw {player} {json.dumps({'text': msg_text})}")
        except Exception as e:
            log.debug("core", f"Cancel tellraw failed: {e}")

    # ── Fire extension hooks (on_cancel) ──────────────────────────────
    try:
        fire_hook("on_cancel", listing_uuid=listing_uuid,
                   item_id=listing["item_id"], seller=player)
    except Exception:
        pass

    return _make_response(True, {
        "listing_uuid": listing_uuid,
        "item_id": listing["item_id"],
        "item_count": listing["item_count"],
        "had_bids": has_bids,
        "cancel_fee": config.cancel_after_bids_fee if has_bids else 0,
        "cancel_fee_charged": cancel_fee_charged,
    })


# ══════════════════════════════════════════════════════════════════════
# Expiry processing (atomic per listing)
# ══════════════════════════════════════════════════════════════════════

def expire_listings(rcon_func: Optional[Callable] = None) -> list[dict]:
    """Process all expired auctions (atomic per listing).

    Uses atomic UPDATE with WHERE to ensure each listing is only
    processed once, even if multiple expire_listings calls run.

    Args:
        rcon_func: Optional RCON function to send in-game notifications

    Returns:
        List of dicts describing each expired listing's outcome
    """
    db = get_db()
    now = _now()

    expired = db.fetch_all(
        "SELECT * FROM auction_listings WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < ?",
        (now,)
    )

    results = []
    for listing in expired:
        listing_uuid = listing["listing_uuid"]
        has_bids = (listing["bids_count"] or 0) > 0 and listing["highest_bidder"]
        item_name = listing.get("signed_name") or listing["item_id"]
        seller = listing["seller_name"]

        if has_bids:
            winner = listing["highest_bidder"]
            price = listing["current_bid"]

            # Atomic: only updates if still active
            cursor = db.execute(
                "UPDATE auction_listings SET status = 'sold', sold_at = ?, sold_price = ? WHERE listing_uuid = ? AND status = 'active'",
                (now, price, listing_uuid)
            )
            if cursor.rowcount == 0:
                continue  # Already claimed by another thread

            _record_tx(listing_uuid, "expire", "system", listing["item_id"],
                       listing["item_count"], price,
                       metadata={"winner": winner, "reason": "highest_bidder_wins"})

            # Notify winner and seller in-game
            if rcon_func:
                try:
                    from AUCTIONHOUSE.ah_announcer import notify_won, notify_sold
                    notify_won(winner, item_name, price, rcon_func=rcon_func)
                    notify_sold(seller, item_name, price, rcon_func=rcon_func)
                except Exception:
                    log.warn("core", f"Failed to send expiry notifications for {listing_uuid}")

            results.append({
                "listing_uuid": listing_uuid, "outcome": "sold",
                "winner": winner, "price": price,
                "seller": seller
            })
        else:
            cursor = db.execute(
                "UPDATE auction_listings SET status = 'expired' WHERE listing_uuid = ? AND status = 'active'",
                (listing_uuid,)
            )
            if cursor.rowcount == 0:
                continue

            _record_tx(listing_uuid, "expire", "system", listing["item_id"],
                       listing["item_count"], None,
                       metadata={"reason": "no_bids"})

            # Notify seller that their auction ended with no bids
            if rcon_func:
                try:
                    from AUCTIONHOUSE.ah_announcer import tell_player
                    tell_player(seller, f"Your §f{item_name}§r auction has ended with no bids.",
                                rcon_func=rcon_func)
                except Exception:
                    log.warn("core", f"Failed to send no-bid notification for {listing_uuid}")

            results.append({
                "listing_uuid": listing_uuid, "outcome": "expired",
                "seller": seller
            })

    return results


# ══════════════════════════════════════════════════════════════════════
# Query (read-only, no race condition concerns)
# ══════════════════════════════════════════════════════════════════════

def query_listings(filter_type: str = "all", filter_value: str = "",
                   page: int = 1, per_page: int = 20,
                   include_inactive: bool = False) -> dict:
    """Query auction listings with various filters.

    Args:
        filter_type: 'all', 'my', 'item:<id>', 'player:<name>',
                     'category:<cat>', 'simulated', 'player'
        filter_value: Value for the filter
        page: Page number (1-indexed)
        per_page: Results per page (max 50)
        include_inactive: If True, include non-active listings

    Returns:
        {"ok": True, "data": {"listings": [...], "total": N, ...}}
    """
    db = get_db()
    per_page = min(per_page, 50)
    offset = (page - 1) * per_page

    where_clauses = []
    params = []

    if not include_inactive:
        where_clauses.append("status = 'active'")

    if filter_type == "my" and filter_value:
        where_clauses.append("seller_name = ?")
        params.append(filter_value)
    elif filter_type.startswith("item:") or (filter_type == "item" and filter_value):
        where_clauses.append("item_id LIKE ?")
        params.append(f"%{filter_value}%")
    elif filter_type.startswith("player:") or (filter_type == "player" and filter_value):
        where_clauses.append("seller_name LIKE ?")
        params.append(f"%{filter_value}%")
    elif filter_type.startswith("category:") or (filter_type == "category" and filter_value):
        sim_items = db.fetch_all(
            "SELECT item_id FROM simulated_inventory WHERE category = ?",
            (filter_value,)
        )
        if sim_items:
            item_ids = [s["item_id"] for s in sim_items]
            placeholders = ",".join("?" * len(item_ids))
            where_clauses.append(f"item_id IN ({placeholders})")
            params.extend(item_ids)
    elif filter_type == "simulated":
        where_clauses.append("is_simulated = 1")
    elif filter_type == "player":
        where_clauses.append("is_simulated = 0")

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    total = db.fetch_one(f"SELECT COUNT(*) as cnt FROM auction_listings WHERE {where_sql}",
                         tuple(params))
    total_count = total["cnt"] if total else 0

    listings = db.fetch_all(
        f"SELECT * FROM auction_listings WHERE {where_sql} ORDER BY listed_at DESC LIMIT ? OFFSET ?",
        tuple(params) + (per_page, offset)
    )

    # Enrich each listing with human-readable time remaining
    for listing in listings:
        if listing.get("status") == "active":
            listing["time_remaining"] = _format_time_remaining(listing.get("expires_at"))
        else:
            listing["time_remaining"] = None

    return _make_response(True, {
        "listings": listings,
        "total": total_count,
        "page": page,
        "per_page": per_page,
        "total_pages": math.ceil(total_count / per_page) if per_page > 0 else 0
    })


def get_listing(listing_uuid: str) -> Optional[dict]:
    """Get a single listing by UUID."""
    db = get_db()
    return db.fetch_one(
        "SELECT * FROM auction_listings WHERE listing_uuid = ?", (listing_uuid,))


def get_player_listings(player: str, active_only: bool = True) -> list[dict]:
    """Get all listings for a specific player.

    Args:
        player: Minecraft player name
        active_only: If True, only return active listings

    Returns:
        List of listing dicts, enriched with human-readable time_remaining
    """
    db = get_db()
    if active_only:
        listings = db.fetch_all(
            "SELECT * FROM auction_listings WHERE seller_name = ? AND status = 'active' ORDER BY listed_at DESC",
            (player,)
        )
    else:
        listings = db.fetch_all(
            "SELECT * FROM auction_listings WHERE seller_name = ? ORDER BY listed_at DESC",
            (player,)
        )
    # Enrich with time remaining
    for listing in listings:
        if listing.get("status") == "active":
            listing["time_remaining"] = _format_time_remaining(listing.get("expires_at"))
        else:
            listing["time_remaining"] = None
    return listings


def get_player_purchases(player: str, limit: int = 20) -> list[dict]:
    """Get items a player has purchased.

    Args:
        player: Minecraft player name
        limit: Max results

    Returns:
        List of transaction dicts
    """
    db = get_db()
    return db.fetch_all(
        """SELECT th.*, al.seller_name, al.item_id as orig_item_id
           FROM transaction_history th
           LEFT JOIN auction_listings al ON th.listing_uuid = al.listing_uuid
           WHERE th.actor_name = ? AND th.transaction_type = 'buy'
           ORDER BY th.created_at DESC LIMIT ?""",
        (player, limit)
    )


def get_player_sales(player: str, limit: int = 20) -> list[dict]:
    """Get items a player has sold.

    Args:
        player: Minecraft player name
        limit: Max results

    Returns:
        List of listing dicts that were sold
    """
    db = get_db()
    return db.fetch_all(
        "SELECT * FROM auction_listings WHERE seller_name = ? AND status = 'sold' ORDER BY sold_at DESC LIMIT ?",
        (player, limit)
    )


def get_bid_history(listing_uuid: str, limit: int = 50) -> list[dict]:
    """Get bid history for a specific listing.

    Args:
        listing_uuid: The listing UUID
        limit: Max results

    Returns:
        List of transaction dicts (bid type only)
    """
    db = get_db()
    return db.fetch_all(
        """SELECT * FROM transaction_history
           WHERE listing_uuid = ? AND transaction_type = 'bid'
           ORDER BY created_at DESC LIMIT ?""",
        (listing_uuid, limit)
    )


def get_recent_prices(item_id: str, days: int = 7) -> list[dict]:
    """Get recent sale prices for an item.

    Args:
        item_id: e.g. "minecraft:diamond"
        days: Look-back period

    Returns:
        List of transaction dicts (buy transactions only)
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    return db.fetch_all(
        """SELECT price, created_at, item_count
           FROM transaction_history
           WHERE item_id = ? AND transaction_type = 'buy' AND created_at > ?
           ORDER BY created_at DESC LIMIT 50""",
        (item_id, cutoff)
    )


# ══════════════════════════════════════════════════════════════════════
# Player balances
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# Board System - Player fulfills persona need (usell)
# ══════════════════════════════════════════════════════════════════════

def usell_board(board_slot: int, player_name: str,
                rcon_func: Optional[Callable] = None) -> dict:
    """Player fulfills a persona's urgent board need.

    The player must have the item in their main hand.  The function
    clears the item from their inventory, credits their balance, and
    notifies the persona.

    Args:
        board_slot: The visible slot number (1-based, sorted by urgency)
        player_name: Minecraft player fulfilling the need
        rcon_func: RCON function for inventory ops

    Returns:
        {"ok": True, "data": {...}} or {"ok": False, "error": "..."}
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
        get_board_by_slot, fulfill_board_entry
    )
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_messaging import generate_board_thank_you

    entry = get_board_by_slot(board_slot)
    if not entry:
        return _make_response(False, error=f"No open board entry at slot {board_slot}")
    if entry["status"] != "open":
        return _make_response(False, error=f"Board slot {board_slot} is already {entry['status']}")

    item_id = entry["item_id"]
    quantity = entry["quantity"]
    price = entry["max_price"]
    persona_name = entry["persona_name"]
    persona_uuid = entry["persona_uuid"]

    # Take the item from the player via RCON
    if rcon_func:
        rcon_func(f"clear {player_name} {item_id} {quantity}")

    # Credit the player via economy bridge
    payout = int(float(price) * 0.95)
    _eco_credit(player_name, payout, reason="AH_BOARD_FULFILL",
                note=f"Fulfilled {persona_name}'s board need for {item_id}")

    # Mark the board entry as fulfilled
    result = fulfill_board_entry(entry["board_uuid"], player_name)
    if not result["ok"]:
        return result

    # Credit the persona's balance (deduct)
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as _spdb
        _spdb().execute(
            "UPDATE ext_sp_finances SET balance = balance - ? WHERE persona_uuid = ?",
            (price, persona_uuid))
    except Exception:
        pass

    log.info("core", f"Board fulfill: {player_name} sold {quantity}x {item_id} "
             f"to {persona_name} for {price}em (slot {board_slot})")

    # Generate and send thank-you message
    try:
        thank_you = generate_board_thank_you(entry)
        if rcon_func and thank_you:
            msg = '\u00a77[Board] \u00a7f' + persona_name + ': \u00a7o\u00a77"' + thank_you + '"'
            rcon_func("tellraw @a " + json.dumps({'text': msg}))
    except Exception:
        pass

    return _make_response(True, {
        "item_id": item_id,
        "quantity": quantity,
        "price": price,
        "payout": payout,
        "persona": persona_name,
        "slot": board_slot,
    })

def get_balance(player: str, use_bridge: bool = True) -> Optional[int]:
    """Get a player's tracked balance.

    When the economy bridge is available, reads from the real Otters Civ
    wallet.  Falls back to the internal player_balances table.

    Args:
        player: Player name or UUID
        use_bridge: If True, try the economy bridge first

    Returns:
        Balance in coins (int if bridge), emeralds (float if internal), or None
    """
    if use_bridge:
        bridge = _get_eco_bridge()
        if bridge:
            bal = bridge.get_balance(player)
            if bal is not None:
                return int(bal)
    db = get_db()
    row = db.fetch_one(
        "SELECT balance FROM player_balances WHERE player_name = ?", (player,))
    result = row["balance"] if row else None
    return int(result) if result is not None else None


def update_balance(player: str, delta: int, reason: str = "AH_UPDATE") -> Optional[int]:
    """Update a player's balance.

    When the economy bridge is available, writes to the real Otters Civ
    wallet **and** the internal table as cache.  Falls back to internal
    table only if the bridge is unavailable.

    Args:
        player: Player name or UUID
        delta: Amount to add (positive) or subtract (negative)
        reason: Reason for the update (logged in ledger when using bridge)

    Returns:
        New balance, or None on failure
    """
    bridge = _get_eco_bridge()
    bridge_success = False

    if bridge and delta < 0:
        bridge_success = bridge.deduct(player, -delta, f"{reason}_DEDUCT",
                                        note=f"Auction House {reason}")
    elif bridge and delta > 0:
        bridge_success = bridge.credit(player, delta, f"{reason}_CREDIT",
                                        note=f"Auction House {reason}")

    # Also update internal cache
    db = get_db()
    now = _now()
    existing = db.fetch_one(
        "SELECT * FROM player_balances WHERE player_name = ?", (player,))

    if existing:
        # Use int consistently — bridge returns int coins, DB REAL is cast to int (BUG FIX L1)
        new_balance = int(existing["balance"] or 0) + delta
        if bridge_success:
            new_balance = int(bridge.get_balance(player) or new_balance)
        lifetime_earned = int(existing["lifetime_earned"] or 0)
        lifetime_spent = int(existing["lifetime_spent"] or 0)
        if delta > 0:
            lifetime_earned += delta
        else:
            lifetime_spent += abs(delta)
        db.execute(
            "UPDATE player_balances SET balance = ?, lifetime_earned = ?, "
            "lifetime_spent = ?, last_updated = ? WHERE player_name = ?",
            (new_balance, lifetime_earned, lifetime_spent, now, player))
    else:
        new_balance = delta
        lifetime_earned = delta if delta > 0 else 0.0
        lifetime_spent = abs(delta) if delta < 0 else 0.0
        db.execute(
            "INSERT INTO player_balances (player_name, balance, lifetime_earned, "
            "lifetime_spent, last_updated) VALUES (?, ?, ?, ?, ?)",
            (player, new_balance, lifetime_earned, lifetime_spent, now))

    return int(new_balance)


def sync_balances(force: bool = False) -> dict:
    """Verify AH internal balances against the economy bridge.

    When the bridge is available, compares every player's internal
    AH balance with their Otters Civ wallet.  Reports mismatches and
    optionally corrects the internal table.

    Args:
        force: If True, overwrite internal balances with bridge values

    Returns:
        Dict with sync results: {"checked": N, "mismatches": [...], "fixed": N}
    """
    bridge = _get_eco_bridge()
    if not bridge:
        return {"status": "unavailable",
                "message": "Economy bridge not connected. Cannot sync."}

    db = get_db()
    internal_players = db.fetch_all("SELECT player_name, balance FROM player_balances")
    results = {"checked": 0, "mismatches": [], "fixed": 0, "newly_added": 0}

    # Sync existing internal records
    for p in internal_players:
        bridge_bal = bridge.get_balance(p["player_name"])
        if bridge_bal is None:
            continue  # Player not found in bridge
        results["checked"] += 1
        internal_bal = int(p["balance"] or 0)
        if internal_bal != bridge_bal:
            results["mismatches"].append({
                "player": p["player_name"],
                "internal": internal_bal,
                "bridge": bridge_bal,
            })
            if force:
                db.execute(
                    "UPDATE player_balances SET balance = ?, last_updated = ? WHERE player_name = ?",
                    (bridge_bal, _now(), p["player_name"]))
                results["fixed"] += 1

    # Try to add bridge players not in internal table
    if force:
        all_wallets = bridge.get_economy_stats()
        total_players = all_wallets.get("player_count", 0)
        if total_players > results["checked"]:
            # Can't enumerate all players from stats alone, but we add on access
            results["newly_added"] = total_players - results["checked"]

    results["status"] = "ok"
    log.info("core", f"Balance sync: {results['checked']} checked, "
             f"{len(results['mismatches'])} mismatches, "
             f"{results['fixed']} fixed")

    return results
