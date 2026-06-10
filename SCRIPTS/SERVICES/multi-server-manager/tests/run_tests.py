#!/usr/bin/env python3
"""Run all Multi-Server Manager tests."""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Discover and run all tests
loader = unittest.TestLoader()
suite = loader.discover(
    os.path.join(os.path.dirname(os.path.abspath(__file__))),
    pattern="test_*.py",
)

runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

# Summary
print(f"\n{'='*60}")
print(f"TESTS RUN: {result.testsRun}")
print(f"PASSED: {result.testsRun - len(result.failures) - len(result.errors)}")
print(f"FAILURES: {len(result.failures)}")
print(f"ERRORS: {len(result.errors)}")
print(f"{'='*60}")

sys.exit(0 if result.wasSuccessful() else 1)
