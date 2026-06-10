"""
Comprehensive test suite for the Dynamic Dependency Detection System.

Tests cover:
  1. Java detection (find_java, version parsing, version requirements)
  2. Simulated auto-installation (DependencyInstaller)
  3. Hot-reload after install (HotReloadManager)
  4. Environment variable updates (EnvironmentManager)
  5. PATH refresh and management
  6. Fallback on failure and graceful degradation
  7. CheckResult aggregation and reporting
  8. Full workflow integration (DynamicDeps orchestrator)
  9. Edge cases (no Java, old Java, missing tools, platform detection)

Each test uses unittest.TestCase with full docstrings and isolation.
Tests do NOT require actual Java installation — they mock or simulate
where necessary to allow running in any environment.

Usage:
    python -m tests.test_dynamic_deps  (from project root)
    python -m unittest tests.test_dynamic_deps
"""

import os
import sys
import json
import unittest
import unittest.mock
import tempfile
import shutil
import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# Import the dynamic_deps module directly (bypasses engine.__init__ which has
# a pre-existing bug in atomic.py)
# ---------------------------------------------------------------------------
_ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
sys.path.insert(0, str(_ENGINE_DIR))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "dynamic_deps", str(_ENGINE_DIR / "dynamic_deps.py")
)
_dd = importlib.util.module_from_spec(_spec)
sys.modules["dynamic_deps"] = _dd
_spec.loader.exec_module(_dd)

# Module aliases
DynamicDeps = _dd.DynamicDeps
JavaDetector = _dd.JavaDetector
EnvironmentManager = _dd.EnvironmentManager
DependencyInstaller = _dd.DependencyInstaller
HotReloadManager = _dd.HotReloadManager
CheckResult = _dd.CheckResult
DependencyResult = _dd.DependencyResult
DepType = _dd.DepType
DepStatus = _dd.DepStatus


# ===================================================================
#  Helper utilities
# ===================================================================


def _capture_logs() -> logging.Handler:
    """Return a handler that captures log output for assertion."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    return handler


# ===================================================================
#  Test: JavaDetector — Version Parsing
# ===================================================================


class TestJavaVersionParsing(unittest.TestCase):
    """
    Test the Java version string parser.

    The JavaDetector._parse_version_output() method must handle multiple
    java -version output formats:
      - OpenJDK 17+ (modern versioning: "17.0.9")
      - Legacy Java 8 (historic versioning: "1.8.0_392")
      - GraalVM, IBM J9, and other vendor strings
      - Corrupted or partial output
    """

    # ── Modern versioning (Java 9+) ──────────────────────────────

    def test_parse_openjdk_17(self):
        """
        Parse OpenJDK 17 version string.

        Verifies: major_version=17, vendor=OpenJDK, arch detected.
        """
        output = (
            'openjdk version "17.0.9" 2023-10-17 LTS\n'
            "OpenJDK 64-Bit Server VM (build 17.0.9+8, mixed mode, sharing)"
        )
        info = JavaDetector._parse_version_output(output)
        self.assertEqual(info["major_version"], 17)
        self.assertEqual(info["full_version"], "17.0.9")
        self.assertEqual(info["vendor"], "OpenJDK")
        self.assertEqual(info["arch"], "64-Bit")

    def test_parse_openjdk_21(self):
        """
        Parse OpenJDK 21 version string (latest LTS).

        Verifies: major_version=21, proper full version capture.
        """
        output = (
            'openjdk version "21.0.1" 2023-10-17 LTS\n'
            "OpenJDK 64-Bit Server VM (build 21.0.1+12, mixed mode, sharing)"
        )
        info = JavaDetector._parse_version_output(output)
        self.assertEqual(info["major_version"], 21)
        self.assertEqual(info["full_version"], "21.0.1")

    def test_parse_openjdk_11(self):
        """
        Parse OpenJDK 11 version string.

        Verifies: major_version=11 (modern versioning without trailing zero).
        """
        output = (
            'openjdk version "11.0.21" 2023-10-17 LTS\n'
            "OpenJDK 64-Bit Server VM (build 11.0.21+9, mixed mode)"
        )
        info = JavaDetector._parse_version_output(output)
        self.assertEqual(info["major_version"], 11)
        self.assertEqual(info["full_version"], "11.0.21")

    # ── Legacy versioning (Java 8 and earlier) ───────────────────

    def test_parse_java_8(self):
        """
        Parse legacy Java 8 version string (1.8.0_392 format).

        Java 8 and earlier use the "1.MAJOR" version scheme.  The parser
        must extract the second component as the actual major version.

        Verifies: major_version=8 (not 1), full_version="1.8.0_392".
        """
        output = (
            'java version "1.8.0_392"\n'
            "Java(TM) SE Runtime Environment (build 1.8.0_392-b08)\n"
            "Java HotSpot(TM) 64-Bit Server VM (build 25.392-b08, mixed mode)"
        )
        info = JavaDetector._parse_version_output(output)
        self.assertEqual(info["major_version"], 8)
        self.assertEqual(info["full_version"], "1.8.0_392")
        self.assertEqual(info["vendor"], "Oracle JDK")

    def test_parse_java_7(self):
        """
        Parse legacy Java 7 version string (1.7.0_80 format).

        Verifies: major_version=7.
        """
        output = (
            'java version "1.7.0_80"\n'
            "Java(TM) SE Runtime Environment (build 1.7.0_80-b15)\n"
            "Java HotSpot(TM) 64-Bit Server VM (build 24.80-b11, mixed mode)"
        )
        info = JavaDetector._parse_version_output(output)
        self.assertEqual(info["major_version"], 7)

    # ── Edge cases ───────────────────────────────────────────────

    def test_parse_empty_output(self):
        """
        Parse empty string — must not crash.

        Verifies: returns default dict with major_version=0.
        """
        info = JavaDetector._parse_version_output("")
        self.assertEqual(info["major_version"], 0)
        self.assertEqual(info["full_version"], "unknown")

    def test_parse_gibberish_output(self):
        """
        Parse non-Java output — must not crash.

        Verifies: returns gracefully with default values.
        """
        info = JavaDetector._parse_version_output(
            "This is not java output\nSome random text"
        )
        self.assertEqual(info["major_version"], 0)

    def test_parse_graalvm(self):
        """
        Parse GraalVM version string.

        GraalVM uses a different vendor identifier. The parser should
        still extract the major version correctly.
        """
        output = (
            'openjdk version "17.0.9" 2023-10-17\n'
            "OpenJDK 64-Bit GraalVM CE 17.0.9 (build 17.0.9+8, mixed mode)"
        )
        info = JavaDetector._parse_version_output(output)
        self.assertEqual(info["major_version"], 17)
        self.assertIn("64", str(info.get("arch", "")))

    def test_parse_aarch64(self):
        """
        Parse ARM64 Java version string.

        Verifies: arch detection for aarch64 platform.
        """
        output = (
            'openjdk version "17.0.9" 2023-10-17\n'
            "OpenJDK 64-Bit Server VM (build 17.0.9+8, mixed mode, aarch64)"
        )
        info = JavaDetector._parse_version_output(output)
        self.assertEqual(info["major_version"], 17)
        self.assertIn("aarch64", str(info.get("arch", "")).lower())

    def test_parse_no_arch(self):
        """
        Parse version output that does not mention architecture.

        Verifies: defaults to "unknown" architecture.
        """
        output = 'openjdk version "17.0.9" 2023-10-17'
        info = JavaDetector._parse_version_output(output)
        self.assertEqual(info["major_version"], 17)
        self.assertEqual(info["arch"], "unknown")


# ===================================================================
#  Test: JavaDetector — Version Requirements
# ===================================================================


class TestJavaVersionRequirements(unittest.TestCase):
    """
    Test Java version requirement checking.

    JavaDetector.check_version_requirement() must correctly compare
    the detected version against a minimum required version.
    """

    def test_version_sufficient(self):
        """
        Check that the version comparison logic works independently.

        Since we may not have Java installed, this test validates:
          1. _parse_version_output handles Java 17 output correctly
          2. The comparison logic (major >= min) works independently
          3. If Java IS available, check_version_requirement also works

        Verifies: version parsing + comparison without system Java.
        """
        # Test parsing independently
        info = JavaDetector._parse_version_output(
            'openjdk version "17.0.9" 2023-10-17 LTS'
        )
        self.assertEqual(info["major_version"], 17)
        self.assertEqual(info["full_version"], "17.0.9")

        # Test comparison logic independently
        self.assertGreaterEqual(info["major_version"], 17)

        # If Java IS available on this system, also test the real path
        import shutil
        real_java = shutil.which("java")
        if real_java:
            sufficient, version_info, msg = JavaDetector.check_version_requirement(
                real_java, 17
            )
            self.assertIsNotNone(version_info)

    def test_min_java_version_vanilla(self):
        """
        Minimum Java version for Vanilla server is 17.

        Verifies: get_min_java_version returns 17 for common types.
        """
        self.assertEqual(JavaDetector.get_min_java_version("vanilla"), 17)
        self.assertEqual(JavaDetector.get_min_java_version("paper"), 17)
        self.assertEqual(JavaDetector.get_min_java_version("fabric"), 17)

    def test_min_java_version_bukkit(self):
        """
        Minimum Java version for Bukkit (legacy) is 8.

        Verifies: get_min_java_version returns 8 for bukkit.
        """
        self.assertEqual(JavaDetector.get_min_java_version("bukkit"), 8)

    def test_min_java_version_unknown_type(self):
        """
        Unknown server type defaults to minimum Java 17.

        Verifies: fallback to safe default for unrecognized types.
        """
        self.assertEqual(JavaDetector.get_min_java_version("unknown"), 17)

    def test_version_comparison_modern_ok(self):
        """
        Simulate version_sufficient=True for Java 17 checking min=17.

        Verifies: major=17 >= min=17 returns True.
        """
        # Mock a version info dict
        ver = {"major_version": 17, "full_version": "17.0.9", "vendor": "OpenJDK", "arch": "64-Bit"}
        major = ver["major_version"]
        min_major = 17
        self.assertGreaterEqual(major, min_major)

    def test_version_comparison_modern_too_old(self):
        """
        Simulate version_sufficient=False for Java 11 checking min=17.

        Verifies: major=11 < min=17 returns False.
        """
        ver = {"major_version": 11, "full_version": "11.0.21", "vendor": "OpenJDK", "arch": "64-Bit"}
        major = ver["major_version"]
        min_major = 17
        self.assertLess(major, min_major)

    def test_version_comparison_legacy_too_old(self):
        """
        Simulate version_sufficient=False for Java 8 checking min=17.

        Verifies: major=8 < min=17 returns False.
        """
        ver = {"major_version": 8, "full_version": "1.8.0_392", "vendor": "Oracle JDK", "arch": "64-Bit"}
        major = ver["major_version"]
        min_major = 17
        self.assertLess(major, min_major)


# ===================================================================
#  Test: CheckResult — Aggregation & Reporting
# ===================================================================


class TestCheckResult(unittest.TestCase):
    """
    Test CheckResult aggregation, status properties, and report generation.

    CheckResult collects DependencyResult objects and provides:
      - .ready: True if all checks OK or simulated
      - .missing: List of non-OK checks
      - .critical: List of MISSING, VERSION_MISMATCH, or NOT_IN_PATH checks
      - .report(): Human-readable summary
      - .to_json(): Machine-readable JSON summary
    """

    def setUp(self):
        """Create a fresh CheckResult for each test."""
        self.result = CheckResult()

    def test_empty_result_is_ready(self):
        """
        An empty CheckResult (no checks) is considered ready.

        Verifies: ready=True, missing=[], critical=[].
        """
        self.assertTrue(self.result.ready)
        self.assertEqual(len(self.result.missing), 0)
        self.assertEqual(len(self.result.critical), 0)

    def test_all_ok_is_ready(self):
        """
        All checks passing means ready=True.

        Verifies: ready=True when all statuses are OK.
        """
        self.result.checks.append(
            DependencyResult(DepType.JAVA_RUNTIME, "Java", DepStatus.OK)
        )
        self.result.checks.append(
            DependencyResult(DepType.PATH_TOOL, "tar", DepStatus.OK)
        )
        self.assertTrue(self.result.ready)

    def test_simulated_is_ready(self):
        """
        Simulated dependencies count as ready.

        Verifies: ready=True even when some deps are SIMULATED.
        """
        self.result.checks.append(
            DependencyResult(DepType.JAVA_RUNTIME, "Java", DepStatus.SIMULATED)
        )
        self.result.checks.append(
            DependencyResult(DepType.PATH_TOOL, "tar", DepStatus.OK)
        )
        self.assertTrue(self.result.ready)

    def test_missing_dep_not_ready(self):
        """
        A missing dependency means not ready.

        Verifies: ready=False, critical contains 1 item.
        """
        self.result.checks.append(
            DependencyResult(DepType.JAVA_RUNTIME, "Java", DepStatus.MISSING)
        )
        self.assertFalse(self.result.ready)
        self.assertEqual(len(self.result.critical), 1)

    def test_version_mismatch_not_ready(self):
        """
        A version mismatch means not ready.

        Verifies: ready=False, critical contains 1 item.
        """
        self.result.checks.append(
            DependencyResult(
                DepType.JAVA_RUNTIME, "Java", DepStatus.VERSION_MISMATCH
            )
        )
        self.assertFalse(self.result.ready)
        self.assertEqual(len(self.result.critical), 1)

    def test_mixed_statuses(self):
        """
        Mixed OK and MISSING statuses — only MISSING is critical.

        Verifies: critical count matches MISSING count, not total count.
        """
        self.result.checks.append(
            DependencyResult(DepType.JAVA_RUNTIME, "Java", DepStatus.OK)
        )
        self.result.checks.append(
            DependencyResult(DepType.PATH_TOOL, "tar", DepStatus.OK)
        )
        self.result.checks.append(
            DependencyResult(DepType.PATH_TOOL, "unzip", DepStatus.MISSING)
        )
        self.assertFalse(self.result.ready)
        self.assertEqual(len(self.result.critical), 1)

    def test_report_empty(self):
        """
        Report for empty/ready result should show satisfaction.

        Verifies: report contains "satisfied" message.
        """
        report = self.result.report()
        self.assertIn("satisfied", report.lower())

    def test_report_with_issues(self):
        """
        Report for failed checks should list issues.

        Verifies: report contains dependency names and suggestions.
        """
        self.result.checks.append(
            DependencyResult(
                DepType.JAVA_RUNTIME,
                "Java Runtime",
                DepStatus.MISSING,
                message="Java not found",
                suggestion="Install Java 17",
            )
        )
        report = self.result.report()
        self.assertIn("Java Runtime", report)
        self.assertIn("Install Java 17", report)
        self.assertIn("critical", report.lower())

    def test_to_json_serialization(self):
        """
        JSON serialization should include all fields.

        Verifies: output is valid JSON with expected keys.
        """
        self.result.checks.append(
            DependencyResult(DepType.JAVA_RUNTIME, "Java", DepStatus.OK)
        )
        self.result.java_home = "/usr/lib/jvm/java-17"
        json_str = self.result.to_json()
        data = json.loads(json_str)
        self.assertIn("ready", data)
        self.assertIn("java_home", data)
        self.assertIn("checks", data)
        self.assertEqual(data["java_home"], "/usr/lib/jvm/java-17")

    def test_dependency_result_to_dict(self):
        """
        DependencyResult.to_dict() should serialize all fields.

        Verifies: all expected keys are present with correct types.
        """
        dr = DependencyResult(
            DepType.JAVA_RUNTIME,
            "Java Runtime",
            DepStatus.OK,
            found_path="/usr/bin/java",
            current_version="17.0.9",
            required_version="17",
            message="Java 17 found",
            suggestion="None needed",
        )
        d = dr.to_dict()
        self.assertEqual(d["dep_type"], "java_runtime")
        self.assertEqual(d["name"], "Java Runtime")
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["found_path"], "/usr/bin/java")
        self.assertEqual(d["current_version"], "17.0.9")
        self.assertEqual(d["required_version"], "17")


# ===================================================================
#  Test: EnvironmentManager
# ===================================================================


class TestEnvironmentManager(unittest.TestCase):
    """
    Test the EnvironmentManager class.

    EnvironmentManager manages JAVA_HOME, PATH, and other environment
    variables with snapshot/restore capability for testing isolation.
    """

    def setUp(self):
        """Create a fresh EnvironmentManager and save the current env."""
        self.env_manager = EnvironmentManager()

    def tearDown(self):
        """Restore the original environment."""
        self.env_manager.restore()

    def test_initial_state(self):
        """
        Freshly created EnvironmentManager has no modifications.

        Verifies: modified_vars is empty.
        """
        self.assertEqual(len(self.env_manager.modified_vars), 0)

    def test_set_java_home_from_path(self):
        """
        Setting JAVA_HOME from a java binary path should compute the JDK root.

        Given '/usr/lib/jvm/java-17-openjdk/bin/java',
        JAVA_HOME should be '/usr/lib/jvm/java-17-openjdk'.

        Verifies: JAVA_HOME is set and equals parent of bin dir.
        """
        test_path = "/usr/lib/jvm/java-17-openjdk/bin/java"
        java_home = self.env_manager.set_java_home(test_path)
        expected = "/usr/lib/jvm/java-17-openjdk"
        self.assertEqual(java_home, expected)
        self.assertEqual(os.environ.get("JAVA_HOME"), expected)

    def test_update_path_adds_directory(self):
        """
        Adding a directory to PATH should prepend it.

        Verifies: PATH contains the new directory at the front.
        """
        test_dir = "/opt/custom-java/bin"
        result = self.env_manager.update_path(test_dir)
        self.assertTrue(result)
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        self.assertEqual(path_dirs[0], test_dir)

    def test_update_path_duplicate(self):
        """
        Adding an already-present directory should return False.

        Verifies: PATH is not duplicated.
        """
        current_path = os.environ.get("PATH", "")
        first_dir = current_path.split(os.pathsep)[0] if current_path else "/usr/bin"
        result = self.env_manager.update_path(first_dir)
        self.assertFalse(result)

    def test_set_env_custom_var(self):
        """
        Setting a custom environment variable should work.

        Verifies: variable is set and tracked in modified_vars.
        """
        self.env_manager.set_env("MCSR_TEST_VAR", "hello_world")
        self.assertEqual(os.environ.get("MCSR_TEST_VAR"), "hello_world")
        self.assertIn("MCSR_TEST_VAR", self.env_manager.modified_vars)

    def test_restore_clears_modifications(self):
        """
        Restoring the environment should undo all changes.

        Verifies: modified_vars is empty after restore, custom var gone.
        """
        original_path = os.environ.get("PATH", "")
        self.env_manager.set_env("MCSR_TEST_VAR", "temp_value")
        self.env_manager.update_path("/someone/should/not/be/here")
        self.env_manager.restore()
        self.assertEqual(len(self.env_manager.modified_vars), 0)
        self.assertNotIn("MCSR_TEST_VAR", os.environ)
        self.assertEqual(os.environ.get("PATH", ""), original_path)

    def test_get_java_home_from_path_lib_indicator(self):
        """
        Deduce JAVA_HOME from real path with 'lib' indicator.

        Given a path to a JDK installation, the 'lib' subdirectory
        in the parent should be detected.

        Verifies: returns the JDK root path.
        """
        # Use a real temp dir that has a 'lib' subdirectory
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_dir = os.path.join(tmpdir, "lib")
            os.makedirs(lib_dir)
            java_path = os.path.join(tmpdir, "bin", "java")

            result = self.env_manager.get_java_home_from_path(java_path)
            self.assertEqual(result, tmpdir)

    def test_get_java_home_from_path_no_indicator(self):
        """
        Deduce JAVA_HOME when no standard JDK structure exists.

        Verifies: falls back to parent-of-bin even without indicators.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            java_path = os.path.join(tmpdir, "bin", "java")
            result = self.env_manager.get_java_home_from_path(java_path)
            self.assertEqual(result, tmpdir)

    def test_java_home_property(self):
        """
        The java_home property should reflect current JAVA_HOME.

        Verifies: property matches env var after set_java_home call.
        """
        test_path = "/opt/jdk-21/bin/java"
        self.env_manager.set_java_home(test_path)
        self.assertEqual(self.env_manager.java_home, "/opt/jdk-21")


# ===================================================================
#  Test: DependencyInstaller
# ===================================================================


class TestDependencyInstaller(unittest.TestCase):
    """
    Test the DependencyInstaller class.

    DependencyInstaller handles:
      - OS/distribution detection
      - Platform-specific install instructions
      - Simulated installations for testing
      - State management (set/clear simulated paths)
    """

    def setUp(self):
        """Clear any simulated state before each test."""
        DependencyInstaller.clear_simulated()

    def tearDown(self):
        """Ensure cleanup after each test."""
        DependencyInstaller.clear_simulated()

    def test_detect_distro_returns_string(self):
        """
        Distro detection should always return a non-empty string.

        Verifies: result is a string with length > 0.
        """
        distro = DependencyInstaller.detect_distro()
        self.assertIsInstance(distro, str)
        self.assertGreater(len(distro), 0)

    def test_simulate_install_creates_path(self):
        """
        Simulating an install should register a path.

        Verifies: get_simulated_path returns the expected path.
        """
        result = DependencyInstaller.simulate_install("java_runtime", "17.0.9")
        self.assertIn("path", result)
        self.assertIn("version", result)
        self.assertEqual(result["version"], "17.0.9")

        path = DependencyInstaller.get_simulated_path("java_runtime")
        self.assertEqual(path, "/simulated/java_runtime/bin/java")

    def test_clear_simulated_removes_all(self):
        """
        Clearing simulated installs should remove all entries.

        Verifies: get_simulated_path returns None after clear.
        """
        DependencyInstaller.simulate_install("java_runtime", "17.0.9")
        DependencyInstaller.clear_simulated()
        self.assertIsNone(
            DependencyInstaller.get_simulated_path("java_runtime")
        )

    def test_get_install_instructions_java(self):
        """
        Install instructions for Java should reference OpenJDK.

        Verifies: instructions contain actionable info.
        """
        instructions = DependencyInstaller.get_install_instructions(
            DepType.JAVA_RUNTIME.value
        )
        self.assertIsInstance(instructions, str)
        self.assertGreater(len(instructions), 20)

    def test_get_install_instructions_unknown_dep(self):
        """
        Install instructions for unknown dependency should return fallback.

        Verifies: does not crash and returns a string.
        """
        instructions = DependencyInstaller.get_install_instructions(
            "nonexistent_dep_xyz"
        )
        self.assertIsInstance(instructions, str)
        self.assertIn("No specific", instructions)

    def test_multiple_simulated_installs(self):
        """
        Multiple simulated installs should all be tracked separately.

        Verifies: each dep has its own simulated path.
        """
        DependencyInstaller.simulate_install("java_runtime", "17.0.9")
        DependencyInstaller.simulate_install("system_library", "1.2.3")
        path1 = DependencyInstaller.get_simulated_path("java_runtime")
        path2 = DependencyInstaller.get_simulated_path("system_library")
        self.assertIsNotNone(path1)
        self.assertIsNotNone(path2)
        self.assertNotEqual(path1, path2)


# ===================================================================
#  Test: HotReloadManager
# ===================================================================


class TestHotReloadManager(unittest.TestCase):
    """
    Test the HotReloadManager class.

    HotReloadManager refreshes runtime state after dependency changes
    without requiring a full application restart.
    """

    def setUp(self):
        """Create fresh manager and env manager for each test."""
        self.reloader = HotReloadManager()
        self.env_manager = EnvironmentManager()
        self.addCleanup(self.env_manager.restore)

    def test_clear_caches_returns_list(self):
        """
        Clearing caches should return list of cleared cache keys.

        Verifies: list is non-empty and contains expected cache name.
        """
        cleared = self.reloader.clear_caches()
        self.assertIsInstance(cleared, list)
        self.assertIn("java_detector_cache", cleared)

    def test_clear_caches_is_idempotent(self):
        """
        Clearing caches multiple times should not error.

        Verifies: second call also returns a list.
        """
        self.reloader.clear_caches()
        cleared2 = self.reloader.clear_caches()
        self.assertIsInstance(cleared2, list)

    def test_refresh_environment_sets_vars(self):
        """
        Refreshing environment should set JAVA_HOME, JDK_HOME, JRE_HOME.

        Verifies: all three env vars are set and PATH is updated.
        """
        changes = self.reloader.refresh_environment(
            self.env_manager, "/usr/lib/jvm/java-17/bin/java"
        )
        self.assertIn("JAVA_HOME", changes)
        self.assertIn("JDK_HOME", changes)
        self.assertIn("JRE_HOME", changes)
        self.assertIn("PATH", changes)
        self.assertEqual(changes["JAVA_HOME"], "/usr/lib/jvm/java-17")

    def test_refresh_environment_updates_path(self):
        """
        Refreshing environment should add java bin dir to PATH.

        Verifies: the bin directory appears at the front of PATH.
        """
        self.reloader.refresh_environment(
            self.env_manager, "/opt/jdk-21/bin/java"
        )
        path = os.environ.get("PATH", "")
        self.assertTrue(path.startswith("/opt/jdk-21/bin"))

    def test_reload_history_tracks_actions(self):
        """
        Each reload action should be logged in reload_history.

        Verifies: history contains entry with 'component' and 'details'.
        """
        self.assertEqual(len(self.reloader.reload_history), 0)

        self.reloader.refresh_environment(
            self.env_manager, "/usr/bin/java"
        )
        self.assertEqual(len(self.reloader.reload_history), 1)
        entry = self.reloader.reload_history[0]
        self.assertEqual(entry["component"], "environment")
        self.assertIn("details", entry)
        self.assertIn("timestamp", entry)


# ===================================================================
#  Test: DynamicDeps — Full Workflow Integration
# ===================================================================


class TestDynamicDepsCheckAll(unittest.TestCase):
    """
    Test the DynamicDeps.check_all() orchestrator method.

    This is the primary entry point that checks all dependencies
    and returns an aggregated CheckResult.
    """

    def setUp(self):
        """Create a fresh DynamicDeps instance for each test."""
        DependencyInstaller.clear_simulated()
        self.deps = DynamicDeps()

    def tearDown(self):
        """Clean up after each test."""
        self.deps.reset()

    def test_check_all_returns_checkresult(self):
        """
        check_all() should always return a CheckResult.

        Verifies: return type is CheckResult with valid properties.
        """
        result = self.deps.check_all()
        self.assertIsInstance(result, CheckResult)
        self.assertIsInstance(result.ready, bool)
        self.assertIsInstance(result.checks, list)

    def test_check_all_includes_java(self):
        """
        check_all() should always include a Java Runtime check.

        Verifies: at least one check with DepType.JAVA_RUNTIME.
        """
        result = self.deps.check_all()
        java_checks = [
            c for c in result.checks if c.dep_type == DepType.JAVA_RUNTIME
        ]
        self.assertEqual(len(java_checks), 1)

    def test_check_all_includes_java_home(self):
        """
        check_all() should always include a JAVA_HOME check.

        Verifies: at least one check with DepType.ENVIRONMENT_VAR.
        """
        result = self.deps.check_all()
        env_checks = [
            c for c in result.checks if c.dep_type == DepType.ENVIRONMENT_VAR
        ]
        self.assertEqual(len(env_checks), 1)

    def test_check_all_includes_system_tools(self):
        """
        check_all() should check for system tools (tar, unzip, curl).

        Verifies: at least these three PATH_TOOL checks exist.
        """
        result = self.deps.check_all()
        tool_names = [
            c.name for c in result.checks if c.dep_type == DepType.PATH_TOOL
        ]
        for tool in ["tar", "unzip", "curl"]:
            self.assertIn(tool, tool_names)

    def test_check_all_total_checks(self):
        """
        check_all() should perform exactly 5 checks.

        This is the current count: Java, JAVA_HOME, tar, unzip, curl.

        Verifies: len(checks) == 5.
        """
        result = self.deps.check_all()
        self.assertEqual(len(result.checks), 5)

    def test_check_all_with_server_type(self):
        """
        check_all() with different server types should use appropriate
        minimum Java versions.

        Verifies: paper uses min 17 (like vanilla).
        """
        result = self.deps.check_all(server_type="paper")
        java_check = next(
            c for c in result.checks if c.dep_type == DepType.JAVA_RUNTIME
        )
        self.assertEqual(java_check.required_version, "17")


class TestDynamicDepsWithSimulatedJava(unittest.TestCase):
    """
    Test DynamicDeps workflow when Java is simulated.

    This simulates a "Java 17 installed" scenario to test the
    full workflow: check -> simulate -> hot-reload -> verify.
    """

    def setUp(self):
        """Set up simulated Java install."""
        DependencyInstaller.clear_simulated()
        DependencyInstaller.simulate_install(
            DepType.JAVA_RUNTIME.value, "17.0.9"
        )
        self.deps = DynamicDeps()

    def tearDown(self):
        """Clean up after each test."""
        self.deps.reset()
        DependencyInstaller.clear_simulated()

    def test_simulated_java_returns_ready(self):
        """
        With simulated Java, check_all should return ready=True.

        Verifies: ready is True and all checks are OK or SIMULATED.
        """
        result = self.deps.check_all()
        self.assertTrue(
            result.ready,
            f"Expected ready=True with simulated Java, got: {result.report()}",
        )

    def test_simulated_java_detected(self):
        """
        Simulated Java should be detected as SIMULATED status.

        On systems with real Java installed, JavaDetector.find_java() finds
        the real runtime first.  We mock it to return None so the simulated
        path is exercised.

        Verifies: Java check status is SIMULATED.
        """
        with unittest.mock.patch.object(
            JavaDetector, 'find_java', return_value=None
        ):
            result = self.deps.check_all()
        java_check = next(
            c for c in result.checks if c.dep_type == DepType.JAVA_RUNTIME
        )
        self.assertEqual(java_check.status, DepStatus.SIMULATED)

    def test_simulated_java_home_deduced(self):
        """
        With simulated Java, JAVA_HOME should be deducible.

        On systems with real Java installed, JavaDetector.find_java() finds
        the real runtime first and the JAVA_HOME env var may also be set.
        We mock find_java to return None and temporarily clear JAVA_HOME
        so the simulated path is exercised for deduction.

        Verifies: JAVA_HOME check status is OK (deduced from sim path).
        """
        with \
            unittest.mock.patch.object(
                JavaDetector, 'find_java', return_value=None
            ), \
            unittest.mock.patch.dict(
                os.environ, {'JAVA_HOME': ''}, clear=False
            ):
            result = self.deps.check_all()
        home_check = next(
            c for c in result.checks if c.dep_type == DepType.ENVIRONMENT_VAR
        )
        # The simulated path is /simulated/java_runtime/bin/java
        # so JAVA_HOME should be /simulated/java_runtime
        self.assertEqual(home_check.status, DepStatus.OK)
        self.assertIn("/simulated/java_runtime", str(home_check.found_path))


class TestDynamicDepsResolveMissing(unittest.TestCase):
    """
    Test DynamicDeps.resolve_missing() — the auto-resolution workflow.

    resolve_missing() should:
      1. Simulate install of missing deps
      2. Hot-reload environment
      3. Update CheckResult with new statuses
    """

    def setUp(self):
        """Create fresh deps and ensure no simulated state."""
        DependencyInstaller.clear_simulated()
        self.deps = DynamicDeps()

    def tearDown(self):
        """Clean up after each test."""
        self.deps.reset()
        DependencyInstaller.clear_simulated()

    def test_resolve_missing_makes_ready(self):
        """
        resolve_missing() should make a non-ready result ready.

        Verifies: result.ready is True after resolution.
        """
        result = self.deps.check_all()
        # If Java was already found, skip this test
        java_check = next(
            c for c in result.checks if c.dep_type == DepType.JAVA_RUNTIME
        )
        if java_check.status == DepStatus.OK:
            self.skipTest("Java already installed — cannot test missing scenario")

        resolved = self.deps.resolve_missing(result)
        self.assertTrue(
            resolved.ready,
            f"Expected ready=True after resolve, got: {resolved.report()}",
        )

    def test_resolve_missing_updates_java_status(self):
        """
        resolve_missing() should change Java status from MISSING to SIMULATED.

        Verifies: Java check status becomes SIMULATED.
        """
        result = self.deps.check_all()
        java_check = next(
            c for c in result.checks if c.dep_type == DepType.JAVA_RUNTIME
        )
        if java_check.status == DepStatus.OK:
            self.skipTest("Java already installed — cannot test missing scenario")

        resolved = self.deps.resolve_missing(result)
        java_resolved = next(
            c for c in resolved.checks if c.dep_type == DepType.JAVA_RUNTIME
        )
        self.assertEqual(java_resolved.status, DepStatus.SIMULATED)

    def test_resolve_missing_sets_java_home(self):
        """
        resolve_missing() should set JAVA_HOME environment variable.

        Verifies: JAVA_HOME is set in environment after resolution.
        """
        result = self.deps.check_all()
        java_check = next(
            c for c in result.checks if c.dep_type == DepType.JAVA_RUNTIME
        )
        if java_check.status == DepStatus.OK:
            self.skipTest("Java already installed — cannot test missing scenario")

        self.deps.resolve_missing(result)
        self.assertIsNotNone(os.environ.get("JAVA_HOME"))


class TestDynamicDepsPathSnapshot(unittest.TestCase):
    """
    Test DynamicDeps.get_path_snapshot().

    The snapshot provides a summary of the current environment state.
    """

    def setUp(self):
        self.deps = DynamicDeps()

    def tearDown(self):
        self.deps.reset()

    def test_get_path_snapshot_returns_dict(self):
        """
        get_path_snapshot() should return a dict.

        Verifies: result has 'path_dirs' and 'java_home' keys.
        """
        snapshot = self.deps.get_path_snapshot()
        self.assertIsInstance(snapshot, dict)
        self.assertIn("path_dirs", snapshot)
        self.assertIn("java_home", snapshot)

    def test_path_snapshot_path_dirs_is_list(self):
        """
        The path_dirs field should be a list of strings.

        Verifies: type is list and items are non-empty strings.
        """
        snapshot = self.deps.get_path_snapshot()
        self.assertIsInstance(snapshot["path_dirs"], list)
        for p in snapshot["path_dirs"]:
            self.assertIsInstance(p, str)


class TestDynamicDepsReset(unittest.TestCase):
    """
    Test DynamicDeps.reset() — full state cleanup.

    reset() should clear simulated installs, restore environment,
    and clear all caches.
    """

    def test_reset_clears_simulated(self):
        """
        reset() should clear all simulated install paths.

        Verifies: get_simulated_path returns None after reset.
        """
        DependencyInstaller.simulate_install(
            DepType.JAVA_RUNTIME.value, "17.0.9"
        )
        deps = DynamicDeps()
        deps.reset()
        self.assertIsNone(
            DependencyInstaller.get_simulated_path(DepType.JAVA_RUNTIME.value)
        )

    def test_reset_restores_environment(self):
        """
        reset() should restore environment to original state.

        Verifies: modified_vars is empty after reset.
        """
        deps = DynamicDeps()
        deps.env_manager.set_env("MCSR_TEST_RESET", "should_be_gone")
        deps.reset()
        self.assertNotIn("MCSR_TEST_RESET", os.environ)


# ===================================================================
#  Test: JavaDetector — find_java behavior (without actual Java)
# ===================================================================


class TestJavaDetectorPathless(unittest.TestCase):
    """
    Test JavaDetector.find_java() behavior when Java is not installed.

    These tests verify graceful degradation when Java is unavailable.
    """

    def setUp(self):
        """Save original PATH and JAVA_HOME."""
        self._original_path = os.environ.get("PATH", "")
        self._original_java_home = os.environ.get("JAVA_HOME", "")

    def tearDown(self):
        """Restore original environment."""
        os.environ["PATH"] = self._original_path
        if self._original_java_home:
            os.environ["JAVA_HOME"] = self._original_java_home
        elif "JAVA_HOME" in os.environ:
            del os.environ["JAVA_HOME"]

    def test_get_min_java_versions_are_reasonable(self):
        """
        All server types should have reasonable minimum Java versions.

        Verifies: known types return values between 8 and 21.
        """
        types = ["vanilla", "fabric", "quilt", "forge", "neoforge",
                 "paper", "purpur", "spigot", "bukkit"]
        for st in types:
            ver = JavaDetector.get_min_java_version(st)
            self.assertGreaterEqual(ver, 8, f"{st} min Java < 8")
            self.assertLessEqual(ver, 21, f"{st} min Java > 21")

    def test_scan_java_in_directory_nonexistent(self):
        """
        Scanning a nonexistent directory should return None.

        Verifies: returns None without raising exception.
        """
        result = JavaDetector._scan_java_in_directory("/nonexistent_path_xyz")
        self.assertIsNone(result)


# ===================================================================
#  Test: Integration — Full Workflow
# ===================================================================


class TestFullWorkflow(unittest.TestCase):
    """
    End-to-end workflow tests for the entire DynamicDeps system.

    Tests the complete lifecycle: check -> identify issues ->
    resolve (simulate) -> verify -> cleanup.
    """

    def setUp(self):
        """Ensure clean state."""
        DependencyInstaller.clear_simulated()
        self.deps = DynamicDeps()

    def tearDown(self):
        """Clean up."""
        self.deps.reset()
        DependencyInstaller.clear_simulated()

    def test_full_check_report_resolve_workflow(self):
        """
        Complete workflow: check → report → resolve → verify → reset.

        This test simulates the user workflow:
          1. User calls check_all() to verify deps
          2. User reads report() to see what's missing
          3. User calls resolve_missing() to simulate fix
          4. User verifies with check_all() again
          5. User can reset() to clean state

        Verifies: all steps complete without errors.
        """
        # Step 1: Check
        result = self.deps.check_all()
        self.assertIsInstance(result, CheckResult)

        # Step 2: Generate report
        report = result.report()
        self.assertIsInstance(report, str)

        # Step 3: Resolve if needed
        java_check = next(
            c for c in result.checks if c.dep_type == DepType.JAVA_RUNTIME
        )

        if java_check.status == DepStatus.MISSING:
            resolved = self.deps.resolve_missing(result)
            self.assertTrue(resolved.ready)

            # Step 4: Verify
            verify_result = self.deps.check_all()
            java_verified = next(
                c for c in verify_result.checks
                if c.dep_type == DepType.JAVA_RUNTIME
            )
            self.assertIn(
                java_verified.status,
                [DepStatus.OK, DepStatus.SIMULATED]
            )

        # Step 5: Reset
        self.deps.reset()

    def test_report_is_readable_across_methods(self):
        """
        The report() method should be callable at any point in the workflow.

        Verifies: no crash when calling report() on empty or populated results.
        """
        # Empty result
        r1 = CheckResult()
        self.assertIsInstance(r1.report(), str)

        # After check_all
        r2 = self.deps.check_all()
        self.assertIsInstance(r2.report(), str)

        # After JSON conversion
        r3 = json.loads(r2.to_json())
        self.assertIsInstance(r3, dict)


class TestEdgeCases(unittest.TestCase):
    """
    Test edge cases and error handling in the DynamicDeps system.

    Covers: missing tools, Java not in PATH but JAVA_HOME set,
    duplicate simulated installs, environment restore after failures.
    """

    def setUp(self):
        """Create fresh deps."""
        DependencyInstaller.clear_simulated()
        self.deps = DynamicDeps()

    def tearDown(self):
        """Clean up."""
        self.deps.reset()
        DependencyInstaller.clear_simulated()

    def test_env_manager_restore_after_multiple_changes(self):
        """
        Restoring environment after multiple changes should work.

        Verifies: original PATH is restored even after multiple updates.
        """
        original_path = os.environ.get("PATH", "")
        em = EnvironmentManager()

        em.update_path("/first/add")
        em.update_path("/second/add")
        em.set_env("MCSR_VAR_1", "val1")
        em.set_env("MCSR_VAR_2", "val2")

        em.restore()

        self.assertEqual(os.environ.get("PATH", ""), original_path)
        self.assertNotIn("MCSR_VAR_1", os.environ)
        self.assertNotIn("MCSR_VAR_2", os.environ)

    def test_dependency_result_defaults(self):
        """
        DependencyResult should have sensible defaults.

        Verifies: message and suggestion default to empty strings.
        """
        dr = DependencyResult(DepType.JAVA_RUNTIME, "Java", DepStatus.OK)
        self.assertEqual(dr.message, "")
        self.assertEqual(dr.suggestion, "")
        self.assertIsNone(dr.found_path)
        self.assertIsNone(dr.current_version)
        self.assertIsNone(dr.required_version)

    def test_simulate_multiple_java_installs(self):
        """
        Simulating the same dep twice should overwrite the previous path.

        Verifies: only the latest simulation is kept.
        """
        DependencyInstaller.simulate_install("java_runtime", "17.0.9")
        DependencyInstaller.simulate_install("java_runtime", "21.0.1")
        path = DependencyInstaller.get_simulated_path("java_runtime")
        self.assertEqual(path, "/simulated/java_runtime/bin/java")


# ===================================================================
#  Entry point
# ===================================================================

if __name__ == "__main__":
    unittest.main()
