"""
db_probe.py — Database Operation Tracing & Verification.

Wraps the database connection to trace every SQL statement executed
during a data flow.  Provides verification helpers for table states,
row counts, FK integrity, and atomic operation success.

Usage:
    db_probe = DatabaseProbe()
    db_probe.attach()  # Start tracing
    result = list_item(...)
    db_probe.detach()  # Stop tracing

    db_probe.assert_table_state("auction_listings", expected_rows=5)
    db_probe.assert_atomic_update_succeeded(listing_uuid)
    db_probe.verify_fk_integrity()
"""

import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from collections import Counter

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()


@dataclass
class SQLTrace:
    """A single traced SQL operation."""
    query: str
    query_type: str  # SELECT, INSERT, UPDATE, DELETE, CREATE, PRAGMA
    params: tuple = ()
    row_count: int = 0
    duration_ms: float = 0.0
    timestamp: float = 0.0
    thread_id: int = 0
    table_names: list[str] = field(default_factory=list)


# ── Known tables in the AH system ──────────────────────────────────
CORE_TABLES = {
    "auction_listings", "transaction_history", "price_history",
    "market_events", "simulated_inventory", "ai_notes", "player_balances",
    "ah_banned_players",
}

SP_TABLES = {
    "ext_sp_profiles", "ext_sp_finances", "ext_sp_needs", "ext_sp_memory",
    "ext_sp_health", "ext_sp_life_events", "ext_sp_world_events",
    "ext_sp_world_areas", "ext_sp_persona_skills", "ext_sp_persona_location",
    "ext_sp_claims", "ext_sp_claims_buildings", "ext_sp_factions",
    "ext_sp_ecosystem", "ext_sp_ecosystem_resources",
    "ext_sp_crafting", "ext_sp_crafting_recipes",
    "ext_sp_behavior_memory", "ext_sp_behavior_event_log",
    "ext_sp_persona_events", "ext_sp_item_cache",
}

SOCIAL_TABLES = {
    "ext_soc_boredom", "ext_soc_exhaustion", "ext_soc_memories",
    "ext_soc_activities", "ext_soc_relationships",
}

RELS_TABLES = {
    "ext_rel_relationships", "ext_rel_situationships",
    "ext_rel_contentions", "ext_rel_skills",
}

CHAT_TABLES = {
    "ext_chat_conversations", "ext_chat_messages",
    "ext_chat_interest", "ext_chat_events",
}

TRADE_TABLES = {
    "ext_tr_routes", "ext_tr_trade_events", "ext_tr_market_data",
    "ext_tr_resources", "ext_tr_economy",
}

HEALTH_TABLES = {
    "ext_shm_blood", "ext_shm_blood_regeneration",
    "ext_shm_anatomy_bones", "ext_shm_anatomy_organs",
    "ext_shm_muscles", "ext_shm_genetics",
    "ext_shm_diseases", "ext_shm_disease_spread",
    "ext_shm_hygiene", "ext_shm_immune_response",
    "ext_shm_pain", "ext_shm_combat_skills",
    "ext_shm_negative_traits", "ext_shm_healing_log",
    "ext_shm_ai_decisions",
}

ALL_TABLES = (CORE_TABLES | SP_TABLES | SOCIAL_TABLES | RELS_TABLES
              | CHAT_TABLES | TRADE_TABLES | HEALTH_TABLES)

TABLE_GROUPS = {
    "core": CORE_TABLES,
    "people": SP_TABLES,
    "social": SOCIAL_TABLES,
    "relationships": RELS_TABLES,
    "chat": CHAT_TABLES,
    "trade": TRADE_TABLES,
    "health": HEALTH_TABLES,
}


def _extract_table_names(query: str) -> list[str]:
    """Extract referenced table names from a SQL query."""
    # Simple regex-based extraction for common patterns
    tables = set()
    # FROM clause
    for m in re.finditer(r'\bFROM\s+(\w+)', query, re.IGNORECASE):
        tables.add(m.group(1).lower())
    # JOIN clause
    for m in re.finditer(r'\bJOIN\s+(\w+)', query, re.IGNORECASE):
        tables.add(m.group(1).lower())
    # UPDATE table
    for m in re.finditer(r'\bUPDATE\s+(\w+)', query, re.IGNORECASE):
        tables.add(m.group(1).lower())
    # INSERT INTO table
    for m in re.finditer(r'\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)', query, re.IGNORECASE):
        tables.add(m.group(1).lower())
    # DELETE FROM table
    for m in re.finditer(r'\bDELETE\s+FROM\s+(\w+)', query, re.IGNORECASE):
        tables.add(m.group(1).lower())
    # CREATE TABLE table
    for m in re.finditer(r'\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)', query, re.IGNORECASE):
        tables.add(m.group(1).lower())
    return list(tables)


def _get_query_type(query: str) -> str:
    """Determine the type of SQL query."""
    try:
        q = query.strip().upper()

    except Exception as e:
        logger.error(f"_get_query_type failed: {e}")
        return ""
    if q.startswith("SELECT"):
        return "SELECT"
    elif q.startswith("INSERT"):
        return "INSERT"
    elif q.startswith("UPDATE"):
        return "UPDATE"
    elif q.startswith("DELETE"):
        return "DELETE"
    elif q.startswith("CREATE"):
        return "CREATE"
    elif q.startswith("PRAGMA"):
        return "PRAGMA"
    elif q.startswith("ALTER"):
        return "ALTER"
    elif q.startswith("DROP"):
        return "DROP"
    else:
        return "OTHER"


# ══════════════════════════════════════════════════════════════════════
# Wrapped connection
# ══════════════════════════════════════════════════════════════════════

class _TracedConnection:
    """Wraps a sqlite3.Connection to trace all operations."""

    def __init__(self, conn, probe: 'DatabaseProbe'):
        self._conn = conn
        self._probe = probe

    def execute(self, sql: str, parameters=None):
        start = time.time()
        try:
            result = self._conn.execute(sql, parameters or ())
            duration = (time.time() - start) * 1000
            self._probe._record_sql(sql, parameters or (), result,
                                    duration)
            return result
        except Exception as e:
            duration = (time.time() - start) * 1000
            self._probe._record_sql(sql, parameters or (), None,
                                    duration, error=str(e))
            raise

    def executescript(self, script: str):
        start = time.time()
        try:
            result = self._conn.executescript(script)
            duration = (time.time() - start) * 1000
            # Split script into individual statements
            for stmt in script.split(';'):
                s = stmt.strip()
                if s:
                    self._probe._record_sql(s, (), None, duration)
            return result
        except Exception as e:
            raise

    def fetch_one(self, sql: str, parameters=None):
        return self.execute(sql, parameters).fetchone()

    def fetch_all(self, sql: str, parameters=None):
        return self.execute(sql, parameters).fetchall()

    def insert_and_get_uuid(self, sql: str, *args, **kwargs):
        # Delegate to original method
        return self._conn.insert_and_get_uuid(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ══════════════════════════════════════════════════════════════════════
# Database Probe
# ══════════════════════════════════════════════════════════════════════

class DatabaseProbe:
    """Traces and verifies all database operations during a test."""

    def __init__(self, trace=None):
        self._traces: list[SQLTrace] = []
        self._lock = threading.Lock()
        self._attached = False
        self._original_db = None
        self._trace_ref = trace  # Optional DataFlowTrace to sync with

    # ── Lifecycle ────────────────────────────────────────────────

    def attach(self):
        """Start tracing database operations."""
        if self._attached:
            return
        self._original_db = get_db()
        # Create wrapped connection
        conn = _TracedConnection(self._original_db, self)
        # We need to monkey-patch the DB module's connection
        import AUCTIONHOUSE.ah_database as ah_db
        self._original_conn_fn = ah_db.get_db
        ah_db.get_db = lambda: conn
        self._attached = True

    def detach(self):
        """Stop tracing and restore original database."""
        if not self._attached:
            return
        import AUCTIONHOUSE.ah_database as ah_db
        ah_db.get_db = self._original_conn_fn
        self._attached = False

    # ── Internal ─────────────────────────────────────────────────

    def _record_sql(self, query: str, params: tuple, result,
                    duration_ms: float, error: str = ""):
        """Record a SQL trace entry."""
        qtype = _get_query_type(query)
        tables = _extract_table_names(query)
        row_count = 0
        if result is not None and hasattr(result, 'rowcount'):
            try:
                row_count = result.rowcount
            except sqlite3.ProgrammingError:
                row_count = -1

        trace = SQLTrace(
            query=query.strip()[:200],
            query_type=qtype,
            params=params,
            row_count=row_count,
            duration_ms=duration_ms,
            timestamp=time.time(),
            thread_id=threading.get_ident(),
            table_names=tables,
        )
        with self._lock:
            self._traces.append(trace)

        # Also record in DataFlowTrace if we have one
        if self._trace_ref:
            self._trace_ref.record(
                f"db.{qtype.lower()}",
                "executed",
                table=tables[0] if tables else "?",
                rows=row_count,
                duration_ms=round(duration_ms, 2),
            )

    # ── Query access ─────────────────────────────────────────────

    def get_all_traces(self) -> list[SQLTrace]:
        """Get all traced SQL operations."""
        with self._lock:
            return list(self._traces)

    def get_traces_by_type(self, qtype: str) -> list[SQLTrace]:
        """Get traces filtered by query type."""
        return [t for t in self.get_all_traces() if t.query_type == qtype]

    def get_traces_for_table(self, table: str) -> list[SQLTrace]:
        """Get traces that touched a specific table."""
        table_lower = table.lower()
        return [t for t in self.get_all_traces()
                if table_lower in t.table_names]

    def clear(self):
        """Clear all recorded traces."""
        with self._lock:
            self._traces.clear()

    def get_query_counts(self) -> dict:
        """Return count of queries by type."""
        return dict(Counter(t.query_type for t in self.get_all_traces()))

    # ── Verification ─────────────────────────────────────────────

    def assert_table_state(self, table: str, expected_rows: int,
                           description: str = ""):
        """Assert a table has the expected number of rows.

        Args:
            table: Table name to check
            expected_rows: Expected row count
            description: Optional assertion description

        Raises:
            AssertionError: If row count doesn't match
        """
        # Detach temporarily to query without tracing
        self.detach()
        try:
            db = get_db()
            result = db.fetch_one(f"SELECT COUNT(*) as cnt FROM {table}")
            actual = result["cnt"] if result else 0
            if actual != expected_rows:
                msg = (f"Table '{table}': expected {expected_rows} rows, "
                       f"got {actual}")
                if description:
                    msg += f" ({description})"
                raise AssertionError(msg)
        finally:
            self.attach()

    def assert_row_exists(self, table: str, where_clause: str,
                          params: tuple = ()):
        """Assert a row exists matching the given WHERE clause."""
        self.detach()
        try:
            db = get_db()
            result = db.fetch_one(
                f"SELECT 1 FROM {table} WHERE {where_clause} LIMIT 1",
                params
            )
            if not result:
                raise AssertionError(
                    f"No row found in '{table}' WHERE {where_clause}"
                )
        finally:
            self.attach()

    def assert_atomic_update_succeeded(self, listing_uuid: str):
        """Verify that an atomic UPDATE on a listing affected exactly 1 row.

        This checks that the TOCTOU-prevention pattern worked correctly.
        """
        updates = self.get_traces_for_table("auction_listings")
        atomic_updates = [
            t for t in updates
            if t.query_type == "UPDATE"
            and listing_uuid in str(t.params)
        ]
        if not atomic_updates:
            raise AssertionError(
                f"No atomic UPDATE found for listing {listing_uuid}"
            )
        ok = any(u.row_count == 1 for u in atomic_updates)
        if not ok:
            raise AssertionError(
                f"Atomic UPDATE for {listing_uuid} affected "
                f"{atomic_updates[-1].row_count} rows (expected 1)"
            )

    def verify_fk_integrity(self, group: str = None) -> list[str]:
        """Verify foreign key integrity across the database.

        Args:
            group: Optional table group to check ('core', 'people', etc.)

        Returns:
            List of FK violation descriptions (empty if clean)
        """
        violations = []
        tables_to_check = (TABLE_GROUPS.get(group, set())
                           if group else ALL_TABLES)

        self.detach()
        try:
            db = get_db()
            # Enable FK checking
            db.execute("PRAGMA foreign_keys = ON")

            # Check for orphaned transaction_history records
            orphaned_tx = db.fetch_all("""
                SELECT th.id FROM transaction_history th
                LEFT JOIN auction_listings al ON th.listing_uuid = al.listing_uuid
                WHERE al.listing_uuid IS NULL
                LIMIT 10
            """)
            for row in orphaned_tx:
                violations.append(
                    f"Orphaned transaction_history row {row['id']} "
                    f"(no matching auction_listing)"
                )

            # Check for orphaned ext_sp_* records (persona_uuid referenced)
            if "ext_sp_profiles" in tables_to_check:
                for ref_table in ["ext_sp_finances", "ext_sp_needs",
                                  "ext_sp_memory", "ext_sp_health"]:
                    if ref_table not in tables_to_check:
                        continue
                    try:
                        orphans = db.fetch_all(f"""
                            SELECT t.id FROM {ref_table} t
                            LEFT JOIN ext_sp_profiles p
                                ON t.persona_uuid = p.persona_uuid
                            WHERE p.persona_uuid IS NULL
                            LIMIT 5
                        """)
                        for row in orphans:
                            violations.append(
                                f"Orphaned {ref_table} row {row['id']}"
                            )
                    except Exception:
                        pass  # Table might not exist

        finally:
            self.attach()

        return violations

    def verify_no_duplicate_uuids(self) -> list[str]:
        """Verify no duplicate UUIDs in listing or transaction tables."""
        violations = []
        self.detach()
        try:
            db = get_db()
            dup_listings = db.fetch_all("""
                SELECT listing_uuid, COUNT(*) as cnt
                FROM auction_listings
                GROUP BY listing_uuid
                HAVING cnt > 1
            """)
            for row in dup_listings:
                violations.append(
                    f"Duplicate listing_uuid: {row['listing_uuid']} "
                    f"({row['cnt']}x)"
                )

            dup_tx = db.fetch_all("""
                SELECT transaction_uuid, COUNT(*) as cnt
                FROM transaction_history
                GROUP BY transaction_uuid
                HAVING cnt > 1
            """)
            for row in dup_tx:
                violations.append(
                    f"Duplicate transaction_uuid: {row['transaction_uuid']} "
                    f"({row['cnt']}x)"
                )
        finally:
            self.attach()

        return violations

    def summary(self) -> str:
        """Return a human-readable summary of all DB operations."""
        traces = self.get_all_traces()
        if not traces:
            return "No database operations recorded."

        counts = self.get_query_counts()
        total_time = sum(t.duration_ms for t in traces)

        lines = [
            f"=== Database Probe: {len(traces)} operations ===",
            f"  Total time: {total_time:.1f}ms",
            f"  By type: {counts}",
        ]

        # Group by table
        table_ops: dict[str, list[str]] = {}
        for t in traces:
            for table in t.table_names:
                if table not in table_ops:
                    table_ops[table] = []
                table_ops[table].append(t.query_type)

        lines.append(f"\n--- Tables touched ({len(table_ops)}) ---")
        for table, ops in sorted(table_ops.items()):
            counts_str = dict(Counter(ops))
            lines.append(f"  {table}: {counts_str}")

        return "\n".join(lines)

