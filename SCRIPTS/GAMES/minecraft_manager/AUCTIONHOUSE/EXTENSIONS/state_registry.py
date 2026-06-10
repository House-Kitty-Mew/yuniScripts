"""
state_registry.py — Thread-safe shared state for simulation extensions.

During a simulation cycle, extensions write intermediate results to this
registry so later-phase extensions can read them.  The cycle manager
clears the state at the start of each cycle.

Lifecycle (Phase 2C ordering):
  1. [PEOPLE]   → writes active persona list, needs, purchases
  2. [SOCIAL]   → reads personas, writes interaction results
  3. [RELS]     → reads interactions, writes relationship changes
  4. [CHAT]     → reads relationships, writes chat messages
  5. [ANNOUNCE] → reads chat + purchases + events, writes announcements
  6. [ALL]      → on_simulation_cycle_end
  7. [PEOPLE]   → economy decisions using updated state

Usage:
    from EXTENSIONS.state_registry import get_state

    # Write
    get_state().set("active_personas", [...], "SIMULATED_PEOPLE")

    # Read
    personas = get_state().get("active_personas", [])

    # Namespace (all keys for a given extension)
    social_data = get_state().get_namespace("SIMULATED_SOCIAL")
"""

import threading
from typing import Any, Optional


class _ExtensionState:
    """Thread-safe shared state for simulation extensions."""

    def __init__(self):
        self._state: dict[str, Any] = {}
        self._owners: dict[str, str] = {}  # key -> extension name
        self._lock = threading.RLock()

    # ── Public API ──────────────────────────────────────────────

    def set(self, key: str, value: Any, extension: str) -> None:
        """Set a shared state value with ownership tracking.

        Args:
            key: State key (convention: snake_case)
            value: Any JSON-serializable value
            extension: Extension name that owns this key (e.g. "SIMULATED_PEOPLE")
        """
        with self._lock:
            self._state[key] = value
            self._owners[key] = extension

    def get(self, key: str, default: Any = None) -> Any:
        """Get a shared state value.

        Args:
            key: State key
            default: Value returned if key doesn't exist

        Returns:
            The stored value, or default
        """
        with self._lock:
            return self._state.get(key, default)

    def get_owner(self, key: str) -> Optional[str]:
        """Return the extension name that owns a key, or None."""
        with self._lock:
            return self._owners.get(key)

    def get_namespace(self, extension: str) -> dict[str, Any]:
        """Get all state entries owned by a specific extension.

        Args:
            extension: Extension name (e.g. "SIMULATED_SOCIAL")

        Returns:
            Dict of key -> value for all keys owned by that extension
        """
        with self._lock:
            return {
                k: v for k, v in self._state.items()
                if self._owners.get(k) == extension
            }

    def update(self, mapping: dict[str, Any], extension: str) -> None:
        """Set multiple values at once (atomic batch write).

        Args:
            mapping: Dict of key -> value pairs
            extension: Extension name that owns these keys
        """
        with self._lock:
            for k, v in mapping.items():
                self._state[k] = v
                self._owners[k] = extension

    def clear(self) -> None:
        """Clear all state. Called at the start of each simulation cycle."""
        with self._lock:
            self._state.clear()
            self._owners.clear()

    def dump(self) -> dict[str, Any]:
        """Snapshot the entire state for debugging/logging.

        Returns:
            Copy of all key-value pairs
        """
        with self._lock:
            return dict(self._state)

    def snapshot(self) -> tuple[dict[str, Any], dict[str, str]]:
        """Full snapshot of both state and owners for rollback.

        Returns:
            (state_dict, owners_dict) tuple
        """
        with self._lock:
            return dict(self._state), dict(self._owners)

    def restore_snapshot(self, state: dict[str, Any],
                         owners: dict[str, str]) -> None:
        """Restore a full snapshot (state + owners) atomically.

        Used by E7 rollback to revert changes made by a failing
        extension callback.

        Args:
            state: State dict from snapshot()
            owners: Owners dict from snapshot()
        """
        with self._lock:
            self._state.clear()
            self._state.update(state)
            self._owners.clear()
            self._owners.update(owners)

    def keys(self) -> list[str]:
        """List all state keys."""
        with self._lock:
            return list(self._state.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._state)


# ── Global singleton ─────────────────────────────────────────────────

_state: Optional[_ExtensionState] = None
_state_lock = threading.Lock()


def get_state() -> _ExtensionState:
    """Return the global ExtensionState singleton."""
    global _state
    if _state is None:
        with _state_lock:
            if _state is None:
                _state = _ExtensionState()
    return _state


def clear_state() -> None:
    """Convenience: clear the shared state for a new cycle."""
    get_state().clear()
