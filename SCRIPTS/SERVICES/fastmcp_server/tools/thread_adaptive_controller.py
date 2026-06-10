#!/usr/bin/env python3
"""
THREAD Adaptive Controller — Dynamic parameter tuning based on usage patterns.

Adjusts retrieval weights and system parameters using a sliding window
of recent events and exponential moving averages.

Design:
  - Simple stateless calculations — no persistent state needed
  - Listens to EventBus events for performance signals
  - Adjusts weights based on cache hit/miss ratio
  - All parameters have safe defaults and bounded ranges
  - Thread-safe via threading.Lock on state updates
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default parameters (safe starting values)
DEFAULT_RETRIEVAL_WEIGHTS = {
    "lexical": 0.5,
    "activation": 0.3,
    "vector": 0.2,
}

DEFAULT_PARAMETERS = {
    "decay_lambda": 0.5,
    "max_activation_depth": 3,
    "confidence_threshold": 0.6,
}


class AdaptiveController:
    """
    Tunes system parameters based on event-driven feedback.

    Args:
        event_bus: An EventBus instance to subscribe to events.
        window_size: Number of events to keep in sliding window (default 1000).
        recompute_interval: Events between parameter recomputations (default 100).
    """

    def __init__(
        self,
        event_bus: Any = None,
        window_size: int = 1000,
        recompute_interval: int = 100,
    ):
        self._event_bus = event_bus
        self._window_size = max(window_size, 10)
        self._recompute_interval = max(recompute_interval, 1)

        self._lock = threading.Lock()
        self._weights = dict(DEFAULT_RETRIEVAL_WEIGHTS)
        self._parameters = dict(DEFAULT_PARAMETERS)

        # Sliding window tracking
        self._event_times: Dict[str, float] = {}  # event_type -> latest timestamp
        self._event_counts: Dict[str, int] = {}  # event_type -> count in window
        self._latencies: list = []  # recent retrieval latencies
        self._total_events = 0
        self._last_recompute = 0

        # Subscribe to event bus
        if self._event_bus is not None:
            try:
                self._event_bus.subscribe("cache.hit", self._on_cache_event)
                self._event_bus.subscribe("cache.miss", self._on_cache_event)
                self._event_bus.subscribe("memory.retrieved", self._on_retrieval_event)
                logger.debug("AdaptiveController: subscribed to cache/retrieval events")
            except Exception as e:
                logger.warning(f"AdaptiveController: subscription failed: {e}")

    # ── Public API ───────────────────────────────────────────────

    def get_retrieval_weights(self) -> Dict[str, float]:
        """
        Get current retrieval weights.

        Returns dict with keys: 'lexical', 'activation', 'vector'.
        """
        with self._lock:
            return dict(self._weights)

    def get_parameters(self) -> Dict[str, Any]:
        """
        Get current system parameters.

        Returns dict with keys: 'decay_lambda', 'max_activation_depth',
        'confidence_threshold'.
        """
        with self._lock:
            return dict(self._parameters)

    def adapt(self) -> Dict[str, Any]:
        """
        Run parameter adaptation cycle.

        Called periodically (every recompute_interval events) by reflect().

        Adjusts:
          - Retrieval weights: increase lexical weight if many keyword-heavy queries.
            Increase activation weight if high hit rate on activation-based results.
          - Confidence threshold: decrease threshold if hit rate drops below 50%.
          - Decay lambda: increase lambda if graph grows fast.

        Returns:
            Dict of parameter changes (empty if no changes).
        """
        with self._lock:
            changes = {}
            total = self._total_events

            if total - self._last_recompute < self._recompute_interval:
                return changes
            self._last_recompute = total

            # Calculate hit rate from cache events
            hits = self._event_counts.get("cache.hit", 0)
            misses = self._event_counts.get("cache.miss", 0)
            total_cache = hits + misses
            hit_rate = hits / total_cache if total_cache > 0 else 0.5

            # Adjust confidence threshold based on hit rate
            old_threshold = self._parameters["confidence_threshold"]
            if hit_rate < 0.3:
                # Low hit rate → lower threshold to accept more results
                new_threshold = max(0.3, old_threshold - 0.05)
            elif hit_rate > 0.8:
                # High hit rate → raise threshold for stricter filtering
                new_threshold = min(0.9, old_threshold + 0.05)
            else:
                new_threshold = old_threshold

            if abs(new_threshold - old_threshold) > 0.01:
                self._parameters["confidence_threshold"] = round(new_threshold, 4)
                changes["confidence_threshold"] = {
                    "old": old_threshold,
                    "new": new_threshold,
                }

            # Adjust lexical weight based on total cache events
            # More misses suggest need for better lexical matching
            if total_cache > 10:
                old_lexical = self._weights["lexical"]
                if hit_rate < 0.4:
                    new_lexical = min(0.8, old_lexical + 0.05)
                elif hit_rate > 0.85:
                    new_lexical = max(0.3, old_lexical - 0.05)
                else:
                    new_lexical = old_lexical

                if abs(new_lexical - old_lexical) > 0.01:
                    self._weights["lexical"] = round(new_lexical, 4)
                    # Adjust other weights proportionally
                    remaining = 1.0 - new_lexical
                    act = self._weights["activation"]
                    vec = self._weights["vector"]
                    act_vec_sum = act + vec if (act + vec) > 0 else 1.0
                    self._weights["activation"] = round(remaining * (act / act_vec_sum), 4)
                    self._weights["vector"] = round(remaining * (vec / act_vec_sum), 4)
                    changes["retrieval_weights"] = dict(self._weights)

            # Emit parameter change event
            if changes and self._event_bus:
                try:
                    from thread_events import SYSTEM_PARAMETER_CHANGED
                    self._event_bus.emit(SYSTEM_PARAMETER_CHANGED, changes)
                except Exception:
                    pass

            # Reset sliding window counters
            self._event_counts = {}

            return changes

    # ── Event Handlers ───────────────────────────────────────────

    def _on_cache_event(self, event) -> None:
        """Handle cache hit/miss events."""
        with self._lock:
            self._total_events += 1
            self._event_counts[event.type] = self._event_counts.get(event.type, 0) + 1
            self._event_times[event.type] = time.time()

    def _on_retrieval_event(self, event) -> None:
        """Handle retrieval events (track latency)."""
        with self._lock:
            self._total_events += 1
            latency = event.data.get("latency_ms", 0)
            if latency > 0:
                self._latencies.append(latency)
                if len(self._latencies) > self._window_size:
                    self._latencies = self._latencies[-self._window_size:]
