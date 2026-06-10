"""
Unit tests for LootPower Database Engine.
"""
import os
import sys
import unittest
import tempfile
import sqlite3

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lp_config import DB_PATH
from lp_database import DatabaseEngine, get_db, SCHEMA


class TestDatabaseEngine(unittest.TestCase):
    """Tests for the consolidated database engine."""

    def setUp(self):
        """Create a temporary database for testing."""
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        # Force reset the singleton
        DatabaseEngine._instance = None
        self.db = DatabaseEngine(db_path=self.db_path)

    def tearDown(self):
        """Clean up temporary database."""
        self.db.close()
        DatabaseEngine._instance = None
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_01_schema_creates_all_tables(self):
        """Verify all expected tables exist after initialization."""
        tables = [
            "users", "loot_table", "users_loot", "lootpower_table",
            "craft_rep", "user_craft", "areas", "user_ore",
            "story_table", "stat_table", "loot_stats", "auction",
            "runtime_codes", "watchers"
        ]
        existing = self.db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        existing_names = {r["name"] for r in existing}
        for tbl in tables:
            self.assertIn(
                tbl, existing_names,
                f"Table '{tbl}' missing from schema"
            )

    def test_02_insert_and_retrieve(self):
        """Test basic insert, retrieve, update cycle."""
        # Insert a user
        self.db.execute(
            "INSERT INTO users (user_id, user_name, password, turns, global_loot_chance_boost, turn_time) "
            "VALUES (?,?,?,?,?,?)",
            ("test1", "TestPlayer", "hash123", 20, 0.0, 100.0)
        )
        self.db.commit()

        # Retrieve
        row = self.db.fetchone(
            "SELECT * FROM users WHERE user_id=?", ("test1",)
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["user_name"], "TestPlayer")
        self.assertEqual(row["turns"], 20)

        # Update
        self.db.execute(
            "UPDATE users SET turns = ? WHERE user_id=?",
            (15, "test1")
        )
        self.db.commit()
        row = self.db.fetchone(
            "SELECT turns FROM users WHERE user_id=?", ("test1",)
        )
        self.assertEqual(row["turns"], 15)

    def test_03_fetchall_multiple_rows(self):
        """Test fetching multiple rows."""
        for i in range(5):
            self.db.execute(
                "INSERT INTO users (user_id, user_name, password, turns) VALUES (?,?,?,?)",
                (f"u{i}", f"User{i}", "pw", 20)
            )
        self.db.commit()
        rows = self.db.fetchall("SELECT * FROM users ORDER BY user_id")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["user_id"], "u0")
        self.assertEqual(rows[4]["user_id"], "u4")

    def test_04_stat_default_row(self):
        """Test that stat_table has default row."""
        row = self.db.fetchone("SELECT * FROM stat_table WHERE rowid=1")
        self.assertIsNotNone(row)
        self.assertEqual(row["adventures"], 0)
        self.assertEqual(row["sold_items"], 0)

    def test_05_runtime_default_code(self):
        """Test default runtime code."""
        row = self.db.fetchone(
            "SELECT code FROM runtime_codes WHERE name='Server'"
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["code"], 0)

    def test_06_transaction_rollback(self):
        """Test that failed transaction rolls back."""
        # Disable dry-run for this test
        import lp_config
        lp_config.DRY_RUN = False

        try:
            with self.db.transaction() as conn:
                self.db.execute(
                    "INSERT INTO users (user_id, user_name) VALUES (?,?)",
                    ("rollback_test", "Rollback")
                )
                # This should fail - violating a constraint
                self.db.execute(
                    "INSERT INTO users (user_id, user_name) VALUES (?,?)",
                    ("rollback_test", "Duplicate")  # Same PK
                )
        except Exception:
            pass

        # Verify no insert happened
        row = self.db.fetchone(
            "SELECT * FROM users WHERE user_id=?", ("rollback_test",)
        )
        self.assertIsNone(row)

    def test_07_singleton_pattern(self):
        """Test that DatabaseEngine is a singleton."""
        db2 = DatabaseEngine()
        self.assertIs(self.db, db2)

    def test_08_dry_run_blocks_writes(self):
        """Test that dry-run mode skips writes."""
        import lp_config
        lp_config.DRY_RUN = True
        before = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM users"
        )["c"]
        # Try write
        self.db.execute(
            "INSERT INTO users (user_id, user_name) VALUES (?,?)",
            ("dry_test", "Dry")
        )
        after = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM users"
        )["c"]
        self.assertEqual(before, after)
        lp_config.DRY_RUN = False


if __name__ == "__main__":
    unittest.main()