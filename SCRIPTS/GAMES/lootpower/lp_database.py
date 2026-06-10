"""
LootPower Database Layer — consolidated single-database architecture.

Replaces the legacy multi-file DB pattern (13 separate .db3 files) with
a single lootpower.db3 containing all tables.
"""
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Any, List, Tuple
from contextlib import contextmanager
import lp_config


# ---------------------------------------------------------------------------
# Schema — CREATE TABLE statements for the consolidated database
# ---------------------------------------------------------------------------
SCHEMA = """
-- Players
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    user_name TEXT UNIQUE,
    password TEXT DEFAULT '',
    turns INTEGER DEFAULT 20 CHECK(turns <= 20),
    global_loot_chance_boost REAL DEFAULT 0.0,
    turn_time REAL DEFAULT 0.0,
    is_active INTEGER DEFAULT 1
);

-- Loot table (what CAN drop)
CREATE TABLE IF NOT EXISTS loot_table (
    loot TEXT,
    loot_chance REAL,
    loot_chance_raise REAL,
    self_loot_chance_lower REAL DEFAULT 0.0,
    loot_lore TEXT DEFAULT '',
    loot_id INTEGER PRIMARY KEY AUTOINCREMENT
);

-- Player inventory per loot type
CREATE TABLE IF NOT EXISTS users_loot (
    user_id TEXT,
    loot TEXT,
    loot_id INTEGER,
    loot_amount INTEGER DEFAULT 0,
    common INTEGER DEFAULT 0,
    uncommon INTEGER DEFAULT 0,
    rare INTEGER DEFAULT 0,
    great INTEGER DEFAULT 0,
    amazing INTEGER DEFAULT 0,
    legendary INTEGER DEFAULT 0,
    epic INTEGER DEFAULT 0,
    godly INTEGER DEFAULT 0,
    mythic INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, loot_id)
);

-- LootPower scores
CREATE TABLE IF NOT EXISTS lootpower_table (
    user_ID TEXT PRIMARY KEY,
    lootpower REAL DEFAULT 0.0
);

-- Crafting recipes
CREATE TABLE IF NOT EXISTS craft_rep (
    item_one_id INTEGER,
    item_two_id INTEGER,
    rep_item TEXT,
    rep_item_id INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE
);

-- Player crafted items
CREATE TABLE IF NOT EXISTS user_craft (
    user_id TEXT,
    craft_id INTEGER,
    amount INTEGER DEFAULT 0,
    rarity TEXT DEFAULT 'common',
    PRIMARY KEY (user_id, craft_id)
);

-- Mining zones
CREATE TABLE IF NOT EXISTS areas (
    zone_x INTEGER,
    zone_y INTEGER,
    user_id TEXT,
    ore TEXT,
    PRIMARY KEY (zone_x, zone_y)
);

-- Player ore inventory
CREATE TABLE IF NOT EXISTS user_ore (
    user_id TEXT PRIMARY KEY,
    power_coin INTEGER DEFAULT 0,
    bag_of_dirt INTEGER DEFAULT 0,
    loot_ore INTEGER DEFAULT 0
);

-- Adventure stories
CREATE TABLE IF NOT EXISTS story_table (
    story_id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_text TEXT,
    story_cat TEXT,
    creation_time REAL
);

-- Global stats
CREATE TABLE IF NOT EXISTS stat_table (
    rowid INTEGER PRIMARY KEY CHECK(rowid = 1),
    adventures INTEGER DEFAULT 0,
    sold_items INTEGER DEFAULT 0
);

-- Per-drop stats logging
CREATE TABLE IF NOT EXISTS loot_stats (
    user_id TEXT,
    loot_name TEXT,
    loot_rarity TEXT,
    year INTEGER,
    month INTEGER,
    day INTEGER,
    hour INTEGER,
    minute INTEGER
);

-- Auction listings
CREATE TABLE IF NOT EXISTS auction (
    seller_id TEXT,
    loot_id INTEGER,
    loot_amount INTEGER DEFAULT 1,
    payment_type INTEGER DEFAULT 0,
    payment_id INTEGER DEFAULT 0,
    payment_amount INTEGER DEFAULT 0,
    auction_id INTEGER PRIMARY KEY AUTOINCREMENT
);

-- Runtime control codes
CREATE TABLE IF NOT EXISTS runtime_codes (
    name TEXT PRIMARY KEY,
    code INTEGER DEFAULT 0
);

-- Watchers (observers)
CREATE TABLE IF NOT EXISTS watchers (
    watcher_id TEXT PRIMARY KEY,
    label TEXT DEFAULT '',
    filters TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    created_at REAL DEFAULT 0.0
);

-- Insert default stat row if missing
INSERT OR IGNORE INTO stat_table (rowid, adventures, sold_items) VALUES (1, 0, 0);

-- Insert default runtime code if missing
INSERT OR IGNORE INTO runtime_codes (name, code) VALUES ('Server', 0);
"""


# ---------------------------------------------------------------------------
# DatabaseEngine — thread-safe singleton connection manager
# ---------------------------------------------------------------------------
class DatabaseEngine:
    """Thread-safe consolidated SQLite database engine."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path: Optional[Path] = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: Optional[Path] = None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.db_path = Path(db_path) if isinstance(db_path, str) else (db_path or lp_config.DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._local = threading.local()
        self._init_db()

    def _init_db(self):
        """Create connection and initialize schema."""
        conn = self._get_raw_conn()
        conn.executescript(SCHEMA)
        conn.commit()

    def _get_raw_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._get_raw_conn()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute SQL and return cursor."""
        if lp_config.DRY_RUN and not sql.strip().upper().startswith("SELECT"):
            print(f"[DRY-RUN] Would execute: {sql[:80]}...")
            return None
        cur = self.conn.execute(sql, params)
        return cur

    def executemany(self, sql: str, seq: List[tuple]) -> sqlite3.Cursor:
        if lp_config.DRY_RUN:
            return None
        cur = self.conn.executemany(sql, seq)
        return cur

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(sql, params)
        return cur.fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        cur = self.conn.execute(sql, params)
        return cur.fetchall()

    def commit(self):
        if not lp_config.DRY_RUN:
            self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self):
        """Context manager for atomic transactions."""
        if lp_config.DRY_RUN:
            yield None
            return
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


def get_db() -> DatabaseEngine:
    """Get the singleton database engine."""
    return DatabaseEngine()