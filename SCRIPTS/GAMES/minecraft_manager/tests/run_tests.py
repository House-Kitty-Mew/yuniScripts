#!/usr/bin/env python3
"""
run_tests.py — Auction House Test Runner

Discovers and runs all tests in the tests/ directory.
Provides a summary of results.

Usage:
    python3 tests/run_tests.py              # Run all tests
    python3 tests/run_tests.py -v           # Verbose mode
    python3 tests/run_tests.py Core         # Run only core tests
    python3 tests/run_tests.py System       # Run only system tests
    python3 tests/run_tests.py --no-bridge  # Skip bridge tests (no network)
    python3 tests/run_tests.py --list       # List available test files
"""

import sys, os, unittest, time, argparse

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# Default test directories to scan
TEST_DIR = os.path.join(PROJECT_ROOT, "tests")


def discover_tests(filter_pattern: str = "", skip_bridge: bool = False):
    """Discover all test cases matching the optional filter.

    Args:
        filter_pattern: Optional substring to filter test file names
        skip_bridge: If True, skip eco_bridge tests (which need network)

    Returns:
        unittest.TestSuite
    """
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for f in sorted(os.listdir(TEST_DIR)):
        if not f.startswith("test_") or not f.endswith(".py"):
            continue
        if f == "run_tests.py":
            continue
        if filter_pattern and filter_pattern.lower() not in f.lower():
            continue
        if skip_bridge and "bridge" in f.lower():
            print(f"  [SKIP] {f} (--no-bridge)")
            continue

        # Load test module
        module_name = f[:-3]  # Remove .py
        try:
            test_suite = loader.discover(TEST_DIR, pattern=f)
            suite.addTests(test_suite)
        except Exception as e:
            print(f"  [ERROR] Could not load {f}: {e}")

    return suite


def main():
    try:
        parser = argparse.ArgumentParser(description="Auction House Test Runner")

    except Exception as e:
        logger.error(f"main failed: {e}")
        return None
    parser.add_argument("filter", nargs="?", default="",
                        help="Filter test files by name substring")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    parser.add_argument("--no-bridge", action="store_true",
                        help="Skip economy bridge tests (need network)")
    parser.add_argument("--list", action="store_true",
                        help="List available test files and exit")
    parser.add_argument("--failfast", action="store_true",
                        help="Stop on first failure")

    args = parser.parse_args()

    if args.list:
        print("Available test files:")
        for f in sorted(os.listdir(TEST_DIR)):
            if f.startswith("test_") and f.endswith(".py"):
                desc = ""
                if "core" in f: desc = "— Core CRUD, race conditions, edge cases"
                elif "database" in f: desc = "— DB schema, seed data, queries"
                elif "market" in f: desc = "— Event lifecycle, cooldowns"
                elif "item" in f: desc = "— Rarity rolls, enchants, lore"
                elif "bridge" in f: desc = "— Economy bridge operations"
                elif "phooks" in f: desc = "— Phooks dispatch + input validation"
                elif "system" in f: desc = "— Full pipeline integration"
                print(f"  {f:35s} {desc}")
        return 0

    print("=" * 65)
    print("  Auction House — Test Suite")
    print("=" * 65)

    if args.filter:
        print(f"  Filter: {args.filter}")

    suite = discover_tests(filter_pattern=args.filter, skip_bridge=args.no_bridge)

    total_tests = suite.countTestCases()
    if total_tests == 0:
        print("  No tests found matching filter.")
        return 0

    print(f"  Found {total_tests} test cases\n")

    runner = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1,
        failfast=args.failfast,
    )

    start_time = time.time()
    result = runner.run(suite)
    elapsed = time.time() - start_time

    print()
    print("=" * 65)
    print(f"  Results: {result.testsRun} tests in {elapsed:.2f}s")
    print(f"  Passed:  {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failed:  {len(result.failures)}")
    print(f"  Errors:  {len(result.errors)}")
    if result.skipped:
        print(f"  Skipped: {len(result.skipped)}")
    print("=" * 65)

    if result.failures or result.errors:
        for test, tb in result.failures + result.errors:
            print(f"\n  FAIL: {test}")
            print(f"  {tb.split(chr(10))[-3] if chr(10) in tb else tb[:120]}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

