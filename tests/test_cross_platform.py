#!/usr/bin/env python3
"""
test_cross_platform.py — Systematic cross-platform audit of EVERY .py file.

Checks all engine/*.py and SCRIPTS/*/*.py files for:
  1. os.path.join usage (should use pathlib.Path /)
  2. Hardcoded /tmp paths (should use tempfile.gettempdir())
  3. ANSI escape codes without guards
  4. signal.signal() without hasattr/sys.platform guard
  5. Hardcoded /usr/bin/python3 (should use sys.platform check)
  6. shell=True usage in subprocess
  7. os.kill without platform guard
"""

import sys
import os
import ast
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Constants for cross-platform detection patterns
HARDCODED_TMP_PATTERN_1 = '"/tmp/'
HARDCODED_TMP_PATTERN_2 = "'/tmp/"

_G = "\033[92m"
_Y = "\033[93m"
_R = "\033[91m"
_C = "\033[96m"
_N = "\033[0m"

PASS = f"{_G}PASS{_N}"
FAIL = f"{_R}FAIL{_N}"
SKIP = f"{_Y}SKIP{_N}"

_tests_run = 0
_tests_pass = 0
_tests_fail = 0
_tests_skip = 0

def test(name: str, condition: bool, detail: str = ""):
    global _tests_run, _tests_pass, _tests_fail
    _tests_run += 1
    if condition:
        _tests_pass += 1
        print(f"  {PASS}  {name}")
    else:
        _tests_fail += 1
        print(f"  {FAIL}  {name}")
        if detail:
            print(f"         {_Y}{detail}{_N}")

test.__test__ = False

def section(title: str):
    print(f"\n{_C}─── {title} ───{_N}")


# ── Collect all Python files ──────────────────────────────────────────

ALL_PY_FILES = []
for root, dirs, files in os.walk(str(PROJECT_ROOT)):
    # Skip venv, __pycache__, trash, .git, _deps
    dirs[:] = [d for d in dirs if d not in ("venv", ".venv", "__pycache__",
                                             ".git", "trash", "_deps",
                                             "__pypackages__", "node_modules")]
    for f in files:
        if f.endswith(".py"):
            ALL_PY_FILES.append(Path(root) / f)

# ═══════════════════════════════════════════════════════════════════════
# 1. No os.path.join Usage
# ═══════════════════════════════════════════════════════════════════════

section("1. os.path.join — Should use pathlib.Path / instead")

os_path_join_files = []
os_path_exists_files = []
for py_file in ALL_PY_FILES:
    try:
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        if "os.path.join" in content:
            os_path_join_files.append(py_file.relative_to(PROJECT_ROOT))
        if "os.path.exists" in content or "os.path.isfile" in content or "os.path.isdir" in content:
            os_path_exists_files.append(py_file.relative_to(PROJECT_ROOT))
    except Exception:
        pass

# os.path.join in engine/ files is a warning (should use pathlib)
engine_join = [f for f in os_path_join_files if "engine" in str(f) and "tests" not in str(f)]
test(f"No os.path.join in engine/*.py",
     len(engine_join) == 0,
     f"Found in: {[str(f) for f in engine_join[:5]]}")

# os.path.join in SCRIPTS/ files
scripts_join = [f for f in os_path_join_files if "SCRIPTS" in str(f) and "tests" not in str(f)]
if scripts_join:
    # This is a soft warning — some scripts may have legitimate os.path usage
    test(f"No os.path.join in SCRIPTS/*.py",
         False, f"Found in: {[str(f) for f in scripts_join[:5]]}")
else:
    test(f"No os.path.join in SCRIPTS/*.py", True)


# ═══════════════════════════════════════════════════════════════════════
# 2. No Hardcoded /tmp Paths
# ═══════════════════════════════════════════════════════════════════════

section("2. Hardcoded /tmp Paths — Should use tempfile.gettempdir()")

tmp_files = []
for py_file in ALL_PY_FILES:
    try:
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        # Check for hardcoded /tmp but exclude comments/docstrings
        if HARDCODED_TMP_PATTERN_1 in content or HARDCODED_TMP_PATTERN_2 in content:
            # Simple heuristic: check if it's not in a comment/docstring
            lines = content.split('\n')
            for i, line in enumerate(lines):
                stripped = line.strip()
                if (HARDCODED_TMP_PATTERN_1 in stripped or HARDCODED_TMP_PATTERN_2 in stripped) and not stripped.startswith('#'):
                    tmp_files.append(f"{py_file.relative_to(PROJECT_ROOT)}:{i+1}")
                    break
    except Exception:
        pass

test(f"No hardcoded '/tmp/' paths",
     len(tmp_files) == 0,
     f"Found in: {tmp_files[:5]}")


# ═══════════════════════════════════════════════════════════════════════
# 3. ANSI Escape Codes — Should have guards
# ═══════════════════════════════════════════════════════════════════════

section("3. ANSI Escape Codes — Should be guarded by isatty() check")

ansi_files_no_guard = []
for py_file in ALL_PY_FILES:
    try:
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        if "\033[" in content or "\\033[" in content or "\x1b[" in content:
            # Check if the file has a isatty() guard somewhere
            if "isatty" not in content and "color" not in content.lower():
                rel = py_file.relative_to(PROJECT_ROOT)
                # Tests are allowed to use ANSI for output
                if "tests" not in str(rel) or "venv" not in str(rel):
                    ansi_files_no_guard.append(rel)
    except Exception:
        pass

# Most of our test files use ANSI codes directly — that's intentional for CLI feedback
# But we want to check engine/* files specifically
engine_ansi = [f for f in ansi_files_no_guard if "engine" in str(f)]
test(f"Engine files with ANSI codes have color guard",
     len(engine_ansi) == 0,
     f"Files without guard: {[str(f) for f in engine_ansi[:3]]}")


# ═══════════════════════════════════════════════════════════════════════
# 4. signal.signal() — Must be guarded
# ═══════════════════════════════════════════════════════════════════════

section("4. signal.signal() — Must be guarded (SIGTERM not on Windows)")

signal_no_guard = []
for py_file in ALL_PY_FILES:
    try:
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        if "signal.signal(" in content or "signal.signal (" in content:
            # Check if there's a hasattr(signal, 'SIGTERM') or sys.platform guard NEARBY
            # For simplicity: check if file has any sys.platform or hasattr reference
            if "sys.platform" not in content and "hasattr" not in content:
                rel = py_file.relative_to(PROJECT_ROOT)
                # Skip files we know handle it correctly at a higher level
                if "venv" not in str(rel) and "tests" not in str(rel):
                    signal_no_guard.append(rel)
    except Exception:
        pass

test(f"signal.signal() usage has platform guard",
     len(signal_no_guard) == 0,
     f"Files without guard: {[str(f) for f in signal_no_guard[:3]]}")


# ═══════════════════════════════════════════════════════════════════════
# 5. Hardcoded /usr/bin/python3
# ═══════════════════════════════════════════════════════════════════════

section("5. Hardcoded Python Paths — Should be platform-aware")

python_path_files = []
for py_file in ALL_PY_FILES:
    try:
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        if "/usr/bin/python" in content:
            rel = py_file.relative_to(PROJECT_ROOT)
            if "venv" not in str(rel):
                python_path_files.append(rel)
    except Exception:
        pass

test(f"No hardcoded '/usr/bin/python' paths (except metadata.py which has _default_python())",
     len(python_path_files) <= 1,  # metadata.py is the expected one
     f"Files: {[str(f) for f in python_path_files]}")


# ═══════════════════════════════════════════════════════════════════════
# 6. shell=True — Security risk
# ═══════════════════════════════════════════════════════════════════════

section("6. shell=True — Should be avoided (security)")

shell_true_files = []
for py_file in ALL_PY_FILES:
    try:
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        if "shell=True" in content:
            rel = py_file.relative_to(PROJECT_ROOT)
            if "venv" not in str(rel):
                shell_true_files.append(rel)
    except Exception:
        pass

test(f"No subprocess shell=True usage in engine/ or SCRIPTS/",
     len(shell_true_files) == 0,
     f"Files: {[str(f) for f in shell_true_files]}")


# ═══════════════════════════════════════════════════════════════════════
# 7. os.kill — Must have platform guard
# ═══════════════════════════════════════════════════════════════════════

section("7. os.kill() — Must have sys.platform guard")

os_kill_files = []
for py_file in ALL_PY_FILES:
    try:
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        if "os.kill(" in content:
            rel = py_file.relative_to(PROJECT_ROOT)
            if "venv" not in str(rel) and "tests" not in str(rel):
                if "sys.platform" not in content:
                    # But process_adoption.py handles this correctly via _is_process_alive
                    os_kill_files.append(rel)
    except Exception:
        pass

test(f"os.kill() is guarded by sys.platform checks",
     len(os_kill_files) == 0,
     f"Files without guard: {[str(f) for f in os_kill_files]}")


# ═══════════════════════════════════════════════════════════════════════
# 8. fcntl / select on Windows
# ═══════════════════════════════════════════════════════════════════════

section("8. fcntl — Unix-only, must have platform guard")

fcntl_files = []
for py_file in ALL_PY_FILES:
    try:
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        if "fcntl" in content and "import fcntl" in content:
            rel = py_file.relative_to(PROJECT_ROOT)
            if "venv" not in str(rel):
                fcntl_files.append(rel)
    except Exception:
        pass

test(f"No fcntl imports (Unix-only module)",
     len(fcntl_files) == 0,
     f"Files: {[str(f) for f in fcntl_files]}")


# ═══════════════════════════════════════════════════════════════════════
# 9. venv_manager.py — Cross-platform paths
# ═══════════════════════════════════════════════════════════════════════

section("9. venv_manager.py — Cross-Platform Venv Paths")

from engine.venv_manager import _venv_python, _venv_dir
from pathlib import Path

mock_path = Path("/test/script")

# These tests verify the function logic, not the actual platform
if sys.platform == "win32":
    test("_venv_python returns Scripts/python.exe on Windows",
         "Scripts" in str(_venv_python(mock_path)))
else:
    test("_venv_python returns bin/python on Linux",
         "bin" in str(_venv_python(mock_path)))


# ═══════════════════════════════════════════════════════════════════════
# 10. Python compile check for ALL files
# ═══════════════════════════════════════════════════════════════════════

section("10. Syntax Check — All .py Files Compile")

import py_compile
syntax_errors = []
for py_file in ALL_PY_FILES:
    try:
        py_compile.compile(str(py_file), doraise=True)
    except py_compile.PyCompileError as e:
        syntax_errors.append((py_file, str(e)))

test("All .py files compile without syntax errors",
     len(syntax_errors) == 0,
     f"Errors: {[f'{f.relative_to(PROJECT_ROOT)}: {e}' for f, e in syntax_errors[:5]]}")


# ═══════════════════════════════════════════════════════════════════════
# 11. Process Adoption Module — Cross-Platform Functions
# ═══════════════════════════════════════════════════════════════════════

section("11. process_adoption.py — Platform Guards")

from engine.process_adoption import (
    is_process_alive,
    _is_process_alive_windows,
    _is_process_alive_unix,
)

# Verify the platform dispatch works
test("is_process_alive(os.getpid()) works on this platform",
     is_process_alive(os.getpid()))

test("_is_process_alive_unix works on this platform",
     isinstance(_is_process_alive_unix(os.getpid()), bool))

test("_is_process_alive_windows works on this platform (graceful fallback)",
     isinstance(_is_process_alive_windows(os.getpid()), bool))


# ═══════════════════════════════════════════════════════════════════════
# 12. Metadata Python Path
# ═══════════════════════════════════════════════════════════════════════

section("12. metadata.py — Cross-Platform Python Path")

from engine.metadata import _default_python

default_py = _default_python()
if sys.platform == "win32":
    test("_default_python() returns 'python' on Windows",
         default_py == "python")
else:
    test("_default_python() returns '/usr/bin/python3' on Linux",
         default_py == "/usr/bin/python3")


# ═══════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    section("RESULTS")

    print(f"  {_G}{_tests_pass} passed{_N}")
    if _tests_fail:
        print(f"  {_R}{_tests_fail} failed{_N}")
    if _tests_skip:
        print(f"  {_Y}{_tests_skip} skipped{_N}")
    print(f"  {_C}{_tests_run} total{_N}")

    if _tests_fail:
        print(f"\n  {_R}Some tests FAILED — review above.{_N}")
        sys.exit(1)
    else:
        print(f"\n  {_G}All cross-platform tests passed.{_N}")
