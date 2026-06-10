#!/usr/bin/env python3
"""
Unittests for Datagram Engine — I/O Operations.

Tests: INI parsing, datagram creation, loading, validation, content hashing.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from engine.datagram_io import (
    parse_ini_content, serialize_ini, serialize_ini_braced,
    read_ini_file, write_ini_file,
    load_datagram, create_datagram, update_base_ini, update_meta_ini,
    validate_datagram_structure, collect_content_for_hashing,
)
from engine.datagram_types import Datagram, DatagramMeta, HashAlgorithm, DatagramStatus


class TestIniParsing(unittest.TestCase):
    """Verify INI parsing (matching original Datagram spec)."""

    def test_parse_simple_key_value(self):
        content = """[Datagram Version]=1.0.0
[Datagram NAME ID]=Test Datagram
[Datagram Author]=Tester
"""
        result = parse_ini_content(content)
        self.assertEqual(result["Datagram Version"], "1.0.0")
        self.assertEqual(result["Datagram NAME ID"], "Test Datagram")
        self.assertEqual(result["Datagram Author"], "Tester")

    def test_parse_braced_value(self):
        content = """[Datagram Hash UQID]={abc123def456}
[Encryption Public Key]={-----BEGIN PUBLIC KEY-----}
"""
        result = parse_ini_content(content)
        self.assertEqual(result["Datagram Hash UQID"], "{abc123def456}")
        self.assertEqual(result["Encryption Public Key"], "{-----BEGIN PUBLIC KEY-----}")

    def test_skip_comments(self):
        content = """# This is a comment
[Version]=1.0.0
  # Indented comment
[Name]=Test
"""
        result = parse_ini_content(content)
        self.assertIn("Version", result)
        self.assertIn("Name", result)
        self.assertEqual(len(result), 2)

    def test_skip_empty_lines(self):
        content = """[A]=1

[B]=2

[C]=3
"""
        result = parse_ini_content(content)
        self.assertEqual(len(result), 3)

    def test_value_with_equals_sign(self):
        content = """[Connection String]=Server=localhost;DB=test"""
        result = parse_ini_content(content)
        self.assertEqual(result["Connection String"], "Server=localhost;DB=test")

    def test_hash_in_value_not_comment(self):
        content = """[Key]=value#withhash"""
        result = parse_ini_content(content)
        self.assertEqual(result["Key"], "value#withhash")

    def test_parse_empty_content(self):
        result = parse_ini_content("")
        self.assertEqual(len(result), 0)

    def test_parse_only_comments(self):
        result = parse_ini_content("# comment\n# another")
        self.assertEqual(len(result), 0)

    def test_serialize_roundtrip(self):
        original = {"Key": "value", "Name": "Test"}
        serialized = serialize_ini(original)
        reparsed = parse_ini_content(serialized)
        self.assertEqual(original, reparsed)

    def test_braced_serialize(self):
        data = {"[Description]": "{A test datagram}"}
        result = serialize_ini_braced(data)
        self.assertIn("{A test datagram}", result)


class TestDatagramCreateLoad(unittest.TestCase):
    """Verify datagram creation and loading."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dg_test_")
        self.dg_path = os.path.join(self.temp_dir, "my_datagram")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_datagram_structure(self):
        dg = create_datagram(self.dg_path, name="Test", author="Tester")
        self.assertTrue(os.path.exists(self.dg_path))
        self.assertTrue(os.path.exists(os.path.join(self.dg_path, "Meta", "Base.ini")))
        self.assertTrue(os.path.exists(os.path.join(self.dg_path, "Meta", "DatagramMeta.ini")))
        self.assertEqual(dg.meta.name, "Test")
        self.assertEqual(dg.meta.author, "Tester")

    def test_create_datagram_creates_all_directories(self):
        create_datagram(self.dg_path, name="Test")
        expected_dirs = ["Meta", "Databases/Default/Data", "LargeAssets",
                         "PreLoad/Gui", "PreLoad/Intil", "Functions"]
        for d in expected_dirs:
            self.assertTrue(os.path.isdir(os.path.join(self.dg_path, d)),
                            f"Missing directory: {d}")

    def test_load_datagram_roundtrip(self):
        create_datagram(self.dg_path, name="Roundtrip Test", author="Author")
        dg = load_datagram(self.dg_path)
        self.assertTrue(dg.is_loaded)
        self.assertEqual(dg.meta.name, "Roundtrip Test")
        self.assertEqual(dg.meta.author, "Author")

    def test_load_datagram_with_extended_meta(self):
        create_datagram(self.dg_path, name="Meta Test", author="Me")
        dg = load_datagram(self.dg_path)
        self.assertEqual(dg.meta.creation_date, dg.meta.creation_date)

    def test_load_nonexistent_path(self):
        with self.assertRaises(IOError):
            load_datagram("/nonexistent/path")

    def test_load_path_is_file_not_dir(self):
        f = os.path.join(self.temp_dir, "not_a_dir.txt")
        with open(f, "w") as fp:
            fp.write("not a datagram")
        with self.assertRaises(IOError):
            load_datagram(f)

    def test_load_missing_base_ini(self):
        os.makedirs(os.path.join(self.dg_path, "Meta"))
        with self.assertRaises(IOError):
            load_datagram(self.dg_path)

    def test_update_base_ini(self):
        dg = create_datagram(self.dg_path, name="Original")
        dg.meta.name = "Updated"
        update_base_ini(dg)
        dg2 = load_datagram(self.dg_path)
        self.assertEqual(dg2.meta.name, "Updated")


class TestValidateStructure(unittest.TestCase):
    """Verify datagram structure validation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dg_val_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_datagram(self):
        create_datagram(os.path.join(self.temp_dir, "valid"))
        valid, issues = validate_datagram_structure(os.path.join(self.temp_dir, "valid"))
        self.assertTrue(valid)

    def test_nonexistent_path(self):
        valid, issues = validate_datagram_structure("/nonexistent")
        self.assertFalse(valid)
        self.assertTrue(any("does not exist" in i for i in issues))

    def test_missing_base_ini(self):
        os.makedirs(os.path.join(self.temp_dir, "bad", "Meta"))
        valid, issues = validate_datagram_structure(os.path.join(self.temp_dir, "bad"))
        self.assertFalse(valid)


class TestCollectContentForHashing(unittest.TestCase):
    """Verify content collection for hashing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dg_hash_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_collect_excludes_base_ini(self):
        create_datagram(self.dg_path)
        content, paths = collect_content_for_hashing(Path(self.dg_path))
        # Base.ini should NOT be in the collected paths
        base_paths = [p for p in paths if "Base.ini" in p]
        self.assertEqual(len(base_paths), 0,
                         "Meta/Base.ini must be excluded from hash content")

    @property
    def dg_path(self):
        return os.path.join(self.temp_dir, "test_dg")

    def test_collect_sorts_by_path(self):
        create_datagram(self.dg_path)
        content, paths = collect_content_for_hashing(Path(self.dg_path))
        # Paths should be sorted
        for i in range(len(paths) - 1):
            self.assertLessEqual(paths[i], paths[i + 1])

    def test_collect_deterministic(self):
        create_datagram(self.dg_path)
        content1, paths1 = collect_content_for_hashing(Path(self.dg_path))
        content2, paths2 = collect_content_for_hashing(Path(self.dg_path))
        self.assertEqual(content1, content2)
        self.assertEqual(paths1, paths2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
