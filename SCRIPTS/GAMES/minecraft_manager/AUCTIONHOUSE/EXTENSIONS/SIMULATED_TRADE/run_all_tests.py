"""
run_all_tests.py — Comprehensive test runner for SIMULATED_TRADE extension.

Runs all tests using unittest framework with detailed reporting.
"""

import sys, os, json, unittest, importlib.util
from pathlib import Path

# ── Ensure AH modules are importable ────────────────────────────────
MC_MANAGER_DIR = Path(__file__).parent.parent.parent.parent  # minecraft_manager/
sys.path.insert(0, str(MC_MANAGER_DIR))  # minecraft_manager/
# Also add project root for any engine-level imports
sys.path.insert(1, str(MC_MANAGER_DIR.parent.parent.parent))  # dev-yuniScripts/
# Add tests directory so conftest.py can be imported
TESTS_DIR = Path(__file__).parent / "tests"
sys.path.insert(2, str(TESTS_DIR))


def import_test_module(name, path):
    """Dynamically import a test module by file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_all_tests():
    """Discover and run all test classes from the tests directory."""
    tests_dir = Path(__file__).parent / "tests"
    test_files = sorted(tests_dir.glob("test_*.py"))

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    results = {"passed": 0, "failed": 0, "errors": [], "details": {}}

    print("=" * 70)
    print("  SIMULATED_TRADE — COMPREHENSIVE TEST SUITE")
    print("=" * 70)

    for test_file in test_files:
        if test_file.name == "__init__.py":
            continue

        module_name = test_file.stem
        print(f"\n{'─' * 70}")
        print(f"  [{module_name}]")
        print(f"{'─' * 70}")

        try:
            module = import_test_module(module_name, str(test_file))
            test_suite = loader.loadTestsFromModule(module)
            test_count = test_suite.countTestCases()

            if test_count == 0:
                print(f"  ⚠ No tests found in {module_name}")
                continue

            print(f"  Running {test_count} tests...\n")

            # Run tests
            test_runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
            result = test_runner.run(test_suite)

            results["passed"] += result.testsRun - len(result.failures) - len(result.errors)
            results["failed"] += len(result.failures) + len(result.errors)

            for test, trace in result.failures:
                results["errors"].append(f"{module_name}.{test.id()}: {trace[:200]}")

            for test, trace in result.errors:
                results["errors"].append(f"{module_name}.{test.id()}: ERROR: {trace[:200]}")

            results["details"][module_name] = {
                "total": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
            }

        except Exception as e:
            import traceback
            error_msg = f"Failed to load {module_name}: {e}\n{traceback.format_exc()[:300]}"
            results["errors"].append(error_msg)
            results["failed"] += 1
            print(f"  ⚠ {error_msg}")

    # ── Summary ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Total tests: {results['passed'] + results['failed']}")
    print(f"  Passed:      {results['passed']}")
    print(f"  Failed:      {results['failed']}")

    if results["errors"]:
        print(f"\n  ERRORS ({len(results['errors'])}):")
        for i, err in enumerate(results["errors"][:10], 1):
            print(f"    {i}. {err[:150]}...")
        if len(results["errors"]) > 10:
            print(f"    ... and {len(results['errors']) - 10} more")

    # Per-module breakdown
    print(f"\n  PER-MODULE BREAKDOWN:")
    for module_name, stats in sorted(results["details"].items()):
        status = "✅" if stats["failures"] == 0 and stats["errors"] == 0 else "❌"
        print(f"    {status} {module_name}: {stats['total']} tests "
              f"({stats['failures']} failures, {stats['errors']} errors)")

    print("=" * 70)

    # Return exit code
    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
