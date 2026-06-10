"""
sp_item_cache.py — Item Registration & Caching Bridge

Bridges the AH market's namespaced item IDs with the Simulated People's
``ITEM_DEFS`` catalogue.  Handles bidirectional item conversion and caches
unknown items so the AI only processes them once.

Also provides:

  - **NBT Preservation**:  When an item with NBT/component data enters the
    SimWorld, the NBT string is stored alongside the ``market_id`` so it
    can be perfectly reconstructed when going back to the market.

  - **Trash Database**:  Every item removal from SimWorld inventory is
    logged to ``ext_sp_item_trash`` with the reason, timestamp, source
    persona, and full item data (including NBT).  Provides query/stats
    functions for auditing and recovery.

NAMESPACE PRESERVATION RULES
────────────────────────────
  The market uses namespaced IDs (e.g. ``minecraft:diamond``, ``mymod:sword``).
  The SimWorld uses bare IDs in ``ITEM_DEFS`` (e.g. ``stone``, ``diamond``).

  1. **Only ``minecraft:`` is stripped** going Market→SimWorld
  2. **Non-minecraft namespaces are PRESERVED** (modded items stay intact)
  3. **Going back SimWorld→Market**, the cache's ``market_id`` is used to
     reconstruct the EXACT original ID (including namespace)

  Examples:
    ``minecraft:diamond``   → ``diamond``              (minecraft: stripped)
    ``mymod:custom_sword``  → ``mymod:custom_sword``   (PRESERVED!)
    ``minecraft:sponge``    → ``sponge``               (minecraft: stripped)

    ``diamond``             → cache lookup ``market_id`` → ``minecraft:diamond``
    ``mymod:custom_sword``  → cache lookup ``market_id`` → ``mymod:custom_sword``
    ``stone`` (native sim)  → no cache → ``minecraft:stone``

NBT PRESERVATION FLOW
─────────────────────
  1. An item with NBT (e.g. a custom sword with enchantments) is listed on
     the AH with ``item_nbt='{"components":{...}}'``.
  2. When a persona buys it, ``register_item(market_id, market_nbt=nbt)``
     stores the NBT string in the cache.
  3. When the persona later sells the item back, ``resolve_item_for_market()``
     returns both the original ``market_id`` and ``market_nbt``.
  4. The NBT is reattached when calling ``list_item(item_nbt=cached_nbt)``.

FLOW DIAGRAM
────────────
  Market → Simulated World
    1. ``minecraft:diamond[+nbt]`` → ``market_to_sim()`` → ``diamond``
    2. ``register_item("minecraft:diamond", market_nbt=nbt)``
    3. ``give_item(persona_uuid, "diamond", 1)``

  Simulated World → Market
    1. ``resolve_item_for_market("diamond")`` → ``{"market_id": "minecraft:diamond", "market_nbt": "{...}"}``
    2. ``list_item(seller=s, item_id="minecraft:diamond", item_nbt="{...}")``

TRASH DATABASE
──────────────
  Table ``ext_sp_item_trash`` records every item removal/reason:

    - ``remove_item()`` automatically logs to trash with reason "discarded"
    - ``consume_item()`` automatically logs to trash with reason "consumed"
    - Manual: ``log_item_removal(persona, item, qty, reason, ...)``
"""

import json
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_database import get_db as ah_get_db
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import (
    get_item_def, ITEM_DEFS,
)

log = get_logger()

# ═══════════════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════════════

_ITEM_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_item_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         TEXT NOT NULL UNIQUE,          -- SimWorld bare ID (e.g. "diamond")
    market_id       TEXT,                          -- Full market ID including namespace (e.g. "minecraft:diamond" or "mymod:custom_sword")
    market_nbt      TEXT,                          -- Original NBT/component data as JSON string (preserved for roundtrip)
    category        TEXT NOT NULL DEFAULT 'unknown',
    weight_kg       REAL NOT NULL DEFAULT 1.0,
    value_estimate  REAL NOT NULL DEFAULT 1.0,
    perishable      INTEGER NOT NULL DEFAULT 0,
    cache_reason    TEXT,
    source_listing  TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    usage_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_item_cache_id ON ext_sp_item_cache(item_id);
CREATE INDEX IF NOT EXISTS idx_item_cache_market ON ext_sp_item_cache(market_id);
"""

_TRASH_TABLE = """
CREATE TABLE IF NOT EXISTS ext_sp_item_trash (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid    TEXT NOT NULL,
    item_id         TEXT NOT NULL,                 -- SimWorld item ID
    market_id       TEXT,                          -- Market ID if known
    market_nbt      TEXT,                          -- Original NBT if known
    quantity        REAL NOT NULL,
    reason          TEXT NOT NULL,                 -- "discarded", "consumed", "crafted", "broke", "equipped", "decayed", "traded", "admin"
    container       TEXT,
    container_id    INTEGER,
    details_json    TEXT,                          -- Extra context (recipe used, hunger restored, etc.)
    trashed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trash_persona ON ext_sp_item_trash(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_trash_item ON ext_sp_item_trash(item_id);
CREATE INDEX IF NOT EXISTS idx_trash_reason ON ext_sp_item_trash(reason);
CREATE INDEX IF NOT EXISTS idx_trash_at ON ext_sp_item_trash(trashed_at);
"""


def _get_db():
    return ah_get_db()


# ── Schema Ensurance ────────────────────────────────────────────────

def _ensure_cache_table():
    """Create the item cache table if it doesn't exist."""
    db = _get_db()
    exists = db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ext_sp_item_cache'")
    if not exists:
        _exec_table_sql(_ITEM_CACHE_TABLE)
        log.info("sp_item_cache", "Item cache table initialized")


def _ensure_trash_table():
    """Create the trash table if it doesn't exist."""
    db = _get_db()
    exists = db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ext_sp_item_trash'")
    if not exists:
        _exec_table_sql(_TRASH_TABLE)
        log.info("sp_item_cache", "Trash table initialized")


# NOTE: This splits SQL on \n\n then ; — fragile if statements contain blank lines or semicolons
def _exec_table_sql(table_sql: str):
    """Execute a multi-statement CREATE TABLE SQL block safely."""
    db = _get_db()
    for block in table_sql.strip().split("\n\n"):
        s = block.strip()
        if s:
            for individual_stmt in s.split(";"):
                individual_stmt = individual_stmt.strip()
                if individual_stmt and individual_stmt.upper().startswith("CREATE"):
                    try:
                        db.execute(individual_stmt)
                    except Exception:
                        pass


def ensure_all_tables():
    """Ensure both cache and trash tables exist."""
    _ensure_cache_table()
    _ensure_trash_table()


# ── NBT Column Migration ────────────────────────────────────────────

def _ensure_nbt_column():
    """Idempotently add the market_nbt column if it doesn't exist.
    
    This handles upgrading an existing cache that was created before
    NBT preservation was added.
    """
    db = _get_db()
    cols = db.fetch_all("PRAGMA table_info(ext_sp_item_cache)")
    col_names = [c["name"] for c in cols]
    if "market_nbt" not in col_names:
        try:
            db.execute("ALTER TABLE ext_sp_item_cache ADD COLUMN market_nbt TEXT")
            log.info("sp_item_cache", "Added market_nbt column to ext_sp_item_cache")
        except Exception:
            pass  # Column already exists or table doesn't exist yet


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _strip_minecraft(item_id: str) -> str:
    """Strip ONLY the ``minecraft:`` namespace prefix.
    
    Non-minecraft namespaces (modded items) are PRESERVED.
    
    ``minecraft:diamond`` → ``diamond``
    ``mymod:custom_sword`` → ``mymod:custom_sword`` (preserved!)
    ``diamond`` → ``diamond`` (no change)
    """
    if item_id.startswith("minecraft:"):
        return item_id[len("minecraft:"):]
    return item_id


def _assert_minecraft(item_id: str) -> str:
    """Ensure an item ID has a namespace for market use.
    
    Bare IDs get ``minecraft:`` prepended (e.g. ``stone`` → ``minecraft:stone``)
    Already-namespaced IDs pass through (e.g. ``mymod:sword`` → ``mymod:sword``)
    """
    if ":" not in item_id:
        return f"minecraft:{item_id}"
    return item_id


def _category_for_item(item_id: str) -> str:
    """Guess a reasonable category for an unknown item based on its ID."""
    try:
        lower = item_id.lower()

    except Exception as e:
        log.error(f"_category_for_item failed: {e}")
        return ""
    if any(t in lower for t in ["sword", "axe", "pickaxe", "shovel", "hoe", "knife", "spear", "bow", "trident"]):
        return "tool"
    if any(t in lower for t in ["helmet", "chestplate", "leggings", "boots", "elytra", "shield"]):
        return "armor"
    if any(t in lower for t in ["steak", "porkchop", "chicken", "fish", "bread", "apple", "berry",
                                 "meat", "food", "soup", "beef", "mutton", "rabbit"]):
        return "food"
    if any(t in lower for t in ["coal", "iron", "gold", "diamond", "emerald", "ore", "ingot",
                                 "stone", "wood", "log", "plank"]):
        return "material"
    if any(t in lower for t in ["potion", "herb", "bandage", "medicine"]):
        return "medicine"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════
# Item Cache API
# ═══════════════════════════════════════════════════════════════════════

def register_item(market_item_id: str, source_listing: Optional[str] = None,
                  cache_reason: str = "market_import",
                  market_nbt: Optional[str] = None) -> dict:
    """Register a market item in the SimWorld cache.

    Stores the ORIGINAL market_id (with full namespace) and NBT data
    so the item can be reconstructed exactly when going back to the market.
    If the item is already known to the SimWorld via ITEM_DEFS, just bumps usage.

    Args:
        market_item_id: The full market item ID (e.g. ``minecraft:diamond``, ``mymod:sword``)
        source_listing: The listing UUID that introduced this item
        cache_reason: Why this item is being registered
        market_nbt: The NBT/component data as a JSON string (preserved for roundtrip)

    Returns:
        {"ok": True, "data": {"item_id": ..., "market_id": ..., "was_new": bool, "source": str}}
    """
    try:
        _ensure_cache_table()

    except Exception as e:
        log.error(f"register_item failed: {e}")
        return {}
    _ensure_nbt_column()
    db = _get_db()
    sim_id = _strip_minecraft(market_item_id)
    market_id = market_item_id
    now = datetime.now(timezone.utc).isoformat()

    # Check if already in ITEM_DEFS
    existing_def = get_item_def(sim_id)
    if existing_def:
        db.execute("""
            INSERT INTO ext_sp_item_cache
            (item_id, market_id, market_nbt, category, weight_kg, value_estimate,
             perishable, cache_reason, source_listing,
             first_seen_at, last_seen_at, usage_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                market_id = COALESCE(excluded.market_id, market_id),
                market_nbt = COALESCE(excluded.market_nbt, market_nbt),
                last_seen_at = excluded.last_seen_at,
                usage_count = usage_count + 1
        """, (sim_id, market_id, market_nbt, existing_def.get("category", "unknown"),
              existing_def.get("weight_kg", 1.0), 1.0,
              1 if existing_def.get("perishable") else 0,
              cache_reason, source_listing, now, now, now))
        return {"ok": True, "data": {"item_id": sim_id, "market_id": market_id,
                                     "market_nbt": market_nbt,
                                     "was_new": False, "source": "ITEM_DEFS"}}

    # Check if already cached
    cached = db.fetch_one("SELECT * FROM ext_sp_item_cache WHERE item_id = ?", (sim_id,))
    if cached:
        db.execute("""
            UPDATE ext_sp_item_cache
            SET last_seen_at = ?, usage_count = usage_count + 1
            WHERE item_id = ?
        """, (now, sim_id))
        # If we have NBT now and didn't before, update it
        if market_nbt and not cached.get("market_nbt"):
            db.execute("UPDATE ext_sp_item_cache SET market_nbt = ? WHERE item_id = ?",
                       (market_nbt, sim_id))
        return {"ok": True, "data": {"item_id": sim_id, "market_id": market_id,
                                     "market_nbt": market_nbt or cached.get("market_nbt"),
                                     "was_new": False, "source": "cache"}}

    # New item — create cache entry with original market_id and NBT
    category = _category_for_item(sim_id)
    weight_kg = 2.0 if category in ("tool", "armor") else 1.0
    perishable = 1 if category == "food" else 0

    db.execute("""
        INSERT INTO ext_sp_item_cache
        (item_id, market_id, market_nbt, category, weight_kg, value_estimate,
         perishable, cache_reason, source_listing,
         first_seen_at, last_seen_at, usage_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    """, (sim_id, market_id, market_nbt, category, weight_kg, 1.0,
          perishable, cache_reason, source_listing, now, now))

    # Also create minimal entry in ext_sp_item_defs
    name = sim_id.replace("_", " ").title()
    db.execute("""
        INSERT OR REPLACE INTO ext_sp_item_defs
        (item_id, name, category, weight_kg, calories_per_unit,
         hydration_per_unit, perishable, decay_per_day,
         crafting_tags, description)
        VALUES (?, ?, ?, ?, 0, 0, ?, 0.0, ?, ?)
    """, (sim_id, name, category, weight_kg,
          perishable,
          json.dumps(["imported"]),
          f"Imported from market. Original: {market_id}"))

    nbt_note = f" (NBT: {market_nbt[:80]}...)" if market_nbt else ""
    log.info("sp_item_cache", f"Registered new item: {sim_id} (market: {market_id}{nbt_note}, category={category})")
    ITEM_DEFS[sim_id] = {"name": name, "category": category, "weight_kg": weight_kg,
                         "perishable": bool(perishable), "crafting_tags": ["imported"],
                         "description": f"Imported from market. Original: {market_id}"}
    return {"ok": True, "data": {"item_id": sim_id, "market_id": market_id,
                                 "market_nbt": market_nbt,
                                 "was_new": True, "category": category, "source": "new"}}


def market_to_sim(market_item_id: str) -> str:
    """Convert a market item ID to a SimWorld item ID.
    
    **Only strips ``minecraft:`` prefix.** Non-minecraft namespaces
    (like ``mymod:``, ``custom:``) are PRESERVED.
    
    ``minecraft:diamond`` → ``diamond``
    ``minecraft:iron_ingot`` → ``iron_ingot``
    ``mymod:custom_sword`` → ``mymod:custom_sword``  (preserved!)
    ``diamond`` → ``diamond`` (no change)
    """
    return _strip_minecraft(market_item_id)


def sim_to_market(sim_item_id: str) -> str:
    """Convert a SimWorld item ID back to a market ID.
    
    First checks the cache for the **original market_id** (with full namespace).
    If found in cache, returns the exact original ID.
    If not cached (native sim item), adds ``minecraft:`` prefix.
    
    ``diamond`` (cached as minecraft:diamond) → ``minecraft:diamond``
    ``mymod:custom_sword`` (cached as mymod:custom_sword) → ``mymod:custom_sword``
    ``stone`` (native sim, not cached) → ``minecraft:stone``
    """
    cache = get_cached_item(sim_item_id)
    if cache and cache.get("market_id"):
        return cache["market_id"]
    return _assert_minecraft(sim_item_id)


def resolve_item_for_market(sim_item_id: str) -> dict:
    """Resolve a SimWorld item ID back to full market data.
    
    Returns both the **market_id** (full namespace) and any stored
    **market_nbt** (original NBT/component data).  This is the function
    to call when a persona wants to sell an item back to the market.
    
    Args:
        sim_item_id: The SimWorld item ID (e.g. ``diamond``, ``mymod:custom_sword``)
    
    Returns:
        {"ok": True, "data": {"market_id": "...", "market_nbt": "..."}}
        or {"ok": False, "error": "..."}
    """
    cache = get_cached_item(sim_item_id)
    if cache and cache.get("market_id"):
        return {
            "ok": True,
            "data": {
                "market_id": cache["market_id"],
                "market_nbt": cache.get("market_nbt"),
            }
        }
    # Native sim item — just add minecraft: prefix
    market_id = _assert_minecraft(sim_item_id)
    return {
        "ok": True,
        "data": {
            "market_id": market_id,
            "market_nbt": None,
        }
    }


def ensure_item_known(market_item_id: str, source_listing: Optional[str] = None,
                      market_nbt: Optional[str] = None) -> dict:
    """Guarantee a market item is known to the SimWorld.
    
    Returns immediately if the item is already in ITEM_DEFS.
    Otherwise registers it in the cache.
    
    Args:
        market_item_id: The full market item ID (e.g. ``minecraft:sponge``)
        source_listing: The listing UUID
        market_nbt: NBT data to preserve

    Returns:
        Result from register_item()
    """
    sim_id = _strip_minecraft(market_item_id)
    if get_item_def(sim_id):
        return {"ok": True, "data": {"item_id": sim_id, "was_new": False, "source": "ITEM_DEFS"}}
    return register_item(market_item_id, source_listing=source_listing,
                         cache_reason="market_import", market_nbt=market_nbt)


def resolve_item_for_persona(persona_uuid: str, market_item_id: str,
                              source_listing: Optional[str] = None,
                              market_nbt: Optional[str] = None) -> dict:
    """Full resolution: convert market item → register if new → return for SimWorld.
    
    Args:
        persona_uuid: The persona buying the item
        market_item_id: The full market item ID (e.g. ``minecraft:diamond``)
        source_listing: The listing UUID
        market_nbt: NBT data to preserve in cache

    Returns:
        {"ok": True, "data": {"sim_item_id": ..., "item_def": ...,
                              "was_cached": bool, "market_nbt": ...}}
    """
    try:
        sim_id = market_to_sim(market_item_id)

    except Exception as e:
        log.error(f"resolve_item_for_per failed: {e}")
        return {}
    item_def = get_item_def(sim_id)
    was_cached = False
    resolved_nbt = market_nbt

    if not item_def:
        reg_result = register_item(market_item_id, source_listing=source_listing,
                                   cache_reason="market_import", market_nbt=market_nbt)
        was_cached = reg_result["data"]["was_new"]
        item_def = get_item_def(sim_id)
        # Use the NBT from registration result if we didn't pass one
        if not resolved_nbt:
            resolved_nbt = reg_result["data"].get("market_nbt")
    else:
        # Item is in ITEM_DEFS but may also be cached with NBT
        cache = get_cached_item(market_item_id)
        if cache and cache.get("market_nbt"):
            resolved_nbt = cache["market_nbt"]
        elif market_nbt:
            # Register the NBT even for known items
            register_item(market_item_id, cache_reason="nbt_update", market_nbt=market_nbt)

    return {
        "ok": True,
        "data": {
            "sim_item_id": sim_id,
            "item_def": item_def,
            "was_cached": was_cached,
            "market_nbt": resolved_nbt,
        }
    }


# ═══════════════════════════════════════════════════════════════════════
# Cache Query
# ═══════════════════════════════════════════════════════════════════════

def get_cache_stats() -> dict:
    """Get statistics about the item cache."""
    _ensure_cache_table()
    db = _get_db()
    total = db.fetch_one("SELECT COUNT(*) as c FROM ext_sp_item_cache")
    by_category = db.fetch_all(
        "SELECT category, COUNT(*) as cnt FROM ext_sp_item_cache GROUP BY category")
    by_reason = db.fetch_all(
        "SELECT cache_reason, COUNT(*) as cnt FROM ext_sp_item_cache GROUP BY cache_reason")
    with_nbt = db.fetch_one(
        "SELECT COUNT(*) as c FROM ext_sp_item_cache WHERE market_nbt IS NOT NULL")

    return {
        "total_cached": total["c"] if total else 0,
        "with_nbt": with_nbt["c"] if with_nbt else 0,
        "by_category": {r["category"]: r["cnt"] for r in by_category} if by_category else {},
        "by_reason": {r["cache_reason"]: r["cnt"] for r in by_reason} if by_reason else {},
    }


def get_cached_item(item_id: str) -> Optional[dict]:
    """Get a cached item entry, or None if not in cache.
    
    Accepts both market IDs (``minecraft:diamond``) and sim IDs (``diamond``).
    """
    _ensure_cache_table()
    db = _get_db()
    sim_id = _strip_minecraft(item_id)
    return db.fetch_one("SELECT * FROM ext_sp_item_cache WHERE item_id = ?", (sim_id,))


def clear_cache():
    """Clear the item cache (for testing)."""
    _ensure_cache_table()
    db = _get_db()
    db.execute("DELETE FROM ext_sp_item_cache")


# ═══════════════════════════════════════════════════════════════════════
# Trash Database API
# ═══════════════════════════════════════════════════════════════════════

VALID_TRASH_REASONS = frozenset({
    "discarded", "consumed", "crafted", "broke", "equipped",
    "decayed", "traded", "admin", "sold",
})


def log_item_removal(persona_uuid: str, item_id: str, quantity: float,
                     reason: str = "discarded",
                     market_id: Optional[str] = None,
                     market_nbt: Optional[str] = None,
                     container: Optional[str] = None,
                     container_id: Optional[int] = None,
                     details: Optional[dict] = None) -> dict:
    """Log an item removal to the trash database.

    This is the central audit trail for all items that leave a persona's
    inventory.  Called automatically by ``remove_item()`` and
    ``consume_item()``, but can also be called manually.

    Args:
        persona_uuid: The persona who lost the item
        item_id: SimWorld item ID
        quantity: How many units were removed
        reason: Why the item was removed ("discarded", "consumed", "crafted",
                "broke", "equipped", "decayed", "traded", "admin", "sold")
        market_id: The AH market ID if known (looked up from cache if not provided)
        market_nbt: The NBT data if known (looked up from cache if not provided)
        container: Which container the item was in (e.g. "inventory", "hands", "storage")
        container_id: The inventory record ID
        details: Optional dict with extra context (recipe used, hunger restored, etc.)

    Returns:
        {"ok": True, "data": {"trash_id": int}}
        or {"ok": False, "error": "..."}
    """
    try:
        _ensure_trash_table()

    except Exception as e:
        log.error(f"log_item_removal failed: {e}")
        return {}

    if reason not in VALID_TRASH_REASONS:
        log.warn("sp_item_cache", f"Unknown trash reason '{reason}' — logging anyway")

    # Auto-resolve market_id and market_nbt from cache if not provided
    if not market_id or not market_nbt:
        cache = get_cached_item(item_id)
        if cache:
            if not market_id:
                market_id = cache.get("market_id")
            if not market_nbt:
                market_nbt = cache.get("market_nbt")

    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    details_json = json.dumps(details) if details else None

    db.execute("""
        INSERT INTO ext_sp_item_trash
        (persona_uuid, item_id, market_id, market_nbt, quantity,
         reason, container, container_id, details_json, trashed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid, item_id, market_id, market_nbt, quantity,
          reason, container, container_id, details_json, now))

    # Use a separate fetch query since db.execute() returns a cursor
    row = db.fetch_one("SELECT last_insert_rowid() as id")
    trash_id = row["id"] if row else 0

    log.info("sp_item_cache", f"Trash: {persona_uuid[:8]}.. lost {quantity}x {item_id} ({reason})")
    return {"ok": True, "data": {"trash_id": trash_id}}


def query_trash(persona_uuid: Optional[str] = None,
                item_id: Optional[str] = None,
                reason: Optional[str] = None,
                since: Optional[str] = None,
                until: Optional[str] = None,
                limit: int = 100,
                offset: int = 0) -> list[dict]:
    """Query the trash database with optional filters.

    Args:
        persona_uuid: Filter by persona
        item_id: Filter by item
        reason: Filter by removal reason
        since: ISO timestamp — only include entries after this time
        until: ISO timestamp — only include entries before this time
        limit: Max results (default 100)
        offset: Pagination offset

    Returns:
        List of trash entries, newest first
    """
    try:
        _ensure_trash_table()

    except Exception as e:
        log.error(f"query_trash failed: {e}")
        return []
    db = _get_db()

    clauses = []
    params = []

    if persona_uuid:
        clauses.append("persona_uuid = ?")
        params.append(persona_uuid)
    if item_id:
        clauses.append("item_id = ?")
        params.append(item_id)
    if reason:
        clauses.append("reason = ?")
        params.append(reason)
    if since:
        clauses.append("trashed_at >= ?")
        params.append(since)
    if until:
        clauses.append("trashed_at <= ?")
        params.append(until)

    where = " AND ".join(clauses) if clauses else "1=1"

    rows = db.fetch_all(f"""
        SELECT * FROM ext_sp_item_trash
        WHERE {where}
        ORDER BY trashed_at DESC
        LIMIT ? OFFSET ?
    """, (*params, limit, offset))

    return rows


def get_trash_stats(persona_uuid: Optional[str] = None) -> dict:
    """Get trash statistics for a persona (or all personas).

    Args:
        persona_uuid: Optional persona to filter by

    Returns:
        Dict with total entries, total quantity, by reason, by item
    """
    _ensure_trash_table()
    db = _get_db()

    where = "WHERE persona_uuid = ?" if persona_uuid else ""
    params = (persona_uuid,) if persona_uuid else ()

    total_rows = db.fetch_one(
        f"SELECT COUNT(*) as c, COALESCE(SUM(quantity), 0) as q FROM ext_sp_item_trash {where}",
        params)

    by_reason = db.fetch_all(
        f"SELECT reason, COUNT(*) as cnt, COALESCE(SUM(quantity), 0) as total_qty "
        f"FROM ext_sp_item_trash {where} GROUP BY reason ORDER BY cnt DESC",
        params)

    top_items = db.fetch_all(
        f"SELECT item_id, COUNT(*) as cnt, COALESCE(SUM(quantity), 0) as total_qty "
        f"FROM ext_sp_item_trash {where} GROUP BY item_id ORDER BY cnt DESC LIMIT 10",
        params)

    return {
        "total_entries": total_rows["c"] if total_rows else 0,
        "total_quantity": total_rows["q"] if total_rows else 0,
        "by_reason": {r["reason"]: {"count": r["cnt"], "quantity": r["total_qty"]}
                      for r in by_reason} if by_reason else {},
        "top_items": {r["item_id"]: {"count": r["cnt"], "quantity": r["total_qty"]}
                      for r in top_items} if top_items else {},
    }


def get_recent_trash(persona_uuid: str, limit: int = 20) -> list[dict]:
    """Get the most recent trash entries for a persona.
    
    Convenience wrapper around query_trash.
    """
    return query_trash(persona_uuid=persona_uuid, limit=limit)


def clear_trash():
    """Clear all trash records (for testing)."""
    _ensure_trash_table()
    db = _get_db()
    db.execute("DELETE FROM ext_sp_item_trash")

