"""
ah_database.py — Database connection manager & schema initialization for the Auction House.

Creates and manages ``auctionhouse.db3`` in the AH data directory.
Provides a connection pool with thread-safe access and helper methods for
common CRUD patterns.

Schema (7 tables):
  - auction_listings     — Core auction item table
  - transaction_history  — Every completed bid/sale/cancel
  - price_history        — Time-series price snapshots
  - market_events        — Active and past market events
  - simulated_inventory  — AI-controlled common item stock
  - ai_notes             — AI helper notes & categories
  - player_balances      — (Optional) player currency tracking
"""

import json, os, sqlite3, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

DB_DIR = Path(__file__).parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "auctionhouse.db3"

# ──────────────────────────────────────────────────────────────────────
# Schema SQL
# ──────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """

CREATE TABLE IF NOT EXISTS auction_listings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_uuid        TEXT NOT NULL UNIQUE,
    seller_name         TEXT NOT NULL,
    item_id             TEXT NOT NULL,
    item_count          INTEGER NOT NULL DEFAULT 1,
    item_nbt            TEXT,
    signed_name         TEXT,
    rarity              TEXT,
    cert_hash           TEXT,
    is_simulated        INTEGER NOT NULL DEFAULT 0,
    start_price         REAL NOT NULL,
    current_bid         REAL,
    buy_now_price       REAL,
    highest_bidder      TEXT,
    bids_count          INTEGER NOT NULL DEFAULT 0,
    currency_type       TEXT NOT NULL DEFAULT 'emerald',
    status              TEXT NOT NULL DEFAULT 'active',
    listed_at           TEXT NOT NULL,
    expires_at          TEXT,
    sold_at             TEXT,
    sold_price          REAL,
    ai_weight           REAL DEFAULT 1.0,
    stale_since         TEXT,
    extra_meta          TEXT,
    sim_lore            TEXT,
    sim_source_event    TEXT,
    sim_enchantments    TEXT,
    sim_durability      INTEGER,
    sim_quality_roll    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_listings_status ON auction_listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_seller ON auction_listings(seller_name);
CREATE INDEX IF NOT EXISTS idx_listings_item ON auction_listings(item_id);
CREATE INDEX IF NOT EXISTS idx_listings_simulated ON auction_listings(is_simulated);
CREATE INDEX IF NOT EXISTS idx_listings_stale ON auction_listings(stale_since);

CREATE TABLE IF NOT EXISTS transaction_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_uuid TEXT NOT NULL UNIQUE,
    listing_uuid    TEXT NOT NULL,
    transaction_type TEXT NOT NULL,
    actor_name      TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    item_count      INTEGER NOT NULL DEFAULT 1,
    price           REAL,
    previous_price  REAL,
    balance_before  REAL,
    balance_after   REAL,
    metadata        TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tx_listing ON transaction_history(listing_uuid);
CREATE INDEX IF NOT EXISTS idx_tx_actor ON transaction_history(actor_name);
CREATE INDEX IF NOT EXISTS idx_tx_type ON transaction_history(transaction_type);
CREATE INDEX IF NOT EXISTS idx_tx_time ON transaction_history(created_at);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,
    price_avg   REAL NOT NULL,
    price_min   REAL NOT NULL,
    price_max   REAL NOT NULL,
    listing_count INTEGER NOT NULL DEFAULT 0,
    volume_sold INTEGER NOT NULL DEFAULT 0,
    snapshot_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ph_item ON price_history(item_id);
CREATE INDEX IF NOT EXISTS idx_ph_time ON price_history(snapshot_at);

CREATE TABLE IF NOT EXISTS market_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uuid      TEXT NOT NULL UNIQUE,
    event_name      TEXT NOT NULL,
    event_title     TEXT NOT NULL,
    event_flavor    TEXT NOT NULL,
    event_type      TEXT NOT NULL DEFAULT 'seasonal',
    rarity_tier     TEXT NOT NULL DEFAULT 'small',
    affected_items  TEXT NOT NULL,
    price_multiplier REAL NOT NULL DEFAULT 1.0,
    demand_boost    REAL NOT NULL DEFAULT 1.0,
    duration_seconds INTEGER NOT NULL DEFAULT 86400,
    trigger_condition TEXT,
    goal_count      INTEGER,
    current_count   INTEGER DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    created_by      TEXT NOT NULL DEFAULT 'AI'
);

CREATE INDEX IF NOT EXISTS idx_me_active ON market_events(is_active);
CREATE INDEX IF NOT EXISTS idx_me_type ON market_events(event_type);

CREATE TABLE IF NOT EXISTS simulated_inventory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         TEXT NOT NULL UNIQUE,
    category        TEXT NOT NULL DEFAULT 'common',
    current_stock   INTEGER NOT NULL DEFAULT 0,
    max_stock       INTEGER NOT NULL DEFAULT 100,
    base_price      REAL NOT NULL,
    current_price   REAL,
    volatility      REAL NOT NULL DEFAULT 0.1,
    trend_direction INTEGER NOT NULL DEFAULT 0,
    trend_strength  REAL NOT NULL DEFAULT 0.0,
    last_updated    TEXT NOT NULL,
    extra_meta      TEXT
);

CREATE TABLE IF NOT EXISTS ai_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,
    content         TEXT NOT NULL,
    reasoning       TEXT,
    related_item_id TEXT,
    related_event   TEXT,
    importance      INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    expires_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_notes_category ON ai_notes(category);
CREATE INDEX IF NOT EXISTS idx_notes_importance ON ai_notes(importance);
CREATE INDEX IF NOT EXISTS idx_notes_created ON ai_notes(created_at);

CREATE TABLE IF NOT EXISTS player_balances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name     TEXT NOT NULL UNIQUE,
    balance         REAL NOT NULL DEFAULT 0.0,
    lifetime_earned REAL NOT NULL DEFAULT 0.0,
    lifetime_spent  REAL NOT NULL DEFAULT 0.0,
    listings_count  INTEGER NOT NULL DEFAULT 0,
    purchases_count INTEGER NOT NULL DEFAULT 0,
    last_updated    TEXT NOT NULL
);
"""

# ──────────────────────────────────────────────────────────────────────
# Seed data for simulated_inventory
# ──────────────────────────────────────────────────────────────────────

_SEED_SIMULATED_INVENTORY = """
INSERT OR IGNORE INTO simulated_inventory (item_id, category, current_stock, max_stock, base_price, current_price, volatility, trend_direction, trend_strength, last_updated)
VALUES
    ('minecraft:coal',         'common',   500,  2000, 0.5,  0.5,  0.20, 0, 0.0, {ts}),
    ('minecraft:iron_ingot',   'common',   300,  1000, 1.5,  1.5,  0.15, 0, 0.0, {ts}),
    ('minecraft:gold_ingot',   'common',   100,  500,  3.0,  3.0,  0.20, 0, 0.0, {ts}),
    ('minecraft:redstone',     'common',   400,  1500, 0.25, 0.25, 0.12, 0, 0.0, {ts}),
    ('minecraft:lapis_lazuli', 'uncommon', 150,  500,  2.0,  2.0,  0.15, 0, 0.0, {ts}),
    ('minecraft:diamond',      'rare',      50,  200,  8.0,  8.0,  0.25, 0, 0.0, {ts}),
    ('minecraft:emerald',      'common',   200,  1000, 1.0,  1.0,  0.10, 0, 0.0, {ts}),
    ('minecraft:netherite_ingot', 'rare',   20,  100,  25.0, 25.0, 0.30, 0, 0.0, {ts}),
    ('minecraft:stick',        'common',  1000, 5000, 0.05, 0.05, 0.08, 0, 0.0, {ts}),
    ('minecraft:oak_log',      'common',   800,  3000, 0.3,  0.3,  0.10, 0, 0.0, {ts}),
    ('minecraft:cobblestone',  'common',  2000, 5000, 0.05, 0.05, 0.05, 0, 0.0, {ts}),
    ('minecraft:wheat',        'common',   300,  1000, 0.5,  0.5,  0.12, 0, 0.0, {ts}),
    ('minecraft:bone',         'common',   200,  800,  0.3,  0.3,  0.15, 0, 0.0, {ts}),
    ('minecraft:ender_pearl',  'uncommon',  50,  200,  4.0,  4.0,  0.20, 0, 0.0, {ts}),
    ('minecraft:blaze_rod',    'uncommon',  30,  150,  6.0,  6.0,  0.20, 0, 0.0, {ts})
ON CONFLICT(item_id) DO NOTHING;
"""

# ──────────────────────────────────────────────────────────────────────
# Connection manager
# ──────────────────────────────────────────────────────────────────────

class DatabaseManager:
    """Thread-safe SQLite connection manager for the Auction House."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._local = threading.local()
        self._lock = threading.Lock()
        self._initialized = False

    # ── Connection ────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection to the database."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self._db_path),
                                                timeout=10)  # 10s busy timeout
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn.execute("PRAGMA busy_timeout=5000")  # 5s busy wait
            self._local.conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety/speed
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._get_conn()

    # ── Initialize ───────────────────────────────────────────────

    def initialize(self, force: bool = False):
        """Create tables and seed data if needed.

        Args:
            force: If True, drop and recreate all tables (destructive!)
        """
        if self._initialized and not force:
            return

        with self._lock:
            c = self._get_conn()

            if force:
                log.warn("database", "Force-recreating all tables!")
                for table in [
                    "auction_listings", "transaction_history", "price_history",
                    "market_events", "simulated_inventory", "ai_notes", "player_balances"
                ]:
                    c.execute(f"DROP TABLE IF EXISTS {table}")

            # Create schema (batched)
            c.executescript(_SCHEMA_SQL)

            # Seed simulated inventory with current timestamp
            ts = datetime.now(timezone.utc).isoformat()
            seed_sql = _SEED_SIMULATED_INVENTORY.replace("{ts}", f"'{ts}'")
            c.executescript(seed_sql)

            c.commit()
            self._initialized = True
            log.info("database", "Schema initialized successfully",
                     {"path": str(self._db_path)})

    # ── Generic query helpers ─────────────────────────────────────

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Fetch a single row as a dict, or None."""
        c = self._get_conn()
        row = c.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """Fetch all rows as a list of dicts."""
        c = self._get_conn()
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def execute(self, sql: str, params: tuple = (), retries: int = 3) -> sqlite3.Cursor:
        """Execute a write statement and commit. Returns cursor.

        Automatically retries up to `retries` times on SQLITE_BUSY.
        """
        import sqlite3 as _sqlite3
        last_error = None
        for attempt in range(retries):
            try:
                c = self._get_conn()
                cur = c.execute(sql, params)
                c.commit()
                return cur
            except _sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < retries - 1:
                    import time
                    wait = 0.1 * (2 ** attempt)  # Exponential backoff: 0.1s, 0.2s, 0.4s
                    log.warn("database", f"SQLITE_BUSY on execute, retry {attempt+1}/{retries} (waiting {wait:.1f}s)")
                    time.sleep(wait)
                    last_error = e
                else:
                    raise
        # If we exhausted retries, raise the last error
        raise last_error or RuntimeError("Execute failed after retries")

    def execute_many(self, sql: str, params_list: list[tuple]):
        """Execute the same SQL with many parameter sets."""
        c = self._get_conn()
        c.executemany(sql, params_list)
        c.commit()

    def insert_and_get_uuid(self, sql: str, params: tuple = (),
                            uuid_col: str = "listing_uuid") -> str:
        """Insert a row, return the UUID of the new row.

        Expects the SQL to have ``?`` for a UUID value at position 0.
        """
        new_uuid = str(uuid.uuid4())
        params = (new_uuid,) + params
        c = self._get_conn()
        c.execute(sql, params)
        c.commit()
        return new_uuid

    def close(self):
        """Close the current thread's connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def reset_database(self) -> list[str]:
        """Wipe ALL data — both core AH tables and extension (ext_sp_*) tables.

        Drops and recreates ALL tables.  Use with extreme caution — this is
        the "factory reset" button.

        Returns:
            List of table names that were wiped.
        """
        from AUCTIONHOUSE.ah_logger import get_logger as _log
        _log = _log()
        c = self._get_conn()

        # Discover all tables
        all_tables = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [r[0] for r in all_tables]

        wiped = []
        for t in table_names:
            if t.startswith("ext_sp_") or t in (
                "auction_listings", "transaction_history", "price_history",
                "market_events", "simulated_inventory", "ai_notes", "player_balances"
            ):
                c.execute(f"DROP TABLE IF EXISTS {t}")
                wiped.append(t)

        # Also drop indices for these tables
        for t in table_names:
            if t.startswith("sqlite_"):
                continue
            indices = c.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (t,)
            ).fetchall()
            for idx in indices:
                try:
                    c.execute(f"DROP INDEX IF EXISTS {idx[0]}")
                except Exception:
                    pass

        c.commit()
        self._initialized = False

        _log.info("database", f"Database wiped: {len(wiped)} tables dropped",
                  {"tables": wiped})
        return wiped

    def close_all(self):
        """Close all connections (use sparingly — at shutdown)."""
        self.close()


# ── Global singleton ─────────────────────────────────────────────────
_db_instance: Optional[DatabaseManager] = None
_db_lock = threading.Lock()


def get_db() -> DatabaseManager:
    """Return the global DatabaseManager singleton."""
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = DatabaseManager()
    return _db_instance


def initialize_database(force: bool = False):
    """Convenience: initialize the database schema and seed data.

    Call this once at startup before any other AH operations.
    """
    db = get_db()
    db.initialize(force=force)
    return db
