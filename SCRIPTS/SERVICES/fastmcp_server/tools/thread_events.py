#!/usr/bin/env python3
"""
THREAD Event Bus — In-process pub/sub with event sourcing log.

Provides:
  - Event class (type, data, timestamp)
  - EventBus singleton (subscribe/emit with optional JSONL audit)
  - Event type constants for the entire THREAD system

Design:
  - Pure in-process observer pattern — no IPC, no network
  - Optional JSONL append-only log for event sourcing replay
  - Thread-safe via threading.Lock on subscriber list and emit
  - Singleton pattern via get_event_bus() — shares one bus across all modules
"""

import json
import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ── Event Type Constants ──────────────────────────────────────────
MEMORY_STORED = "memory.stored"
MEMORY_RETRIEVED = "memory.retrieved"
EDGE_ADDED = "edge.added"
EDGE_REMOVED = "edge.removed"
MEMORY_DECAYED = "memory.decayed"
MEMORY_FORGOTTEN = "memory.forgotten"
CACHE_HIT = "cache.hit"
CACHE_MISS = "cache.miss"
SYSTEM_PARAMETER_CHANGED = "system.parameter_changed"

ALL_EVENT_TYPES = frozenset({
    MEMORY_STORED, MEMORY_RETRIEVED,
    EDGE_ADDED, EDGE_REMOVED,
    MEMORY_DECAYED, MEMORY_FORGOTTEN,
    CACHE_HIT, CACHE_MISS,
    SYSTEM_PARAMETER_CHANGED,
})


# ── Event Class ────────────────────────────────────────────────────

class Event:
    """An event with type, data payload, and timestamp."""

    __slots__ = ('type', 'data', 'timestamp')

    def __init__(self, event_type: str, data: Optional[Dict[str, Any]] = None):
        if event_type not in ALL_EVENT_TYPES:
            logger.debug(f"Unknown event type '{event_type}' — not in {ALL_EVENT_TYPES}")
        self.type = event_type
        self.data = data or {}
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON logging."""
        return {
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    def __repr__(self) -> str:
        return f"Event(type={self.type}, data_keys={list(self.data.keys())})"


# ── EventBus ──────────────────────────────────────────────────────

class EventBus:
    """
    In-process pub/sub event system.

    Features:
      - subscribe(event_type, callback): Register callback for event type.
        Use '*' to subscribe to ALL events.
      - emit(event_type, data): Create Event + notify all matching subscribers.
      - Optional JSONL file: if jsonl_path is set, every emit appends to the log.
      - event_count: atomic counter of total events emitted.
      - unsubscribe: remove a previously registered callback.

    Thread-safe via threading.Lock on subscriber list mutations.
    """

    def __init__(self, jsonl_path: Optional[str] = None):
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = {}
        self._lock = threading.Lock()
        self._event_count = 0
        self._jsonl_path = jsonl_path
        self._jsonl_lock = threading.Lock()
        if jsonl_path:
            try:
                import os
                os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)
            except Exception:
                pass

    @property
    def event_count(self) -> int:
        return self._event_count

    def subscribe(self, event_type: str, callback: Callable[[Event], None]) -> None:
        """
        Register a callback for an event type.

        Args:
            event_type: Specific type (e.g., MEMORY_STORED) or '*' for all.
            callback: Callable that accepts an Event object.
        """
        if not callable(callback):
            logger.error(f"EventBus.subscribe: callback is not callable: {callback}")
            return
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)
            logger.debug(f"EventBus: subscribed to '{event_type}' ({len(self._subscribers[event_type])} total)")

    def unsubscribe(self, event_type: str, callback: Callable[[Event], None]) -> bool:
        """
        Remove a previously registered callback. Returns True if removed.
        """
        with self._lock:
            if event_type in self._subscribers and callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)
                if not self._subscribers[event_type]:
                    del self._subscribers[event_type]
                return True
        return False

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> Event:
        """
        Create and dispatch an event to all matching subscribers.

        Notifies:
          - Subscribers of this specific event_type
          - Subscribers of '*' (wildcard/all)

        Returns the Event object.
        """
        event = Event(event_type, data)
        self._event_count += 1

        # Collect callbacks to invoke (copy under lock to avoid holding during invocation)
        callbacks: List[Callable] = []
        with self._lock:
            specific = self._subscribers.get(event_type, [])
            wildcard = self._subscribers.get('*', [])
            callbacks = specific + wildcard

        # Invoke callbacks outside lock to prevent deadlocks
        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.warning(f"EventBus: callback error for {event_type}: {e}")

        # Append to JSONL log if configured
        if self._jsonl_path:
            self._append_to_log(event)

        return event

    def _append_to_log(self, event: Event) -> None:
        """Append event to JSONL file under lock."""
        import os
        with self._jsonl_lock:
            try:
                with open(self._jsonl_path, 'a') as f:
                    f.write(json.dumps(event.to_dict()) + '\n')
            except (IOError, OSError) as e:
                logger.debug(f"EventBus: JSONL append failed: {e}")

    def get_subscriber_count(self, event_type: Optional[str] = None) -> int:
        """
        Get the number of subscribers for an event type, or total across all types.
        """
        with self._lock:
            if event_type:
                return len(self._subscribers.get(event_type, []))
            return sum(len(cbs) for cbs in self._subscribers.values())

    def clear_subscribers(self) -> None:
        """Remove all subscribers (useful for testing)."""
        with self._lock:
            self._subscribers.clear()


# ── Singleton ────────────────────────────────────────────────────

_event_bus_instance: Optional[EventBus] = None
_event_bus_lock = threading.Lock()


def get_event_bus(db=None, jsonl_path: Optional[str] = None) -> EventBus:
    """
    Get or create the global EventBus singleton.

    Args:
        db: Ignored — kept for API compatibility with thoughts_manager.py.
        jsonl_path: Optional path to JSONL audit log file.
                    Only used on first call (singleton creation).

    Returns:
        The global EventBus instance.
    """
    global _event_bus_instance
    if _event_bus_instance is None:
        with _event_bus_lock:
            if _event_bus_instance is None:
                _event_bus_instance = EventBus(jsonl_path=jsonl_path)
    return _event_bus_instance
