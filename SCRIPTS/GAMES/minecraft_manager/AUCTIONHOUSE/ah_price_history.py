"""
ah_price_history.py — Price tracking, snapshots, and trend analysis.

Provides:
  - take_snapshot()          — Record current prices for all tracked items
  - get_price_trend()        — Get price trend for a specific item over N days
  - get_market_summary()     — Overall market health summary for the AI prompt
  - get_stale_listings()     — Find listings with no activity beyond the threshold

The AI uses this data to make informed pricing decisions.
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_config import get_config

log = get_logger()
cfg = get_config


def take_snapshot() -> int:
    """Record a price snapshot for every item currently in ``simulated_inventory``
    plus any player-listed items.

    Call this at the end of each AI simulation cycle.

    Returns:
        Number of price records written
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    records = 0

    # 1. Snapshot simulated inventory items
    sim_items = db.fetch_all("SELECT item_id, current_price, base_price FROM simulated_inventory")
    for item in sim_items:
        price = item["current_price"] or item["base_price"] or 0.0

        # Count active listings and volume for this item
        listing_stats = db.fetch_one("""
            SELECT
                COUNT(*) as listing_count,
                COALESCE(AVG(COALESCE(current_bid, start_price)), 0) as price_avg,
                COALESCE(MIN(COALESCE(current_bid, start_price)), 0) as price_min,
                COALESCE(MAX(COALESCE(current_bid, start_price)), 0) as price_max
            FROM auction_listings
            WHERE item_id = ? AND status = 'active'
        """, (item["item_id"],))

        # Count volume sold in last 24h
        yesterday = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        volume = db.fetch_one("""
            SELECT COALESCE(SUM(item_count), 0) as vol
            FROM transaction_history
            WHERE item_id = ?
              AND transaction_type IN ('buy', 'expire')
              AND created_at > ?
        """, (item["item_id"], yesterday))

        db.execute("""
            INSERT INTO price_history
            (item_id, price_avg, price_min, price_max, listing_count, volume_sold, snapshot_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            item["item_id"],
            listing_stats["price_avg"] or price,
            listing_stats["price_min"] or price,
            listing_stats["price_max"] or price,
            listing_stats["listing_count"] or 0,
            volume["vol"] if volume else 0,
            now
        ))
        records += 1

    # 2. Also snapshot any player-listed items not in sim inventory
    player_items = db.fetch_all("""
        SELECT DISTINCT item_id FROM auction_listings
        WHERE is_simulated = 0 AND status = 'active'
        AND item_id NOT IN (SELECT item_id FROM simulated_inventory)
    """)
    for pitem in player_items:
        listing_stats = db.fetch_one("""
            SELECT
                COUNT(*) as listing_count,
                COALESCE(AVG(COALESCE(current_bid, start_price)), 0) as price_avg,
                COALESCE(MIN(COALESCE(current_bid, start_price)), 0) as price_min,
                COALESCE(MAX(COALESCE(current_bid, start_price)), 0) as price_max
            FROM auction_listings
            WHERE item_id = ? AND status = 'active'
        """, (pitem["item_id"],))

        db.execute("""
            INSERT INTO price_history
            (item_id, price_avg, price_min, price_max, listing_count, volume_sold, snapshot_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (
            pitem["item_id"],
            listing_stats["price_avg"] or 0,
            listing_stats["price_min"] or 0,
            listing_stats["price_max"] or 0,
            listing_stats["listing_count"] or 0,
            now
        ))
        records += 1

    log.info("price_history", f"Price snapshot taken: {records} items recorded")
    return records


def get_price_trend(item_id: str, hours: int = 168) -> list[dict]:
    """Get price trend data for a specific item over the last N hours.

    Args:
        item_id: e.g. "minecraft:coal"
        hours: Look-back window (default 168 = 7 days)

    Returns:
        List of {snapshot_at, price_avg, price_min, price_max, volume_sold}
        ordered by time (oldest first)
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    db = get_db()
    return db.fetch_all("""
        SELECT snapshot_at, price_avg, price_min, price_max, volume_sold
        FROM price_history
        WHERE item_id = ? AND snapshot_at > ?
        ORDER BY snapshot_at ASC
    """, (item_id, cutoff))


def get_market_summary() -> dict:
    """Build a comprehensive market summary for the AI prompt.

    Returns:
        Dict with:
          - total_active_listings: int
          - player_listings_count: int
          - sim_listings_count: int
          - transactions_24h: int
          - volume_24h: float (total emerald volume)
          - most_active_items: list[dict]
          - price_snapshots: str (formatted for the AI prompt)
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    # Counts
    total_active = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM auction_listings WHERE status = 'active'")
    player_count = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM auction_listings WHERE status = 'active' AND is_simulated = 0")
    sim_count = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM auction_listings WHERE status = 'active' AND is_simulated = 1")

    # Transaction stats
    tx_stats = db.fetch_one("""
        SELECT COUNT(*) as tx_count, COALESCE(SUM(price), 0) as total_volume
        FROM transaction_history
        WHERE created_at > ?
    """, (yesterday,))

    # Most active items (by listing count)
    active_items = db.fetch_all("""
        SELECT item_id, COUNT(*) as cnt
        FROM auction_listings
        WHERE status = 'active'
        GROUP BY item_id
        ORDER BY cnt DESC
        LIMIT 10
    """)

    # Format price snapshots for AI prompt
    snapshots = db.fetch_all("""
        SELECT ph.item_id, ph.price_avg, ph.price_min, ph.price_max,
               ph.listing_count, ph.volume_sold, ph.snapshot_at,
               si.category, si.base_price, si.current_price
        FROM price_history ph
        LEFT JOIN simulated_inventory si ON ph.item_id = si.item_id
        WHERE ph.snapshot_at = (SELECT MAX(snapshot_at) FROM price_history)
        ORDER BY ph.volume_sold DESC
    """)

    price_lines = []
    for s in snapshots:
        current = s["current_price"] or s["base_price"] or s["price_avg"]
        cat = s["category"] or "player"
        price_lines.append(
            f"  {s['item_id']:30s} | {cat:10s} | avg={s['price_avg']:>6.2f} "
            f"min={s['price_min']:>6.2f} max={s['price_max']:>6.2f} "
            f"base={s['base_price'] or '-':>6} current={current:>6.2f} "
            f"vol_24h={s['volume_sold']:>4d}"
        )

    return {
        "total_active_listings": total_active["cnt"] if total_active else 0,
        "player_listings_count": player_count["cnt"] if player_count else 0,
        "sim_listings_count": sim_count["cnt"] if sim_count else 0,
        "transactions_24h": tx_stats["tx_count"] if tx_stats else 0,
        "volume_24h": tx_stats["total_volume"] if tx_stats else 0.0,
        "most_active_items": active_items,
        "price_snapshots_formatted": "\n".join(price_lines),
        "_raw_snapshots": snapshots,
    }


def get_stale_listings(hours: Optional[int] = None) -> list[dict]:
    """Find listings with no bid activity beyond the stale threshold.

    Items listed by players that have had zero bids and were listed longer
    than ``hours`` ago are considered "stale" and flagged for AI review.

    Args:
        hours: Stale threshold. Defaults to config.stale_hours_threshold.

    Returns:
        List of listing dicts that are stale
    """
    config = cfg()
    hours = hours or config.stale_hours_threshold
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    db = get_db()
    stale = db.fetch_all("""
        SELECT * FROM auction_listings
        WHERE status = 'active'
          AND is_simulated = 0
          AND bids_count = 0
          AND listed_at < ?
        ORDER BY listed_at ASC
    """, (cutoff,))

    return stale


def get_simulated_inventory_state() -> list[dict]:
    """Get the current state of simulated inventory for the AI prompt.

    Returns:
        List of dicts with item_id, category, current_stock, max_stock,
        base_price, current_price, volatility, trend info
    """
    db = get_db()
    return db.fetch_all("SELECT * FROM simulated_inventory ORDER BY category, item_id")
