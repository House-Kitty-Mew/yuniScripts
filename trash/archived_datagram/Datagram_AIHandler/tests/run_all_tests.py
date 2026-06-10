#!/usr/bin/env python3
"""
Datagram Data Consistency Test Suite Runner
============================================
Runs all 4 levels of data consistency tests and generates a summary report.

Usage:
    python3 run_all_tests.py              # Run all tests
    python3 run_all_tests.py --level 1    # Run only Level 1
    python3 run_all_tests.py --verbose    # Verbose output
    python3 run_all_tests.py --junit      # Generate JUnit XML report
"""

import os
import sys
import json
import time
import argparse
import unittest
import traceback
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_DIR = os.path.dirname(os.path.abspath(__file__))

LEVELS = {
    1: "Level 1: Structural Consistency",
    2: "Level 2: Functional Consistency",
    3: "Level 3: Data Integrity Consistency",
    4: "Level 4: Cross-Component Integration",
}


def discover_tests(level=None):
    """Discover test cases for specified level(s)."""
    if level:
        loader = unittest.TestLoader()
        test_file = os.path.join(TEST_DIR, f"test_level{level}_*.py")
        suite = loader.discover(TEST_DIR, pattern=f"test_level{level}_*.py")
        return suite
    else:
        loader = unittest.TestLoader()
        suite = loader.discover(TEST_DIR, pattern="test_level*.py")
        return suite


def run_tests(level=None, verbose=False):
    """Run tests and return results."""
    suite = discover_tests(level)

    runner = unittest.TextTestRunner(
        verbosity=2 if verbose else 1,
        stream=sys.stdout,
    )

    start_time = time.time()
    result = runner.run(suite)
    elapsed = time.time() - start_time

    return result, elapsed


def generate_report(result, elapsed, level=None):
    """Generate a structured report of test results."""
    report = {
        "project": "Datagram",
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "level": level,
        "level_name": LEVELS.get(level, "All Levels") if level else "All Levels",
        "summary": {
            "total": result.testsRun,
            "passed": result.testsRun - len(result.failures) - len(result.errors),
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
        },
        "failures": [],
        "errors": [],
    }

    for test, tb in result.failures:
        report["failures"].append({
            "test": str(test),
            "traceback": tb,
        })

    for test, tb in result.errors:
        report["errors"].append({
            "test": str(test),
            "traceback": tb,
        })

    return report


def print_report(report):
    """Print formatted test report."""
    sep = "=" * 72
    print(f"\n{sep}")
    print(f"  Datagram Data Consistency Test Suite")
    print(f"  {report['level_name']}")
    print(f"  Timestamp: {report['timestamp']}")
    print(f"  Duration: {report['elapsed_seconds']}s")
    print(sep)

    s = report["summary"]
    status = "PASSED" if s["failures"] == 0 and s["errors"] == 0 else "FAILED"
    print(f"\n  SUMMARY: {status}")
    print(f"    Total:  {s['total']}")
    print(f"    Passed: {s['passed']}")
    print(f"    Failures: {s['failures']}")
    print(f"    Errors:  {s['errors']}")
    print(f"    Skipped: {s['skipped']}")

    if report["failures"]:
        print(f"\n  {'*' * 68}")
        print(f"  FAILURES:")
        print(f"  {'*' * 68}")
        for i, f in enumerate(report["failures"], 1):
            test_name = f["test"]
            # Extract meaningful message from traceback (last line)
            tb_lines = f["traceback"].strip().split("\n")
            msg = tb_lines[-1] if tb_lines else "Unknown error"
            print(f"\n  [{i}] {test_name}")
            print(f"      {msg}")

    if report["errors"]:
        print(f"\n  {'*' * 68}")
        print(f"  ERRORS:")
        print(f"  {'*' * 68}")
        for i, e in enumerate(report["errors"], 1):
            test_name = e["test"]
            tb_lines = e["traceback"].strip().split("\n")
            msg = tb_lines[-1] if tb_lines else "Unknown error"
            print(f"\n  [{i}] {test_name}")
            print(f"      {msg}")

    print(f"\n{sep}\n")
    return s["failures"] == 0 and s["errors"] == 0


def print_level_header(level):
    """Print a nice header for each test level."""
    sep = "#" * 72
    print(f"\n{sep}")
    print(f"#  {LEVELS[level]:<65}#")
    print(f"{sep}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Datagram Data Consistency Test Suite",
    )
    parser.add_argument(
        "--level", "-l",
        type=int,
        choices=[1, 2, 3, 4],
        help="Run only a specific test level (1-4)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--junit",
        action="store_true",
        help="Generate JUnit XML report (requires junit_xml)",
    )
    args = parser.parse_args()

    overall_passed = True
    total_tests = 0
    total_failures = 0
    total_errors = 0
    total_start = time.time()

    if args.level:
        print_level_header(args.level)
        result, elapsed = run_tests(args.level, args.verbose)
        report = generate_report(result, elapsed, args.level)
        level_passed = print_report(report)
        if not level_passed:
            overall_passed = False
        total_tests += report["summary"]["total"]
        total_failures += report["summary"]["failures"]
        total_errors += report["summary"]["errors"]
    else:
        for level in sorted(LEVELS.keys()):
            print_level_header(level)
            result, elapsed = run_tests(level, args.verbose)
            report = generate_report(result, elapsed, level)
            level_passed = print_report(report)
            if not level_passed:
                overall_passed = False
            total_tests += report["summary"]["total"]
            total_failures += report["summary"]["failures"]
            total_errors += report["summary"]["errors"]

    total_elapsed = time.time() - total_start

    # Print grand summary
    sep = "=" * 72
    print(f"\n{sep}")
    print(f"  GRAND SUMMARY")
    print(f"{sep}")
    print(f"  All Levels: {'PASSED' if overall_passed else 'FAILED'}")
    print(f"  Total Tests: {total_tests}")
    print(f"  Total Failures: {total_failures}")
    print(f"  Total Errors: {total_errors}")
    print(f"  Total Duration: {total_elapsed:.2f}s")
    print(f"{sep}\n")

    sys.exit(0 if overall_passed else 1)


if __name__ == "__main__":
    main()
