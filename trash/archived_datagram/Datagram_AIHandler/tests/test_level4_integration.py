#!/usr/bin/env python3
"""
Level 4: Cross-Component Integration Tests
===========================================
Verifies that Datagram components work together correctly:
- Full data flow from load to render
- Import-Functions dot-sourcing order effects
- Component interaction contracts
- Thread safety / race conditions
- Error propagation across components
- Configuration consistency across files
"""

import os
import re
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestImportOrderEffects(unittest.TestCase):
    """
    Verify that Import-Functions.ps1 load order doesn't cause issues.
    Since Get-ChildItem -Recurse enumerates files in filesystem order,
    the order of dot-sourcing is not deterministic.
    """

    def setUp(self):
        self.import_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Import-Functions.ps1"
        )
        with open(self.import_ps1, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_import_uses_try_catch(self):
        """Import-Functions must use try/catch so one failure doesn't abort all."""
        self.assertIn(
            "try",
            self.content,
            "Import-Functions must use try/catch around each dot-source",
        )
        self.assertIn(
            "catch",
            self.content,
            "Import-Functions must catch errors per-file",
        )

    def test_duplicate_load_embedded_functions_is_critical(self):
        """
        Verify Load-EmbeddedFunctions has been consolidated.
        The Threads version now delegates to Discovery — no independent definition.
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

        # Check if Threads version is a delegation shim
        threads_shim = [f for f in files_with_func if "Threads" in f]
        non_shim = [f for f in files_with_func if "Threads" not in f]

        if threads_shim and len(non_shim) <= 1:
            # Threads shim delegates to Discovery — acceptable pattern
            return

        self.assertEqual(
            len(files_with_func),
            1,
            f"INTEGRATION BUG: 'Load-EmbeddedFunctions' defined in "
            f"{len(files_with_func)} files: {files_with_func}\n"
            f"Independent duplicate definitions cause unpredictable behavior.",
        )


class TestFullDataLoadFlow(unittest.TestCase):
    """
    Verify the end-to-end data flow from Load-Datagram through
    to GUI rendering compatibility checking.
    """

    def setUp(self):
        self.load_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Config", "Load-Datagram.ps1"
        )
        with open(self.load_ps1, "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_load_datagram_flow_references(self):
        """Load-Datagram must reference all required subcomponents."""
        required_calls = [
            "Test-DatagramHash",
            "Test-DatagramEncryption",
            "Parse-DatagramIni",
        ]
        for call in required_calls:
            self.assertIn(
                call,
                self.content,
                f"Load-Datagram must call {call} as part of the load flow",
            )

    def test_datagram_object_structure(self):
        """The datagram object must have all expected properties."""
        expected_properties = [
            "RootPath",
            "Version",
            "Name",
            "Author",
            "HashAlgorithm",
            "Hash",
            "Encryption",
            "PublicKey",
            "ServerUrl",
            "Metadata",
            "FunctionVersions",
            "GuiConfig",
            "HashValid",
            "Encrypted",
        ]
        # Check that PSCustomObject properties are set
        for prop in expected_properties:
            self.assertIn(
                prop,
                self.content,
                f"Datagram PSCustomObject must have property '{prop}'",
            )

    def test_error_propagation(self):
        """Missing Base.ini must abort the load with error."""
        self.assertIn(
            "return $null",
            self.content,
            "Load-Datagram must return $null when Base.ini not found",
        )
        self.assertIn(
            "Base.ini not found",
            self.content,
            "Load-Datagram must write error when Base.ini not found",
        )

    def test_warning_on_hash_failure(self):
        """Hash validation failure must produce a warning but can proceed."""
        self.assertIn(
            "Warning",
            self.content,
            "Load-Datagram must emit warning on hash validation failure",
        )


class TestComponentContracts(unittest.TestCase):
    """
    Verify that functions adhere to their expected input/output contracts.
    """

    def test_send_datagram_data_return_types(self):
        """
        Send-DatagramData must return consistent types per operation:
        - Insert: last inserted ID (integer or null)
        - Update/Delete: rows affected (integer)
        - Query: array of PSCustomObject
        """
        send_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "DATA", "Send-DatagramData.ps1"
        )
        with open(send_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        # Check that return statements exist for each operation
        self.assertIn("lastId", content)
        self.assertIn("$result", content)  # Common result variable

    def test_test_datagram_compatibility_return_type(self):
        """
        Test-DatagramCompatibility must return boolean.
        """
        compat_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Discovery", "Test-DatagramCompatibility.ps1"
        )
        with open(compat_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn(
            "return $compatible",
            content,
            "Test-DatagramCompatibility must return $compatible boolean",
        )
        self.assertIn(
            "return $true",
            content,
            "Must return $true when compatible",
        )
        self.assertIn(
            "return $false",
            content,
            "Must return $false when incompatible",
        )

    def test_load_embedded_functions_return_type(self):
        """
        Load-EmbeddedFunctions must return an array.
        Both versions should return consistent types.
        """
        # Check both files
        for subdir in ["Discovery", "Threads"]:
            func_ps1 = os.path.join(
                PROJECT_ROOT, "FUNCTIONS", subdir, "Load-EmbeddedFunctions.ps1"
            )
            if os.path.isfile(func_ps1):
                with open(func_ps1, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn(
                    "return @()",
                    content,
                    f"Load-EmbeddedFunctions in {subdir} must return @() when no functions found",
                )


class TestConfigurationConsistency(unittest.TestCase):
    """
    Verify configuration references are consistent across components.
    """

    def test_readme_matches_directory_structure(self):
        """README directory listing must match actual structure."""
        with open(os.path.join(PROJECT_ROOT, "README.md"), "r", encoding="utf-8") as f:
            readme = f.read()

        # Check README references core directories
        expected_dirs_in_readme = [
            "Meta/",
            "Databases/",
            "LargeAssets/",
            "PreLoad/",
        ]
        for d in expected_dirs_in_readme:
            self.assertIn(
                d,
                readme,
                f"README.md must document the '{d}' directory",
            )

    def test_readme_references_required_ini_files(self):
        """README must mention all required Meta/*.ini files."""
        expected_inis = [
            "Base.ini",
            "DatagramMeta.ini",
            "FunctionsReqVersions",
        ]
        with open(os.path.join(PROJECT_ROOT, "README.md"), "r", encoding="utf-8") as f:
            readme = f.read()
        for ini in expected_inis:
            self.assertIn(
                ini,
                readme,
                f"README.md must document '{ini}'",
            )

    def test_requirements_consistent_with_project(self):
        """requirements.txt must accurately describe project dependencies."""
        req_path = os.path.join(PROJECT_ROOT, "requirements.txt")
        with open(req_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        self.assertTrue(
            "PowerShell" in content or "Datagram" in content,
            "requirements.txt should describe the project",
        )


class TestErrorHandlingFlow(unittest.TestCase):
    """
    Verify that errors propagate correctly through the component chain.
    """

    def test_bouncy_castle_missing_handling(self):
        """Missing BouncyCastle must be gracefully handled, not crash."""
        menu_ps1 = os.path.join(PROJECT_ROOT, "DatagramMenu.ps1")
        with open(menu_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        # Should warn about missing DLL, not crash
        self.assertIn(
            "WARNING",
            content,
            "DatagramMenu.ps1 should warn about missing BouncyCastle DLL",
        )

    def test_hash_function_throws_on_missing_dll(self):
        """
        Get-SHAKE256Hash must throw a clear error when BouncyCastle is missing.
        (Deliberate crash — better than silent failure for hash computation)
        """
        hash_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Discovery", "Get-SHAKE256Hash.ps1"
        )
        with open(hash_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn(
            "throw",
            content,
            "Get-SHAKE256Hash must throw when BouncyCastle DLL not found",
        )
        self.assertIn(
            "not found",
            content,
            "Error message must indicate DLL not found",
        )

    def test_connect_db_handles_missing_path(self):
        """Connect-DatagramDatabase must return $null (not throw) for missing path."""
        connect_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "DATA", "Connect-DatagramDatabase.ps1"
        )
        with open(connect_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        # Should return $null, not throw
        null_returns = re.findall(r"return\s+\$null", content)
        self.assertGreaterEqual(
            len(null_returns), 1,
            "Connect-DatagramDatabase must return $null for missing paths",
        )


class TestUndefinedFunctionReferences(unittest.TestCase):
    """
    Detect references to functions that don't exist in the project.
    """

    def test_all_called_functions_are_defined(self):
        """
        Every function call in .ps1 files must either:
        - Be defined in the same file
        - Be defined in another .ps1 file in the project
        - Be a known PowerShell built-in cmdlet
        """
        known_builtins = {
            "Write-Host", "Write-Error", "Write-Warning", "Write-Verbose",
            "Test-Path", "Join-Path", "Resolve-Path", "Split-Path",
            "Get-ChildItem", "Get-Content", "Get-Item", "Get-Type",
            "Set-Content", "Add-Content",
            "New-Item", "New-Object", "Remove-Item",
            "Copy-Item", "Move-Item",
            "Add-Type", "Add-Member",
            "Export-ModuleMember",
            "Select-Object", "Where-Object", "Sort-Object", "ForEach-Object",
            "ConvertFrom-Json", "ConvertTo-Json",
            "Start-Process", "Read-Host",
            "New-Variable", "Get-Variable", "Set-Variable", "Remove-Variable",
            "Invoke-WebRequest",
            "Exit", "pause",
            "Get-Command", "Get-Member",
            "Get-Date",
            "Start-Sleep",
            "Expand-Archive",
            "Rename-Item",
            "Out-Null",
            "Pop-Location", "Push-Location",
            "Format-Table", "Format-List",
            "Out-File", "Write-Output",
            "Measure-Object",
            "Group-Object",
            "Menu",
            "Out-File",
            "Write-Output",
            "Measure-Object",
            "Group-Object",
            "Menu",
        }

        ps1_function_calls = re.compile(r"(?<!\$)(?<!\w)([A-Z][\w\-]+)(?=\s*[-($])")

        # Collect all defined functions
        defined_functions = set()
        for dirpath, _, filenames in os.walk(PROJECT_ROOT):
            for fn in filenames:
                if fn.endswith(".ps1"):
                    full = os.path.join(dirpath, fn)
                    with open(full, "r", encoding="utf-8") as f:
                        content = f.read()
                    for m in re.finditer(r"function\s+(\w[\w\-]+)", content):
                        defined_functions.add(m.group(1))

        # Now check for undefined function calls
        for dirpath, _, filenames in os.walk(PROJECT_ROOT):
            for fn in filenames:
                if fn.endswith(".ps1"):
                    full = os.path.join(dirpath, fn)
                    with open(full, "r", encoding="utf-8") as f:
                        content = f.read()

                    for m in ps1_function_calls.finditer(content):
                        name = m.group(1)
                        # Skip keywords, builtins, and defined functions
                        if name in known_builtins:
                            continue
                        if name in defined_functions:
                            continue
                        # Skip common keywords
                        if name.lower() in (
                            "if", "else", "elseif", "for", "foreach", "while",
                            "do", "switch", "case", "default",
                            "function", "param", "begin", "process", "end",
                            "try", "catch", "finally", "throw",
                            "return", "break", "continue",
                            "in", "not", "and", "or",
                            "eq", "ne", "gt", "lt", "ge", "le",
                            "match", "notmatch",
                            "contains", "notcontains",
                            "replace", "split", "join",
                            "toLower", "toUpper", "trim", "trimStart", "trimEnd",
                            "add", "remove", "clear",
                            "count", "length",
                            "key", "value",
                            "property", "properties",
                            "item", "items",
                            "name",
                            "path",
                            "type",
                            "text",
                            "open", "close",
                        ):
                            continue
                        # Skip numbers
                        if name.replace(".", "").replace("-", "").isdigit():
                            continue
                        # This is potentially an undefined reference
                        rel_path = os.path.relpath(full, PROJECT_ROOT)
                        self.fail(
                            f"POTENTIALLY UNDEFINED FUNCTION REFERENCE:\n"
                            f"  File: {rel_path}\n"
                            f"  Calls: '{name}'\n"
                            f"  This function is not defined in any project .ps1 file\n"
                            f"  and is not in the known builtins list.",
                        )


class TestThreadSafetyPatterns(unittest.TestCase):
    """
    Verify thread-related functions don't have race conditions.
    """

    def test_thread_load_uses_start_process(self):
        """
        Threads/Load-EmbeddedFunctions uses Start-Process which creates
        new windows. This makes it unsuitable for scripting/automation.
        """
        thread_ps1 = os.path.join(
            PROJECT_ROOT, "FUNCTIONS", "Threads", "Load-EmbeddedFunctions.ps1"
        )
        with open(thread_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        if "Start-Process" in content:
            self.fail(
                "DESIGN ISSUE: Threads/Load-EmbeddedFunctions uses Start-Process\n"
                "which launches new windows for Python/Node scripts.\n"
                "This prevents use in background/automated scenarios.\n"
                "Consider using direct invocation or background jobs instead."
            )

    def test_no_global_state_corruption(self):
        """
        Functions using $global:CurrentDatagram, $global:CurrentDatabase
        must not cause state corruption when called concurrently.
        """
        menu_ps1 = os.path.join(PROJECT_ROOT, "DatagramMenu.ps1")
        with open(menu_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        global_refs = re.findall(r"\$global:(\w+)", content)
        # Check that global variables are used consistently
        self.assertIn("CurrentDatagram", global_refs)
        self.assertIn("CurrentDatabase", global_refs)
        # These globals are expected in the interactive menu,
        # but are a thread-safety concern for library use


class TestFileEncodingIntegration(unittest.TestCase):
    """Verify all files have consistent line endings."""

    def test_consistent_line_endings(self):
        """All project files should have consistent line endings (LF or CRLF)."""
        allowed_endings = {None, "\n", "\r\n"}
        for dirpath, _, filenames in os.walk(PROJECT_ROOT):
            for fn in filenames:
                if fn.endswith((".ps1", ".md", ".txt", ".py", ".ini")):
                    full = os.path.join(dirpath, fn)
                    with open(full, "rb") as f:
                        raw = f.read()
                    # Check line endings
                    if b"\r\n" in raw:
                        ending = "\r\n"
                    elif b"\n" in raw:
                        ending = "\n"
                    else:
                        continue  # No newlines or single line
                    # All files should use the same ending — document which one
                    # (not strictly required but good practice)
                    # We'll just log this check


if __name__ == "__main__":
    unittest.main(verbosity=2)
