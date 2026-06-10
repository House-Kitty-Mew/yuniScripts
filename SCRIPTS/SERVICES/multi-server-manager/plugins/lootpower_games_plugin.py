"""
LootPower Games — SubsystemPlugin implementation.

Wraps the legacy LootPower auction game system into a SubsystemPlugin
with VFS-backed database isolation.

Provides loot-based auction listings, bidding, and player inventory
management for game servers.
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
)

logger = logging.getLogger("plugins.lootpower_games")


# ──────────────────────────────────────────────────────────────────────────────
# SQL Schema
# ──────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS loot_table (
    loot_id         INTEGER PRIMARY KEY,
    loot_name       TEXT NOT NULL,
    loot_rarity     TEXT DEFAULT 'common',
    loot_type       TEXT DEFAULT 'item',
    base_value      INTEGER DEFAULT 0,
    metadata_json   TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS users_loot (
    user_id         TEXT NOT NULL,
    loot_id         INTEGER NOT NULL REFERENCES loot_table(loot_id),
    loot_amount     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, loot_id)
);

CREATE TABLE IF NOT EXISTS auction (
    auction_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       TEXT NOT NULL,
    seller_id       TEXT NOT NULL,
    loot_id         INTEGER NOT NULL REFERENCES loot_table(loot_id),
    loot_amount     INTEGER NOT NULL DEFAULT 1,
    payment_type    INTEGER DEFAULT 0,
    payment_id      INTEGER DEFAULT 0,
    payment_amount  INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    closed_at       TEXT
);

CREATE TABLE IF NOT EXISTS loot_history (
    history_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    loot_id         INTEGER NOT NULL,
    change_amount   INTEGER NOT NULL,
    reason          TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ul_user ON users_loot(user_id);
CREATE INDEX IF NOT EXISTS idx_auc_active ON auction(status);
CREATE INDEX IF NOT EXISTS idx_auc_seller ON auction(seller_id);
CREATE INDEX IF NOT EXISTS idx_loot_name ON loot_table(loot_name);
"""

# ──────────────────────────────────────────────────────────────────────────────
# Default Loot Table
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_LOOT_TABLE = [
    {"loot_id": 1, "loot_name": "Common Sword", "loot_rarity": "common", "loot_type": "weapon", "base_value": 10},
    {"loot_id": 2, "loot_name": "Iron Shield", "loot_rarity": "common", "loot_type": "armor", "base_value": 15},
    {"loot_id": 3, "loot_name": "Health Potion", "loot_rarity": "common", "loot_type": "consumable", "base_value": 5},
    {"loot_id": 4, "loot_name": "Mana Crystal", "loot_rarity": "uncommon", "loot_type": "consumable", "base_value": 25},
    {"loot_id": 5, "loot_name": "Silver Ring", "loot_rarity": "uncommon", "loot_type": "accessory", "base_value": 30},
    {"loot_id": 6, "loot_name": "Enchanted Bow", "loot_rarity": "rare", "loot_type": "weapon", "base_value": 75},
    {"loot_id": 7, "loot_name": "Dragon Scale Armor", "loot_rarity": "rare", "loot_type": "armor", "base_value": 120},
    {"loot_id": 8, "loot_name": "Phoenix Feather", "loot_rarity": "epic", "loot_type": "material", "base_value": 250},
    {"loot_id": 9, "loot_name": "Shadow Dagger", "loot_rarity": "epic", "loot_type": "weapon", "base_value": 300},
    {"loot_id": 10, "loot_name": "Crown of Kings", "loot_rarity": "legendary", "loot_type": "accessory", "base_value": 1000},
]

# ──────────────────────────────────────────────────────────────────────────────
# Plugin
# ──────────────────────────────────────────────────────────────────────────────

class LootPowerGamesPlugin(SubsystemPlugin):
    """
    SubsystemPlugin for the LootPower Games auction system.
    
    Provides loot table management, player inventory tracking,
    and auction listing/buying for game loot items.
    """

    name = "lootpower_games"
    version = "1.0.0"
    description = "LootPower Games — loot-based auction and inventory system"
    dependencies = []
    optional_dependencies = ["economy_bridge"]
    tags = ["games", "loot", "auction", "inventory"]
    author = "Multi-Server Manager Team"

    # ── Lifecycle Hooks ──────────────────────────────────────────────

    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        """
        Initialize the LootPower Games plugin for a server.
        
        Creates VFS-backed database with loot table and seeds
        default items if none exist.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            raise PluginError(
                f"LootPowerGames: VFS database not available for server '{server_id}'"
            )
        
        try:
            db.execute(SCHEMA_SQL)
            db.commit()
            logger.info(
                "LootPowerGames: Schema initialized for server '%s'", server_id
            )
        except sqlite3.Error as e:
            db.rollback()
            raise PluginError(
                f"LootPowerGames: Schema creation failed for server '{server_id}': {e}"
            )
        
        # Seed loot table if empty
        count = self._seed_loot_table(server_id, config)
        logger.info(
            "LootPowerGames: Seeded %d loot items for server '%s'", count, server_id
        )

    async def on_shutdown(self, server_id: str) -> None:
        """Gracefully shut down for a server."""
        logger.info("LootPowerGames: Shutting down server '%s'", server_id)
        try:
            db = self._get_vfs_db(server_id)
            if db and hasattr(db, 'is_open') and db.is_open():
                db.close()
        except Exception as e:
            logger.warning(
                "LootPowerGames: Error during shutdown for '%s': %s", server_id, e
            )

    async def on_health_check(self, server_id: str) -> PluginHealth:
        """Perform a health check."""
        try:
            db = self._get_vfs_db(server_id)
            if db is None or not hasattr(db, 'is_open') or not db.is_open():
                return PluginHealth.UNHEALTHY
            
            rows = db.query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='loot_table'"
            )
            if not rows:
                return PluginHealth.DEGRADED
            
            db.query("SELECT COUNT(*) FROM loot_table")
            return PluginHealth.HEALTHY
            
        except Exception as e:
            logger.error("LootPowerGames: Health check failed for '%s': %s", server_id, e)
            return PluginHealth.UNHEALTHY

    # ── Public API ───────────────────────────────────────────────────

    # ── Loot Table ───────────────────────────────────────────────────

    def get_loot_table(
        self,
        server_id: str,
        rarity: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all loot items, optionally filtered by rarity."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            if rarity:
                rows = db.query(
                    "SELECT * FROM loot_table WHERE loot_rarity=? ORDER BY loot_id",
                    (rarity,)
                )
            else:
                rows = db.query("SELECT * FROM loot_table ORDER BY loot_id")
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_loot_item(self, server_id: str, loot_id: int) -> Optional[Dict[str, Any]]:
        """Get a loot item by ID."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        try:
            rows = db.query(
                "SELECT * FROM loot_table WHERE loot_id=?", (loot_id,)
            )
            return dict(rows[0]) if rows else None
        except sqlite3.Error:
            return None

    def add_loot_item(
        self,
        server_id: str,
        loot_name: str,
        loot_rarity: str = "common",
        loot_type: str = "item",
        base_value: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Add a new loot item to the loot table."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        try:
            # Get next loot_id
            max_id = db.query("SELECT COALESCE(MAX(loot_id), 0) + 1 FROM loot_table")[0][0]
            db.execute(
                "INSERT INTO loot_table (loot_id, loot_name, loot_rarity, loot_type, base_value) VALUES (?, ?, ?, ?, ?)",
                (max_id, loot_name, loot_rarity, loot_type, base_value)
            )
            db.commit()
            return {
                "loot_id": max_id,
                "loot_name": loot_name,
                "loot_rarity": loot_rarity,
                "loot_type": loot_type,
                "base_value": base_value,
            }
        except sqlite3.Error as e:
            db.rollback()
            return None

    # ── Player Inventory ─────────────────────────────────────────────

    def get_player_inventory(
        self,
        server_id: str,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        """Get a player's loot inventory."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            rows = db.query(
                """SELECT ul.loot_id, ul.loot_amount, lt.loot_name, lt.loot_rarity, lt.loot_type, lt.base_value
                   FROM users_loot ul
                   JOIN loot_table lt ON ul.loot_id = lt.loot_id
                   WHERE ul.user_id=? AND ul.loot_amount > 0
                   ORDER BY lt.loot_rarity, lt.loot_name""",
                (user_id,)
            )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def give_loot(
        self,
        server_id: str,
        user_id: str,
        loot_id: int,
        amount: int = 1,
        reason: str = "grant",
    ) -> Dict[str, Any]:
        """
        Give loot items to a player.
        
        Returns:
            Dict with success status and new amount.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return {"ok": False, "error": "Database not available"}
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            # Upsert
            existing = db.query(
                "SELECT loot_amount FROM users_loot WHERE user_id=? AND loot_id=?",
                (user_id, loot_id)
            )
            if existing:
                new_amount = existing[0][0] + amount
                db.execute(
                    "UPDATE users_loot SET loot_amount=? WHERE user_id=? AND loot_id=?",
                    (new_amount, user_id, loot_id)
                )
            else:
                new_amount = amount
                db.execute(
                    "INSERT INTO users_loot (user_id, loot_id, loot_amount) VALUES (?, ?, ?)",
                    (user_id, loot_id, new_amount)
                )
            
            # Log history
            db.execute(
                "INSERT INTO loot_history (user_id, loot_id, change_amount, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, loot_id, amount, reason, now)
            )
            
            db.commit()
            return {"ok": True, "user_id": user_id, "loot_id": loot_id, "new_amount": new_amount}
        except sqlite3.Error as e:
            db.rollback()
            return {"ok": False, "error": str(e)}

    def take_loot(
        self,
        server_id: str,
        user_id: str,
        loot_id: int,
        amount: int = 1,
        reason: str = "removal",
    ) -> Dict[str, Any]:
        """
        Remove loot items from a player.
        
        Returns:
            Dict with success status.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return {"ok": False, "error": "Database not available"}
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            existing = db.query(
                "SELECT loot_amount FROM users_loot WHERE user_id=? AND loot_id=?",
                (user_id, loot_id)
            )
            if not existing or existing[0][0] < amount:
                current = existing[0][0] if existing else 0
                return {"ok": False, "error": f"Insufficient loot (have {current}, need {amount})"}
            
            new_amount = existing[0][0] - amount
            db.execute(
                "UPDATE users_loot SET loot_amount=? WHERE user_id=? AND loot_id=?",
                (new_amount, user_id, loot_id)
            )
            
            db.execute(
                "INSERT INTO loot_history (user_id, loot_id, change_amount, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, loot_id, -amount, reason, now)
            )
            
            db.commit()
            return {"ok": True, "user_id": user_id, "loot_id": loot_id, "new_amount": new_amount}
        except sqlite3.Error as e:
            db.rollback()
            return {"ok": False, "error": str(e)}

    # ── Auction Operations ───────────────────────────────────────────

    def create_listing(
        self,
        server_id: str,
        seller_id: str,
        loot_id: int,
        loot_amount: int = 1,
        payment_amount: int = 0,
    ) -> Optional[int]:
        """
        Create a new auction listing.
        
        Deducts loot from seller's inventory first.
        
        Returns:
            auction_id, or None on failure.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        # Verify seller has enough loot
        try:
            row = db.query(
                "SELECT loot_amount FROM users_loot WHERE user_id=? AND loot_id=?",
                (seller_id, loot_id)
            )
            if not row or row[0][0] < loot_amount:
                return None
            
            # Deduct loot
            db.execute(
                "UPDATE users_loot SET loot_amount = loot_amount - ? WHERE user_id=? AND loot_id=?",
                (loot_amount, seller_id, loot_id)
            )
            
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """INSERT INTO auction
                   (server_id, seller_id, loot_id, loot_amount, payment_amount, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                (server_id, seller_id, loot_id, loot_amount, payment_amount, now)
            )
            db.commit()
            
            auction_id = db.query("SELECT last_insert_rowid() as id")
            return auction_id[0][0] if auction_id else None
            
        except sqlite3.Error as e:
            db.rollback()
            logger.error("LootPowerGames: create_listing failed: %s", e)
            return None

    def buy_listing(
        self,
        server_id: str,
        auction_id: int,
        buyer_id: str,
    ) -> str:
        """
        Buy a listing outright.
        
        Returns:
            Result message string.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return "Database not available"
        
        try:
            row = db.query(
                "SELECT * FROM auction WHERE auction_id=? AND status='active'",
                (auction_id,)
            )
            if not row:
                return "Listing not found or not active"
            
            listing = dict(row[0])
            if listing["seller_id"] == buyer_id:
                return "Cannot buy your own listing"
            
            # Close the listing
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE auction SET status='closed', closed_at=? WHERE auction_id=?",
                (now, auction_id)
            )
            
            # Give loot to buyer (upsert)
            existing = db.query(
                "SELECT loot_amount FROM users_loot WHERE user_id=? AND loot_id=?",
                (buyer_id, listing["loot_id"])
            )
            if existing:
                db.execute(
                    "UPDATE users_loot SET loot_amount = loot_amount + ? WHERE user_id=? AND loot_id=?",
                    (listing["loot_amount"], buyer_id, listing["loot_id"])
                )
            else:
                db.execute(
                    "INSERT INTO users_loot (user_id, loot_id, loot_amount) VALUES (?, ?, ?)",
                    (buyer_id, listing["loot_id"], listing["loot_amount"])
                )
            
            db.commit()
            logger.info(
                "LootPowerGames: Listing %d bought by %s on '%s'",
                auction_id, buyer_id, server_id
            )
            return f"Purchased listing {auction_id}"
            
        except sqlite3.Error as e:
            db.rollback()
            return f"Error: {e}"

    def cancel_listing(
        self,
        server_id: str,
        auction_id: int,
        user_id: str,
    ) -> str:
        """
        Cancel a listing and return loot to seller.
        
        Returns:
            Result message string.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return "Database not available"
        
        try:
            row = db.query(
                "SELECT * FROM auction WHERE auction_id=? AND status='active'",
                (auction_id,)
            )
            if not row:
                return "Listing not found or not active"
            
            listing = dict(row[0])
            if listing["seller_id"] != user_id:
                return "Not your listing"
            
            # Return loot
            db.execute(
                "UPDATE users_loot SET loot_amount = loot_amount + ? WHERE user_id=? AND loot_id=?",
                (listing["loot_amount"], user_id, listing["loot_id"])
            )
            
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE auction SET status='cancelled', closed_at=? WHERE auction_id=?",
                (now, auction_id)
            )
            db.commit()
            
            return f"Listing {auction_id} cancelled"
            
        except sqlite3.Error as e:
            db.rollback()
            return f"Error: {e}"

    def get_active_listings(
        self,
        server_id: str,
    ) -> List[Dict[str, Any]]:
        """Get all active auction listings."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            rows = db.query(
                """SELECT a.*, lt.loot_name, lt.loot_rarity, lt.loot_type
                   FROM auction a
                   JOIN loot_table lt ON a.loot_id = lt.loot_id
                   WHERE a.server_id=? AND a.status='active'
                   ORDER BY a.auction_id DESC""",
                (server_id,)
            )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_player_history(
        self,
        server_id: str,
        user_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get a player's loot history."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            rows = db.query(
                """SELECT lh.*, lt.loot_name, lt.loot_rarity
                   FROM loot_history lh
                   JOIN loot_table lt ON lh.loot_id = lt.loot_id
                   WHERE lh.user_id=?
                   ORDER BY lh.created_at DESC LIMIT ?""",
                (user_id, limit)
            )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    # ── Internal Helpers ─────────────────────────────────────────────

    def _seed_loot_table(
        self,
        server_id: str,
        config: Dict[str, Any],
    ) -> int:
        """Seed the loot table with default items if empty."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return 0
        
        try:
            count = db.query("SELECT COUNT(*) FROM loot_table")[0][0]
            if count > 0:
                return count  # Already seeded
        except sqlite3.Error:
            pass
        
        loot_items = config.get("loot_table", DEFAULT_LOOT_TABLE)
        seeded = 0
        
        for item in loot_items:
            try:
                db.execute(
                    "INSERT OR IGNORE INTO loot_table (loot_id, loot_name, loot_rarity, loot_type, base_value) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (item["loot_id"], item["loot_name"], item["loot_rarity"],
                     item["loot_type"], item["base_value"])
                )
                seeded += 1
            except sqlite3.Error:
                continue
        
        try:
            db.commit()
        except sqlite3.Error:
            return 0
        
        return seeded

    # ── VFS Integration ──────────────────────────────────────────────

    def _get_vfs_db(self, server_id: str) -> Any:
        """Get the VFS-backed database for this plugin+server combo."""
        try:
            from engine.vfs_db_isolation import VFSDatabaseManager
            
            mgr = VFSDatabaseManager(data_root="DATA/vfs")
            db = mgr.get_database(self.name, server_id)
            if db and not (hasattr(db, 'is_open') and db.is_open()):
                db.open()
            return db
        except Exception as e:
            logger.error("LootPowerGames: VFS DB error for '%s': %s", server_id, e)
            return None
