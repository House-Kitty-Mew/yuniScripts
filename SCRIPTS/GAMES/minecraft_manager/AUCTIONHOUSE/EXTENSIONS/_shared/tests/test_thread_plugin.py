"""
test_thread_plugin.py — Tests for THREAD plugin interface.

Tests:
  - Discovery logic (finds THREAD modules correctly)
  - Lazy import works (can import known symbols)
  - Graceful degradation when THREAD unavailable
  - importlib-based discovery vs old sys.path approach
  - Module-level convenience functions
"""

import sys, os
from unittest import TestCase, main

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED_DIR = os.path.dirname(_HERE)
_EXT_DIR = os.path.dirname(_SHARED_DIR)
_AH_DIR = os.path.dirname(_EXT_DIR)
for p in [_AH_DIR, _EXT_DIR, _SHARED_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from EXTENSIONS._shared.thread_plugin import (
    lazy_thread, thread_available, get_thread_modules,
    _THREAD_SEARCH_PATHS, _THREAD_MODULES,
)


class TestThreadDiscovery(TestCase):
    """THREAD directory discovery logic."""

    def test_search_paths_exist(self):
        """At least one search path should contain thread modules."""
        from EXTENSIONS._shared.thread_plugin import _find_thread_dir
        thread_dir = _find_thread_dir()
        self.assertIsNotNone(thread_dir,
                             "THREAD modules should be discoverable")
        self.assertTrue(os.path.isdir(thread_dir))
        # Verify thread_memory_graph.py exists
        self.assertTrue(
            os.path.isfile(os.path.join(thread_dir, "thread_memory_graph.py")),
            f"thread_memory_graph.py not found in {thread_dir}"
        )

    def test_search_paths_listed(self):
        """Known THREAD directories should be in the search list."""
        self.assertGreater(len(_THREAD_SEARCH_PATHS), 0)
        # At least one should exist
        found = [p for p in _THREAD_SEARCH_PATHS if os.path.isdir(p)]
        self.assertGreater(len(found), 0,
                           "At least one THREAD search path should exist")


class TestLazyThreadImports(TestCase):
    """Lazy import via _LazyThread container."""

    def test_lazy_thread_available(self):
        """lazy_thread.available should be True when THREAD modules exist."""
        self.assertTrue(
            lazy_thread.available,
            "THREAD should be available (modules exist in yuniScripts)"
        )

    def test_can_import_memory_graph_store(self):
        """MemoryGraphStore should be importable via lazy_thread."""
        mgs = lazy_thread.MemoryGraphStore
        self.assertIsNotNone(mgs)
        # Verify it's a class
        import inspect
        self.assertTrue(inspect.isclass(mgs))

    def test_can_import_activation_engine(self):
        """ActivationEngine should be importable via lazy_thread."""
        ae = lazy_thread.ActivationEngine
        self.assertIsNotNone(ae)
        import inspect
        self.assertTrue(inspect.isclass(ae))

    def test_can_import_hybrid_retriever(self):
        """HybridRetriever should be importable."""
        hr = lazy_thread.HybridRetriever
        self.assertIsNotNone(hr)

    def test_can_import_event_bus(self):
        """EventBus and get_event_bus should be importable."""
        eb = lazy_thread.EventBus
        ge = lazy_thread.get_event_bus
        self.assertIsNotNone(eb)
        self.assertIsNotNone(ge)

    def test_can_import_constants(self):
        """Relation type constants should be importable."""
        sgt = lazy_thread.SHOULD_GO_TOGETHER
        mgt = lazy_thread.MIGHT_GO_TOGETHER
        wgt = lazy_thread.WENT_TOGETHER_BEFORE
        wngt = lazy_thread.WILL_NOT_GO_TOGETHER
        aar = lazy_thread.ALMOST_ABSOLUTE_REJECTION
        self.assertIsNotNone(sgt)
        self.assertIsNotNone(mgt)
        self.assertIsNotNone(wgt)
        self.assertIsNotNone(wngt)
        self.assertIsNotNone(aar)

    def test_all_defined_symbols_importable(self):
        """Every symbol in _THREAD_MODULES should be importable."""
        for name, (module_name, symbol_name) in _THREAD_MODULES.items():
            with self.subTest(symbol=name):
                symbol = getattr(lazy_thread, name, None)
                self.assertIsNotNone(
                    symbol,
                    f"Symbol '{name}' ({module_name}.{symbol_name}) should be importable"
                )

    def test_unknown_attr_raises(self):
        """Unknown attributes should raise AttributeError."""
        with self.assertRaises(AttributeError):
            _ = lazy_thread.NonExistentSymbol


class TestThreadPluginConvenience(TestCase):
    """Convenience functions."""

    def test_thread_available_function(self):
        """thread_available() should match lazy_thread.available."""
        self.assertEqual(thread_available(), lazy_thread.available)

    def test_get_thread_modules_returns_singleton(self):
        """get_thread_modules() returns the same instance."""
        tm1 = get_thread_modules()
        tm2 = get_thread_modules()
        self.assertIs(tm1, tm2)
        self.assertIs(tm1, lazy_thread)

    def test_search_path_property(self):
        """search_path should be a valid directory."""
        path = lazy_thread.search_path
        self.assertIsNotNone(path)
        self.assertTrue(os.path.isdir(path))


if __name__ == "__main__":
    main()
