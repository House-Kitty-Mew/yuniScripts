"""
Unit tests for VFS-Backed Database Isolation.
Uses unittest (NOT pytest).
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Any, List

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.vfs_db_isolation import (
    VFSDatabaseManager,
    VFSDatabase,
    VFSDatabaseInfo,
    VFSDBError,
    VFSDBConnectionError,
    VFSDBQueryError,
    VFSDBConfigError,
)


class TestVFSDatabaseBasic(unittest.TestCase):
    """Test basic VFSDatabase operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = VFSDatabase(
            subsystem="test_sub",
            server_id="test_server",
            db_path=self.db_path,
            auto_backup=False,
        )

    def tearDown(self):
        try:
            self.db.close()
            if os.path.exists(self.db_path):
                os.unlink(self.db_path)
            # Clean up WAL/SHM
            for ext in ["-wal", "-shm"]:
                p = self.db_path + ext
                if os.path.exists(p):
                    os.unlink(p)
            os.rmdir(self.temp_dir)
        except (OSError, PermissionError):
            pass

    def test_open_creates_file(self):
        """Opening the database should create the file."""
        self.assertFalse(os.path.exists(self.db_path))
        self.db.open()
        self.assertTrue(os.path.exists(self.db_path))

    def test_open_twice_is_idempotent(self):
        """Opening an already-open database should be safe."""
        self.db.open()
        self.db.open()  # Should not raise
        self.db.close()
        self.db.close()  # Should not raise

    def test_execute_create_table(self):
        """Executing CREATE TABLE should succeed."""
        self.db.open()
        result = self.db.execute(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT)"
        )
        # rowcount is -1 for DDL statements in sqlite3
        self.assertEqual(result, -1)  # CREATE TABLE returns -1

    def test_execute_insert(self):
        """Inserting data should work."""
        self.db.open()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT)"
        )
        result = self.db.execute(
            "INSERT INTO test (name) VALUES (?)", ("hello",)
        )
        self.assertEqual(result, 1)

    def test_execute_on_closed_db(self):
        """Executing without opening should raise."""
        with self.assertRaises(VFSDBConnectionError):
            self.db.execute("SELECT 1")

    def test_query_select(self):
        """SELECT queries should return results."""
        self.db.open()
        self.db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        self.db.execute("INSERT INTO test (name) VALUES (?)", ("alice",))
        self.db.execute("INSERT INTO test (name) VALUES (?)", ("bob",))
        
        rows = self.db.query("SELECT * FROM test ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "alice")
        self.assertEqual(rows[1]["name"], "bob")

    def test_query_one(self):
        """query_one should return first row or None."""
        self.db.open()
        self.db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        self.db.execute("INSERT INTO test (name) VALUES (?)", ("only",))
        
        row = self.db.query_one("SELECT * FROM test")
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "only")
        
        # Empty table
        self.db.execute("DELETE FROM test")
        row = self.db.query_one("SELECT * FROM test")
        self.assertIsNone(row)

    def test_query_on_closed_db(self):
        """Querying without opening should raise."""
        with self.assertRaises(VFSDBConnectionError):
            self.db.query("SELECT 1")

    def test_table_exists(self):
        """table_exists should work correctly."""
        self.db.open()
        self.assertFalse(self.db.table_exists("test"))
        self.db.execute("CREATE TABLE test (id INT)")
        self.assertTrue(self.db.table_exists("test"))

    def test_get_table_names(self):
        """get_table_names should return all user tables."""
        self.db.open()
        self.db.execute("CREATE TABLE t1 (id INT)")
        self.db.execute("CREATE TABLE t2 (name TEXT)")
        tables = self.db.get_table_names()
        self.assertIn("t1", tables)
        self.assertIn("t2", tables)

    def test_get_info(self):
        """get_info should return correct metadata."""
        self.db.open()
        self.db.execute("CREATE TABLE test (id INT)")
        info = self.db.get_info()
        self.assertIsInstance(info, VFSDatabaseInfo)
        self.assertEqual(info.subsystem, "test_sub")
        self.assertEqual(info.server_id, "test_server")
        self.assertGreater(info.size_bytes, 0)
        self.assertEqual(info.table_count, 1)

    def test_vacuum(self):
        """VACUUM should not raise."""
        self.db.open()
        self.db.execute("CREATE TABLE test (id INT)")
        self.db.execute("INSERT INTO test VALUES (1)")
        try:
            self.db.vacuum()
        except Exception as e:
            self.fail(f"VACUUM raised: {e}")

    def test_executemany(self):
        """executemany should insert multiple rows."""
        self.db.open()
        self.db.execute("CREATE TABLE test (id INT, val TEXT)")
        params = [(1, "a"), (2, "b"), (3, "c")]
        total = self.db.executemany(
            "INSERT INTO test VALUES (?, ?)", params
        )
        self.assertEqual(total, 3)
        rows = self.db.query("SELECT * FROM test ORDER BY id")
        self.assertEqual(len(rows), 3)


class TestVFSDatabaseBackup(unittest.TestCase):
    """Test database backup functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = VFSDatabase(
            subsystem="test",
            server_id="srv",
            db_path=self.db_path,
            auto_backup=True,
            backup_limit=3,
        )

    def tearDown(self):
        try:
            self.db.close()
            for f in Path(self.temp_dir).rglob("*"):
                try:
                    os.unlink(str(f))
                except OSError:
                    pass
            os.rmdir(self.temp_dir)
        except OSError:
            pass

    def test_create_backup(self):
        """Creating a backup should produce a file."""
        self.db.open()
        self.db.execute("CREATE TABLE test (id INT)")
        backup_path = self.db.create_backup()
        self.assertTrue(os.path.exists(backup_path))

    def test_backup_contains_data(self):
        """Backup file should contain the same data."""
        self.db.open()
        self.db.execute("CREATE TABLE test (id INT, val TEXT)")
        self.db.execute("INSERT INTO test VALUES (1, 'data')")
        
        backup_path = self.db.create_backup()
        
        # Verify backup
        import sqlite3
        conn = sqlite3.connect(backup_path)
        cursor = conn.execute("SELECT val FROM test WHERE id=1")
        row = cursor.fetchone()
        self.assertEqual(row[0], "data")
        conn.close()

    def test_backup_limit_enforced(self):
        """Old backups beyond limit should be cleaned up."""
        self.db.open()
        self.db.execute("CREATE TABLE test (id INT)")
        
        paths = set()
        for i in range(5):
            p = self.db.create_backup()
            paths.add(p)
        
        # Should only have 3 backups (the limit)
        backup_dir = Path(self.db_path).parent / ".backups"
        existing = list(backup_dir.glob("*.db"))
        self.assertLessEqual(len(existing), 3)

    def test_backup_nonexistent_db(self):
        """Backup of nonexistent database should raise."""
        with self.assertRaises(VFSDBError):
            self.db.create_backup()


class TestVFSDatabaseManager(unittest.TestCase):
    """Test VFSDatabaseManager operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.manager = VFSDatabaseManager(
            data_root=self.temp_dir,
            auto_backup=False,
        )

    def tearDown(self):
        try:
            self.manager.close_all()
            for f in Path(self.temp_dir).rglob("*"):
                try:
                    if f.is_file():
                        os.unlink(str(f))
                except OSError:
                    pass
            for d in sorted(Path(self.temp_dir).rglob("*"), key=lambda x: str(x), reverse=True):
                try:
                    if d.is_dir():
                        os.rmdir(str(d))
                except OSError:
                    pass
        except OSError:
            pass

    def test_get_database_creates_new(self):
        """Getting a database for a new subsystem+server should create it."""
        db = self.manager.get_database("sub_x", "server_1")
        self.assertIsInstance(db, VFSDatabase)
        self.assertEqual(db.subsystem, "sub_x")
        self.assertEqual(db.server_id, "server_1")

    def test_get_database_reuses_existing(self):
        """Getting the same subsystem+server should return the same instance."""
        db1 = self.manager.get_database("sub", "srv")
        db2 = self.manager.get_database("sub", "srv")
        self.assertIs(db1, db2)

    def test_get_database_creates_file(self):
        """Getting a database should create the database file on disk."""
        db = self.manager.get_database("sub", "srv")
        db_path = Path(db.db_path)
        self.assertTrue(db_path.exists())

    def test_get_database_invalid_subsystem(self):
        """Empty subsystem name should raise."""
        with self.assertRaises(VFSDBConfigError):
            self.manager.get_database("", "server")

    def test_get_database_invalid_server(self):
        """Empty server_id should raise."""
        with self.assertRaises(VFSDBConfigError):
            self.manager.get_database("sub", "")

    def test_remove_database(self):
        """Removing a database should close it."""
        db = self.manager.get_database("sub", "srv")
        result = self.manager.remove_database("sub", "srv")
        self.assertTrue(result)
        self.assertEqual(self.manager.database_count, 0)

    def test_remove_database_nonexistent(self):
        """Removing a nonexistent database should return False."""
        result = self.manager.remove_database("ghost", "server")
        self.assertFalse(result)

    def test_remove_database_delete_file(self):
        """Removing a database with delete_file should remove the file."""
        db = self.manager.get_database("sub", "srv")
        db_path = db.db_path
        self.manager.remove_database("sub", "srv", delete_file=True)
        self.assertFalse(os.path.exists(db_path))

    def test_list_databases(self):
        """list_databases should return all managed databases."""
        self.manager.get_database("a", "s1")
        self.manager.get_database("b", "s1")
        self.manager.get_database("a", "s2")
        
        dbs = self.manager.list_databases()
        self.assertEqual(len(dbs), 3)

    def test_get_databases_for_subsystem(self):
        """Getting databases by subsystem should filter correctly."""
        self.manager.get_database("auction", "s1")
        self.manager.get_database("auction", "s2")
        self.manager.get_database("economy", "s1")
        
        dbs = self.manager.get_databases_for_subsystem("auction")
        self.assertEqual(len(dbs), 2)

    def test_get_databases_for_server(self):
        """Getting databases by server should filter correctly."""
        self.manager.get_database("a", "server1")
        self.manager.get_database("b", "server1")
        self.manager.get_database("a", "server2")
        
        dbs = self.manager.get_databases_for_server("server1")
        self.assertEqual(len(dbs), 2)

    def test_close_all(self):
        """close_all should close all database connections."""
        db1 = self.manager.get_database("a", "s1")
        db2 = self.manager.get_database("b", "s2")
        self.manager.close_all()
        # After close, queries should fail
        with self.assertRaises(VFSDBConnectionError):
            db1.query("SELECT 1")

    def test_create_database_structure(self):
        """Creating a database with schema should initialize tables."""
        schema = """
            CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE items (id INTEGER PRIMARY KEY, owner_id INT, name TEXT);
        """
        db = self.manager.create_database_structure("sub", "srv", schema)
        tables = db.get_table_names()
        self.assertIn("users", tables)
        self.assertIn("items", tables)

    def test_health_check_all(self):
        """health_check_all should return status for all databases."""
        self.manager.get_database("a", "s1")
        self.manager.get_database("b", "s2")
        
        results = self.manager.health_check_all()
        self.assertEqual(len(results), 2)
        for key, healthy in results.items():
            self.assertTrue(healthy, f"Database {key} should be healthy")

    def test_backup_all(self):
        """backup_all should create backups for all databases."""
        db1 = self.manager.get_database("a", "s1")
        db1.execute("CREATE TABLE t (id INT)")
        db1.execute("INSERT INTO t VALUES (1)")
        
        db2 = self.manager.get_database("b", "s2")
        db2.execute("CREATE TABLE t (id INT)")
        db2.execute("INSERT INTO t VALUES (2)")
        
        results = self.manager.backup_all()
        self.assertEqual(len(results), 2)
        for key, path in results.items():
            self.assertFalse(path.startswith("ERROR"), f"Backup failed: {path}")
            self.assertTrue(os.path.exists(path), f"Backup file missing: {path}")

    def test_sanitize_name(self):
        """_sanitize_name should clean unsafe characters."""
        dirty = "hello/world:test@#$%.db"
        clean = VFSDatabaseManager._sanitize_name(dirty)
        self.assertNotIn("/", clean)
        self.assertNotIn(":", clean)
        self.assertNotIn("@", clean)
        self.assertIn("hello_world_test____.db", clean)

    def test_sanitize_name_empty(self):
        """_sanitize_name should handle empty strings."""
        result = VFSDatabaseManager._sanitize_name("")
        self.assertEqual(result, "unnamed")

    def test_sanitize_name_long(self):
        """_sanitize_name should truncate long strings."""
        long_name = "a" * 200
        result = VFSDatabaseManager._sanitize_name(long_name)
        self.assertLessEqual(len(result), 100)


class TestVFSDatabaseEdgeCases(unittest.TestCase):
    """Test edge cases for VFS database operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "edge.db")
        self.db = VFSDatabase(
            subsystem="edge",
            server_id="test",
            db_path=self.db_path,
            auto_backup=False,
        )

    def tearDown(self):
        try:
            self.db.close()
            for f in Path(self.temp_dir).rglob("*"):
                try:
                    os.unlink(str(f))
                except OSError:
                    pass
            os.rmdir(self.temp_dir)
        except OSError:
            pass

    def test_special_characters_in_data(self):
        """Unicode and special characters should work in data."""
        self.db.open()
        self.db.execute("CREATE TABLE t (id INT, val TEXT)")
        specials = [
            "héllo wörld",
            "emoji 🔥 test",
            "with 'quotes' and \"double\"",
            "line1\nline2",
            "<script>alert('xss')</script>",
        ]
        for i, val in enumerate(specials):
            self.db.execute("INSERT INTO t VALUES (?, ?)", (i, val))
        
        rows = self.db.query("SELECT * FROM t ORDER BY id")
        for i, row in enumerate(rows):
            self.assertEqual(row["val"], specials[i])

    def test_large_number_of_rows(self):
        """Inserting many rows should work."""
        self.db.open()
        self.db.execute("CREATE TABLE t (id INT, val TEXT)")
        
        import string
        for i in range(100):
            self.db.execute(
                "INSERT INTO t VALUES (?, ?)",
                (i, f"row_{i:04d}")
            )
        
        rows = self.db.query("SELECT COUNT(*) as cnt FROM t")
        self.assertEqual(rows[0]["cnt"], 100)

    def test_concurrent_reads(self):
        """Multiple reads should not deadlock."""
        self.db.open()
        self.db.execute("CREATE TABLE t (id INT)")
        for i in range(10):
            self.db.execute("INSERT INTO t VALUES (?)", (i,))
        
        # Sequential reads (thread-safe within single connection)
        for _ in range(20):
            rows = self.db.query("SELECT * FROM t ORDER BY id")
            self.assertEqual(len(rows), 10)

    def test_schema_version_tracking(self):
        """Schema version should be trackable."""
        self.db.open()
        self.assertEqual(self.db.get_schema_version(), 0)
        self.db.set_schema_version(3)
        self.assertEqual(self.db.get_schema_version(), 3)

    def test_large_blob_storage(self):
        """Storing and retrieving BLOBs should work."""
        self.db.open()
        self.db.execute("CREATE TABLE t (id INT, data BLOB)")
        large_data = b"x" * 10000
        self.db.execute("INSERT INTO t VALUES (?, ?)", (1, large_data))
        
        rows = self.db.query("SELECT id, length(data) as sz FROM t WHERE id=1")
        self.assertEqual(rows[0]["sz"], 10000)

    def test_multiple_tables(self):
        """Multiple tables should work independently."""
        self.db.open()
        self.db.execute("CREATE TABLE a (id INT)")
        self.db.execute("CREATE TABLE b (id INT)")
        self.db.execute("CREATE TABLE c (id INT)")
        
        tables = self.db.get_table_names()
        self.assertEqual(len(tables), 3)

    def test_transaction_rollback_on_error(self):
        """Failed statements should not corrupt the database."""
        self.db.open()
        self.db.execute("CREATE TABLE t (id INT PRIMARY KEY)")
        self.db.execute("INSERT INTO t VALUES (1)")
        
        # This should fail (duplicate key)
        with self.assertRaises(VFSDBQueryError):
            self.db.execute("INSERT INTO t VALUES (1)")
        
        # Database should still be usable
        rows = self.db.query("SELECT * FROM t")
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
