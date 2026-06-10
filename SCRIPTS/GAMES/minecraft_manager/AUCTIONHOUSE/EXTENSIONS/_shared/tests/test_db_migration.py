"""
test_db_migration.py — Tests for the database migration system.

Validates:
  - Version tracking table creation
  - Migration execution (SQL statements)
  - Pending migration detection and application
  - Idempotency (re-running doesn't re-apply)
  - Failure isolation (one failed migration doesn't break others)
  - Version query and reset
  - Edge cases: empty migrations, version 1 skip, concurrent extensions
"""

import sys, os, json, sqlite3, tempfile
from unittest import TestCase, main

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))      # .../_shared/tests
_SHARED_DIR = os.path.dirname(_HERE)                    # .../_shared
_EXT_DIR = os.path.dirname(_SHARED_DIR)                 # .../EXTENSIONS
_AH_DIR = os.path.dirname(_EXT_DIR)                     # .../AUCTIONHOUSE
for p in [_SHARED_DIR, _EXT_DIR, _AH_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ═══════════════════════════════════════════════════════════════════
# Test helpers
# ═══════════════════════════════════════════════════════════════════

class _TestDB:
    """Minimal database wrapper for migration testing using SQLite."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params: tuple = ()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def fetch_one(self, sql: str, params: tuple = ()):
        cur = self.conn.execute(sql, params)
        return cur.fetchone()

    def fetch_all(self, sql: str, params: tuple = ()):
        cur = self.conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def close(self):
        self.conn.close()


def _has_table(db: _TestDB, table_name: str) -> bool:
    row = db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return row is not None


def _get_columns(db: _TestDB, table_name: str) -> list:
    return [dict(r) for r in db.conn.execute(f"PRAGMA table_info({table_name})").fetchall()]


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

class TestMigrationSystem(TestCase):
    """Core migration functionality."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = _TestDB(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_version_table_created_on_first_call(self):
        """Calling migrate() creates the _schema_version table."""
        from EXTENSIONS._shared.db_migration import migrate

        result = migrate("test_ext", self.db, 1, {})

        self.assertTrue(_has_table(self.db, "_schema_version"))
        self.assertEqual(result["current_version"], 1)

    def test_no_pending_migrations_returns_empty(self):
        """When current version >= target, no migrations are applied."""
        from EXTENSIONS._shared.db_migration import migrate

        result = migrate("test_ext", self.db, 1, {})
        self.assertEqual(result["applied"], [])
        self.assertEqual(result["failed"], [])

        # Second call at same version
        result2 = migrate("test_ext", self.db, 1, {})
        self.assertEqual(result2["applied"], [])

    def test_simple_table_creation_migration(self):
        """Version 2 adds a new table."""
        from EXTENSIONS._shared.db_migration import migrate

        result = migrate("test_ext", self.db, 1, {})  # v1 = initial

        # Version 2: add a column
        self.db.execute("CREATE TABLE IF NOT EXISTS test_items (id INTEGER PRIMARY KEY, name TEXT)")

        result2 = migrate("test_ext", self.db, 2, {
            2: [
                ("ALTER TABLE test_items ADD COLUMN description TEXT", None),
            ],
        })

        self.assertEqual(result2["applied"], [2])
        cols = _get_columns(self.db, "test_items")
        col_names = [c["name"] for c in cols]
        self.assertIn("description", col_names)

    def test_multi_column_sequential_migrations(self):
        """Multiple versions apply in order."""
        from EXTENSIONS._shared.db_migration import migrate

        self.db.execute("CREATE TABLE test_players (id INTEGER PRIMARY KEY, name TEXT)")

        # v2, v3, v4
        result = migrate("multi_ext", self.db, 4, {
            2: [("ALTER TABLE test_players ADD COLUMN level INTEGER DEFAULT 1", None)],
            3: [("ALTER TABLE test_players ADD COLUMN guild TEXT DEFAULT ''", None)],
            4: [("ALTER TABLE test_players ADD COLUMN title TEXT DEFAULT ''", None)],
        })

        self.assertEqual(result["applied"], [2, 3, 4])
        cols = _get_columns(self.db, "test_players")
        col_names = [c["name"] for c in cols]
        self.assertIn("level", col_names)
        self.assertIn("guild", col_names)
        self.assertIn("title", col_names)

    def test_idempotent_repeated_migration(self):
        """Running the same migration twice doesn't double-apply."""
        from EXTENSIONS._shared.db_migration import migrate

        self.db.execute("CREATE TABLE test_idem (id INTEGER PRIMARY KEY)")

        # First application
        r1 = migrate("idem_ext", self.db, 2, {
            2: [("ALTER TABLE test_idem ADD COLUMN data TEXT", None)],
        })
        self.assertEqual(r1["applied"], [2])

        # Second call at same version
        r2 = migrate("idem_ext", self.db, 2, {
            2: [("ALTER TABLE test_idem ADD COLUMN data TEXT", None)],
        })
        self.assertEqual(r2["applied"], [])

    def test_version_tracking_persists(self):
        """Version persists across DB connections (different session)."""
        from EXTENSIONS._shared.db_migration import migrate, get_schema_version

        # First session
        migrate("persist_ext", self.db, 3, {
            2: [("CREATE TABLE IF NOT EXISTS persist_t (id INTEGER PRIMARY KEY)", None)],
            3: [("ALTER TABLE persist_t ADD COLUMN val TEXT", None)],
        })

        # Close and reopen
        self.db.close()
        self.db = _TestDB(self.tmp.name)

        version = get_schema_version(self.db, "persist_ext")
        self.assertEqual(version, 3)

    def test_failed_migration_stops_and_reports(self):
        """A failing migration stops the chain and reports the error."""
        from EXTENSIONS._shared.db_migration import migrate

        self.db.execute("CREATE TABLE test_fail (id INTEGER PRIMARY KEY)")

        result = migrate("fail_ext", self.db, 4, {
            2: [("ALTER TABLE test_fail ADD COLUMN a TEXT", None)],
            3: [("THIS IS INVALID SQL", None)],  # Will fail
            4: [("ALTER TABLE test_fail ADD COLUMN b TEXT", None)],  # Never reached
        })

        self.assertEqual(result["applied"], [2])
        self.assertEqual(result["failed"], [3])
        self.assertEqual(len(result["errors"]), 1)

        # Version 4 was never applied because version 3 failed
        from EXTENSIONS._shared.db_migration import get_schema_version
        version = get_schema_version(self.db, "fail_ext")
        self.assertEqual(version, 2)

        # Column 'b' should not exist
        cols = _get_columns(self.db, "test_fail")
        col_names = [c["name"] for c in cols]
        self.assertNotIn("b", col_names)


class TestMigrationMultipleExtensions(TestCase):
    """Multiple extensions with independent version tracking."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = _TestDB(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_independent_version_tracking(self):
        """Two extensions can have different versions."""
        from EXTENSIONS._shared.db_migration import migrate

        self.db.execute("CREATE TABLE ext_a (id INTEGER PRIMARY KEY)")
        self.db.execute("CREATE TABLE ext_b (id INTEGER PRIMARY KEY)")

        r_a = migrate("EXT_A", self.db, 3, {
            2: [("ALTER TABLE ext_a ADD COLUMN data_a TEXT", None)],
            3: [("ALTER TABLE ext_a ADD COLUMN extra_a TEXT", None)],
        })
        r_b = migrate("EXT_B", self.db, 2, {
            2: [("ALTER TABLE ext_b ADD COLUMN data_b TEXT", None)],
        })

        self.assertEqual(r_a["applied"], [2, 3])
        self.assertEqual(r_b["applied"], [2])

        from EXTENSIONS._shared.db_migration import get_schema_version
        self.assertEqual(get_schema_version(self.db, "EXT_A"), 3)
        self.assertEqual(get_schema_version(self.db, "EXT_B"), 2)

    def test_missing_migration_definition_warns(self):
        """A version without a migration definition is skipped with a warning."""
        from EXTENSIONS._shared.db_migration import migrate

        self.db.execute("CREATE TABLE test_skip (id INTEGER PRIMARY KEY)")

        result = migrate("skip_ext", self.db, 4, {
            # version 2 intentionally missing
            3: [("ALTER TABLE test_skip ADD COLUMN data TEXT", None)],
            4: [("ALTER TABLE test_skip ADD COLUMN extra TEXT", None)],
        })

        # Migration 2 was skipped (no definition), 3 and 4 applied
        self.assertEqual(result["applied"], [3, 4])


class TestResetMigration(TestCase):
    """reset_migration() functionality."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = _TestDB(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_reset_clears_version(self):
        """reset_migration() resets version tracking (schema is NOT undone)."""
        from EXTENSIONS._shared.db_migration import migrate, reset_migration, get_schema_version

        self.db.execute("CREATE TABLE test_reset (id INTEGER PRIMARY KEY)")

        migrate("reset_ext", self.db, 2, {
            2: [("ALTER TABLE test_reset ADD COLUMN data TEXT", None)],
        })
        self.assertEqual(get_schema_version(self.db, "reset_ext"), 2)

        # Reset version tracking
        reset_migration(self.db, "reset_ext")
        self.assertEqual(get_schema_version(self.db, "reset_ext"), 1)

        # After reset, version is back to 1. Re-applying the same migration
        # will fail because the column already exists (reset only clears the
        # version counter, not the schema). This is by design.
        r = migrate("reset_ext", self.db, 2, {
            2: [("ALTER TABLE test_reset ADD COLUMN data TEXT", None)],
        })
        # Column already exists, so ALTER TABLE fails
        self.assertEqual(r["failed"], [2], "ALTER TABLE fails because column exists after reset")


if __name__ == "__main__":
    main()
