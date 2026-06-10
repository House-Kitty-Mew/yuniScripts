#!/usr/bin/env python3
"""
B4 Verification Test: Verify all open() calls have proper guards.

Tests that god_watcher.py, main.py, and related files properly guard
their file operations against missing files with os.path.exists()
checks and/or try/except blocks.
"""

import unittest
from pathlib import Path


GOD_WATCHER_PATH = Path(__file__).parent.parent / 'god_watcher.py'
MAIN_PATH = Path(__file__).parent.parent / 'main.py'
MSM_MAIN_PATH = Path('/home/deck/Documents/dev-yuniScripts/SCRIPTS/SERVICES/multi-server-manager/main.py')


class TestB4OpenCallProtection(unittest.TestCase):
    """Verify all open() calls have proper guards (B4 fix verification)."""

    # ── G3: create_notification ────────────────────────────────────

    def test_g3_notification_has_exists_guard(self):
        """G3 create_notification checks os.path.exists() before open()."""
        source = GOD_WATCHER_PATH.read_text()
        in_method = False
        has_exists = False
        has_open_read = False
        has_open_write = False
        for line in source.split('\n'):
            s = line.strip()
            if 'def create_notification' in s:
                in_method = True
                continue
            if in_method:
                if s.startswith('def '):
                    break
                if 'os.path.exists' in s:
                    has_exists = True
                if "with open(notify_path, 'r')" in s:
                    has_open_read = True
                if "with open(notify_path, 'w')" in s:
                    has_open_write = True
        self.assertTrue(has_exists,
                        "create_notification must check os.path.exists()")
        self.assertTrue(has_open_read,
                        "create_notification must read notify_path")
        self.assertTrue(has_open_write,
                        "create_notification must write notify_path")

    def test_g3_notification_has_try_except(self):
        """G3 create_notification wraps file ops in try/except."""
        source = GOD_WATCHER_PATH.read_text()
        start = source.find('def create_notification')
        end = source.find('\n    def ', start + 10)
        if end == -1:
            end = source.find('\nclass ', start + 10)
        method_body = source[start:end]
        self.assertIn('try:', method_body,
                      "create_notification must use try/except")
        self.assertIn('except Exception', method_body,
                      "create_notification must catch exceptions")

    # ── G4: _create_alert_notification ────────────────────────────

    def test_g4_alert_has_makedirs_guard(self):
        """G4 _create_alert_notification uses os.makedirs before open()."""
        source = GOD_WATCHER_PATH.read_text()
        in_method = False
        has_makedirs = False
        has_alert_write = False
        has_history_write = False
        has_try = False
        for line in source.split('\n'):
            s = line.strip()
            if 'def _create_alert_notification' in s:
                in_method = True
                continue
            if in_method:
                if s.startswith('def '):
                    break
                if 'os.makedirs' in s:
                    has_makedirs = True
                if "with open(alert_path, 'w')" in s:
                    has_alert_write = True
                if "with open(history_path, 'a')" in s:
                    has_history_write = True
                if s.startswith('try:'):
                    has_try = True
        self.assertTrue(has_makedirs,
                        "_create_alert_notification must call os.makedirs")
        self.assertTrue(has_alert_write,
                        "_create_alert_notification must write alert_path")
        self.assertTrue(has_history_write,
                        "_create_alert_notification must write history_path")
        self.assertTrue(has_try,
                        "_create_alert_notification must wrap in try/except")

    # ── fastmcp_server main.py ────────────────────────────────────

    def test_main_load_config_has_try_except(self):
        """FastMCP main.py _load_config catches FileNotFoundError."""
        source = MAIN_PATH.read_text()
        self.assertIn('FileNotFoundError', source,
                      "main.py must catch FileNotFoundError")
        self.assertIn('try:', source,
                      "main.py must use try/except for file ops")

    # ── multi-server-manager main.py ──────────────────────────────

    def test_msm_main_has_exists_guard(self):
        """multi-server-manager main.py guards open() with .exists()."""
        if not MSM_MAIN_PATH.exists():
            self.skipTest("multi-server-manager main.py not found")
        source = MSM_MAIN_PATH.read_text()
        has_exists = '.exists()' in source
        has_try = 'try:' in source
        has_open = 'open(' in source
        self.assertTrue(has_exists or has_try,
                        "MSM main.py must guard open() calls")
        self.assertTrue(has_open,
                        "MSM main.py must have open() calls")


if __name__ == '__main__':
    unittest.main(verbosity=2)
