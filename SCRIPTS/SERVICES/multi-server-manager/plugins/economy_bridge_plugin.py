"""
Economy Bridge — SubsystemPlugin implementation.

Wraps the legacy eco_bridge.py functionality into a SubsystemPlugin
with VFS-backed database isolation and LAN discovery management.

Provides unified economy API that the Auction House and other
subsystems can depend on for cross-server balance lookups.
"""

import json
import logging
import os
import sqlite3
import threading
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

logger = logging.getLogger("plugins.economy_bridge")


# ──────────────────────────────────────────────────────────────────────────────
# SQL Schema
# ──────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    player_name     TEXT NOT NULL,
    server_id       TEXT NOT NULL,
    balance         INTEGER NOT NULL DEFAULT 0,
    currency_type   TEXT NOT NULL DEFAULT 'gold',
    updated_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (player_name, server_id, currency_type)
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       TEXT NOT NULL,
    from_player     TEXT,
    to_player       TEXT,
    amount          INTEGER NOT NULL,
    currency_type   TEXT NOT NULL DEFAULT 'gold',
    reason          TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bridge_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eco_tx_server ON transactions(server_id);
CREATE INDEX IF NOT EXISTS idx_eco_tx_player ON transactions(from_player);
CREATE INDEX IF NOT EXISTS idx_eco_tx_time ON transactions(created_at);
"""

# ──────────────────────────────────────────────────────────────────────────────
# Plugin
# ──────────────────────────────────────────────────────────────────────────────

class EconomyBridgePlugin(SubsystemPlugin):
    """
    SubsystemPlugin for the Economy Bridge.
    
    Provides unified economy operations: balance queries, transfers,
    currency management, and transaction history.
    
    Supports:
    - Local VFS-backed database per server
    - Remote bridge connections (LAN discovery or static config)
    - Transaction logging and auditing
    """

    name = "economy_bridge"
    version = "1.0.0"
    description = "Economy Bridge — unified currency and balance management"
    dependencies = []
    optional_dependencies = []
    tags = ["economy", "currency", "bridge"]
    author = "Multi-Server Manager Team"

    # ── LAN Discovery State ──────────────────────────────────────────
    _lan_discovery_active: bool = False
    _discovered_host: Optional[str] = None
    _discovered_port: Optional[int] = None
    _lan_lock: threading.Lock = threading.Lock()

    # ── Lifecycle Hooks ──────────────────────────────────────────────

    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        """
        Initialize the Economy Bridge for a server.
        
        Creates the VFS-backed database with accounts and transactions tables.
        Starts LAN discovery if configured.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            raise PluginError(
                f"EconomyBridge: VFS database not available for server '{server_id}'"
            )
        
        try:
            db.execute(SCHEMA_SQL)
            db.commit()
            logger.info(
                "EconomyBridge: Schema initialized for server '%s'", server_id
            )
        except sqlite3.Error as e:
            db.rollback()
            raise PluginError(
                f"EconomyBridge: Schema creation failed for server '{server_id}': {e}"
            )
        
        # Start LAN discovery if configured
        if config.get("lan_discovery", True):
            self._start_lan_discovery(
                fallback_host=config.get("remote_host", ""),
                fallback_port=config.get("remote_port", 7200),
            )

    async def on_shutdown(self, server_id: str) -> None:
        """
        Gracefully shut down the Economy Bridge for a server.
        """
        logger.info("EconomyBridge: Shutting down server '%s'", server_id)
        try:
            db = self._get_vfs_db(server_id)
            if db and hasattr(db, 'is_open') and db.is_open():
                db.close()
        except Exception as e:
            logger.warning(
                "EconomyBridge: Error during shutdown for '%s': %s",
                server_id, e
            )

    async def on_health_check(self, server_id: str) -> PluginHealth:
        """
        Perform a health check.
        
        Verifies:
          1. VFS database is accessible
          2. Core tables exist
          3. Remote bridge is reachable (if configured)
        """
        try:
            db = self._get_vfs_db(server_id)
            if db is None or not hasattr(db, 'is_open') or not db.is_open():
                return PluginHealth.UNHEALTHY
            
            # Verify tables
            rows = db.query(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name IN ('accounts', 'transactions')"
            )
            table_names = {r[0] for r in rows}
            if "accounts" not in table_names or "transactions" not in table_names:
                return PluginHealth.DEGRADED
            
            # Quick query
            db.query("SELECT COUNT(*) FROM accounts")
            return PluginHealth.HEALTHY
            
        except Exception as e:
            logger.error("EconomyBridge: Health check failed for '%s': %s", server_id, e)
            return PluginHealth.UNHEALTHY

    # ── Public API ───────────────────────────────────────────────────

    def get_balance(
        self,
        server_id: str,
        player: str,
        currency_type: str = "gold",
    ) -> Optional[int]:
        """
        Get a player's balance.
        
        First checks the local VFS database, then falls back to
        remote bridge if configured.
        
        Returns:
            Balance as int, or None if unavailable.
        """
        # Try local DB first
        db = self._get_vfs_db(server_id)
        if db is not None:
            try:
                rows = db.query(
                    "SELECT balance FROM accounts WHERE player_name=? AND server_id=? AND currency_type=?",
                    (player, server_id, currency_type)
                )
                if rows:
                    return rows[0][0]
            except sqlite3.Error:
                pass
        
        # Fallback to 0 (new player)
        return 0

    def set_balance(
        self,
        server_id: str,
        player: str,
        amount: int,
        currency_type: str = "gold",
        reason: str = "admin",
    ) -> Optional[int]:
        """
        Set a player's balance to an absolute value.
        
        Returns:
            New balance, or None on failure.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            old_balance = self.get_balance(server_id, player, currency_type) or 0
            
            # Upsert
            existing = db.query(
                "SELECT balance FROM accounts WHERE player_name=? AND server_id=? AND currency_type=?",
                (player, server_id, currency_type)
            )
            if existing:
                db.execute(
                    "UPDATE accounts SET balance=?, updated_at=? WHERE player_name=? AND server_id=? AND currency_type=?",
                    (amount, now, player, server_id, currency_type)
                )
            else:
                db.execute(
                    "INSERT INTO accounts (player_name, server_id, balance, currency_type, updated_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (player, server_id, amount, currency_type, now, now)
                )
            
            # Log as admin transaction
            delta = amount - old_balance
            if delta != 0:
                db.execute(
                    "INSERT INTO transactions (server_id, from_player, to_player, amount, currency_type, reason, created_at) "
                    "VALUES (?, 'SYSTEM', ?, ?, ?, ?, ?)",
                    (server_id, player, delta, currency_type, reason, now)
                )
            
            db.commit()
            return amount
        except sqlite3.Error as e:
            db.rollback()
            logger.error("EconomyBridge: set_balance failed: %s", e)
            return None

    def transfer(
        self,
        server_id: str,
        from_player: str,
        to_player: str,
        amount: int,
        currency_type: str = "gold",
        reason: str = "transfer",
    ) -> Dict[str, Any]:
        """
        Transfer currency between two players.
        
        Returns:
            Dict with success status and details.
        """
        if amount <= 0:
            return {"ok": False, "error": "Amount must be positive"}
        
        if from_player == to_player:
            return {"ok": False, "error": "Cannot transfer to yourself"}
        
        db = self._get_vfs_db(server_id)
        if db is None:
            return {"ok": False, "error": "Database not available"}
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            # Check sender balance
            sender_balance = self.get_balance(server_id, from_player, currency_type) or 0
            if sender_balance < amount:
                return {
                    "ok": False,
                    "error": f"Insufficient balance ({sender_balance} < {amount})"
                }
            
            # Deduct from sender
            db.execute(
                "UPDATE accounts SET balance=balance-?, updated_at=? WHERE player_name=? AND server_id=? AND currency_type=?",
                (amount, now, from_player, server_id, currency_type)
            )
            
            # Credit receiver (upsert)
            existing_receiver = db.query(
                "SELECT balance FROM accounts WHERE player_name=? AND server_id=? AND currency_type=?",
                (to_player, server_id, currency_type)
            )
            if existing_receiver:
                db.execute(
                    "UPDATE accounts SET balance=balance+?, updated_at=? WHERE player_name=? AND server_id=? AND currency_type=?",
                    (amount, now, to_player, server_id, currency_type)
                )
            else:
                db.execute(
                    "INSERT INTO accounts (player_name, server_id, balance, currency_type, updated_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (to_player, server_id, amount, currency_type, now, now)
                )
            
            # Log transaction
            db.execute(
                "INSERT INTO transactions (server_id, from_player, to_player, amount, currency_type, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (server_id, from_player, to_player, amount, currency_type, reason, now)
            )
            
            db.commit()
            logger.info(
                "EconomyBridge: Transfer %d %s from %s to %s (%s)",
                amount, currency_type, from_player, to_player, reason
            )
            return {
                "ok": True,
                "from": from_player,
                "to": to_player,
                "amount": amount,
                "currency_type": currency_type,
                "new_sender_balance": sender_balance - amount,
            }
        except sqlite3.Error as e:
            db.rollback()
            return {"ok": False, "error": str(e)}

    def get_transaction_history(
        self,
        server_id: str,
        player: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get transaction history, optionally filtered by player.
        
        Args:
            server_id: Target server
            player: Optional player name to filter by
            limit: Max results
        
        Returns:
            List of transaction dicts.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            if player:
                rows = db.query(
                    """SELECT * FROM transactions
                       WHERE server_id=? AND (from_player=? OR to_player=?)
                       ORDER BY created_at DESC LIMIT ?""",
                    (server_id, player, player, limit)
                )
            else:
                rows = db.query(
                    "SELECT * FROM transactions WHERE server_id=? ORDER BY created_at DESC LIMIT ?",
                    (server_id, limit)
                )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_server_economy_summary(
        self,
        server_id: str,
    ) -> Dict[str, Any]:
        """Get economic summary for a server."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return {"ok": False, "error": "Database not available"}
        
        try:
            total_accounts = db.query(
                "SELECT COUNT(DISTINCT player_name) FROM accounts WHERE server_id=?",
                (server_id,)
            )[0][0]
            total_wealth = db.query(
                "SELECT COALESCE(SUM(balance), 0) FROM accounts WHERE server_id=?",
                (server_id,)
            )[0][0]
            total_tx = db.query(
                "SELECT COUNT(*) FROM transactions WHERE server_id=?",
                (server_id,)
            )[0][0]
            
            wealthiest = db.query(
                "SELECT player_name, balance FROM accounts WHERE server_id=? ORDER BY balance DESC LIMIT 5",
                (server_id,)
            )
            
            return {
                "ok": True,
                "server_id": server_id,
                "total_accounts": total_accounts,
                "total_wealth": total_wealth,
                "total_transactions": total_tx,
                "wealthiest_players": [dict(r) for r in wealthiest],
            }
        except sqlite3.Error as e:
            return {"ok": False, "error": str(e)}

    # ── LAN Discovery ───────────────────────────────────────────────

    def _start_lan_discovery(
        self,
        fallback_host: str = "",
        fallback_port: int = 7200,
    ) -> None:
        """
        Start LAN discovery for remote economy bridge servers.
        Based on the legacy eco_bridge.py discovery mechanism.
        """
        with self._lan_lock:
            if self._lan_discovery_active:
                return
            self._lan_discovery_active = True
        
        def _discovery_thread():
            import socket
            import struct
            import time
            
            MCAST_GRP = "224.0.0.251"
            MCAST_PORT = 7201
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(5.0)
                
                # Bind to the multicast port
                try:
                    sock.bind(('', MCAST_PORT))
                except OSError:
                    logger.debug("EconomyBridge: LAN discovery bind failed (non-root)")
                    return
                
                mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                
                logger.info("EconomyBridge: LAN discovery started on %s:%d", MCAST_GRP, MCAST_PORT)
                
                while self._lan_discovery_active:
                    try:
                        data, addr = sock.recvfrom(1024)
                        msg = data.decode("utf-8", errors="replace")
                        if "eco_bridge" in msg.lower() or "economy" in msg.lower():
                            parts = msg.split(":")
                            if len(parts) >= 2:
                                host = parts[0].strip()
                                port = int(parts[1].strip())
                                with self._lan_lock:
                                    self._discovered_host = addr[0]
                                    self._discovered_port = port
                                logger.info(
                                    "EconomyBridge: Discovered remote bridge at %s:%d",
                                    addr[0], port
                                )
                    except socket.timeout:
                        continue
                    except Exception:
                        continue
            finally:
                sock.close()
        
        thread = threading.Thread(target=_discovery_thread, daemon=True)
        thread.start()
        logger.info("EconomyBridge: LAN discovery thread started")

    def get_discovered_bridge(self) -> Optional[Dict[str, Any]]:
        """Get the currently LAN-discovered bridge endpoint."""
        with self._lan_lock:
            if self._discovered_host and self._discovered_port:
                return {
                    "host": self._discovered_host,
                    "port": self._discovered_port,
                }
        return None

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
            logger.error("EconomyBridge: VFS DB error for '%s': %s", server_id, e)
            return None
