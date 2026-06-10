#!/usr/bin/env python3
"""
Unittests for Datagram Engine — Type System.

Tests: DatagramVersion, DatagramHash, DatagramMeta, DatagramValue,
       Datagram, HashAlgorithm, DatabaseType, DataType, EncryptionMode.
"""

import os
import sys
import tempfile
import unittest
import uuid as uuid_mod
from pathlib import Path

# Ensure engine is importable
PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from engine.datagram_types import (
    DatagramVersion, DatagramHash, DatagramMeta, DatagramValue, Datagram,
    DatagramFunction, DatabaseRecord,
    HashAlgorithm, EncryptionMode, DatabaseType, DatagramStatus, DataType,
)


class TestDatagramVersion(unittest.TestCase):
    """Verify semantic versioning and compatibility logic."""

    def test_parse_full_version(self):
        v = DatagramVersion.parse("1.2.3")
        self.assertEqual(v.major, 1)
        self.assertEqual(v.minor, 2)
        self.assertEqual(v.patch, 3)

    def test_parse_major_only(self):
        v = DatagramVersion.parse("2")
        self.assertEqual(v.major, 2)
        self.assertEqual(v.minor, 0)
        self.assertEqual(v.patch, 0)

    def test_parse_empty_string(self):
        v = DatagramVersion.parse("")
        self.assertEqual(v, DatagramVersion(0, 0, 0))

    def test_parse_whitespace(self):
        v = DatagramVersion.parse("  ")
        self.assertEqual(v, DatagramVersion(0, 0, 0))

    def test_equal_versions(self):
        self.assertTrue(DatagramVersion(1, 0, 0).is_compatible_with(DatagramVersion(1, 0, 0)))

    def test_newer_minor_compatible(self):
        self.assertTrue(DatagramVersion(1, 1, 0).is_compatible_with(DatagramVersion(1, 0, 0)))

    def test_newer_patch_compatible(self):
        self.assertTrue(DatagramVersion(1, 0, 1).is_compatible_with(DatagramVersion(1, 0, 0)))

    def test_older_major_incompatible(self):
        self.assertFalse(DatagramVersion(1, 9, 9).is_compatible_with(DatagramVersion(2, 0, 0)))

    def test_higher_major_always_compatible(self):
        self.assertTrue(DatagramVersion(2, 0, 0).is_compatible_with(DatagramVersion(1, 0, 0)))

    def test_older_minor_incompatible(self):
        self.assertFalse(DatagramVersion(1, 4, 9).is_compatible_with(DatagramVersion(1, 5, 0)))

    def test_older_patch_incompatible(self):
        self.assertFalse(DatagramVersion(1, 0, 0).is_compatible_with(DatagramVersion(1, 0, 1)))

    def test_string_representation(self):
        self.assertEqual(str(DatagramVersion(1, 2, 3)), "1.2.3")

    def test_less_than_comparison(self):
        self.assertLess(DatagramVersion(1, 0, 0), DatagramVersion(1, 0, 1))
        self.assertLess(DatagramVersion(1, 0, 0), DatagramVersion(1, 1, 0))
        self.assertLess(DatagramVersion(1, 0, 0), DatagramVersion(2, 0, 0))

    def test_dict_roundtrip(self):
        v = DatagramVersion(2, 1, 3)
        d = v.to_dict()
        v2 = DatagramVersion.from_dict(d)
        self.assertEqual(v, v2)


class TestDatagramHash(unittest.TestCase):
    """Verify hash computation and verification."""

    def test_sha256_hash_consistent(self):
        data = b"Hello, Datagram!"
        h = DatagramHash.compute(HashAlgorithm.SHA256, data)
        self.assertEqual(len(h.hex_value), 64)  # SHA256 = 64 hex chars
        self.assertTrue(h.verify(data))

    def test_hash_detects_corruption(self):
        data = b"Original content"
        h = DatagramHash.compute(HashAlgorithm.SHA256, data)
        self.assertFalse(h.verify(b"Modified content"))

    def test_hash_empty_content(self):
        data = b""
        h = DatagramHash.compute(HashAlgorithm.SHA256, data)
        # Known SHA256 of empty string
        self.assertEqual(
            h.hex_value,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_ini_value_roundtrip(self):
        data = b"Test content for hash"
        h = DatagramHash.compute(HashAlgorithm.SHA256, data)
        ini_val = h.to_ini_value()
        h2 = DatagramHash.from_ini_value(HashAlgorithm.SHA256, ini_val)
        self.assertEqual(h.hex_value, h2.hex_value)

    def test_bool_false_when_empty(self):
        h = DatagramHash()
        self.assertFalse(bool(h))

    def test_bool_true_when_has_hash(self):
        h = DatagramHash(hex_value="abc123")
        self.assertTrue(bool(h))

    def test_sha3_256(self):
        data = b"Test"
        try:
            h = DatagramHash.compute(HashAlgorithm.SHA3_256, data)
            self.assertEqual(len(h.hex_value), 64)
            self.assertTrue(h.verify(data))
        except ValueError:
            self.skipTest("SHA3 not available in this Python version")


class TestDatagramMeta(unittest.TestCase):
    """Verify metadata construction and conversion."""

    def test_default_meta(self):
        meta = DatagramMeta()
        self.assertEqual(meta.name, "Untitled Datagram")
        self.assertEqual(meta.version, DatagramVersion(1, 0, 0))
        self.assertEqual(meta.encryption, EncryptionMode.NONE)
        self.assertEqual(meta.status, DatagramStatus.CREATED)

    def test_meta_ini_roundtrip(self):
        meta = DatagramMeta()
        meta.name = "Test Datagram"
        meta.author = "Test Author"
        meta.version = DatagramVersion(2, 1, 0)
        meta.encryption = EncryptionMode.PUBLIC_KEY
        meta.encryption_key = "test-public-key-12345"

        ini_dict = meta.to_ini_dict(include_hash=False)
        meta2 = DatagramMeta.from_ini_dict(ini_dict)

        self.assertEqual(meta2.name, "Test Datagram")
        self.assertEqual(meta2.author, "Test Author")
        self.assertEqual(meta2.version, DatagramVersion(2, 1, 0))
        self.assertEqual(meta2.encryption, EncryptionMode.PUBLIC_KEY)
        self.assertEqual(meta2.encryption_key, "test-public-key-12345")

    def test_meta_roundtrip_with_hash(self):
        meta = DatagramMeta()
        meta.datagram_hash = DatagramHash(hex_value="abcdef1234567890" * 4)

        ini_dict = meta.to_ini_dict(include_hash=True)
        meta2 = DatagramMeta.from_ini_dict(ini_dict)

        self.assertTrue(meta2.datagram_hash)
        self.assertEqual(meta2.datagram_hash.hex_value, "abcdef1234567890" * 4)

    def test_meta_meta_dict_roundtrip(self):
        meta = DatagramMeta()
        meta.description = "A test datagram"
        meta.tags = ["test", "datagram", "archive"]
        meta.license = "MIT"

        meta_dict = meta.to_meta_dict()
        meta2 = DatagramMeta.from_meta_dict(meta_dict, base=meta)

        self.assertEqual(meta2.description, "A test datagram")
        self.assertEqual(meta2.tags, ["test", "datagram", "archive"])
        self.assertEqual(meta2.license, "MIT")

    def test_uuid_is_generated(self):
        meta = DatagramMeta()
        # Verify it's a valid UUID
        parsed = uuid_mod.UUID(meta.datagram_uuid)
        self.assertEqual(str(parsed), meta.datagram_uuid)

    def test_to_dict_contains_expected_keys(self):
        meta = DatagramMeta()
        d = meta.to_dict()
        self.assertIn("version", d)
        self.assertIn("name", d)
        self.assertIn("author", d)
        self.assertIn("hash_algorithm", d)
        self.assertIn("uuid", d)
        self.assertIn("status", d)


class TestDatagramValue(unittest.TestCase):
    """Verify typed value storage and conversion."""

    def test_string_value(self):
        v = DatagramValue(DataType.STRING, "hello")
        self.assertEqual(v.to_python(), "hello")

    def test_integer_value(self):
        v = DatagramValue(DataType.INTEGER, "42")
        self.assertEqual(v.to_python(), 42)

    def test_float_value(self):
        v = DatagramValue(DataType.FLOAT, "3.14")
        self.assertAlmostEqual(v.to_python(), 3.14)

    def test_boolean_true(self):
        v = DatagramValue(DataType.BOOLEAN, True)
        self.assertTrue(v.to_python())

    def test_boolean_false_string(self):
        v = DatagramValue(DataType.BOOLEAN, "false")
        self.assertFalse(v.to_python())

    def test_json_value(self):
        orig = {"key": "value", "count": 42}
        v = DatagramValue(DataType.JSON, orig)
        self.assertEqual(v.to_python(), orig)

    def test_null_value(self):
        v = DatagramValue(DataType.NULL, None)
        self.assertIsNone(v.to_python())

    def test_binary_value(self):
        v = DatagramValue(DataType.BINARY, b"\x00\x01\x02")
        result = v.to_python()
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, b"\x00\x01\x02")

    def test_json_compatible_binary(self):
        v = DatagramValue(DataType.BINARY, b"\x00\xff")
        jc = v.to_json_compatible()
        self.assertIsInstance(jc, str)
        self.assertEqual(jc, "00ff")

    def test_uuid_value(self):
        uid = str(uuid_mod.uuid4())
        v = DatagramValue(DataType.UUID, uid)
        self.assertEqual(v.to_python(), uid)

    def test_datetime_value(self):
        v = DatagramValue(DataType.DATETIME, "2026-01-11T00:00:00")
        self.assertEqual(v.to_python(), "2026-01-11T00:00:00")

    def test_to_dict_roundtrip(self):
        v = DatagramValue(DataType.INTEGER, 42)
        d = v.to_dict()
        self.assertEqual(d["type"], "integer")
        self.assertEqual(d["value"], 42)

    def test_coerce_none(self):
        v = DatagramValue(DataType.STRING, None)
        self.assertIsNone(v.value)


class TestDatabaseRecord(unittest.TestCase):
    """Verify database record operations."""

    def test_create_record(self):
        r = DatabaseRecord({"id": 1, "name": "Test"})
        self.assertEqual(r["id"], 1)
        self.assertEqual(r["name"], "Test")

    def test_get_with_default(self):
        r = DatabaseRecord({"id": 1})
        self.assertEqual(r.get("missing", "fallback"), "fallback")

    def test_contains(self):
        r = DatabaseRecord({"key": "value"})
        self.assertIn("key", r)
        self.assertNotIn("missing", r)

    def test_to_dict(self):
        r = DatabaseRecord({"a": 1, "b": 2})
        self.assertEqual(r.to_dict(), {"a": 1, "b": 2})

    def test_set_item(self):
        r = DatabaseRecord({"a": 1})
        r["b"] = 2
        self.assertEqual(r["b"], 2)


class TestDatagramFunction(unittest.TestCase):
    """Verify function metadata."""

    def test_create_function(self):
        f = DatagramFunction(
            name="test_func",
            version=DatagramVersion(1, 0, 0),
            source="def main(): return 42",
            description="A test function",
        )
        self.assertEqual(f.name, "test_func")
        self.assertEqual(f.entry_point, "main")
        self.assertTrue(f.required)

    def test_to_dict(self):
        f = DatagramFunction(name="fn", source="x=1")
        d = f.to_dict()
        self.assertEqual(d["name"], "fn")
        self.assertIn("version", d)
        self.assertIn("source_length", d)


class TestHashAlgorithm(unittest.TestCase):
    """Verify hash algorithm utilities."""

    def test_from_name_sha256(self):
        algo = HashAlgorithm.from_name("SHA256")
        self.assertEqual(algo, HashAlgorithm.SHA256)

    def test_from_name_shake256(self):
        algo = HashAlgorithm.from_name("SHAKE256-1024")
        self.assertEqual(algo, HashAlgorithm.SHAKE256_1024)

    def test_from_name_case_insensitive(self):
        algo = HashAlgorithm.from_name("sha3-256")
        self.assertEqual(algo, HashAlgorithm.SHA3_256)

    def test_from_name_fallback(self):
        algo = HashAlgorithm.from_name("UNKNOWN")
        self.assertEqual(algo, HashAlgorithm.SHA256)

    def test_display_name(self):
        self.assertEqual(HashAlgorithm.SHA256.display_name, "SHA256")
        self.assertEqual(HashAlgorithm.SHAKE256_1024.display_name, "SHAKE256-1024")

    def test_digest_size(self):
        self.assertEqual(HashAlgorithm.SHA256.digest_size, 32)
        self.assertEqual(HashAlgorithm.SHAKE256_1024.digest_size, 128)


class TestDatagram(unittest.TestCase):
    """Verify root Datagram object."""

    def test_create_datagram(self):
        dg = Datagram(root_path="/tmp/test")
        self.assertFalse(dg.is_loaded)
        self.assertFalse(dg.hash_valid)

    def test_mark_loaded(self):
        dg = Datagram()
        dg.mark_loaded()
        self.assertTrue(dg.is_loaded)
        self.assertEqual(dg.meta.status, DatagramStatus.LOADED)

    def test_to_dict(self):
        dg = Datagram(root_path="/tmp/dg")
        dg.mark_loaded()
        d = dg.to_dict()
        self.assertEqual(d["root_path"], "/tmp/dg")
        self.assertTrue(d["loaded"])
        self.assertIn("datagram_uuid", d)

    def test_validate_schema_valid(self):
        dg = Datagram()
        dg.meta.name = "Valid Name"
        valid, errors = dg.validate_schema()
        self.assertTrue(valid)
        self.assertEqual(len(errors), 0)

    def test_validate_schema_missing_name(self):
        dg = Datagram()
        dg.meta.name = ""
        valid, errors = dg.validate_schema()
        self.assertFalse(valid)
        self.assertIn("name", errors[0].lower())

    def test_validate_schema_bad_version(self):
        dg = Datagram()
        dg.meta.version = DatagramVersion(0, 0, 0)
        valid, errors = dg.validate_schema()
        self.assertFalse(valid)

    def test_empty_schema_validation(self):
        """A bare-minimum valid schema should pass."""
        dg = Datagram()
        dg.meta.name = "Test"
        dg.meta.datagram_uuid = str(uuid_mod.uuid4())
        dg.meta.version = DatagramVersion(1, 0, 0)
        valid, errors = dg.validate_schema()
        self.assertTrue(valid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
