#!/usr/bin/env python3
"""
THREAD Activation Cache — LRU cache with TTL and graph version invalidation.

Provides:
  - ActivationCache for memoizing retrieval results
  - LRU eviction when max_size is reached
  - Per-entry TTL (configurable default + per-key override)
  - Graph version tracking: entries cached before a graph_version bump are invalidated
  - Stats tracking (hits, misses, evictions, expired)

Design:
  - Pure in-memory dict-based LRU (no SQLite — cache is ephemeral by nature)
  - Thread-safe via threading.Lock
  - Keys are hashable tuples; values are arbitrary cached data
  - TTL checked on get(); expired entries are lazily removed
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, Hashable, Optional, Tuple

logger = logging.getLogger(__name__)


class _CacheEntry:
    """Internal cache entry with metadata."""

    __slots__ = ('value', 'expires_at', 'graph_version', 'access_count', 'created_at')

    def __init__(self, value: Any, ttl: float, graph_version: int):
        now = time.time()
        self.value = value
        self.expires_at = now + ttl
        self.graph_version = graph_version
        self.access_count = 0
        self.created_at = now

    def is_expired(self, current_graph_version: int) -> bool:
        """Check if entry is expired by TTL or graph version."""
        if time.time() > self.expires_at:
            return True
        if current_graph_version > self.graph_version:
            return True
        return False

    def touch(self, ttl: float):
        """Extend TTL on access."""
        self.expires_at = time.time() + ttl
        self.access_count += 1


class ActivationCache:
    """
    LRU cache with TTL and graph version invalidation.

    Args:
        max_size: Maximum number of entries before LRU eviction (default 10000).
        default_ttl: Default TTL in seconds (default 300 = 5 minutes).
        graph_store: Optional object with get_graph_version() method.
                     If provided, cache entries store the graph version at creation
                     time and are invalidated if the graph version changes.
    """

    def __init__(
        self,
        max_size: int = 10000,
        default_ttl: float = 300,
        graph_store: Optional[Any] = None,
    ):
        self._max_size = max(max_size, 1)
        self._default_ttl = max(default_ttl, 1.0)
        self._graph_store = graph_store
        self._cache: Dict[Hashable, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expired_count = 0

    # ── Public API ───────────────────────────────────────────────

    def get_or_compute(
        self,
        key_parts: Tuple[Hashable, ...],
        compute_func: Callable[[], Any],
        ttl: Optional[float] = None,
    ) -> Any:
        """
        Get cached value or compute and cache it.

        Args:
            key_parts: Tuple of hashable items forming the cache key.
            compute_func: Zero-argument callable that computes the value if not cached.
            ttl: Optional per-key TTL override. Uses default_ttl if None.

        Returns:
            The cached or freshly computed value.
        """
        key = self._make_key(key_parts)
        graph_version = self._current_graph_version()

        # Fast path: try cache hit
        entry = self._get_entry(key, graph_version)
        if entry is not None:
            self._hits += 1
            entry.touch(self._default_ttl if ttl is None else ttl)
            return entry.value

        # Cache miss — compute value
        self._misses += 1
        try:
            value = compute_func()
        except Exception as e:
            logger.warning(f"ActivationCache: compute_func failed: {e}")
            raise

        # Store in cache
        resolved_ttl = self._default_ttl if ttl is None else ttl
        self._put_entry(key, value, resolved_ttl, graph_version)

        return value

    def get(self, key_parts: Tuple[Hashable, ...]) -> Optional[Any]:
        """
        Get cached value without computing. Returns None if not found or expired.
        """
        key = self._make_key(key_parts)
        graph_version = self._current_graph_version()
        entry = self._get_entry(key, graph_version)
        if entry is not None:
            self._hits += 1
            entry.touch(self._default_ttl)
            return entry.value
        self._misses += 1
        return None

    def set(self, key_parts: Tuple[Hashable, ...], value: Any, ttl: Optional[float] = None) -> None:
        """Manually set a cache entry."""
        key = self._make_key(key_parts)
        resolved_ttl = self._default_ttl if ttl is None else ttl
        graph_version = self._current_graph_version()
        self._put_entry(key, value, resolved_ttl, graph_version)

    def invalidate(self, key_parts: Tuple[Hashable, ...]) -> bool:
        """Remove a specific cache entry. Returns True if existed."""
        key = self._make_key(key_parts)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
        return False

    def invalidate_all(self) -> int:
        """Remove ALL cache entries. Returns count of removed entries."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
        return count

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries. Returns count of removed entries.

        Called periodically by reflect() to reclaim memory.
        """
        graph_version = self._current_graph_version()
        count = 0
        with self._lock:
            expired_keys = [
                k for k, v in self._cache.items()
                if v.is_expired(graph_version)
            ]
            for k in expired_keys:
                del self._cache[k]
            count = len(expired_keys)
            self._expired_count += count
        if count > 0:
            logger.debug(f"ActivationCache: cleaned {count} expired entries")
        return count

    # ── Stats ─────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        with self._lock:
            size = len(self._cache)
        return {
            "size": size,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "expired_cleaned": self._expired_count,
            "hit_rate": round(hit_rate, 4),
            "default_ttl_s": self._default_ttl,
        }

    # ── Internal ──────────────────────────────────────────────────

    @staticmethod
    def _make_key(key_parts: Tuple[Hashable, ...]) -> Hashable:
        """Normalize key parts into a single hashable key."""
        return tuple(str(k) if not isinstance(k, Hashable) else k for k in key_parts)

    def _current_graph_version(self) -> int:
        """Get current graph version from store, or 0 if unavailable."""
        if self._graph_store is not None:
            try:
                return self._graph_store.get_graph_version()
            except Exception:
                pass
        return 0

    def _get_entry(self, key: Hashable, graph_version: int) -> Optional[_CacheEntry]:
        """Get a cache entry if it exists and is not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired(graph_version):
                del self._cache[key]
                self._expired_count += 1
                return None
            return entry

    def _put_entry(self, key: Hashable, value: Any, ttl: float, graph_version: int) -> None:
        """Store a cache entry, evicting LRU if at capacity."""
        with self._lock:
            # If key already exists, update in place
            if key in self._cache:
                self._cache[key] = _CacheEntry(value, ttl, graph_version)
                return

            # Evict if at capacity
            if len(self._cache) >= self._max_size:
                self._evict_one()

            self._cache[key] = _CacheEntry(value, ttl, graph_version)

    def _evict_one(self) -> None:
        """Evict the single least-recently-used entry."""
        if not self._cache:
            return
        # Find entry with lowest access_count (approximate LRU)
        # This is O(n) but cache size is bounded by max_size (default 10K)
        lru_key = min(self._cache, key=lambda k: self._cache[k].access_count)
        del self._cache[lru_key]
        self._evictions += 1
