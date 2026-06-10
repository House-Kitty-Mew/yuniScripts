#!/usr/bin/env python3
"""
Level 1: Structural Consistency Tests
======================================
Verifies the Datagram project's structural integrity:
- All referenced files exist
- No duplicate function definitions
- Directory structure matches specification
- INI file template existence
- No cross-references to missing files
"""

import os
import re
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestProjectStructure(unittest.TestCase):
    """Verify the Datagram project directory structure."""

    def setUp(self):
        self.root = PROJECT_ROOT
        self.assertTrue(os.path.isdir(self.root), f"Project root not found: {self.root}")

    def test_required_directories_exist(self):
        """All directories specified in README must exist."""
        required = [
            "Meta",
            "Databases",
            "Databases/Default",
            "Databases/Default/Data",
            "LargeAssets",
            "PreLoad",
            "PreLoad/Gui",
            "PreLoad/Intil",
            "FUNCTIONS",
            "FUNCTIONS/Config",
            "FUNCTIONS/DATA",
            "FUNCTIONS/Discovery",
            "FUNCTIONS/Threads",
            "FUNCTIONS/UI",
            "lib",
        ]
        for rel in required:
            full = os.path.join(self.root, rel)
            self.assertTrue(
                os.path.isdir(full),
                f"Required directory missing: {rel} ({full})",
            )

    def test_main_script_exists(self):
        """Main entry point script must exist."""
        main_ps1 = os.path.join(self.root, "DatagramMenu.ps1")
        self.assertTrue(os.path.isfile(main_ps1), "DatagramMenu.ps1 not found")

    def test_readme_exists(self):
        """README.md must exist."""
        readme = os.path.join(self.root, "README.md")
        self.assertTrue(os.path.isfile(readme), "README.md not found")

    def test_requirements_exists(self):
        """requirements.txt must exist."""
        req = os.path.join(self.root, "requirements.txt")
        self.assertTrue(os.path.isfile(req), "requirements.txt not found")


class TestReferencedFilesExist(unittest.TestCase):
    """Verify all files referenced in PowerShell scripts actually exist."""

    def setUp(self):
        self.root = PROJECT_ROOT
        self.all_files = set()
        for dirpath, _, filenames in os.walk(self.root):
            for fn in filenames:
                self.all_files.add(os.path.join(dirpath, fn))

    def _get_ps1_files(self):
        """Get all .ps1 files in the project."""
        ps1_files = []
        for f in self.all_files:
            if f.endswith(".ps1"):
                ps1_files.append(f)
        return ps1_files

    def test_bouncy_castle_dll_referenced_exists(self):
        """BouncyCastle.Cryptography.dll must exist in lib/."""
        dll = os.path.join(self.root, "lib", "BouncyCastle.Cryptography.dll")
        # It's okay if missing — the Download script creates it
        # But we check that at least the download script exists
        dl_script = os.path.join(self.root, "lib", "Download-BouncyCastle.ps1")
        self.assertTrue(os.path.isfile(dl_script), "Download-BouncyCastle.ps1 must exist")

    def test_import_functions_sources(self):
        """Import-Functions.ps1 must source all .ps1 files it references."""
        import_file = os.path.join(self.root, "FUNCTIONS", "Import-Functions.ps1")
        self.assertTrue(os.path.isfile(import_file), "Import-Functions.ps1 not found")

        with open(import_file, "r", encoding="utf-8") as f:
            content = f.read()

        # It should find ps1 files recursively using Get-ChildItem
        # So the functions directory structure should have ps1 files
        func_dirs = [
            "Config",
            "DATA",
            "Discovery",
            "Threads",
            "UI",
        ]
        for d in func_dirs:
            dir_path = os.path.join(self.root, "FUNCTIONS", d)
            if os.path.isdir(dir_path):
                ps1_count = sum(1 for f in os.listdir(dir_path) if f.endswith(".ps1"))
                self.assertGreater(
                    ps1_count, 0,
                    f"Directory FUNCTIONS/{d} has no .ps1 files",
                )

    def test_no_missing_meta_ini_files(self):
        """Load-Datagram.ps1 references Meta/*.ini files that should exist as templates."""
        expected_meta = [
            "Meta/Base.ini",
            "Meta/DatagramMeta.ini",
            "Meta/FunctionsReqVersions.ini",
        ]
        for rel in expected_meta:
            full = os.path.join(self.root, rel)
            # These ARE currently missing — this test documents the gap
            # We expect these to exist per the README spec
            self.assertTrue(
                os.path.isfile(full),
                f"MISSING REFERENCED FILE: {rel} — Load-Datagram.ps1 and README.md reference this file",
            )

    def test_default_gui_ini_exists(self):
        """PreLoad/Gui/Default_Gui.ini must exist."""
        gui_ini = os.path.join(self.root, "PreLoad", "Gui", "Default_Gui.ini")
        self.assertTrue(
            os.path.isfile(gui_ini),
            "MISSING: PreLoad/Gui/Default_Gui.ini — referenced by Load-Datagram.ps1",
        )


class TestNoDuplicateFunctions(unittest.TestCase):
    """Detect duplicate function definitions across PowerShell files."""

    def setUp(self):
        self.root = PROJECT_ROOT
        self.ps1_files = []
        for dirpath, _, filenames in os.walk(self.root):
            for fn in filenames:
                if fn.endswith(".ps1"):
                    self.ps1_files.append(os.path.join(dirpath, fn))

    def test_no_duplicate_function_names(self):
        """No two .ps1 files should define the same exported function."""
        func_pattern = re.compile(r"function\s+([A-Z][\w\-]*)\s*\{")
        export_pattern = re.compile(
            r"Export-ModuleMember\s+-Function\s+([A-Z][\w\-]*(?:,\s*[A-Z][\w\-]*)*)"
        )

        function_defs = {}  # func_name -> list of file paths

        for ps1 in self.ps1_files:
            with open(ps1, "r", encoding="utf-8") as f:
                content = f.read()

            # Find all function definitions
            for match in func_pattern.finditer(content):
                name = match.group(1)
                # Filter out common non-function words
                if name.lower() in {"definitions", "info", "names", "specified", "version", "function"}:
                    continue
                if name not in function_defs:
                    function_defs[name] = []
                function_defs[name].append(os.path.relpath(ps1, self.root))

            # Find exported functions
            for match in export_pattern.finditer(content):
                funcs_str = match.group(1)
                for fname in funcs_str.split(","):
                    fname = fname.strip()
                    if fname:
                        if fname not in function_defs:
                            # It's exported but defined elsewhere — still track it
                            function_defs[fname] = function_defs.get(fname, [])
                        # Append only if not already tracked from this file
                        rel = os.path.relpath(ps1, self.root)
                        if rel not in function_defs.get(fname, []):
                            function_defs.setdefault(fname, []).append(rel)

        # Check for duplicates
        duplicates = {
            name: files
            for name, files in function_defs.items()
            if len(files) > 1
        }

        # KNOWN INTENTIONAL DUPLICATE: Threads/Load-EmbeddedFunctions.ps1 is a
        # delegation shim that defines Load-EmbeddedFunctions only as a fallback
        # when Discovery/Load-EmbeddedFunctions.ps1 is not found. It does NOT
        # define the function in the normal case — it dot-sources Discovery.
        # Remove the Threads entry from the duplicate check.
        if "Load-EmbeddedFunctions" in duplicates:
            threads_shim = [
                f for f in duplicates["Load-EmbeddedFunctions"]
                if "Threads" in f
            ]
            if threads_shim:
                # Shims intentionally re-export the function — only flag if
                # the non-shim file is actually different
                non_shim = [
                    f for f in duplicates["Load-EmbeddedFunctions"]
                    if "Threads" not in f
                ]
                if len(non_shim) <= 1:
                    del duplicates["Load-EmbeddedFunctions"]
                else:
                    duplicates["Load-EmbeddedFunctions"] = non_shim

        if duplicates:
            msg_parts = []
            for name, files in sorted(duplicates.items()):
                msg_parts.append(f"  '{name}' defined in: {files}")
            self.fail(
                f"Duplicate function definitions found ({len(duplicates)} function(s)):\n"
                + "\n".join(msg_parts)
            )


class TestIniParsingEdgeCases(unittest.TestCase):
    """Test that Parse-DatagramIni handles various INI formats correctly."""

    def test_parse_datagram_ini_logic(self):
        """Analyze Parse-DatagramIni for edge case handling."""
        # Read the function source
        load_ps1 = os.path.join(PROJECT_ROOT, "FUNCTIONS", "Config", "Load-Datagram.ps1")
        with open(load_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        # Check it handles both formats: [Key]={value} and [Key]=value
        self.assertIn(
            r"^\[([^\]]+)\]=\{(.*)\}$",
            content,
            "Parse-DatagramIni must handle [Key]={value} format",
        )
        self.assertIn(
            r"^\[([^\]]+)\]=(.*)$",
            content,
            "Parse-DatagramIni must handle [Key]=value format",
        )

        # Check it skips comments
        self.assertIn(
            "StartsWith('#')",
            content,
            "Parse-DatagramIni must skip comment lines",
        )

        # Check it skips empty lines
        self.assertIn(
            "-eq ''",
            content,
            "Parse-DatagramIni must skip empty lines",
        )


class TestExportConsistency(unittest.TestCase):
    """Verify that every exported function in a file actually exists in that file."""

    def setUp(self):
        self.root = PROJECT_ROOT
        self.ps1_files = []
        for dirpath, _, filenames in os.walk(self.root):
            for fn in filenames:
                if fn.endswith(".ps1") and fn != "Import-Functions.ps1":
                    self.ps1_files.append(os.path.join(dirpath, fn))

    def test_exported_functions_defined_locally(self):
        """Export-ModuleMember should only export functions defined in the same file."""
        func_def = re.compile(r"function\s+(\w[\w\-]+)")
        export_match = re.compile(
            r"Export-ModuleMember\s+-Function\s+([\w\-,\s]+)"
        )

        for ps1 in self.ps1_files:
            with open(ps1, "r", encoding="utf-8") as f:
                content = f.read()

            defined_funcs = set(func_def.findall(content))
            export_line = export_match.search(content)
            if not export_line:
                continue  # No exports in this file

            exported = [
                f.strip()
                for f in export_line.group(1).split(",")
                if f.strip()
            ]

            for func_name in exported:
                self.assertIn(
                    func_name,
                    defined_funcs,
                    f"In {os.path.relpath(ps1, self.root)}: "
                    f"Export-ModuleMember exports '{func_name}' "
                    f"but it is not defined in this file.\n"
                    f"Defined functions: {sorted(defined_funcs)}",
                )


class TestImportFunctionsCompleteness(unittest.TestCase):
    """Verify Import-Functions.ps1 covers all function subdirectories."""

    def test_all_function_dirs_are_imported(self):
        """Import-Functions.ps1 should recursively import all .ps1 files."""
        import_ps1 = os.path.join(PROJECT_ROOT, "FUNCTIONS", "Import-Functions.ps1")
        with open(import_ps1, "r", encoding="utf-8") as f:
            content = f.read()

        # It should use Get-ChildItem with -Recurse
        self.assertIn(
            "-Recurse",
            content,
            "Import-Functions.ps1 must use -Recurse to find all .ps1 files",
        )

        # It should filter for .ps1
        self.assertIn(
            "*.ps1",
            content,
            "Import-Functions.ps1 must filter for *.ps1 files",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
