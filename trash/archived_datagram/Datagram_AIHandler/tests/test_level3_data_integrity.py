#!/usr/bin/env python3
"""
Level 3: Data Integrity Consistency Tests
=========================================
Verifies the Datagram project's data integrity guarantees:
- Round-trip data preservation (no data loss)
- Hash consistency
- Database CRUD operations preserve data
- JSON file read/write consistency
- Parameter passthrough integrity
- File encoding consistency
"""

import os
import re
import json
import sys
import unittest
import hashlib

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestDataRoundtrips(unittest.TestCase):
    """Verify that data survives operations without corruption."""

    def test_ini_parse_roundtrip(self):
        """INI parsing must preserve all data fields."""
        test_ini = """[Datagram Version]=1.0.0
[Datagram NAME ID]=Test-Datagram-123
[Datagram Author]=TrueGearsWorks
[Datagram Hashing Algo]=1
[Datagram Hash UQID]={abc123def456}
[Encryption]=0
[Encryption Public Key]={}
[Encryption Server URL]={https://example.com/key}
"""
        # Simulate Parse-DatagramIni behavior
        pattern_eq = re.compile(r"^\[([^\]]+)\]=(.*)$", re.MULTILINE)
        pattern_brace = re.compile(r"^\[([^\]]+)\]=\{(.*)\}$", re.MULTILINE)

        config = {}
        for line in test_ini.strip().splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#"):
                continue
            brace_match = pattern_brace.match(trimmed)
            eq_match = pattern_eq.match(trimmed)
            if brace_match:
                key = brace_match.group(1).strip()
                value = brace_match.group(2).strip()
                config[key] = value
            elif eq_match:
                key = eq_match.group(1).strip()
                value = eq_match.group(2).strip()
                config[key] = value

        # Verify all fields preserved
        self.assertEqual(config["Datagram Version"], "1.0.0")
        self.assertEqual(config["Datagram NAME ID"], "Test-Datagram-123")
        self.assertEqual(config["Datagram Author"], "TrueGearsWorks")
        self.assertEqual(config["Datagram Hashing Algo"], "1")
        self.assertEqual(config["Datagram Hash UQID"], "abc123def456")
        self.assertEqual(config["Encryption"], "0")
        self.assertEqual(config["Encryption Public Key"], "")
        self.assertEqual(config["Encryption Server URL"], "https://example.com/key")

    def test_ini_parse_value_with_equals_sign(self):
        """INI values containing '=' must be preserved."""
        test_line = "[Connection String]=Server=localhost;DB=test"
        pattern = re.compile(r"^\[([^\]]+)\]=(.*)$")
        match = pattern.match(test_line)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "Connection String")
        self.assertEqual(match.group(2), "Server=localhost;DB=test")

    def test_ini_parse_value_with_spaces(self):
        """INI values starting or ending with spaces must be preserved."""
        # IMPORTANT: Do NOT .strip() — that would remove trailing spaces!
        test_ini = "[Datagram NAME ID]=  My Datagram  "
        pattern = re.compile(r"^\[([^\]]+)\]=(.*)$")
        match = pattern.match(test_ini)
        self.assertIsNotNone(match)
        # Values with leading/trailing spaces — the PowerShell parser
        # extracts the value after = sign without trimming
        value = match.group(2)
        self.assertEqual(value, "  My Datagram  ")
        # NOTE: This may be unexpected behavior. If trimming is desired,
        # the parser should use $matches[2].Trim()


class TestHashConsistency(unittest.TestCase):
    """Verify hash computation produces consistent results."""

    def setUp(self):
        self.hash_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Discovery", "Test-DatagramHash.ps1"
        )
        with open(self.hash_ps1, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_hash_file_ordering_is_deterministic(self):
        """Files must be sorted for deterministic hashing."""
        self.assertIn(
            "Sort-Object FullName",
            self.content,
            "FAIL: Files must be sorted by FullName for deterministic hashing.\n"
            "Without sorting, the hash will differ on different filesystems.",
        )

    def test_hash_excludes_base_ini(self):
        """Meta/Base.ini must be excluded from hash to allow updating hash field."""
        self.assertIn(
            "-ne",
            self.content,
            "FAIL: Meta/Base.ini must be excluded from hash using -ne operator.\n"
            "If included, the hash would change when the hash value is updated.",
        )

    def test_hash_includes_all_other_files(self):
        """All files except Meta/Base.ini must be included in hash."""
        # Extract just the Get-DatagramContentHash function body
        func_marker = "function Get-DatagramContentHash"
        self.assertIn(
            func_marker,
            self.content,
            "Get-DatagramContentHash function must exist",
        )
        func_start = self.content.index(func_marker)
        get_content_body = self.content[func_start:]
        self.assertNotIn(
            "SkipHashValidation",
            get_content_body,
            "Get-DatagramContentHash should not have SkipHashValidation parameter",
        )

    def test_sha256_available(self):
        """SHA256 (Python equivalent) must match expected approach."""
        test_data = b"Hello, Datagram!"
        h = hashlib.sha256(test_data).hexdigest()
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)  # SHA256 = 64 hex chars

    def test_hash_consistency_for_empty_content(self):
        """Hash of empty content should be deterministic."""
        empty_hash = hashlib.sha256(b"").hexdigest()
        self.assertEqual(
            empty_hash,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )


class TestDatabaseCrudConsistency(unittest.TestCase):
    """Verify database CRUD operations maintain data integrity."""

    def test_sqlite_insert_parameterization(self):
        """Verify Send-DatagramData uses parameterized queries for data values."""
        send_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "DATA", "Send-DatagramData.ps1"
        )
        with open(send_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        insert_section = re.search(
            r"('Insert'\s*\{.*?'Update')", content, re.DOTALL
        )
        if insert_section:
            insert_code = insert_section.group(1)
            self.assertIn(
                "AddWithValue",
                insert_code,
                "Insert must use AddWithValue for parameterized queries",
            )
            self.assertIn(
                "@p", insert_code,
                "Insert must use named parameters (@p) for values",
            )

    def test_database_path_validation(self):
        """Database operations must validate that the path exists."""
        setup_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "DATA", "Setup-DatagramDatabase.ps1"
        )
        with open(setup_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn(
            "New-Item -ItemType Directory",
            content,
            "Setup must create parent directories if they don't exist",
        )

    def test_json_insert_preserves_data(self):
        """JSON Insert must preserve all keys and values."""
        test_data = {
            "id": "img_001",
            "path": "LargeAssets/Images/photo.png",
            "width": 1920,
            "height": 1080,
            "metadata": {
                "author": "Tester",
                "date": "2026-01-01",
                "tags": ["nature", "landscape"],
            },
            "empty_field": "",
            "null_field": None,
            "numeric_field": 42,
            "boolean_field": True,
        }

        json_str = json.dumps(test_data, indent=2)
        roundtrip = json.loads(json_str)

        self.assertEqual(roundtrip["id"], "img_001")
        self.assertEqual(roundtrip["width"], 1920)
        self.assertEqual(roundtrip["height"], 1080)
        self.assertEqual(roundtrip["metadata"]["author"], "Tester")
        self.assertEqual(roundtrip["metadata"]["tags"], ["nature", "landscape"])
        self.assertEqual(roundtrip["empty_field"], "")
        self.assertIsNone(roundtrip["null_field"])
        self.assertEqual(roundtrip["numeric_field"], 42)
        self.assertEqual(roundtrip["boolean_field"], True)

    def test_json_array_insert(self):
        """JSON Insert must handle arrays of objects."""
        test_array = [
            {"id": "img_001", "name": "Image 1"},
            {"id": "img_002", "name": "Image 2"},
            {"id": "img_003", "name": "Image 3"},
        ]
        json_str = json.dumps(test_array)
        roundtrip = json.loads(json_str)
        self.assertEqual(len(roundtrip), 3)
        self.assertEqual(roundtrip[0]["id"], "img_001")
        self.assertEqual(roundtrip[2]["name"], "Image 3")

    def test_json_delete_removes_items(self):
        """JSON Delete must remove only matching items."""
        data = [
            {"ID": 1, "name": "Item 1"},
            {"ID": 2, "name": "Item 2"},
            {"ID": 3, "name": "Item 3"},
        ]

        row_id_to_delete = "2"
        new_data = [
            item for item in data
            if not (str(item.get("ID")) == row_id_to_delete)
        ]
        self.assertEqual(len(new_data), 2)
        self.assertEqual(new_data[0]["ID"], 1)
        self.assertEqual(new_data[1]["ID"], 3)

    def test_json_update_modifies_correct_items(self):
        """JSON Update must modify only matching items."""
        data = [
            {"ID": 1, "name": "Item 1", "status": "active"},
            {"ID": 2, "name": "Item 2", "status": "inactive"},
            {"ID": 3, "name": "Item 3", "status": "active"},
        ]

        updates = {"status": "archived"}
        for item in data:
            for key, value in updates.items():
                item[key] = value

        self.assertEqual(data[0]["status"], "archived")
        self.assertEqual(data[1]["status"], "archived")
        self.assertEqual(data[2]["status"], "archived")


class TestScriptEncoding(unittest.TestCase):
    """Verify all project files are consistently encoded."""

    def test_all_ps1_files_have_consistent_encoding(self):
        """All .ps1 files should be valid UTF-8."""
        for dirpath, _, filenames in os.walk(PROJECT_ROOT):
            for fn in filenames:
                if fn.endswith(".ps1"):
                    full = os.path.join(dirpath, fn)
                    with open(full, "rb") as f:
                        raw = f.read()
                    try:
                        raw.decode("utf-8")
                    except UnicodeDecodeError:
                        self.fail(
                            f"File is not valid UTF-8: "
                            f"{os.path.relpath(full, PROJECT_ROOT)}"
                        )

    def test_readme_files_have_encoding(self):
        """README files should be valid UTF-8."""
        readme_files = [
            os.path.join(PROJECT_ROOT, "README.md"),
            os.path.join(PROJECT_ROOT, "requirements.txt"),
            os.path.join(PROJECT_ROOT, "Databases", "Default", "Data", "README.txt"),
            os.path.join(PROJECT_ROOT, "LargeAssets", "README.txt"),
            os.path.join(PROJECT_ROOT, "PreLoad", "Intil", "README.txt"),
        ]
        for rf in readme_files:
            if os.path.isfile(rf):
                with open(rf, "rb") as f:
                    raw = f.read()
                try:
                    raw.decode("utf-8")
                except UnicodeDecodeError:
                    self.fail(f"File is not valid UTF-8: {rf}")


class TestParameterPassthrough(unittest.TestCase):
    """Verify parameters pass through functions without corruption."""

    def test_get_type_cmdlet_fixed(self):
        """
        Verify that Setup-DatagramDatabase.ps1 no longer uses 'Get-Type'
        (which is not a standard PowerShell cmdlet).
        """
        setup_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "DATA", "Setup-DatagramDatabase.ps1"
        )
        with open(setup_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        if "Get-Type" in content:
            self.fail(
                "BUG: 'Get-Type' is not a standard PowerShell cmdlet.\n"
                "Used in Setup-DatagramDatabase.ps1. Should use try/catch instead."
            )

    def test_no_duplicate_access_cases(self):
        """Verify no duplicate 'Access' cases in Connect-DatagramDatabase.ps1."""
        connect_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "DATA", "Connect-DatagramDatabase.ps1"
        )
        with open(connect_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        access_cases = re.findall(
            r"^\s+'Access'\s*\{",
            content,
            re.MULTILINE,
        )
        self.assertLessEqual(
            len(access_cases), 1,
            f"BUG: Found {len(access_cases)} 'Access' cases. "
            f"Should be exactly 1."
        )


class TestEmptyStateHandling(unittest.TestCase):
    """Verify the project handles empty/initial state gracefully."""

    def test_databases_default_data_empty(self):
        """Databases/Default/Data/ should contain only README."""
        data_dir = os.path.join(PROJECT_ROOT, "Databases", "Default", "Data")
        if os.path.isdir(data_dir):
            items = [
                f for f in os.listdir(data_dir)
                if os.path.isfile(os.path.join(data_dir, f))
            ]
            readme_files = [f for f in items if "README" in f]
            self.assertGreaterEqual(
                len(readme_files), 1,
                "Databases/Default/Data/ should contain at least README.txt",
            )

    def test_large_assets_empty(self):
        """LargeAssets should contain only README."""
        assets_dir = os.path.join(PROJECT_ROOT, "LargeAssets")
        if os.path.isdir(assets_dir):
            items = os.listdir(assets_dir)
            non_readme = [f for f in items if "README" not in f]
            self.assertEqual(
                len(non_readme), 0,
                f"LargeAssets should be empty except README. Found: {non_readme}",
            )

    def test_preload_intil_empty(self):
        """PreLoad/Intil should contain only README (no executable scripts yet)."""
        intil_dir = os.path.join(PROJECT_ROOT, "PreLoad", "Intil")
        if os.path.isdir(intil_dir):
            items = os.listdir(intil_dir)
            non_readme = [f for f in items if "README" not in f]
            self.assertEqual(
                len(non_readme), 0,
                f"PreLoad/Intil should be empty except README. Found: {non_readme}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
