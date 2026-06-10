"""
run_all_dataflow_tests.py — Data Flow Test Orchestrator

Discovers and runs all data flow tests, producing a comprehensive
report of path coverage, pass/fail counts, and any bugs found.

Usage:
    cd /path/to/minecraft_manager
    python -m AUCTIONHOUSE.tests.dataflow.run_all_dataflow_tests
    python -m AUCTIONHOUSE.tests.dataflow.run_all_dataflow_tests --list
"""

import sys
import os
import argparse
import json
import time
import unittest
from datetime import datetime, timezone


# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_TESTS_DIR = os.path.dirname(_HERE)
_AH_DIR = os.path.dirname(_TESTS_DIR)
_MANAGER_DIR = os.path.dirname(_AH_DIR)
for p in [_AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ══════════════════════════════════════════════════════════════════════
# Test Result Collector
# ══════════════════════════════════════════════════════════════════════

class DataFlowTestResult(unittest.TestResult):
    """Extended test result with timing and categorization."""

    def __init__(self, verbose=False):
        super().__init__()
        self.verbose = verbose
        self.start_times: dict[str, float] = {}
        self.test_durations: dict[str, float] = {}
        self.test_categories: dict[str, str] = {}

    def startTest(self, test):
        self.start_times[test.id()] = time.time()
        super().startTest(test)
        parts = test.id().split('.')
        for p in parts:
            if p in ('flows', 'integrations', 'edge_cases', 'stress'):
                self.test_categories[test.id()] = p
                break

    def stopTest(self, test):
        if test.id() in self.start_times:
            self.test_durations[test.id()] = time.time() - self.start_times[test.id()]
        super().stopTest(test)

    def get_summary(self) -> dict:
        return {
            "total": self.testsRun,
            "passed": self.testsRun - len(self.failures) - len(self.errors),
            "failures": len(self.failures),
            "errors": len(self.errors),
            "skipped": len(self.skipped),
            "duration": sum(self.test_durations.values()),
            "slowest_tests": sorted(
                self.test_durations.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5],
            "categories": self._get_category_summary(),
        }

    def _get_category_summary(self) -> dict:
        cats = {}
        for test_id, category in self.test_categories.items():
            if category not in cats:
                cats[category] = {"total": 0, "passed": 0, "failed": 0}
            cats[category]["total"] += 1
            failed_ids = {f[0].id() for f in self.failures + self.errors}
            if test_id in failed_ids:
                cats[category]["failed"] += 1
            else:
                cats[category]["passed"] += 1
        return cats


# ══════════════════════════════════════════════════════════════════════
# Test Runner
# ══════════════════════════════════════════════════════════════════════

class DataFlowTestRunner:
    """Discovers and runs all data flow tests."""

    def __init__(self, verbose=False, pattern="test_*.py"):
        self.verbose = verbose
        self.pattern = pattern
        self.start_dir = os.path.dirname(os.path.abspath(__file__))

    def discover_tests(self) -> unittest.TestSuite:
        """Discover all data flow tests by importing modules directly."""
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()

        # Ensure the manager dir is first in sys.path
        if _MANAGER_DIR not in sys.path:
            sys.path.insert(0, _MANAGER_DIR)
        if _AH_DIR not in sys.path:
            sys.path.insert(0, _AH_DIR)

        for subdir in ['flows', 'integrations', 'edge_cases', 'stress']:
            path = os.path.join(self.start_dir, subdir)
            if not os.path.isdir(path):
                continue

            try:
                # Discover using the dataflow dir as top level
                # (so module paths resolve as flows.test_flow_listing etc.)
                if path not in sys.path:
                    sys.path.insert(0, path)

                subsuite = loader.discover(
                    start_dir=path,
                    pattern=self.pattern,
                    top_level_dir=self.start_dir,
                )
                suite.addTest(subsuite)
                if self.verbose:
                    print(f"  {subdir}: {subsuite.countTestCases()} tests discovered")
            except ImportError as e:
                if self.verbose:
                    print(f"  {subdir}: skipped (import error: {e})")
            except Exception as e:
                if self.verbose:
                    print(f"  {subdir}: skipped ({e})")

        return suite

    def list_tests(self):
        """List all discovered test cases without running them."""
        suite = self.discover_tests()

        def _list(suite_or_case, indent=""):
            if isinstance(suite_or_case, unittest.TestCase):
                test_id = suite_or_case.id()
                parts = test_id.split('.')
                class_name = parts[-2] if len(parts) >= 2 else "?"
                method_name = parts[-1]
                category = "?"
                for p in parts:
                    if p in ('flows', 'integrations', 'edge_cases', 'stress'):
                        category = p
                        break
                print(f"  [{category:12s}] {class_name}.{method_name}")
            else:
                for test in suite_or_case:
                    _list(test)

        print(f"\n=== Data Flow Tests ({suite.countTestCases()} total) ===\n")
        _list(suite)
        print(f"\nTotal: {suite.countTestCases()} test cases\n")

    def run(self) -> DataFlowTestResult:
        """Run all data flow tests and return results."""
        try:
            suite = self.discover_tests()

        except Exception as e:
            logger.error(f"run failed: {e}")
            return None
        total = suite.countTestCases()

        print(f"\n{'='*60}")
        print(f"  Auction House Data Flow Test Suite")
        print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"  Total test cases: {total}")
        print(f"{'='*60}\n")

        result = DataFlowTestResult(verbose=self.verbose)
        suite.run(result)

        summary = result.get_summary()
        print(f"\n{'='*60}")
        print(f"  RESULTS")
        print(f"{'='*60}")
        print(f"  Total:    {summary['total']}")
        print(f"  Passed:   {summary['passed']}  "
              f"({'OK' if summary['failures'] == 0 else 'ISSUES FOUND'})")
        print(f"  Failures: {summary['failures']}")
        print(f"  Errors:   {summary['errors']}")
        print(f"  Skipped:  {summary['skipped']}")
        print(f"  Duration: {summary['duration']:.2f}s")
        print()

        if summary['categories']:
            print("  By Category:")
            for cat, stats in sorted(summary['categories'].items()):
                status = "\u2713" if stats['failed'] == 0 else "\u2717"
                print(f"    {status} {cat:15s}: "
                      f"{stats['passed']}/{stats['total']} passed")
            print()

        if summary['failures'] > 0 or summary['errors'] > 0:
            print(f"  FAILURES:")
            for test, tb in result.failures + result.errors:
                test_id = test.id()
                dur = result.test_durations.get(test_id, 0)
                print(f"    \u2717 {test_id} ({dur:.2f}s)")
                tb_lines = tb.strip().split('\n')
                for line in tb_lines[:3]:
                    print(f"      {line.strip()}")
            print()

        if summary['slowest_tests']:
            print("  Slowest Tests:")
            for test_id, dur in summary['slowest_tests']:
                print(f"    {dur:.2f}s  {test_id.split('.')[-1]}")
            print()

        # Write JSON report
        report_path = os.path.join(self.start_dir, 'dataflow_test_report.json')
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "failures": [
                {"test": f[0].id(), "traceback": f[1][:500]}
                for f in result.failures
            ],
            "errors": [
                {"test": e[0].id(), "traceback": e[1][:500]}
                for e in result.errors
            ],
        }
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"  Report saved to: {report_path}\n")

        return result


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Auction House Data Flow Test Orchestrator"
    )
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')
    parser.add_argument('--list', action='store_true',
                        help='List all test cases without running')
    parser.add_argument('--pattern', '-p', default='test_*.py',
                        help='Test file pattern (default: test_*.py)')

    args = parser.parse_args()

    runner = DataFlowTestRunner(verbose=args.verbose, pattern=args.pattern)

    if args.list:
        runner.list_tests()
        return

    result = runner.run()
    if result.failures or result.errors:
        sys.exit(1)


if __name__ == '__main__':
    main()

