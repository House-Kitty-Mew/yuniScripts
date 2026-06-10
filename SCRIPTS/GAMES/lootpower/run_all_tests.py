#!/usr/bin/env python3
"""
LootPower — Run All Tests

Usage:
    python run_all_tests.py              # Run all tests
    python run_all_tests.py -v           # Verbose
    python run_all_tests.py database     # Only database tests
    python run_all_tests.py player       # Only player tests
"""

import sys
import os
import unittest

# Ensure we can import from the lootpower package
sys.path.insert(0, os.path.dirname(__file__))

TEST_MODULES = {
    "database": "tests.test_lp_database",
    "chance": "tests.test_lp_chance",
    "player": "tests.test_lp_player",
    "adventure": "tests.test_lp_adventure",
    "mining": "tests.test_lp_mining",
    "crafting": "tests.test_lp_crafting",
    "auction": "tests.test_lp_auction",
    "leaderboard": "tests.test_lp_leaderboard",
    "watcher": "tests.test_lp_watcher",
    "data_flow": "tests.test_lp_data_flow",
    "edge_cases": "tests.test_lp_edge_cases",
}


def run_all():
    """Run all test modules."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    modules = list(TEST_MODULES.values())
    if len(sys.argv) > 1:
        # Filter by requested module
        requested = [m for m in sys.argv[1:] if m in TEST_MODULES]
        if requested:
            modules = [TEST_MODULES[m] for m in requested]
        elif "-v" in sys.argv or "--verbose" in sys.argv:
            pass  # Run all with verbose
        else:
            # Try as module name partial match
            search = sys.argv[1].lower()
            modules = [v for k, v in TEST_MODULES.items() if search in k]

    for mod_name in modules:
        try:
            suite.addTests(loader.loadTestsFromName(mod_name))
        except ModuleNotFoundError as e:
            print(f"WARNING: Could not load {mod_name}: {e}")

    verbosity = 2 if ("-v" in sys.argv or "--verbose" in sys.argv) else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)