"""
Auction House — SubsystemPlugin implementation.

Wraps the legacy ah_core.py functionality into a SubsystemPlugin
with VFS-backed database isolation, lifecycle hooks, and health checks.

Dependencies:
  - economy_bridge (optional): for cross-server balance lookups
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine.plugin_registry import (
    SubsystemPlugin,
    PluginRegistry,
    PluginHealth,
    PluginState,
    PluginError,
    PluginConfigError,
)

logger = logging.getLogger("plugins.auction_house")


# ──────────────────────────────────────────────────────────────────────────────
# SQL Schema
# ──────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    listing_uuid    TEXT PRIMARY KEY,
    server_id       TEXT NOT NULL,
    seller          TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    count           INTEGER NOT NULL DEFAULT 1,
    start_price     REAL NOT NULL DEFAULT 0.0,
    buy_now_price   REAL,
    currency_type   TEXT NOT NULL DEFAULT 'gold',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    closed_at       TEXT,
    buyer           TEXT,
    final_price     REAL
);

CREATE TABLE IF NOT EXISTS bids (
    bid_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_uuid    TEXT NOT NULL REFERENCES listings(listing_uuid),
    bidder          TEXT NOT NULL,
    amount          REAL NOT NULL,
    bid_time        TEXT NOT NULL,
    is_winning      INTEGER NOT NULL DEFAULT 0,
    is_retracted    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_uuid    TEXT NOT NULL REFERENCES listings(listing_uuid),
    tx_type         TEXT NOT NULL,
    actor           TEXT NOT NULL,
    item_id         TEXT,
    count           INTEGER DEFAULT 1,
    price           REAL,
    currency_type   TEXT DEFAULT 'gold',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_balances (
    player_name     TEXT NOT NULL,
    server_id       TEXT NOT NULL,
    balance         INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (player_name, server_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_seller ON listings(seller);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_item ON listings(item_id);
CREATE INDEX IF NOT EXISTS idx_bids_listing ON bids(listing_uuid);
CREATE INDEX IF NOT EXISTS idx_tx_listing ON transactions(listing_uuid);
"""

# ──────────────────────────────────────────────────────────────────────────────
# Plugin
# ──────────────────────────────────────────────────────────────────────────────

class AuctionHousePlugin(SubsystemPlugin):
    """
    SubsystemPlugin for the Auction House.
    
    Provides full auction lifecycle: listing, bidding, buy-now, expiry,
    balance tracking, and transaction history.
    
    Uses a VFS-backed SQLite database per server instance.
    """

    name = "auction_house"
    version = "1.0.0"
    description = "Auction House subsystem — listing, bidding, and trading"
    dependencies = []
    optional_dependencies = ["economy_bridge"]
    tags = ["economy", "trading", "market"]
    author = "Multi-Server Manager Team"

    # ── Lifecycle Hooks ──────────────────────────────────────────────

    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        """
        Initialize the Auction House plugin for a server.
        
        Creates the VFS-backed database and runs schema migration.
        Loads configuration defaults and validates settings.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            raise PluginError(
                f"AuctionHouse: VFS database not available for server '{server_id}'"
            )
        
        # Run schema creation
        try:
            db.execute(SCHEMA_SQL)
            db.commit()
            logger.info(
                "AuctionHouse: Schema initialized for server '%s'", server_id
            )
        except sqlite3.Error as e:
            db.rollback()
            raise PluginError(
                f"AuctionHouse: Schema creation failed for server '{server_id}': {e}"
            )

    async def on_shutdown(self, server_id: str) -> None:
        """
        Gracefully shut down the Auction House for a server.
        
        Expires all active listings and closes the database connection.
        """
        logger.info("AuctionHouse: Shutting down server '%s'", server_id)
        try:
            db = self._get_vfs_db(server_id)
            if db and db.is_open():
                db.close()
        except Exception as e:
            logger.warning(
                "AuctionHouse: Error during shutdown for '%s': %s",
                server_id, e
            )

    async def on_health_check(self, server_id: str) -> PluginHealth:
        """
        Perform a health check for the Auction House on a server.
        
        Verifies:
          1. VFS database is accessible
          2. Core tables exist
          3. Can execute a simple query
        """
        try:
            db = self._get_vfs_db(server_id)
            if db is None or not db.is_open():
                return PluginHealth.UNHEALTHY
            
            # Verify core tables exist
            rows = db.query(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name IN ('listings', 'bids', 'transactions')"
            )
            table_names = {r[0] for r in rows}
            required = {"listings", "bids", "transactions"}
            
            if not required.issubset(table_names):
                missing = required - table_names
                logger.warning(
                    "AuctionHouse: Missing tables on '%s': %s",
                    server_id, missing
                )
                return PluginHealth.DEGRADED
            
            # Quick query test
            db.query("SELECT COUNT(*) FROM listings")
            return PluginHealth.HEALTHY
            
        except Exception as e:
            logger.error(
                "AuctionHouse: Health check failed for '%s': %s",
                server_id, e
            )
            return PluginHealth.UNHEALTHY

    # ── Public API ───────────────────────────────────────────────────

    def list_item(
        self,
        server_id: str,
        seller: str,
        item_id: str,
        count: int = 1,
        start_price: float = 0.0,
        buy_now_price: Optional[float] = None,
        currency_type: str = "gold",
    ) -> Dict[str, Any]:
        """
        Create a new auction listing.
        
        Returns:
            Dict with success status and listing_uuid or error message.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return {"ok": False, "error": "Database not available"}
        
        import uuid
        listing_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        try:
            db.execute(
                """INSERT INTO listings
                   (listing_uuid, server_id, seller, item_id, count,
                    start_price, buy_now_price, currency_type,
                    status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
                (listing_uuid, server_id, seller, item_id, count,
                 start_price, buy_now_price, currency_type, now)
            )
            db.commit()
            logger.info(
                "AuctionHouse: %s listed %dx %s for %s (seller=%s,server=%s)",
                listing_uuid[:8], count, item_id, start_price, seller, server_id
            )
            return {
                "ok": True,
                "listing_uuid": listing_uuid,
                "created_at": now,
            }
        except sqlite3.Error as e:
            db.rollback()
            return {"ok": False, "error": str(e)}

    def place_bid(
        self,
        server_id: str,
        listing_uuid: str,
        bidder: str,
        amount: float,
    ) -> Dict[str, Any]:
        """
        Place a bid on an active listing.
        
        Returns:
            Dict with success status and bid info or error message.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return {"ok": False, "error": "Database not available"}
        
        try:
            # Check listing exists and is active
            rows = db.query(
                "SELECT * FROM listings WHERE listing_uuid=? AND status='active'",
                (listing_uuid,)
            )
            if not rows:
                return {"ok": False, "error": "Listing not found or not active"}
            
            listing = dict(rows[0])
            
            # Check minimum price
            if amount < listing["start_price"]:
                return {
                    "ok": False,
                    "error": f"Bid below start price ({listing['start_price']})"
                }
            
            # Check if there's a winning bid to outbid
            winning = db.query(
                "SELECT * FROM bids WHERE listing_uuid=? AND is_winning=1",
                (listing_uuid,)
            )
            if winning:
                current_high = winning[0]["amount"]
                if amount <= current_high:
                    return {
                        "ok": False,
                        "error": f"Bid must exceed current high bid ({current_high})"
                    }
                # Unseat previous winner
                db.execute(
                    "UPDATE bids SET is_winning=0 WHERE listing_uuid=? AND is_winning=1",
                    (listing_uuid,)
                )
            
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """INSERT INTO bids
                   (listing_uuid, bidder, amount, bid_time, is_winning)
                   VALUES (?, ?, ?, ?, 1)""",
                (listing_uuid, bidder, amount, now)
            )
            db.commit()
            
            logger.info(
                "AuctionHouse: Bid %s on %s by %s (%.2f)",
                listing_uuid[:8], listing["item_id"], bidder, amount
            )
            return {
                "ok": True,
                "listing_uuid": listing_uuid,
                "bidder": bidder,
                "amount": amount,
                "bid_time": now,
            }
        except sqlite3.Error as e:
            db.rollback()
            return {"ok": False, "error": str(e)}

    def buy_now(
        self,
        server_id: str,
        listing_uuid: str,
        buyer: str,
        quantity: int = 1,
    ) -> Dict[str, Any]:
        """
        Buy a listing at the buy-now price.
        
        Returns:
            Dict with success status or error message.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return {"ok": False, "error": "Database not available"}
        
        try:
            rows = db.query(
                "SELECT * FROM listings WHERE listing_uuid=? AND status='active'",
                (listing_uuid,)
            )
            if not rows:
                return {"ok": False, "error": "Listing not found or not active"}
            
            listing = dict(rows[0])
            
            if listing["buy_now_price"] is None:
                return {"ok": False, "error": "Listing has no buy-now price"}
            
            if listing["seller"] == buyer:
                return {"ok": False, "error": "Cannot buy your own listing"}
            
            now = datetime.now(timezone.utc).isoformat()
            
            # Close the listing
            db.execute(
                """UPDATE listings SET
                   status='closed', buyer=?, final_price=?,
                   closed_at=?
                   WHERE listing_uuid=?""",
                (buyer, listing["buy_now_price"], now, listing_uuid)
            )
            
            # Record transaction
            db.execute(
                """INSERT INTO transactions
                   (listing_uuid, tx_type, actor, item_id, count,
                    price, currency_type, created_at)
                   VALUES (?, 'buy_now', ?, ?, ?, ?, ?, ?)""",
                (listing_uuid, buyer, listing["item_id"],
                 listing["count"], listing["buy_now_price"],
                 listing["currency_type"], now)
            )
            db.commit()
            
            logger.info(
                "AuctionHouse: BuyNow %s by %s (%.2f)",
                listing_uuid[:8], buyer, listing["buy_now_price"]
            )
            return {
                "ok": True,
                "listing_uuid": listing_uuid,
                "buyer": buyer,
                "price": listing["buy_now_price"],
                "item_id": listing["item_id"],
                "count": listing["count"],
            }
        except sqlite3.Error as e:
            db.rollback()
            return {"ok": False, "error": str(e)}

    def cancel_listing(
        self,
        server_id: str,
        listing_uuid: str,
        player: str,
    ) -> Dict[str, Any]:
        """
        Cancel an active listing (seller only).
        
        Returns:
            Dict with success status or error message.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return {"ok": False, "error": "Database not available"}
        
        try:
            rows = db.query(
                "SELECT * FROM listings WHERE listing_uuid=? AND status='active'",
                (listing_uuid,)
            )
            if not rows:
                return {"ok": False, "error": "Listing not found or not active"}
            
            listing = dict(rows[0])
            if listing["seller"] != player:
                return {"ok": False, "error": "Not your listing"}
            
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE listings SET status='cancelled', closed_at=? WHERE listing_uuid=?",
                (now, listing_uuid)
            )
            db.commit()
            
            logger.info("AuctionHouse: Cancelled %s by %s", listing_uuid[:8], player)
            return {"ok": True, "listing_uuid": listing_uuid}
        except sqlite3.Error as e:
            db.rollback()
            return {"ok": False, "error": str(e)}

    def expire_listings(self, server_id: str) -> List[Dict[str, Any]]:
        """
        Expire all listings that have passed their expiration time.
        
        Returns:
            List of expired listing dicts.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            rows = db.query(
                "SELECT * FROM listings WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (now,)
            )
            expired = [dict(r) for r in rows]
            
            for listing in expired:
                db.execute(
                    "UPDATE listings SET status='expired', closed_at=? WHERE listing_uuid=?",
                    (now, listing["listing_uuid"])
                )
            
            if expired:
                db.commit()
                logger.info(
                    "AuctionHouse: Expired %d listings on '%s'",
                    len(expired), server_id
                )
            
            return expired
        except sqlite3.Error as e:
            db.rollback()
            logger.error("AuctionHouse: Expiry failed: %s", e)
            return []

    def query_listings(
        self,
        server_id: str,
        filter_type: str = "all",
        filter_value: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Query auction listings with optional filters.
        
        Args:
            server_id: Target server
            filter_type: "all", "active", "seller", "item", "bids"
            filter_value: Value for the filter (seller name, item id, etc.)
        
        Returns:
            List of listing dicts.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            if filter_type == "seller":
                rows = db.query(
                    "SELECT * FROM listings WHERE seller=? ORDER BY created_at DESC",
                    (filter_value,)
                )
            elif filter_type == "item":
                rows = db.query(
                    "SELECT * FROM listings WHERE item_id=? ORDER BY created_at DESC",
                    (filter_value,)
                )
            elif filter_type == "active":
                rows = db.query(
                    "SELECT * FROM listings WHERE status='active' ORDER BY created_at DESC"
                )
            else:
                rows = db.query(
                    "SELECT * FROM listings ORDER BY created_at DESC"
                )
            
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_listing(
        self,
        server_id: str,
        listing_uuid: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a single listing by UUID."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        try:
            rows = db.query(
                "SELECT * FROM listings WHERE listing_uuid=?",
                (listing_uuid,)
            )
            return dict(rows[0]) if rows else None
        except sqlite3.Error:
            return None

    def get_player_listings(
        self,
        server_id: str,
        player: str,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Get all listings for a player."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            if active_only:
                rows = db.query(
                    "SELECT * FROM listings WHERE seller=? AND status='active' ORDER BY created_at DESC",
                    (player,)
                )
            else:
                rows = db.query(
                    "SELECT * FROM listings WHERE seller=? ORDER BY created_at DESC",
                    (player,)
                )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_bid_history(
        self,
        server_id: str,
        listing_uuid: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get bid history for a listing."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            rows = db.query(
                "SELECT * FROM bids WHERE listing_uuid=? ORDER BY bid_time DESC LIMIT ?",
                (listing_uuid, limit)
            )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_balance(
        self,
        server_id: str,
        player: str,
    ) -> Optional[int]:
        """Get a player's balance from internal tracking."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        try:
            rows = db.query(
                "SELECT balance FROM player_balances WHERE player_name=? AND server_id=?",
                (player, server_id)
            )
            return rows[0][0] if rows else 0
        except sqlite3.Error:
            return None

    def update_balance(
        self,
        server_id: str,
        player: str,
        delta: int,
    ) -> Optional[int]:
        """Update a player's balance (positive or negative delta)."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            # Upsert
            existing = db.query(
                "SELECT balance FROM player_balances WHERE player_name=? AND server_id=?",
                (player, server_id)
            )
            if existing:
                new_balance = existing[0][0] + delta
                db.execute(
                    "UPDATE player_balances SET balance=?, updated_at=? WHERE player_name=? AND server_id=?",
                    (new_balance, now, player, server_id)
                )
            else:
                new_balance = delta
                db.execute(
                    "INSERT INTO player_balances (player_name, server_id, balance, updated_at) VALUES (?, ?, ?, ?)",
                    (player, server_id, new_balance, now)
                )
            db.commit()
            return new_balance
        except sqlite3.Error:
            db.rollback()
            return None

    # ── VFS Integration ──────────────────────────────────────────────

    def _get_vfs_db(self, server_id: str) -> Any:
        """
        Get the VFS-backed database for this plugin+server combo.
        
        Uses the VFSDatabaseManager via the LifecycleManager's vfs instance.
        Falls back to direct path-based SQLite if VFS is unavailable.
        """
        try:
            from engine.vfs_db_isolation import VFSDatabaseManager
            
            # Try to get the lifecycle manager's vfs instance
            lifecycle = self._get_lifecycle()
            if lifecycle and hasattr(lifecycle, 'vfs') and lifecycle.vfs:
                db = lifecycle.vfs.get_database(self.name, server_id)
                if db and db.is_open():
                    return db
                # Open if not open
                if db:
                    db.open()
                    return db
            
            # Fallback: direct VFS path
            vfs_root = getattr(lifecycle, 'vfs_data_root', 'DATA/vfs') if lifecycle else 'DATA/vfs'
            mgr = VFSDatabaseManager(data_root=vfs_root)
            db = mgr.get_database(self.name, server_id)
            if db:
                db.open()
            return db
            
        except Exception as e:
            logger.error("AuctionHouse: VFS DB error for '%s': %s", server_id, e)
            return None

    def _get_lifecycle(self):
        """Get the LifecycleManager if available."""
        try:
            from engine.lifecycle_manager import LifecycleManager
            # noinspection PyUnresolvedReferences
            # The lifecycle manager is accessible via the registry
            return None  # Will be set by the main.py initialization
        except ImportError:
            return None
