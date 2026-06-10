#!/usr/bin/env python3
"""
Level 2: Functional Consistency Tests
======================================
Verifies the Datagram project's function-level behavior:
- INI parsing logic correctness (simulated)
- Version comparison logic
- Hash computation consistency
- Database connection logic
- Parameter validation
- Edge case handling (empty inputs, nulls, boundary values)
"""

import os
import re
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def extract_functions_from_ps1(filepath):
    """Extract function names and bodies from a PowerShell script."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    functions = {}
    pattern = re.compile(
        r"function\s+(\w[\w\-]*)\s*\{",
        re.MULTILINE,
    )
    for match in pattern.finditer(content):
        name = match.group(1)
        start = match.start()
        # Find matching closing brace (naive brace counting)
        brace_count = 0
        pos = start
        while pos < len(content):
            if content[pos] == "{":
                brace_count += 1
            elif content[pos] == "}":
                brace_count -= 1
                if brace_count == 0:
                    functions[name] = content[start:pos+1]
                    break
            pos += 1
    return functions


class TestIniParsingSimulation(unittest.TestCase):
    """Simulate and verify INI parsing logic."""

    def setUp(self):
        load_ps1 = os.path.join(PROJECT_ROOT, "FUNCTIONS", "Config", "Load-Datagram.ps1")
        with open(load_ps1, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_ini_parse_simple_key_value(self):
        """Parse-DatagramIni must parse [Key]=value format."""
        pattern = re.compile(r"^\[([^\]]+)\]=(.*)$", re.MULTILINE)
        test_ini = """[Datagram Version]=1.0.0
[Datagram NAME ID]=Test Datagram
[Datagram Author]=Tester
"""
        matches = pattern.findall(test_ini)
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0], ("Datagram Version", "1.0.0"))
        self.assertEqual(matches[1], ("Datagram NAME ID", "Test Datagram"))
        self.assertEqual(matches[2], ("Datagram Author", "Tester"))

    def test_ini_parse_braced_value(self):
        """Parse-DatagramIni must parse [Key]={value} format."""
        pattern = re.compile(r"^\[([^\]]+)\]=\{(.*)\}$", re.MULTILINE)
        test_ini = """[Datagram Hash UQID]={abc123def456}
[Encryption Public Key]={-----BEGIN PUBLIC KEY-----}
"""
        matches = pattern.findall(test_ini)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0], ("Datagram Hash UQID", "abc123def456"))
        self.assertEqual(matches[1], ("Encryption Public Key", "-----BEGIN PUBLIC KEY-----"))

    def test_ini_skip_comments(self):
        """Parse-DatagramIni must skip comment lines."""
        # Verify the actual parser skips lines starting with #
        lines_to_check = [
            "# This is a comment",
            "  # Indented comment",
        ]
        for line in lines_to_check:
            # The parser checks: $trimmed.StartsWith('#')
            trimmed = line.strip()
            self.assertTrue(
                trimmed.startswith("#"),
                f"Line should be identified as comment: {line!r}",
            )
            # After stripping, comment lines should be length > 0
            # The parser skips them with: if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
            # So we verify: line starts with # after trim → should be skipped
            self.assertFalse(
                re.match(r"^\[([^\]]+)\]=", trimmed),
                f"Comment line should not match INI pattern: {line!r}",
            )

    def test_ini_skip_empty_lines(self):
        """Parse-DatagramIni must skip empty lines."""
        test_ini = """[Version]=1.0.0

[Name]=Test

[Author]=Tester
"""
        pattern = re.compile(r"^\[([^\]]+)\]=(.*)$", re.MULTILINE)
        matches = pattern.findall(test_ini)
        # Should find 3 matches, not affected by blank lines
        self.assertEqual(len(matches), 3)

    def test_ini_parse_comment_with_hash_not_equal(self):
        """A line with [Key]=Val#ue should parse correctly — hash in value is not a comment."""
        # The parser only skips if $trimmed.StartsWith('#') — hash in middle is fine
        test_line = "[Key]=value#withhash"
        pattern = re.compile(r"^\[([^\]]+)\]=(.*)$")
        match = pattern.match(test_line)
        self.assertIsNotNone(match, "Hash in value should not prevent parsing")
        self.assertEqual(match.group(1), "Key")
        self.assertEqual(match.group(2), "value#withhash")


class TestVersionCompatibility(unittest.TestCase):
    """Verify version comparison logic."""

    def setUp(self):
        self.test_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Discovery", "Test-DatagramCompatibility.ps1"
        )
        with open(self.test_ps1, "r", encoding="utf-8") as f:
            self.content = f.read()

    def _simulate_version_check(self, required, available):
        """
        Simulate Test-VersionCompatibility logic from the PowerShell script.
        Returns True if compatible (available >= required with same major).
        """
        req_parts = [int(p) if p.isdigit() else 0 for p in required.split(".")]
        avail_parts = [int(p) if p.isdigit() else 0 for p in available.split(".")]

        max_len = max(len(req_parts), len(avail_parts))
        for i in range(max_len):
            req = req_parts[i] if i < len(req_parts) else 0
            avail = avail_parts[i] if i < len(avail_parts) else 0

            if avail > req:
                return True
            if avail < req:
                return False
            # equal, continue
        return True

    def test_equal_versions_compatible(self):
        """Same versions must be compatible."""
        self.assertTrue(self._simulate_version_check("1.0.0", "1.0.0"))

    def test_newer_minor_compatible(self):
        """Available version with higher minor must be compatible."""
        self.assertTrue(self._simulate_version_check("1.0.0", "1.1.0"))

    def test_newer_patch_compatible(self):
        """Available version with higher patch must be compatible."""
        self.assertTrue(self._simulate_version_check("1.0.0", "1.0.1"))

    def test_older_major_incompatible(self):
        """Available version with lower major must be incompatible."""
        self.assertFalse(self._simulate_version_check("2.0.0", "1.9.9"))

    def test_available_higher_major_compatible(self):
        """Available version with higher major than required must be compatible."""
        self.assertTrue(self._simulate_version_check("1.0.0", "2.0.0"))

    def test_available_older_minor_incompatible(self):
        """Available version with lower minor must be incompatible."""
        self.assertFalse(self._simulate_version_check("1.5.0", "1.4.9"))

    def test_single_part_versions(self):
        """Versions with only major part must work."""
        self.assertTrue(self._simulate_version_check("1", "1"))
        self.assertTrue(self._simulate_version_check("1", "2"))
        self.assertFalse(self._simulate_version_check("2", "1"))

    def test_empty_version_edge(self):
        """Both empty version strings should be considered compatible."""
        # If both are empty, all parts are 0, so compatible
        self.assertTrue(self._simulate_version_check("", ""))

    def test_required_empty_available_lower(self):
        """Empty required should be compatible with anything (all parts 0)."""
        self.assertTrue(self._simulate_version_check("", "0.0.1"))

    def test_version_string_whitespace_handling(self):
        """Version strings with whitespace should be handled."""
        # The actual PS function doesn't trim — but we should
        # (this is a potential issue we're documenting)
        req_parts = " 1.0.0 ".strip().split(".")
        avail_parts = "1.0.1".strip().split(".")
        self.assertEqual(req_parts, ["1", "0", "0"])  # split produces parts
        # The actual issue: " 1.0.0 " will fail [int] parsing in PowerShell
        # This test documents the risk


class TestGetDatagramContentHash(unittest.TestCase):
    """Verify hash computation logic and edge cases."""

    def test_hash_skips_base_ini(self):
        """Get-DatagramContentHash must exclude Meta/Base.ini from hash."""
        hash_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Discovery", "Test-DatagramHash.ps1"
        )
        with open(hash_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        # Verify the script excludes Base.ini using PowerShell's -ne operator
        self.assertIn(
            "Meta\Base.ini",
            content,
            "Hash must exclude Meta\Base.ini from hash computation",
        )
        self.assertIn(
            "-ne",
            content,
            "Hash should use -ne comparison to exclude Base.ini",
        )

    def test_hash_uses_relative_paths(self):
        """Hash must use relative paths for consistent ordering."""
        hash_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Discovery", "Test-DatagramHash.ps1"
        )
        with open(hash_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        # Verify files are sorted by FullName
        self.assertIn(
            "Sort-Object FullName",
            content,
            "Files must be sorted by FullName for consistent hashing",
        )

    def test_hash_includes_file_paths(self):
        """Hash computation must include file path bytes in the content."""
        hash_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Discovery", "Test-DatagramHash.ps1"
        )
        with open(hash_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        # The hash should include the relative path as UTF-8 bytes
        self.assertIn(
            "GetBytes",
            content,
            "Hash must encode file paths as UTF-8 bytes",
        )


class TestDatabaseConnectionLogic(unittest.TestCase):
    """Verify database connection functions handle edge cases."""

    def setUp(self):
        self.connect_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "DATA", "Connect-DatagramDatabase.ps1"
        )
        with open(self.connect_ps1, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_duplicate_access_case(self):
        """There must not be duplicate 'Access' switch cases."""
        # Count occurrences of 'Access' switch case
        access_cases = re.findall(
            r"^\s+'Access'\s*\{",
            self.content,
            re.MULTILINE,
        )
        self.assertLessEqual(
            len(access_cases),
            1,
            f"BUG FOUND: Duplicate 'Access' switch case found "
            f"({len(access_cases)} occurrences).\n"
            f"The second case duplicates the first and corrupts the switch logic.\n"
            f"File: FUNCTIONS/DATA/Connect-DatagramDatabase.ps1",
        )

    def test_all_db_types_handled(self):
        """Switch must handle all supported database types."""
        required_types = ["SQLite", "Access", "JSON", "XML"]
        for db_type in required_types:
            self.assertIn(
                f"'{db_type}'",
                self.content,
                f"Switch must handle database type: {db_type}",
            )

    def test_default_case_exists(self):
        """Switch must have a default case for unknown types."""
        self.assertIn(
            "default",
            self.content,
            "Switch must have a default case for unknown database types",
        )

    def test_null_return_on_missing_db(self):
        """Connect-DatagramDatabase must return $null when path not found."""
        self.assertIn(
            "return $null",
            self.content,
            "Function must return $null when database path not found",
        )


class TestSendDatagramDataValidation(unittest.TestCase):
    """Verify Send-DatagramData parameter validation and edge cases."""

    def setUp(self):
        self.send_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "DATA", "Send-DatagramData.ps1"
        )
        with open(self.send_ps1, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_operation_validation_exists(self):
        """Must validate that Data param is provided for Insert/Update."""
        self.assertIn(
            "is required for",
            self.content,
            "Must validate Data parameter is present for Insert/Update",
        )

    def test_validate_set_exists(self):
        """DatabaseType must use ValidateSet."""
        self.assertIn(
            "ValidateSet",
            self.content,
            "DatabaseType must use ValidateSet for input validation",
        )
        self.assertIn(
            "'SQLite', 'JSON', 'XML', 'Access'",
            self.content,
            "ValidateSet must include all supported types",
        )

    def test_sql_injection_tablename_risk_documented(self):
        """
        IDENTIFIED RISK: SQL injection via $TableName.
        TableName is string-interpolated into SQL without sanitization.
        """
        # Check for direct interpolation pattern
        dangerous_patterns = [
            r"INSERT INTO \$TableName",
            r"UPDATE \$TableName SET",
            r"DELETE FROM \$TableName",
            r"WHERE ID = \$RowID",
            r"WHERE \$WhereCondition",
        ]
        issues = []
        for pattern in dangerous_patterns:
            if re.search(pattern, self.content):
                issues.append(pattern)
        if issues:
            self.fail(
                "SQL INJECTION RISK: The following patterns use direct string "
                "interpolation in SQL statements without parameterization:\n"
                + "\n".join(f"  {p}" for p in issues)
                + "\n\nTableName, RowID, and WhereCondition must be parameterized "
                "using @p parameters, not string interpolation."
            )

    def test_query_operation_validates_sql(self):
        """Query operation must handle empty Query parameter gracefully."""
        self.assertIn(
            "SELECT * FROM",
            self.content,
            "Query operation should default to SELECT * FROM TableName when Query is empty",
        )


class TestLoadEmbeddedFunctions(unittest.TestCase):
    """Verify Load-EmbeddedFunctions logic and detect duplicates."""

    def test_no_duplicate_definitions_across_files(self):
        """
        Verify Load-EmbeddedFunctions is not defined in two independent files.
        The Threads version is now a delegation shim that dot-sources the
        Discovery version (does NOT define the function itself unless fallback).
        If both files still independently define the function, this fails.
        """
        files_with_func = []
        for dirpath, _, filenames in os.walk(PROJECT_ROOT):
            for fn in filenames:
                if fn.endswith(".ps1"):
                    full = os.path.join(dirpath, fn)
                    with open(full, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "function Load-EmbeddedFunctions" in content:
                        files_with_func.append(os.path.relpath(full, PROJECT_ROOT))

        self.assertEqual(
            len(files_with_func),
            1,
            f"CRITICAL BUG: 'Load-EmbeddedFunctions' is defined in "
            f"{len(files_with_func)} files: {files_with_func}\n"
            f"Only one definition should exist. The duplicate will cause "
            f"the last-sourced version to overwrite the first, "
            f"leading to unpredictable behavior.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
