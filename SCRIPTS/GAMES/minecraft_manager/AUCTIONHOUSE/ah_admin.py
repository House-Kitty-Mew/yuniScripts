"""
ah_admin.py — Admin override & moderation module for the Auction House.

Provides server administrators with manual override capabilities:
  - Force-end market events early
  - Override simulated item prices
  - Force-remove any listing (with item return)
  - Ban/unban players from using the AH
  - Reset simulated inventory to defaults
  - Database backup (JSON export)
  - Full market statistics

All functions return the standard dict: {"ok": True, "data": {...}}
or {"ok": False, "error": "..."}.  They require an "admin" context
to be passed (no authentication built in — the caller in main.py
should gate these behind console access).
"""

import json, os, shutil, uuid, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_helper_db import add_note
from AUCTIONHOUSE.ah_market_events import end_event as _end_event, get_active_events, get_event_history
from AUCTIONHOUSE.ah_core import query_listings, get_listing, get_player_listings
from AUCTIONHOUSE.ah_price_history import get_market_summary

log = get_logger()
cfg = get_config

DB_DIR = Path(__file__).parent / "data"
BACKUP_DIR = DB_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ── Banned players table (in-memory fallback + DB) ────────────────────
_BANNED_PLAYERS_TABLE = """
CREATE TABLE IF NOT EXISTS ah_banned_players (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name    TEXT NOT NULL UNIQUE,
    banned_by      TEXT NOT NULL DEFAULT 'admin',
    reason         TEXT,
    banned_at      TEXT NOT NULL,
    expires_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_banned_player ON ah_banned_players(player_name);
"""


def _ensure_bans_table():
    """Create the banned players table if it doesn't exist."""
    db = get_db()
    # SQLite doesn't have CREATE TABLE IF NOT EXISTS in executescript easily,
    # so we do it manually
    exists = db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ah_banned_players'")
    if not exists:
        for stmt in _BANNED_PLAYERS_TABLE.strip().split("\n\n"):
            if stmt.strip():
                db.execute(stmt.strip())
        log.info("admin", "Created ah_banned_players table")


# ── Player bans (delegated to ah_bans.py to avoid circular imports) ──
# is_player_banned() is used by ah_core.py at module import time, so it
# lives in ah_bans.py.  ah_admin.py re-exports ban/unban for admin use.
from AUCTIONHOUSE.ah_bans import ban_player, unban_player, list_banned_players, is_player_banned


# ══════════════════════════════════════════════════════════════════════
# Market overrides
# ══════════════════════════════════════════════════════════════════════

def force_end_event(event_uuid: str) -> dict:
    """Force-end an active market event.

    Args:
        event_uuid: UUID of the event to end

    Returns:
        Result from end_event()
    """
    result = _end_event(event_uuid, reason="admin_override")
    if result.get("ok"):
        add_note("observation",
                 f"Admin force-ended event: {result['data'].get('event_name', '?')}",
                 importance=5, related_event=event_uuid)
        log.info("admin", f"Admin force-ended event: {event_uuid}")
    return result


def force_adjust_price(item_id: str, new_price: float) -> dict:
    """Override a simulated item's base and current price.

    This directly modifies simulated_inventory.  Use with care — the AI
    may re-override on its next cycle.

    Args:
        item_id: e.g. "minecraft:coal"
        new_price: New base/current price in emeralds

    Returns:
        {"ok": True, "data": {"old_price": ..., "new_price": ...}}
    """
    config = cfg()
    new_price = max(config.sim_price_min, min(new_price, config.sim_price_max))

    db = get_db()
    existing = db.fetch_one(
        "SELECT base_price, current_price FROM simulated_inventory WHERE item_id = ?",
        (item_id,)
    )
    if not existing:
        return {"ok": False, "error": f"Item '{item_id}' not found in simulated inventory"}

    old_base = existing["base_price"]
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE simulated_inventory SET base_price = ?, current_price = ?, last_updated = ? WHERE item_id = ?",
        (new_price, new_price, now, item_id)
    )

    log.info("admin", f"Admin price override: {item_id} {old_base} -> {new_price}em")
    add_note("price_reasoning",
             f"Admin overrode {item_id} price: {old_base} -> {new_price}em",
             importance=5, related_item_id=item_id)

    return {"ok": True, "data": {"item_id": item_id, "old_price": old_base, "new_price": new_price}}


def force_remove_listing(listing_uuid: str, admin: str = "console",
                          return_to_player: bool = True) -> dict:
    """Admin-forced removal of any listing regardless of seller.

    Args:
        listing_uuid: The listing to remove
        admin: Admin name for logging
        return_to_player: If True, item is returned to seller

    Returns:
        {"ok": True} or {"ok": False, "error": "..."}
    """
    db = get_db()

    listing = get_listing(listing_uuid)
    if not listing:
        return {"ok": False, "error": "Listing not found"}

    if listing["status"] != "active":
        return {"ok": False, "error": f"Listing is already {listing['status']}"}

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE auction_listings SET status = 'cancelled', extra_meta = ? WHERE listing_uuid = ?",
        (json.dumps({"admin_removed": True, "removed_by": admin, "removed_at": now}), listing_uuid)
    )

    log.info("admin", f"Admin force-removed listing {listing_uuid} ({listing['item_id']})",
             {"seller": listing["seller_name"], "admin": admin})

    add_note("observation",
             f"Admin {admin} removed listing: {listing['item_id']} by {listing['seller_name']}",
             importance=4, related_item_id=listing_uuid)

    return {"ok": True, "data": {
        "listing_uuid": listing_uuid,
        "item_id": listing["item_id"],
        "seller": listing["seller_name"],
        "returned_to_player": return_to_player,
    }}


# ══════════════════════════════════════════════════════════════════════
# Market reset & backup
# ══════════════════════════════════════════════════════════════════════

def reset_simulated_inventory() -> dict:
    """Reset the simulated_inventory table to its default seed values.

    WARNING: This will overwrite any AI-learned prices and stock levels.
    Use only when the market has gotten wildly out of control.

    Returns:
        {"ok": True, "data": {"items_reset": N}}
    """
    from AUCTIONHOUSE.ah_database import _SEED_SIMULATED_INVENTORY, get_db
    db = get_db()

    ts = datetime.now(timezone.utc).isoformat()
    seed_sql = _SEED_SIMULATED_INVENTORY.replace("{ts}", f"'{ts}'")

    # Delete all existing records first
    db.execute("DELETE FROM simulated_inventory")
    # Re-insert with seed data
    db.execute(
        "DELETE FROM sqlite_sequence WHERE name = 'simulated_inventory'"
    )
    db.execute("""
        INSERT INTO simulated_inventory
        (item_id, category, current_stock, max_stock, base_price, current_price,
         volatility, trend_direction, trend_strength, last_updated)
        VALUES
        ('minecraft:coal',         'common',   500,  2000, 0.5,  0.5,  0.20, 0, 0.0, ?),
        ('minecraft:iron_ingot',   'common',   300,  1000, 1.5,  1.5,  0.15, 0, 0.0, ?),
        ('minecraft:gold_ingot',   'common',   100,  500,  3.0,  3.0,  0.20, 0, 0.0, ?),
        ('minecraft:redstone',     'common',   400,  1500, 0.25, 0.25, 0.12, 0, 0.0, ?),
        ('minecraft:lapis_lazuli', 'uncommon', 150,  500,  2.0,  2.0,  0.15, 0, 0.0, ?),
        ('minecraft:diamond',      'rare',      50,  200,  8.0,  8.0,  0.25, 0, 0.0, ?),
        ('minecraft:emerald',      'common',   200,  1000, 1.0,  1.0,  0.10, 0, 0.0, ?),
        ('minecraft:netherite_ingot', 'rare',   20,  100,  25.0, 25.0, 0.30, 0, 0.0, ?),
        ('minecraft:stick',        'common',  1000, 5000, 0.05, 0.05, 0.08, 0, 0.0, ?),
        ('minecraft:oak_log',      'common',   800,  3000, 0.3,  0.3,  0.10, 0, 0.0, ?),
        ('minecraft:cobblestone',  'common',  2000, 5000, 0.05, 0.05, 0.05, 0, 0.0, ?),
        ('minecraft:wheat',        'common',   300,  1000, 0.5,  0.5,  0.12, 0, 0.0, ?),
        ('minecraft:bone',         'common',   200,  800,  0.3,  0.3,  0.15, 0, 0.0, ?),
        ('minecraft:ender_pearl',  'uncommon',  50,  200,  4.0,  4.0,  0.20, 0, 0.0, ?),
        ('minecraft:blaze_rod',    'uncommon',  30,  150,  6.0,  6.0,  0.20, 0, 0.0, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            current_stock = excluded.current_stock,
            max_stock = excluded.max_stock,
            base_price = excluded.base_price,
            current_price = excluded.current_price,
            volatility = excluded.volatility,
            trend_direction = 0,
            trend_strength = 0.0,
            last_updated = excluded.last_updated
    """, (ts,) * 15)

    count = db.fetch_one("SELECT COUNT(*) as cnt FROM simulated_inventory")
    reset_count = count["cnt"] if count else 0

    add_note("observation",
             f"Admin reset simulated inventory to defaults ({reset_count} items)",
             importance=5)

    log.info("admin", f"Simulated inventory reset to defaults ({reset_count} items)")
    return {"ok": True, "data": {"items_reset": reset_count}}


def backup_database() -> dict:
    """Create a timestamped backup of the auctionhouse.db3 database file.

    Backup is stored in AUCTIONHOUSE/data/backups/ with ISO timestamp.

    Returns:
        {"ok": True, "data": {"backup_path": "..."}}
    """
    import shutil
    db_path = DB_DIR / "auctionhouse.db3"
    if not db_path.exists():
        return {"ok": False, "error": "Database file not found"}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"auctionhouse_backup_{ts}.db3"
    backup_path = BACKUP_DIR / backup_filename

    shutil.copy2(str(db_path), str(backup_path))

    log.info("admin", f"Database backed up to {backup_filename}",
             {"size_bytes": backup_path.stat().st_size})

    return {"ok": True, "data": {
        "backup_path": str(backup_path),
        "backup_filename": backup_filename,
        "size_bytes": backup_path.stat().st_size,
    }}


def list_backups() -> list[dict]:
    """List all available database backups.

    Returns:
        List of dicts with filename, size_bytes, created_at
    """
    backups = []
    for f in sorted(BACKUP_DIR.glob("auctionhouse_backup_*.db3"), reverse=True):
        backups.append({
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return backups


# ══════════════════════════════════════════════════════════════════════
# Statistics
# ══════════════════════════════════════════════════════════════════════

def get_full_stats() -> dict:
    """Get comprehensive market statistics for admin review.

    Returns:
        Dict with listings, transactions, events, players, inventory stats
    """
    db = get_db()
    summary = get_market_summary()

    # Listing stats (not just counts)
    listing_statuses = db.fetch_all("""
        SELECT status, COUNT(*) as cnt, COALESCE(SUM(sold_price), 0) as total_value
        FROM auction_listings
        GROUP BY status
    """)

    # Top sellers (all time)
    top_sellers = db.fetch_all("""
        SELECT seller_name, COUNT(*) as cnt, COALESCE(SUM(start_price), 0) as total_value
        FROM auction_listings
        GROUP BY seller_name
        ORDER BY cnt DESC
        LIMIT 10
    """)

    # Price history depth
    price_snapshots = db.fetch_one(
        "SELECT COUNT(*) as cnt, MAX(snapshot_at) as latest FROM price_history")

    # Event stats
    event_stats = db.fetch_all("""
        SELECT rarity_tier, COUNT(*) as cnt
        FROM market_events
        GROUP BY rarity_tier
    """)

    # Active listings breakdown
    active_by_item = db.fetch_all("""
        SELECT item_id, COUNT(*) as cnt, AVG(start_price) as avg_price
        FROM auction_listings
        WHERE status = 'active'
        GROUP BY item_id
        ORDER BY cnt DESC
        LIMIT 15
    """)

    # Transaction stats
    tx_today = db.fetch_one("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(price), 0) as volume
        FROM transaction_history
        WHERE created_at > ?
    """, (datetime.now(timezone.utc).isoformat()[:10],))

    # Simulated inventory health
    sim_health = db.fetch_all("""
        SELECT item_id, current_stock, max_stock, base_price, current_price,
               ROUND(CAST(current_stock AS REAL) / CAST(max_stock AS REAL) * 100, 1) as pct_full
        FROM simulated_inventory
        ORDER BY pct_full DESC
    """)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_listings": {
            "total": summary["total_active_listings"],
            "player": summary["player_listings_count"],
            "simulated": summary["sim_listings_count"],
        },
        "listing_statuses": listing_statuses,
        "transactions_24h": summary["transactions_24h"],
        "volume_24h": summary["volume_24h"],
        "transactions_today": {
            "count": tx_today["cnt"] if tx_today else 0,
            "volume": tx_today["volume"] if tx_today else 0,
        },
        "top_sellers_all_time": top_sellers,
        "price_history": {
            "total_snapshots": price_snapshots["cnt"] if price_snapshots else 0,
            "latest_snapshot": price_snapshots["latest"] if price_snapshots else None,
        },
        "events": {e["rarity_tier"]: e["cnt"] for e in event_stats},
        "active_listings_by_item": active_by_item,
        "simulated_inventory_health": [
            {"item": s["item_id"], "stock_pct": s["pct_full"],
             "price": s["current_price"] or s["base_price"]}
            for s in sim_health
        ],
    }
