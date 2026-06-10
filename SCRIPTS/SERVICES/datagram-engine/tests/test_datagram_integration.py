#!/usr/bin/env python3
"""
Unittests for Datagram Engine — Integration Tests.

Tests: End-to-end datagram lifecycle, hash integrity, compatibility,
       embedded functions, type storage consistency, data flow.
"""

import os
import sys
import tempfile
import unittest
import json
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from engine import (
    Datagram, DatagramMeta, DatagramVersion, DatagramHash,
    DatagramFunction, DatagramValue, DatabaseRecord,
    HashAlgorithm, EncryptionMode, DatabaseType, DatagramStatus, DataType,
    load_datagram, create_datagram, update_base_ini, update_meta_ini,
    validate_datagram_structure, parse_ini_content,
    compute_datagram_hash, verify_datagram_hash, update_datagram_hash,
    CompatibilityChecker, CompatibilityResult,
    SQLiteDatabase, JSONDatabase, create_database, DatabaseError,
    FunctionRegistry, FunctionLoadError,
)


class TestEndToEndLifecycle(unittest.TestCase):
    """Full datagram lifecycle: create → hash → validate → load → modify."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dg_e2e_")
        self.dg_path = os.path.join(self.temp_dir, "lifecycle")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_lifecycle(self):
        """Create, hash, validate, load, and verify a datagram."""
        # 1. CREATE
        dg = create_datagram(
            self.dg_path,
            name="Lifecycle Test",
            author="Test Suite",
            version=DatagramVersion(1, 0, 0),
            description="Testing the full lifecycle",
        )
        self.assertEqual(dg.meta.status, DatagramStatus.CREATED)
        self.assertTrue(os.path.exists(self.dg_path))

        # 2. VALIDATE structure
        valid, issues = validate_datagram_structure(self.dg_path)
        self.assertTrue(valid, f"Validation failed: {issues}")

        # 3. COMPUTE hash
        dg = load_datagram(self.dg_path)
        hash_value = compute_datagram_hash(dg)
        self.assertTrue(len(hash_value.hex_value) > 0)

        # 4. UPDATE hash
        updated_hash = update_datagram_hash(dg)
        self.assertTrue(len(updated_hash.hex_value) > 0)

        # 5. RELOAD and verify
        dg2 = load_datagram(self.dg_path)
        self.assertTrue(dg2.is_loaded)
        self.assertEqual(dg2.meta.name, "Lifecycle Test")

        # 6. VERIFY hash
        is_valid, computed = verify_datagram_hash(dg2)
        self.assertTrue(is_valid, "Hash verification failed after update")

        # 7. MODIFY metadata
        dg2.meta.description = "Updated description"
        update_meta_ini(dg2)

        # 8. Recompute hash after modification
        h2 = update_datagram_hash(dg2)
        self.assertTrue(len(h2.hex_value) > 0)

    def test_create_and_store_data(self):
        """Create a datagram, insert data, verify retrieval."""
        dg = create_datagram(self.dg_path, name="Data Test")

        # Insert data via SQLite database
        db_path = os.path.join(self.dg_path, "Databases", "Default", "Data", "records.db")
        db = SQLiteDatabase(db_path, name="records")
        try:
            db.insert("entries", {"title": "Entry 1", "value": 100})
            db.insert("entries", {"title": "Entry 2", "value": 200})
            db.insert("entries", {"title": "Entry 3", "value": 300})

            results = db.select("entries")
            self.assertEqual(len(results), 3)

            results = db.select("entries", where={"value": 200})
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["title"], "Entry 2")
        finally:
            db.close()

    def test_create_with_metadata(self):
        """Create datagram with full metadata, verify roundtrip."""
        dg = create_datagram(self.dg_path, name="Meta Test")
        dg.meta.description = "A comprehensive test"
        dg.meta.tags = ["test", "yuniscripts", "datagram"]
        dg.meta.license = "MIT"
        update_meta_ini(dg)

        dg2 = load_datagram(self.dg_path)
        self.assertEqual(dg2.meta.description, "A comprehensive test")
        self.assertEqual(dg2.meta.tags, ["test", "yuniscripts", "datagram"])


class TestHashIntegrity(unittest.TestCase):
    """Verify hash integrity guarantees."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dg_hash_")
        self.dg_path = os.path.join(self.temp_dir, "hash_test")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detect_corruption(self):
        """Hash verification must detect file modifications."""
        create_datagram(self.dg_path, name="Hash Test")
        dg = load_datagram(self.dg_path)
        update_datagram_hash(dg)  # Store initial hash

        # Corrupt a file
        target = os.path.join(self.dg_path, "Meta", "DatagramMeta.ini")
        with open(target, "a") as f:
            f.write("\n# Tampered!")

        # Verify detects corruption
        is_valid, _ = verify_datagram_hash(dg)
        self.assertFalse(is_valid, "Hash should detect tampered file")

    def test_hash_excludes_base_ini(self):
        """Modifying Base.ini should NOT invalidate the hash (per spec)."""
        create_datagram(self.dg_path, name="Base Edit Test")
        dg = load_datagram(self.dg_path)
        update_datagram_hash(dg)

        # Modify Base.ini (name change)
        dg.meta.name = "Changed Name"
        update_base_ini(dg)

        # Verify hash is still valid (Base.ini excluded from hashing)
        dg2 = load_datagram(self.dg_path)
        is_valid, _ = verify_datagram_hash(dg2)
        self.assertTrue(is_valid,
                        "Hash should be valid after Base.ini changes")

    def test_all_hash_algorithms(self):
        """All supported hash algorithms must work."""
        for algo in HashAlgorithm:
            try:
                data = b"Test data for hashing"
                h = DatagramHash.compute(algo, data)
                self.assertTrue(len(h.hex_value) > 0)
                self.assertTrue(h.verify(data))
            except (ValueError, AttributeError):
                # Some algorithms may not be available
                pass


class TestCompatibilityChecking(unittest.TestCase):
    """Verify compatibility checker logic."""

    def test_datagram_compatible_with_engine(self):
        checker = CompatibilityChecker(engine_version=DatagramVersion(1, 0, 0))
        result = checker.check_datagram_compatibility(DatagramVersion(1, 0, 0))
        self.assertTrue(result.compatible)

    def test_higher_major_compatible(self):
        checker = CompatibilityChecker(engine_version=DatagramVersion(1, 0, 0))
        result = checker.check_datagram_compatibility(DatagramVersion(2, 0, 0))
        self.assertTrue(result.compatible)

    def test_lower_major_incompatible(self):
        checker = CompatibilityChecker(engine_version=DatagramVersion(2, 0, 0))
        result = checker.check_datagram_compatibility(DatagramVersion(1, 0, 0))
        self.assertFalse(result.compatible)

    def test_all_compatible_property(self):
        checker = CompatibilityChecker(DatagramVersion(2, 0, 0))
        checker.check_datagram_compatibility(DatagramVersion(1, 0, 0))
        checker.check_datagram_compatibility(DatagramVersion(2, 0, 0))
        self.assertFalse(checker.all_compatible)

    def test_failures_list(self):
        checker = CompatibilityChecker(DatagramVersion(1, 0, 0))
        checker.check_datagram_compatibility(DatagramVersion(2, 0, 0))  # OK
        checker.check_datagram_compatibility(DatagramVersion(0, 5, 0))  # FAIL
        self.assertEqual(len(checker.failures), 1)

    def test_register_component(self):
        checker = CompatibilityChecker()
        checker.register_component("loader", DatagramVersion(2, 0, 0))
        result = checker.check_required_version("loader", DatagramVersion(1, 5, 0))
        self.assertTrue(result.compatible)

    def test_register_missing_component(self):
        checker = CompatibilityChecker()
        result = checker.check_required_version("missing_comp", DatagramVersion(1, 0, 0))
        self.assertFalse(result.compatible)

    def test_function_requirements_all_pass(self):
        checker = CompatibilityChecker()
        funcs = [
            DatagramFunction("loader", version=DatagramVersion(1, 0, 0), source="x=1"),
            DatagramFunction("viewer", version=DatagramVersion(1, 0, 0), source="x=1"),
        ]
        results = checker.check_function_requirements(funcs)
        for r in results:
            self.assertTrue(r.compatible)

    def test_clear_results(self):
        checker = CompatibilityChecker()
        checker.check_datagram_compatibility(DatagramVersion(1, 0, 0))
        self.assertEqual(len(checker._results), 1)
        checker.clear()
        self.assertEqual(len(checker._results), 0)


class TestFunctionRegistry(unittest.TestCase):
    """Verify embedded function loading and execution."""

    def test_register_and_get(self):
        registry = FunctionRegistry()
        func = DatagramFunction("test", source="def main(): return 42")
        registry.register(func)
        self.assertTrue(registry.has_function("test"))
        self.assertEqual(registry.get_function("test").name, "test")

    def test_load_embedded_function(self):
        registry = FunctionRegistry()
        func = DatagramFunction("greet", source="def main(name): return f'Hello, {name}!'")
        callable_obj = registry.load_embedded(func)
        self.assertTrue(registry.has_callable("greet"))
        result = registry.execute("greet", "World")
        self.assertEqual(result, "Hello, World!")

    def test_load_embedded_entry_point_default(self):
        registry = FunctionRegistry()
        func = DatagramFunction("compute", source="def main(a, b): return a + b")
        registry.load_embedded(func)
        result = registry.execute("compute", 3, 4)
        self.assertEqual(result, 7)

    def test_execute_unloaded_function(self):
        registry = FunctionRegistry()
        with self.assertRaises(FunctionLoadError):
            registry.execute("nonexistent")

    def test_load_embedded_missing_entry_point(self):
        registry = FunctionRegistry()
        func = DatagramFunction("bad", source="x = 1", entry_point="missing")
        with self.assertRaises(FunctionLoadError):
            registry.load_embedded(func)

    def test_load_embedded_none_callable(self):
        registry = FunctionRegistry()
        func = DatagramFunction("bad2", source="main = 42  # not a function")
        with self.assertRaises(FunctionLoadError):
            registry.load_embedded(func)

    def test_register_callable(self):
        registry = FunctionRegistry()
        def my_func(): return "called"
        func = DatagramFunction("my_func", source="")
        registry.register(func, callable_obj=my_func)
        result = registry.execute("my_func")
        self.assertEqual(result, "called")

    def test_function_count(self):
        registry = FunctionRegistry()
        self.assertEqual(registry.function_count, 0)
        registry.register(DatagramFunction("a"))
        registry.register(DatagramFunction("b"))
        self.assertEqual(registry.function_count, 2)

    def test_function_names(self):
        registry = FunctionRegistry()
        registry.register(DatagramFunction("alpha"))
        registry.register(DatagramFunction("beta"))
        names = registry.function_names
        self.assertIn("alpha", names)
        self.assertIn("beta", names)


class TestDataTypeConsistency(unittest.TestCase):
    """Verify data type storage and retrieval consistency."""

    def test_all_types_roundtrip(self):
        """All supported types must roundtrip correctly."""
        test_cases = [
            (DataType.STRING, "hello", "hello"),
            (DataType.INTEGER, "42", 42),
            (DataType.INTEGER, 42, 42),
            (DataType.FLOAT, "3.14", 3.14),
            (DataType.FLOAT, 3.14, 3.14),
            (DataType.BOOLEAN, "true", True),
            (DataType.BOOLEAN, True, True),
            (DataType.BOOLEAN, "false", False),
            (DataType.NULL, None, None),
            (DataType.UUID, "550e8400-e29b-41d4-a716-446655440000",
             "550e8400-e29b-41d4-a716-446655440000"),
        ]
        for dtype, input_val, expected in test_cases:
            with self.subTest(dtype=dtype, input=input_val):
                v = DatagramValue(dtype, input_val)
                result = v.to_python()
                if dtype == DataType.FLOAT:
                    self.assertAlmostEqual(result, expected)
                else:
                    self.assertEqual(result, expected,
                                     f"Failed for {dtype.value} with input {input_val!r}")

    def test_json_value_preserves_structure(self):
        data = {"users": [{"name": "Alice", "scores": [10, 20]}], "count": 1}
        v = DatagramValue(DataType.JSON, data)
        result = v.to_python()
        self.assertEqual(result, data)
        self.assertEqual(result["users"][0]["name"], "Alice")

    def test_binary_roundtrip(self):
        original = bytes(range(256))
        v = DatagramValue(DataType.BINARY, original)
        result = v.to_python()
        self.assertEqual(result, original)

    def test_json_compatible(self):
        """to_json_compatible must return JSON-safe values."""
        data = {"key": "value"}
        v = DatagramValue(DataType.JSON, data)
        jc = v.to_json_compatible()
        self.assertEqual(jc, data)

    def test_to_dict_contains_type_and_value(self):
        v = DatagramValue(DataType.STRING, "test")
        d = v.to_dict()
        self.assertEqual(d["type"], "string")
        self.assertEqual(d["value"], "test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
