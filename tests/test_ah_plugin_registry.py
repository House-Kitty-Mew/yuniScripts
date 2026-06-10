#!/usr/bin/env python3
"""
test_ah_plugin_registry.py — Comprehensive unittest suite for the AH plugin
registry (_HookRegistry module).

Tests 10 edge cases:
   1. Unknown hook registration is logged and ignored
   2. Same extension registers same hook twice (duplicate callbacks)
   3. Hook callback raises exception – other callbacks still fire
   4. Thread safety of _hooks dict during iteration
   5. Empty hook list returns []
   6. register/unregister race with fire()
   7. Extension import failure during discover_and_load
   8. Fire on invalid hook returns []
   9. Multiple callbacks all succeeding
  10. unregister_all removes all hooks

Run: python3 -m unittest tests.test_ah_plugin_registry -v
"""

import os
import sys
import time
import json
import threading
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call
from pathlib import Path
from typing import Callable

# ── Path setup ───────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Insert project root first so engine/* modules resolve correctly
# even when other test suites insert different paths.
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)
_AH_PATH = str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager")
if _AH_PATH not in sys.path:
    sys.path.insert(0, _AH_PATH)


# ── Imports under test ───────────────────────────────────────────────
from AUCTIONHOUSE.ah_plugin_registry import (
    _HookRegistry,
    VALID_HOOKS,
    get_registry,
    fire_hook,
    discover_extensions,
    register_hook,
)

# Helper: pick a few known-good hooks for tests
_HOOK_A = "on_simulation_cycle_start"
_HOOK_B = "on_listing_created"
_HOOK_C = "on_purchase"
_HOOK_INVALID = "on_nonexistent_hook_xyz"


# =====================================================================
# 1. Unknown hook registration is logged and ignored
# =====================================================================
class TestUnknownHookIgnored(unittest.TestCase):
    """Registering a hook not in VALID_HOOKS must log a warning and
    MUST NOT add an entry to the _hooks dict."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()

    def test_unknown_hook_warns_and_is_ignored(self):
        hook_count_before = len(self.registry._hooks)
        # Attempt to register an invalid hook
        self.registry.register(_HOOK_INVALID, "TestExt", lambda **kw: "ok")

        # The invalid hook must NOT be added as a new key
        self.assertNotIn(_HOOK_INVALID, self.registry._hooks)
        self.assertEqual(len(self.registry._hooks), hook_count_before)
        # The log.warn must be called
        self.mock_log.warn.assert_called_once()
        args, _ = self.mock_log.warn.call_args
        self.assertIn(_HOOK_INVALID, str(args))

    def test_unknown_hook_fire_returns_empty(self):
        """Firing an unknown hook also returns [] directly."""
        result = self.registry.fire(_HOOK_INVALID, key="val")
        self.assertEqual(result, [])


# =====================================================================
# 2. Same extension registers same hook twice (duplicate callbacks)
# =====================================================================
class TestDuplicateRegistration(unittest.TestCase):
    """The registry does NOT deduplicate; same callback registered twice
    results in two entries, both of which fire."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()
        self.call_count = 0

    def _callback(self, **kw):
        self.call_count += 1
        return self.call_count

    def test_duplicate_same_callback_registered_twice(self):
        ext = "DupeExt"
        # Bind the callback once so we pass the exact same object
        cb = self._callback
        self.registry.register(_HOOK_A, ext, cb)
        self.registry.register(_HOOK_A, ext, cb)

        hooks = self.registry._hooks[_HOOK_A]
        self.assertEqual(len(hooks), 2)
        # Both entries should point to the same function object
        self.assertIs(hooks[0][1], hooks[1][1])

        results = self.registry.fire(_HOOK_A)
        self.assertEqual(len(results), 2)
        # Each should have fired, so call_count should be 2
        self.assertEqual(self.call_count, 2)
        # Results should show both as ok
        for r in results:
            self.assertTrue(r["ok"])

    def test_duplicate_different_callbacks(self):
        """Two different callbacks for same hook/extension."""
        count = [0, 0]

        def cb1(**kw): count[0] += 1; return "a"
        def cb2(**kw): count[1] += 1; return "b"

        self.registry.register(_HOOK_A, "Ext", cb1)
        self.registry.register(_HOOK_A, "Ext", cb2)

        results = self.registry.fire(_HOOK_A)
        self.assertEqual(len(results), 2)
        self.assertEqual(count, [1, 1])
        self.assertEqual(results[0]["data"], "a")
        self.assertEqual(results[1]["data"], "b")


# =====================================================================
# 3. Hook callback raises exception – verify other callbacks still fire
# =====================================================================
class TestCallbackExceptionIsolation(unittest.TestCase):
    """A crashing callback must NOT prevent other callbacks from running."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()
        self.good_called = False

    def _good_callback(self, **kw):
        self.good_called = True
        return "success"

    def _bad_callback(self, **kw):
        raise RuntimeError("Intentional crash for test")

    def test_exception_does_not_block_others(self):
        self.registry.register(_HOOK_A, "BadExt", self._bad_callback)
        self.registry.register(_HOOK_A, "GoodExt", self._good_callback)

        results = self.registry.fire(_HOOK_A)

        # Good callback must have been called
        self.assertTrue(self.good_called)
        self.assertEqual(len(results), 2)

        # Check individual results
        result_bad = results[0]  # registered first
        result_good = results[1]

        self.assertEqual(result_bad["extension"], "BadExt")
        self.assertFalse(result_bad["ok"])
        self.assertIn("Intentional crash", result_bad["error"])

        self.assertEqual(result_good["extension"], "GoodExt")
        self.assertTrue(result_good["ok"])
        self.assertEqual(result_good["data"], "success")

    def test_all_bad_all_logged(self):
        """Multiple bad callbacks — each failure is isolated."""
        self.registry.register(_HOOK_B, "B1", lambda **kw: (_ for _ in ()).throw(ValueError("b1")))
        self.registry.register(_HOOK_B, "B2", lambda **kw: (_ for _ in ()).throw(TypeError("b2")))

        results = self.registry.fire(_HOOK_B)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertFalse(r["ok"])


# =====================================================================
# 4. Thread safety of _hooks dict during iteration
# =====================================================================
class TestThreadSafetyDuringFire(unittest.TestCase):
    """fire() copies the callback list under lock; concurrent register()
    must not cause races or corrupt iteration."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()
        self.caught = []

    def _slow_callback(self, **kw):
        """Callback that pauses so a racing thread can try to register."""
        self.caught.append("in_slow")
        time.sleep(0.15)
        self.caught.append("out_slow")
        return "ok"

    def test_concurrent_register_during_fire(self):
        """Verify that a register() in another thread while fire() runs
        does not affect the snapshot that is already being iterated."""
        self.registry.register(_HOOK_A, "SlowExt", self._slow_callback)

        racing_results = []

        def race_register():
            time.sleep(0.05)  # let fire() start and take its snapshot
            self.registry.register(_HOOK_A, "LateExt", lambda **kw: "late")
            racing_results.append("registered")

        t = threading.Thread(target=race_register, daemon=True)
        t.start()

        results = self.registry.fire(_HOOK_A)
        t.join()

        # The fire() should only see the slow callback (1 result)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["extension"], "SlowExt")
        self.assertEqual(results[0]["data"], "ok")
        self.assertEqual(self.caught, ["in_slow", "out_slow"])

        # But the late extension IS now registered
        self.assertEqual(len(self.registry._hooks[_HOOK_A]), 2)

    def test_concurrent_fire_unregister_all(self):
        """Fire in one thread while unregister_all in another."""
        def cb(**kw): return "alive"

        self.registry.register(_HOOK_A, "Target", cb)
        self.registry.register(_HOOK_B, "Target", cb)

        unregistered_ok = []

        def unregister_race():
            time.sleep(0.05)
            self.registry.unregister_all("Target")
            unregistered_ok.append(True)

        t = threading.Thread(target=unregister_race, daemon=True)
        t.start()

        results = self.registry.fire(_HOOK_A)
        t.join()

        # The fire must see the callback (because it copies under lock)
        # BUT the lock is shared; unregister_all waits on the lock,
        # and fire() copies then releases. So the timing determines
        # whether the callback is included. Both outcomes are acceptable
        # as long as there's no crash.
        # At minimum fire must not crash and must return a list.
        self.assertIsInstance(results, list)


# =====================================================================
# 5. Empty hook list returns []
# =====================================================================
class TestEmptyHookReturnsEmpty(unittest.TestCase):
    """Firing a valid hook that has zero registered callbacks returns [].

    This is the nominal "no extensions loaded" case."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()

    def test_empty_hook_returns_empty_list(self):
        for hook in [_HOOK_A, _HOOK_B, _HOOK_C]:
            result = self.registry.fire(hook, data="test")
            self.assertEqual(result, [])
            # Verify the hook list is indeed empty
            self.assertEqual(len(self.registry._hooks[hook]), 0)

    def test_empty_hook_after_unregister_all(self):
        """After unregister_all, firing returns []."""
        self.registry.register(_HOOK_A, "Ext", lambda **kw: "x")
        self.registry.unregister_all("Ext")
        result = self.registry.fire(_HOOK_A)
        self.assertEqual(result, [])


# =====================================================================
# 6. register/unregister race with fire()
# =====================================================================
class TestRegisterUnregisterRace(unittest.TestCase):
    """Stress the registry with concurrent register/unregister/fire."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()

    def test_stress_concurrent_ops(self):
        """Spawn multiple threads that register, unregister, and fire
        simultaneously.  The system must not deadlock or crash."""
        N_THREADS = 10
        errors = []
        lock = threading.Lock()

        def worker(worker_id: int):
            try:
                for i in range(20):
                    ext = f"Worker{worker_id}-{i}"
                    # Register
                    self.registry.register(_HOOK_A, ext, lambda **kw: ext)
                    # Fire
                    self.registry.fire(_HOOK_A)
                    # Unregister
                    self.registry.unregister_all(ext)
                    # Fire again (should be empty)
                    self.registry.fire(_HOOK_A)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(wid,), daemon=True)
                   for wid in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"Concurrent ops produced errors: {errors}")

    def test_fire_mid_register_unregister_cycle(self):
        """Exact scenario: register → start fire → unregister during fire
        → assert no crash and deterministic result shape."""
        barrier = threading.Barrier(2, timeout=5)

        def delayed_unregister():
            barrier.wait()
            self.registry.unregister_all("Racer")

        def cb(**kw): return "hit"

        self.registry.register(_HOOK_A, "Racer", cb)

        t = threading.Thread(target=delayed_unregister, daemon=True)
        t.start()

        barrier.wait()
        results = self.registry.fire(_HOOK_A)
        t.join()

        # fire() copies callbacks under the same lock,
        # so either the callback is in or not — either is valid
        self.assertIsInstance(results, list)
        self.assertLessEqual(len(results), 1)
        if results:
            self.assertEqual(results[0]["extension"], "Racer")


# =====================================================================
# 7. Extension import failure during discover_and_load
# =====================================================================
class TestDiscoverAndLoadFailure(unittest.TestCase):
    """If an extension's __init__.py raises during import, the error
    must be logged and the system must continue loading others."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()
        # Create a temporary extensions directory
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.ext_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_extension(self, name: str, code: str):
        """Helper: create an extension subdirectory with __init__.py."""
        ext_path = self.ext_dir / name
        ext_path.mkdir(parents=True, exist_ok=True)
        with open(ext_path / "__init__.py", "w") as f:
            f.write(code)

    def test_broken_extension_import_error(self):
        """Extension with syntax error logs error and continues."""
        self._create_extension("GOOD_EXT", """
def on_load(registry):
    registry.register("on_simulation_cycle_start", "GOOD_EXT", lambda **kw: "ok")
""")
        self._create_extension("BROKEN_EXT", """
this is not valid python @@
""")
        self._create_extension("ANOTHER_GOOD", """
def on_load(registry):
    registry.register("on_listing_created", "ANOTHER_GOOD", lambda **kw: "ok")
""")

        # discover_and_load should not crash
        self.registry.discover_and_load(str(self.ext_dir))

        # The good extensions should be registered
        self.assertEqual(len(self.registry._hooks[_HOOK_A]), 1)
        self.assertEqual(len(self.registry._hooks[_HOOK_B]), 1)

        # The broken extension should have triggered an error log
        error_calls = [c for c in self.mock_log.error.call_args_list
                       if "BROKEN_EXT" in str(c)]
        self.assertGreaterEqual(len(error_calls), 1, "Broken extension should be logged")

        # Registry should still be marked loaded
        self.assertTrue(self.registry._extensions_loaded)

    def test_missing_on_load(self):
        """Extension that imports but has no on_load() is silently skipped."""
        self._create_extension("NO_ONLOAD", """
# Valid Python, but no on_load function
x = 42
""")
        self.registry.discover_and_load(str(self.ext_dir))
        # No hooks registered, but no crash; a log.info should mention
        # that the extension has no on_load()
        info_messages = [str(c) for c in self.mock_log.info.call_args_list]
        has_no_onload = any("no on_load" in msg or "NO_ONLOAD" in msg for msg in info_messages)
        self.assertTrue(has_no_onload,
                        "Should log that extension has no on_load()")

    def test_nonexistent_extensions_dir(self):
        """If the EXTENSIONS dir does not exist, log info and set flag."""
        fake_dir = "/tmp/this_does_not_exist_xyz789"
        self.registry.discover_and_load(fake_dir)
        self.assertTrue(self.registry._extensions_loaded)
        # Should have logged info about missing dir
        info_messages = [str(c) for c in self.mock_log.info.call_args_list]
        has_missing = any("No EXTENSIONS directory" in msg for msg in info_messages)
        self.assertTrue(has_missing)

    def test_private_dirs_skipped(self):
        """Directories starting with underscore are skipped."""
        self._create_extension("_private", """
def on_load(registry):
    registry.register("on_purchase", "_private", lambda **kw: "nope")
""")
        self._create_extension("PUBLIC", """
def on_load(registry):
    registry.register("on_purchase", "PUBLIC", lambda **kw: "ok")
""")
        self.registry.discover_and_load(str(self.ext_dir))
        # Only PUBLIC should be registered
        self.assertEqual(len(self.registry._hooks[_HOOK_C]), 1)
        self.assertEqual(self.registry._hooks[_HOOK_C][0][0], "PUBLIC")

    def test_double_discover_noop(self):
        """Calling discover_and_load twice does nothing the second time."""
        self._create_extension("ONCE", """
def on_load(registry):
    registry.register("on_simulation_cycle_start", "ONCE", lambda **kw: "once")
""")
        self.registry.discover_and_load(str(self.ext_dir))
        count1 = len(self.registry._hooks[_HOOK_A])

        # The flag is set; second call should be no-op
        self.registry.discover_and_load(str(self.ext_dir))
        count2 = len(self.registry._hooks[_HOOK_A])
        self.assertEqual(count1, count2)


# =====================================================================
# 8. Fire on invalid hook returns []
# =====================================================================
class TestFireInvalidHook(unittest.TestCase):
    """Firing a hook not in VALID_HOOKS must return [] immediately."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()

    def test_fire_nonexistent_hook_returns_empty(self):
        result = self.registry.fire("this_hook_does_not_exist")
        self.assertEqual(result, [])

    def test_fire_empty_string_hook(self):
        result = self.registry.fire("")
        self.assertEqual(result, [])

    def test_fire_none_hook_returns_empty(self):
        """None is not a valid hook string."""
        result = self.registry.fire(None)  # type: ignore[arg-type]
        self.assertEqual(result, [])

    def test_fire_invalid_hook_with_args_returns_empty(self):
        result = self.registry.fire(_HOOK_INVALID, extra="data", number=42)
        self.assertEqual(result, [])


# =====================================================================
# 9. Multiple callbacks all succeeding
# =====================================================================
class TestMultipleCallbacksAllSucceed(unittest.TestCase):
    """Register many callbacks on several hooks — all must fire and
    return ok=True."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()
        self.order = []

    def test_three_callbacks_on_same_hook(self):
        cbs = ["A", "B", "C"]

        for letter in cbs:
            self.registry.register(
                _HOOK_A, f"Ext{letter}",
                lambda l=letter, **kw: f"result_{l}"
            )

        results = self.registry.fire(_HOOK_A, phase="test")
        self.assertEqual(len(results), 3)
        for i, letter in enumerate(cbs):
            self.assertTrue(results[i]["ok"], f"Ext{letter} should succeed")
            self.assertEqual(results[i]["data"], f"result_{letter}")

    def test_callbacks_share_kwargs(self):
        """All callbacks receive the same kwargs."""
        received = []

        def cb1(**kw): received.append(dict(kw)); return 1
        def cb2(**kw): received.append(dict(kw)); return 2

        self.registry.register(_HOOK_B, "A", cb1)
        self.registry.register(_HOOK_B, "B", cb2)
        self.registry.fire(_HOOK_B, player="Alice", amount=100)

        for recv in received:
            self.assertEqual(recv["player"], "Alice")
            self.assertEqual(recv["amount"], 100)

    def test_across_multiple_hooks(self):
        """Callbacks on different hooks only fire when their hook fires."""
        hook_a_results = []
        hook_b_results = []

        def cb_a(**kw): hook_a_results.append(kw); return "a"
        def cb_b(**kw): hook_b_results.append(kw); return "b"

        self.registry.register(_HOOK_A, "ExtA", cb_a)
        self.registry.register(_HOOK_B, "ExtB", cb_b)

        self.registry.fire(_HOOK_A, msg="only A")
        self.assertEqual(len(hook_a_results), 1)
        self.assertEqual(len(hook_b_results), 0)

        self.registry.fire(_HOOK_B, msg="only B")
        self.assertEqual(len(hook_a_results), 1)
        self.assertEqual(len(hook_b_results), 1)

    def test_preserves_registration_order(self):
        """Callbacks fire in the order they were registered."""
        order = []

        def mk_cb(label):
            def cb(**kw):
                order.append(label)
                return label
            return cb

        self.registry.register(_HOOK_A, "First", mk_cb("first"))
        self.registry.register(_HOOK_A, "Second", mk_cb("second"))
        self.registry.register(_HOOK_A, "Third", mk_cb("third"))

        self.registry.fire(_HOOK_A)
        self.assertEqual(order, ["first", "second", "third"])

    def test_large_number_of_callbacks(self):
        """50 callbacks all on the same hook — verify count and no crash."""
        N = 50
        for i in range(N):
            self.registry.register(
                _HOOK_A, f"Massive{i}",
                lambda idx=i, **kw: idx
            )

        results = self.registry.fire(_HOOK_A)
        self.assertEqual(len(results), N)
        self.assertTrue(all(r["ok"] for r in results))


# =====================================================================
# 10. unregister_all removes all hooks
# =====================================================================
class TestUnregisterAll(unittest.TestCase):
    """unregister_all(extension_name) must remove every hook for that
    extension across all hook points."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()

    def test_unregister_all_removes_from_multiple_hooks(self):
        """Extension registers on 3 hooks; unregister_all removes all."""
        ext = "MultiExt"

        def cb_a(**kw): return "a"
        def cb_b(**kw): return "b"
        def cb_c(**kw): return "c"

        self.registry.register(_HOOK_A, ext, cb_a)
        self.registry.register(_HOOK_B, ext, cb_b)
        self.registry.register(_HOOK_C, ext, cb_c)

        # Verify all present before
        self.assertEqual(len(self.registry._hooks[_HOOK_A]), 1)
        self.assertEqual(len(self.registry._hooks[_HOOK_B]), 1)
        self.assertEqual(len(self.registry._hooks[_HOOK_C]), 1)

        self.registry.unregister_all(ext)

        # Verify all removed
        self.assertEqual(len(self.registry._hooks[_HOOK_A]), 0)
        self.assertEqual(len(self.registry._hooks[_HOOK_B]), 0)
        self.assertEqual(len(self.registry._hooks[_HOOK_C]), 0)

        # Firing each should return []
        self.assertEqual(self.registry.fire(_HOOK_A), [])
        self.assertEqual(self.registry.fire(_HOOK_B), [])
        self.assertEqual(self.registry.fire(_HOOK_C), [])

    def test_unregister_all_other_extensions_unaffected(self):
        """Unregistering one extension must leave others intact."""
        def cb_keep(**kw): return "keep"
        def cb_remove(**kw): return "remove"

        self.registry.register(_HOOK_A, "KeepExt", cb_keep)
        self.registry.register(_HOOK_A, "RemoveExt", cb_remove)

        self.registry.unregister_all("RemoveExt")

        results = self.registry.fire(_HOOK_A)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["extension"], "KeepExt")
        self.assertEqual(results[0]["data"], "keep")

    def test_unregister_all_twice_noop(self):
        """Calling unregister_all on an already-unregistered extension
        must not crash and must be a no-op."""
        self.registry.register(_HOOK_A, "Gone", lambda **kw: "x")
        self.registry.unregister_all("Gone")
        # Second call — should not crash
        self.registry.unregister_all("Gone")
        self.assertEqual(len(self.registry._hooks[_HOOK_A]), 0)

    def test_unregister_all_nonexistent_extension(self):
        """Unregistering an extension that was never registered must not
        affect existing hooks."""
        self.registry.register(_HOOK_A, "Real", lambda **kw: "real")
        self.registry.unregister_all("NeverRegistered")
        self.assertEqual(len(self.registry._hooks[_HOOK_A]), 1)

    def test_unregister_all_partial_cleanup(self):
        """Extension has hooks on some but not all hooks — verify only
        its hooks are removed."""
        def cb(**kw): return "x"

        self.registry.register(_HOOK_A, "Partial", cb)
        self.registry.register(_HOOK_B, "Partial", cb)
        # NOT registering on _HOOK_C

        # Also register a different extension
        self.registry.register(_HOOK_A, "Other", cb)

        self.registry.unregister_all("Partial")

        self.assertEqual(len(self.registry._hooks[_HOOK_A]), 1)
        self.assertEqual(self.registry._hooks[_HOOK_A][0][0], "Other")
        self.assertEqual(len(self.registry._hooks[_HOOK_B]), 0)
        # _HOOK_C was never touched


# =====================================================================
# Global convenience wrappers (lightweight integration checks)
# =====================================================================
class TestGlobalConvenienceWrappers(unittest.TestCase):
    """Smoke tests for get_registry(), fire_hook(), register_hook(),
    and discover_extensions().  These call the real singleton."""

    def tearDown(self):
        # Ensure we don't pollute the global singleton for other tests
        from AUCTIONHOUSE.ah_plugin_registry import _registry, _registry_lock
        with _registry_lock:
            _registry = None

    @patch("AUCTIONHOUSE.ah_plugin_registry.log")
    def test_get_registry_singleton(self, mock_log):
        reg1 = get_registry()
        reg2 = get_registry()
        self.assertIs(reg1, reg2)

    @patch("AUCTIONHOUSE.ah_plugin_registry.log")
    def test_fire_hook_safe_uninitialized(self, mock_log):
        """fire_hook must return [] even if registry hasn't been init'd."""
        result = fire_hook(_HOOK_A)
        self.assertEqual(result, [])

    @patch("AUCTIONHOUSE.ah_plugin_registry.log")
    def test_register_hook_and_fire(self, mock_log):
        results = []
        def cb(**kw): results.append(kw["val"]); return "done"

        register_hook(_HOOK_A, "TestExt", cb)
        try:
            fire_result = fire_hook(_HOOK_A, val=42)
            self.assertEqual(len(fire_result), 1)
            self.assertTrue(fire_result[0]["ok"])
            self.assertEqual(fire_result[0]["data"], "done")
            self.assertEqual(results, [42])
        finally:
            # Cleanup the global singleton
            reg = get_registry()
            reg.unregister_all("TestExt")


# =====================================================================
# Validation hooks dict structure
# =====================================================================
class TestHookRegistryStructure(unittest.TestCase):
    """Structural invariants of the registry."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()

    def test_all_valid_hooks_have_lists(self):
        """Every hook key has a list value."""
        for hook in VALID_HOOKS:
            self.assertIn(hook, self.registry._hooks)
            self.assertIsInstance(self.registry._hooks[hook], list)

    def test_no_extra_keys_in_hooks(self):
        """_hooks dict has exactly the keys in VALID_HOOKS."""
        self.assertEqual(set(self.registry._hooks.keys()), VALID_HOOKS)

    def test_initial_hook_lists_are_empty(self):
        for hook in VALID_HOOKS:
            self.assertEqual(len(self.registry._hooks[hook]), 0,
                             f"Hook '{hook}' should start empty")


# =====================================================================
# Registry flag and idempotency
# =====================================================================
class TestExtensionsLoadedFlag(unittest.TestCase):
    """_extensions_loaded flag and guard correctly."""

    def setUp(self):
        patcher = patch("AUCTIONHOUSE.ah_plugin_registry.log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = _HookRegistry()

    def test_flag_starts_false(self):
        self.assertFalse(self.registry._extensions_loaded)

    def test_flag_set_after_discover(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.registry.discover_and_load(tmp)
        self.assertTrue(self.registry._extensions_loaded)

    def test_second_discover_is_noop(self):
        """If flag is already True, discover_and_load returns early."""
        self.registry._extensions_loaded = True
        # Patch the actual filesystem check path
        with patch("pathlib.Path.exists", return_value=True) as mock_exists:
            self.registry.discover_and_load("/fake")
            # exists() should NOT be called because flag short-circuits
            mock_exists.assert_not_called()


# =====================================================================
# Main
# =====================================================================
if __name__ == "__main__":
    unittest.main(verbosity=2)
