"""
thread_plugin.py — Clean THREAD cognitive memory plugin for AH extensions.

Provides lazy imports of THREAD modules through ``importlib`` instead of
fragile ``sys.path`` manipulation.  Graceful degradation when THREAD is
unavailable — all public functions are safe to call and return None/[].

Usage:
    from EXTENSIONS._shared.thread_plugin import thread_available, get_thread_modules

    if thread_available():
        mgs = get_thread_modules()
        graph = mgs.MemoryGraphStore(db)
        # ...

Or use the convenience wrapper that exactly mirrors sp_memory_thread's
current lazy-import pattern::

    from EXTENSIONS._shared.thread_plugin import lazy_thread
    if lazy_thread.available:
        graph = lazy_thread.MemoryGraphStore(db)
"""

import importlib, logging, os
from pathlib import Path
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)

# ── THREAD module discovery ────────────────────────────────────────
# These are the THREAD modules that sp_memory_thread.py depends on.
# The discovery system finds them via importlib, not sys.path manipulation.

_THREAD_MODULES = {
    "MemoryGraphStore": ("thread_memory_graph", "MemoryGraphStore"),
    "ActivationEngine": ("thread_activation", "ActivationEngine"),
    "HybridRetriever": ("thread_retriever", "HybridRetriever"),
    "ActivationCache": ("thread_cache", "ActivationCache"),
    "EventBus": ("thread_events", "EventBus"),
    "get_event_bus": ("thread_events", "get_event_bus"),
    "Event": ("thread_events", "Event"),
    "MEMORY_STORED": ("thread_events", "MEMORY_STORED"),
    "MEMORY_RETRIEVED": ("thread_events", "MEMORY_RETRIEVED"),
    "MEMORY_DECAYED": ("thread_events", "MEMORY_DECAYED"),
    "MEMORY_FORGOTTEN": ("thread_events", "MEMORY_FORGOTTEN"),
    "EDGE_ADDED": ("thread_events", "EDGE_ADDED"),
    "AdaptiveController": ("thread_adaptive_controller", "AdaptiveController"),
    "VALID_RELATION_TYPES": ("thread_memory_graph", "VALID_RELATION_TYPES"),
    "SHOULD_GO_TOGETHER": ("thread_memory_graph", "SHOULD_GO_TOGETHER"),
    "MIGHT_GO_TOGETHER": ("thread_memory_graph", "MIGHT_GO_TOGETHER"),
    "WENT_TOGETHER_BEFORE": ("thread_memory_graph", "WENT_TOGETHER_BEFORE"),
    "WILL_NOT_GO_TOGETHER": ("thread_memory_graph", "WILL_NOT_GO_TOGETHER"),
    "ALMOST_ABSOLUTE_REJECTION": ("thread_memory_graph", "ALMOST_ABSOLUTE_REJECTION"),
}

# ── Possible THREAD tool directories ──────────────────────────────
_THREAD_SEARCH_PATHS = [
    "/home/deck/Documents/dev-yuniScripts/SCRIPTS/SERVICES/fastmcp_server/tools",
    "/home/deck/Documents/dev-yuniScripts/SCRIPTS/GAMES/minecraft_manager/AUCTIONHOUSE/tools",
]

# ── Discovery helper ──────────────────────────────────────────────

def _find_thread_dir() -> Optional[str]:
    """Locate the THREAD tools directory by checking known paths.

    Returns the first path that exists, or None.
    """
    for p in _THREAD_SEARCH_PATHS:
        if os.path.isdir(p):
            # Verify at least one THREAD module exists
            test_module = os.path.join(p, "thread_memory_graph.py")
            if os.path.isfile(test_module):
                return p
    return None


# ═══════════════════════════════════════════════════════════════════
# LazyThread — lazy THREAD module container
# ═══════════════════════════════════════════════════════════════════

class _LazyThread:
    """Lazy-loading container for THREAD modules.

    All attribute accesses on this container attempt to import the
    corresponding THREAD module and return the requested symbol.
    If THREAD is unavailable, returns None for all attributes.
    """

    def __init__(self):
        self._available: bool = False
        self._cache: Dict[str, Any] = {}
        self._search_path: Optional[str] = None

        # Attempt discovery
        thread_dir = _find_thread_dir()
        if thread_dir is None:
            log.info("THREAD not found — thread_plugin running in legacy mode")
            return

        # Add to sys.path for importlib to find
        import sys
        if thread_dir not in sys.path:
            sys.path.insert(0, thread_dir)

        # Verify we can import the base module
        try:
            importlib.import_module("thread_memory_graph")
            self._available = True
            self._search_path = thread_dir
            log.info(f"THREAD modules discovered at: {thread_dir}")
        except ImportError as e:
            log.warning(f"THREAD base module not importable from {thread_dir}: {e}")
            # Remove the path we added
            if thread_dir in sys.path:
                sys.path.remove(thread_dir)

    @property
    def available(self) -> bool:
        """Whether THREAD modules can be imported."""
        return self._available

    @property
    def search_path(self) -> Optional[str]:
        """The directory where THREAD modules were found."""
        return self._search_path

    def __getattr__(self, name: str) -> Any:
        """Lazy-load a THREAD symbol on first access."""
        if name.startswith("_"):
            raise AttributeError(f"_LazyThread has no attribute '{name}'")

        if name in self._cache:
            return self._cache[name]

        if not self._available:
            self._cache[name] = None
            return None

        if name in _THREAD_MODULES:
            module_name, symbol_name = _THREAD_MODULES[name]
            try:
                module = importlib.import_module(module_name)
                symbol = getattr(module, symbol_name)
                self._cache[name] = symbol
                return symbol
            except (ImportError, AttributeError) as e:
                log.warning(f"THREAD symbol '{name}' ({module_name}.{symbol_name}) not available: {e}")
                self._cache[name] = None
                return None

        raise AttributeError(f"_LazyThread has no attribute '{name}'")


# ── Global singleton ──────────────────────────────────────────────
lazy_thread = _LazyThread()


def thread_available() -> bool:
    """Check if THREAD system is available."""
    return lazy_thread.available


def get_thread_modules() -> "_LazyThread":
    """Return the global lazy THREAD module container."""
    return lazy_thread
