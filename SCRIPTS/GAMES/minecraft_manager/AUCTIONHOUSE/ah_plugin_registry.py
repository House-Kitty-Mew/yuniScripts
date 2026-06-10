"""
ah_plugin_registry.py — Central hook registry for AH extensions.

Extensions register callbacks for hook points.  The main AH code fires
hooks at key moments (simulation cycle, listing created, purchase, etc.).

Design principles:
  1. A crash in one extension NEVER blocks the main AH or other extensions.
  2. Every hook is wrapped in try/except with logging.
  3. Extensions that aren't installed are simply skipped.
  4. Hook order is deterministic (registration order).
"""

import os, threading, traceback, json
from pathlib import Path
from typing import Any, Callable, Optional
from datetime import datetime, timezone

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

# ── Hook Points (documented in EXTENSIONS/DESIGN.md) ────────────────
VALID_HOOKS = frozenset({
    "on_simulation_cycle_start",
    "on_simulation_cycle_end",
    "on_listing_created",
    "on_purchase",
    "on_cancel",
    "on_expiry",
    "on_listing_queried",
    "on_player_balance_change",
    # ── World simulation integration hooks (v3) ────────────────
    "on_persona_activated",
    "on_persona_deactivated",
    "on_social_interaction",
    "on_relationship_change",
    "on_chat_message",
    "on_announcement",
    "on_world_event",
    "on_persona_purchase",
})


# ── Registry ─────────────────────────────────────────────────────────

class _HookRegistry:
    """Thread-safe registry of extension hook callbacks."""

    def __init__(self):
        self._hooks: dict[str, list[tuple[str, Callable]]] = {h: [] for h in VALID_HOOKS}
        self._lock = threading.Lock()
        self._extensions_loaded = False

    # ── Registration ─────────────────────────────────────────────

    def register(self, hook: str, extension_name: str, callback: Callable, priority: int = 100):
        """Register a callback for a hook point.

        Args:
            hook: One of VALID_HOOKS
            extension_name: e.g. "SIMULATED_PEOPLE"
            callback: Callable that accepts (**kwargs)
        """
        if hook not in VALID_HOOKS:
            log.warn("registry", f"Unknown hook '{hook}' — ignored (valid: {sorted(VALID_HOOKS)})")
            return
        with self._lock:
            self._hooks[hook].append((extension_name, callback, priority))
            self._hooks[hook].sort(key=lambda x: x[2])  # Lower priority runs first
            log.info("registry", f"Extension '{extension_name}' registered hook: {hook} priority={priority}")

    def unregister_all(self, extension_name: str):
        """Remove all hooks for a given extension (used on unload)."""
        with self._lock:
            for hook in self._hooks:
                self._hooks[hook] = [(n, c, p) for n, c, p in self._hooks[hook] if n != extension_name]
            log.info("registry", f"Unregistered all hooks for '{extension_name}'")

    # ── Firing ───────────────────────────────────────────────────

    def fire(self, hook: str, **kwargs) -> list[dict]:
        """Fire a hook to all registered extensions.

        Every callback is wrapped in try/except.  A failure in one
        extension NEVER propagates to other extensions or the caller.

        For ``on_simulation_cycle_end``, a snapshot of the shared state is
        taken before each callback, and rolled back if the callback fails
        (E7 fix: transaction-like rollback).

        Args:
            hook: One of VALID_HOOKS
            **kwargs: Passed to each callback

        Returns:
            List of result dicts from each callback, one per extension.
            Each result: {"extension": str, "ok": bool, "data": ..., "error": ...}
        """
        if hook not in VALID_HOOKS:
            return []

        results = []
        # Snapshot the callbacks under lock, then fire outside the lock
        with self._lock:
            callbacks = list(self._hooks.get(hook, []))

        # ── E7: Transaction-like rollback for on_simulation_cycle_end ──
        needs_rollback = (hook == "on_simulation_cycle_end")

        for ext_name, callback, _priority in callbacks:
            # Snapshot shared state + owners before firing (for rollback)
            _snap_data = None
            if needs_rollback:
                try:
                    from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state as _gs
                    _snap_data = _gs().snapshot()  # (state, owners)
                except Exception:
                    _snap_data = None

            try:
                result = callback(**kwargs)
                results.append({"extension": ext_name, "ok": True, "data": result})
            except Exception as e:
                log.error("registry",
                          f"Extension '{ext_name}' hook '{hook}' error: {e}\n{traceback.format_exc()}")
                results.append({"extension": ext_name, "ok": False, "error": str(e)})

                # ── E7: Rollback shared state to pre-callback snapshot ──
                if needs_rollback and _snap_data is not None:
                    try:
                        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state as _gs
                        state_snap, owners_snap = _snap_data
                        _gs().restore_snapshot(state_snap, owners_snap)
                        log.info("registry",
                                 f"Rolled back shared state for '{ext_name}' after hook failure")
                    except Exception as rb_e:
                        log.error("registry",
                                  f"Rollback also failed for '{ext_name}': {rb_e}")

        return results

    # ── Discovery ────────────────────────────────────────────────

    def discover_and_load(self, extensions_dir: Optional[str] = None):
        """Scan EXTENSIONS/ directory and load all extensions.

        Each extension should expose an ``on_load(registry)`` function
        in its ``__init__.py``.

        Args:
            extensions_dir: Path to extensions directory.  Defaults to
                           the EXTENSIONS folder next to this file.
        """
        if self._extensions_loaded:
            return

        if extensions_dir is None:
            extensions_dir = os.path.join(os.path.dirname(__file__), "EXTENSIONS")

        ext_path = Path(extensions_dir)
        if not ext_path.exists():
            log.info("registry", f"No EXTENSIONS directory at {extensions_dir}")
            self._extensions_loaded = True
            return

        loaded = 0
        for item in sorted(ext_path.iterdir()):
            if not item.is_dir():
                continue
            init_py = item / "__init__.py"
            if not init_py.exists():
                continue

            ext_name = item.name
            if ext_name.startswith("_"):
                continue  # Skip private/internal dirs

            try:
                # We need to import dynamically.  Since EXTENSIONS might not
                # be a regular package, use importlib.
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    f"AUCTIONHOUSE.EXTENSIONS.{ext_name}",
                    str(init_py)
                )
                if spec is None or spec.loader is None:
                    log.warn("registry", f"Could not load extension '{ext_name}': spec empty")
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "on_load"):
                    module.on_load(self)
                    loaded += 1
                    log.info("registry", f"Extension loaded: {ext_name}")
                else:
                    log.info("registry", f"Extension '{ext_name}' has no on_load() — skipping")

            except Exception as e:
                log.error("registry",
                          f"Failed to load extension '{ext_name}': {e}\n{traceback.format_exc()}")

        if loaded == 0:
            log.info("registry", "No extensions loaded (EXTENSIONS directory is empty or missing __init__.py)")
        else:
            log.info("registry", f"Loaded {loaded} extension(s)")

        self._extensions_loaded = True


# ── Global singleton ─────────────────────────────────────────────────

_registry: Optional[_HookRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> _HookRegistry:
    """Return the global HookRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = _HookRegistry()
    return _registry


def fire_hook(hook: str, **kwargs) -> list[dict]:
    """Convenience: fire a hook on the global registry.

    Safe to call even if the registry hasn't been initialized.
    Returns empty list if no extensions are loaded.
    """
    try:
        reg = get_registry()
        return reg.fire(hook, **kwargs)
    except Exception:
        return []  # Registry not available — no extensions


def discover_extensions():
    """Scan and load all extensions from the EXTENSIONS directory.

    Call this once at AH startup.
    """
    reg = get_registry()
    reg.discover_and_load()


def register_hook(hook: str, extension_name: str, callback: Callable, priority: int = 100):
    """Convenience: register a hook on the global registry.

    Intended for use by extensions' ``on_load()`` functions.
    """
    reg = get_registry()
    reg.register(hook, extension_name, callback, priority)
