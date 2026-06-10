"""
ah_reports.py — Market report generation.

Generates:
  - Weekly market overview reports
  - Player-specific activity reports
  - Daily price change summaries
  - Simple text reports that can be broadcast in-game
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_price_history import get_market_summary

log = get_logger()


def get_weekly_report() -> dict:
    """Generate a weekly market overview report.

    Returns:
        Dict with summary sections: overview, top_movers, events, recommendations
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()

    summary = get_market_summary()

    # Transactions this week
    tx_week = db.fetch_one("""
        SELECT COUNT(*) as count, COALESCE(SUM(price), 0) as volume
        FROM transaction_history
        WHERE created_at > ?
    """, (week_ago,))

    # Top sellers
    top_sellers = db.fetch_all("""
        SELECT actor_name, COUNT(*) as tx_count, COALESCE(SUM(price), 0) as total
        FROM transaction_history
        WHERE transaction_type = 'buy' AND created_at > ?
        GROUP BY actor_name
        ORDER BY total DESC
        LIMIT 5
    """, (week_ago,))

    # Recent events
    recent_events = db.fetch_all("""
        SELECT event_title, rarity_tier, started_at
        FROM market_events
        WHERE started_at > ?
        ORDER BY started_at DESC
        LIMIT 5
    """, (week_ago,))

    # Price movers (volatility)
    top_volatility = db.fetch_all("""
        SELECT item_id, COUNT(*) as snapshots,
               MAX(price_avg) - MIN(price_avg) as price_range,
               AVG(volume_sold) as avg_volume
        FROM price_history
        WHERE snapshot_at > ?
        GROUP BY item_id
        ORDER BY price_range DESC
        LIMIT 5
    """, (week_ago,))

    # Fetch latest AI market assessment from the notes
    ai_assessment = db.fetch_one("""
        SELECT content, created_at FROM ai_notes
        WHERE category = 'market_health' AND created_at > ?
        ORDER BY created_at DESC LIMIT 1
    """, (week_ago,))

    return {
        "report_date": now.isoformat(),
        "period": "7 days",
        "overview": {
            "total_transactions": tx_week["count"] if tx_week else 0,
            "total_volume": tx_week["volume"] if tx_week else 0.0,
            "active_listings": summary["total_active_listings"],
            "avg_daily_transactions": (tx_week["count"] or 0) // 7,
        },
        "top_sellers": [
            {"player": s["actor_name"], "transactions": s["tx_count"], "total": s["total"]}
            for s in top_sellers
        ],
        "recent_events": [
            {"title": e["event_title"], "tier": e["rarity_tier"], "started": e["started_at"][:10]}
            for e in recent_events
        ],
        "price_movers": [
            {"item": v["item_id"], "range": round(v["price_range"], 2), "avg_volume": round(v["avg_volume"], 1)}
            for v in top_volatility
        ],
        "ai_assessment": ai_assessment["content"] if ai_assessment else None,
        "ai_assessment_at": ai_assessment["created_at"][:16] if ai_assessment else None,
    }


def get_player_report(player: str, days: int = 7) -> dict:
    """Generate an activity report for a specific player.

    Args:
        player: Minecraft player name
        days: Look-back period

    Returns:
        Dict with player stats
    """
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    listings = db.fetch_all(
        "SELECT * FROM auction_listings WHERE seller_name = ? AND listed_at > ? ORDER BY listed_at DESC",
        (player, cutoff)
    )

    transactions = db.fetch_all(
        "SELECT * FROM transaction_history WHERE actor_name = ? AND created_at > ? ORDER BY created_at DESC",
        (player, cutoff)
    )

    total_spent = sum(t["price"] or 0 for t in transactions if t["transaction_type"] == "buy")
    # BUG FIX H2: Previously used "list"/"expire" transaction types which are start_prices
    # and system entries, not actual earnings. Now correctly reads sold_price from listings
    # where this player was the seller and the item was sold.
    sales_data = db.fetch_all(
        "SELECT sold_price FROM auction_listings "
        "WHERE seller_name = ? AND status = 'sold' AND sold_at > ?",
        (player, cutoff)
    )
    total_earned = sum(s["sold_price"] or 0 for s in sales_data)

    return {
        "player": player,
        "period_days": days,
        "listings_count": len(listings),
        "active_listings": sum(1 for l in listings if l["status"] == "active"),
        "transactions_count": len(transactions),
        "total_spent": round(total_spent, 2),
        "total_earned": round(total_earned, 2),
        "recent_listings": [
            {"item": l["item_id"], "price": l["start_price"], "status": l["status"], "listed": l["listed_at"][:16]}
            for l in listings[:10]
        ],
    }


def format_report_for_chat(report: dict) -> list[str]:
    """Format a market report into Minecraft chat-safe strings.

    Args:
        report: Dict from get_weekly_report()

    Returns:
        List of strings (each can be broadcast via tellraw)
    """
    lines = []
    ov = report.get("overview", {})
    lines.append("§6═══ §e📊 Auction House Report §6═══")

    # Show AI assessment if available
    assessment = report.get("ai_assessment")
    if assessment and isinstance(assessment, str):
        # Clean up "AI Market Assessment:" prefix for display
        display = assessment.replace("AI Market Assessment: ", "").strip()
        if display:
            lines.append(f" §5{display}")
            lines.append("")
    lines.append(f"§7Period: §f{report['period']}")
    lines.append(f"§7Transactions: §f{ov.get('total_transactions', 0)} §7(§f{ov.get('avg_daily_transactions', 0)}§7/day)")
    lines.append(f"§7Volume: §f{ov.get('total_volume', 0):.2f} §7emeralds")
    lines.append(f"§7Active Listings: §f{ov.get('active_listings', 0)}")

    # Top sellers
    sellers = report.get("top_sellers", [])
    if sellers:
        lines.append("§6--- §eTop Buyers §6---")
        for s in sellers:
            lines.append(f" §7• §f{s['player']:16s} §7- {s['transactions']} purchases ({s['total']:.2f}em)")

    # Recent events
    events = report.get("recent_events", [])
    if events:
        lines.append("§6--- §eRecent Events §6---")
        for e in events:
            lines.append(f" §7• §f{e['title']} §7({e['tier']})")

    # Price movers
    movers = report.get("price_movers", [])
    if movers:
        lines.append("§6--- §eTop Price Movers §6---")
        for m in movers:
            lines.append(f" §7• §f{m['item']:25s} §7range: §e{m['range']:.2f}em")

    lines.append("§6═══════════════════════════════")
    return lines

