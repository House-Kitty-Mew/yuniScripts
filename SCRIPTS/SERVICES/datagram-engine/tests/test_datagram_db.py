#!/usr/bin/env python3
"""
Unittests for Datagram Engine — Database Operations.

Tests: SQLite CRUD, JSON CRUD, edge cases, data integrity.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from engine.datagram_db import (
    SQLiteDatabase, JSONDatabase, create_database, DatabaseError, Database
)
from engine.datagram_types import DatabaseType, DatabaseRecord


class TestSQLiteDatabase(unittest.TestCase):
    """Verify SQLite database CRUD operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dg_sqlite_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = SQLiteDatabase(self.db_path, name="test")

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_and_select(self):
        row_id = self.db.insert("items", {"name": "Test Item", "value": 42})
        self.assertGreater(row_id, 0)
        results = self.db.select("items")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Test Item")
        self.assertEqual(results[0]["value"], 42)

    def test_select_with_where(self):
        self.db.insert("items", {"name": "A", "value": 1})
        self.db.insert("items", {"name": "B", "value": 2})
        self.db.insert("items", {"name": "C", "value": 1})
        results = self.db.select("items", where={"value": 1})
        self.assertEqual(len(results), 2)

    def test_select_with_order(self):
        self.db.insert("items", {"name": "C", "value": 3})
        self.db.insert("items", {"name": "A", "value": 1})
        self.db.insert("items", {"name": "B", "value": 2})
        results = self.db.select("items", order_by="name")
        self.assertEqual(results[0]["name"], "A")
        self.assertEqual(results[2]["name"], "C")

    def test_select_with_limit(self):
        for i in range(10):
            self.db.insert("items", {"name": f"Item {i}", "value": i})
        results = self.db.select("items", limit=3)
        self.assertEqual(len(results), 3)

    def test_update(self):
        self.db.insert("items", {"name": "Original", "value": 0})
        affected = self.db.update("items", {"name": "Updated"}, where={"name": "Original"})
        self.assertEqual(affected, 1)
        results = self.db.select("items", where={"name": "Updated"})
        self.assertEqual(len(results), 1)

    def test_delete(self):
        self.db.insert("items", {"name": "Delete Me"})
        self.db.insert("items", {"name": "Keep Me"})
        deleted = self.db.delete("items", where={"name": "Delete Me"})
        self.assertEqual(deleted, 1)
        results = self.db.select("items")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Keep Me")

    def test_delete_all(self):
        self.db.insert("items", {"name": "A"})
        self.db.insert("items", {"name": "B"})
        deleted = self.db.delete("items", where={"name": "A"})
        self.assertEqual(deleted, 1)

    def test_insert_complex_types(self):
        row_id = self.db.insert("items", {
            "name": "Complex",
            "tags": json.dumps(["tag1", "tag2"]),
            "meta": json.dumps({"key": "value"})
        })
        results = self.db.select("items", where={"name": "Complex"})
        self.assertEqual(len(results), 1)

    def test_select_empty_table(self):
        results = self.db.select("empty_table")
        self.assertEqual(len(results), 0)

    def test_update_nonexistent(self):
        affected = self.db.update("items", {"name": "X"}, where={"name": "Z"})
        self.assertEqual(affected, 0)

    def test_delete_nonexistent(self):
        deleted = self.db.delete("items", where={"name": "Z"})
        self.assertEqual(deleted, 0)

    def test_close_reopen(self):
        self.db.insert("items", {"name": "Persist", "value": 99})
        self.db.close()
        db2 = SQLiteDatabase(self.db_path, name="test")
        results = db2.select("items")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["value"], 99)
        db2.close()

    def test_execute_raw_select(self):
        self.db.insert("items", {"name": "Raw SQL", "value": 1})
        results = self.db.execute_raw("SELECT * FROM items WHERE value = ?", [1])
        self.assertEqual(len(results), 1)

    def test_table_is_created_automatically(self):
        self.db.insert("auto_table", {"test": "value"})
        results = self.db.select("auto_table")
        self.assertEqual(len(results), 1)

    def test_auto_create_with_all_types(self):
        self.db.insert("all_types", {
            "str_col": "text",
            "int_col": 42,
            "float_col": 3.14,
            "bool_col": True,
        })
        results = self.db.select("all_types")
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["str_col"], "text")
        self.assertEqual(r["int_col"], 42)
        self.assertAlmostEqual(r["float_col"], 3.14)


class TestJSONDatabase(unittest.TestCase):
    """Verify JSON database CRUD operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dg_json_")
        self.db_path = os.path.join(self.temp_dir, "test.json")
        self.db = JSONDatabase(self.db_path, name="test")

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_and_select(self):
        row_id = self.db.insert("items", {"name": "Test", "value": 42})
        self.assertGreater(row_id, 0)
        results = self.db.select("items")
        self.assertEqual(len(results), 1)

    def test_select_with_where(self):
        self.db.insert("items", {"type": "A"})
        self.db.insert("items", {"type": "B"})
        self.db.insert("items", {"type": "A"})
        results = self.db.select("items", where={"type": "A"})
        self.assertEqual(len(results), 2)

    def test_update(self):
        self.db.insert("items", {"status": "active"})
        affected = self.db.update("items", {"status": "inactive"}, where={"status": "active"})
        self.assertEqual(affected, 1)
        results = self.db.select("items", where={"status": "inactive"})
        self.assertEqual(len(results), 1)

    def test_delete(self):
        self.db.insert("items", {"name": "X"})
        self.db.insert("items", {"name": "Y"})
        deleted = self.db.delete("items", where={"name": "X"})
        self.assertEqual(deleted, 1)
        results = self.db.select("items")
        self.assertEqual(len(results), 1)

    def test_empty_table(self):
        results = self.db.select("nonexistent")
        self.assertEqual(len(results), 0)

    def test_persistence(self):
        self.db.insert("items", {"name": "Persist Me"})
        self.db.close()
        db2 = JSONDatabase(self.db_path, name="test")
        results = db2.select("items")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Persist Me")
        db2.close()

    def test_json_file_is_valid(self):
        self.db.insert("items", {"name": "JSON Check"})
        import json
        with open(self.db_path, "r") as f:
            data = json.load(f)
        self.assertIn("items", data)
        self.assertEqual(len(data["items"]), 1)

    def test_insert_complex_data(self):
        data = {
            "string": "hello",
            "integer": 42,
            "float": 3.14,
            "boolean": True,
            "none": None,
            "nested": {"a": [1, 2, 3]},
        }
        row_id = self.db.insert("complex", data)
        results = self.db.select("complex")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["string"], "hello")
        self.assertEqual(results[0]["integer"], 42)


class TestDatabaseFactory(unittest.TestCase):
    """Verify database factory creates correct types."""

    def test_create_sqlite(self):
        db = create_database(DatabaseType.SQLITE, "/tmp/test_sqlite.db")
        self.assertIsInstance(db, SQLiteDatabase)
        db.close()

    def test_create_json(self):
        db = create_database(DatabaseType.JSON, "/tmp/test_json.json")
        self.assertIsInstance(db, JSONDatabase)
        db.close()

    def test_create_unsupported(self):
        # Simulate future type that doesn't exist
        class FakeType:
            value = "FAKE"
        with self.assertRaises(DatabaseError):
            create_database(DatabaseType, "/tmp/fake.db")  # type: ignore


class TestDatabaseEdgeCases(unittest.TestCase):
    """Verify edge case handling."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dg_edge_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sqlite_concurrent_tables(self):
        db = SQLiteDatabase(os.path.join(self.temp_dir, "multi.db"))
        db.insert("users", {"name": "Alice"})
        db.insert("posts", {"title": "Post 1"})
        self.assertEqual(len(db.select("users")), 1)
        self.assertEqual(len(db.select("posts")), 1)
        db.close()

    def test_json_with_empty_where(self):
        db = JSONDatabase(os.path.join(self.temp_dir, "empty.json"))
        db.insert("items", {"id": 1})
        db.insert("items", {"id": 2})
        results = db.select("items")  # No where clause
        self.assertEqual(len(results), 2)
        db.close()

    def test_sqlite_update_no_match(self):
        db = SQLiteDatabase(os.path.join(self.temp_dir, "no_match.db"))
        affected = db.update("items", {"x": 1}, where={"y": 999})
        self.assertEqual(affected, 0)
        db.close()


import json  # needed for complex type test
