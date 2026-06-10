"""
test_mod_cache_advanced.py — Tests for ModCache advanced features.

Tests the features added for Work Order #1049:
  1. ETag/If-None-Match conditional request storage and retrieval
  2. Cache freshness helpers (TTL-based expiry)
  3. Rate limit tracking and exponential backoff
  4. Background cache validation (validate_cache_background)
  5. Cache cleanup (clear_cache with age-based removal)
  6. Cache stats reporting

All tests use mocks — no real API calls are made.

Usage:
    python -m unittest tests.test_mod_cache_advanced
"""

import os
import sys
import json
import time
import hashlib
import unittest
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

logging.disable(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Import setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

ENGINE_AVAILABLE = True
_IMPORT_ERROR = None

try:
    from engine.database import Database
    from engine.vfs import VFS
    from engine.mod_cache import (
        ModCache, MODRINTH_API,
        SEARCH_CACHE_DAYS, PROJECT_CACHE_DAYS, VERSION_CACHE_HOURS,
        RATE_LIMIT_MAX_RETRIES, RATE_LIMIT_BASE_DELAY, RATE_LIMIT_MAX_DELAY,
        RATE_LIMIT_COOLDOWN, ETAG_PREFIX, CACHE_TIME_PREFIX,
    )
    CACHE_IMPORTABLE = True
except ImportError as exc:
    CACHE_IMPORTABLE = False
    _IMPORT_ERROR = str(exc)

# Faux requests for environments without the requests library
class _FauxRequestsExceptions:
    class RequestException(Exception): pass
    class HTTPError(RequestException): pass
    class ConnectionError(RequestException): pass
    class Timeout(RequestException): pass

class _FauxRequests:
    exceptions = _FauxRequestsExceptions

try:
    import requests.exceptions
    REQ_EXC = requests.exceptions
except ImportError:
    _FAUX_REQ = _FauxRequests()
    sys.modules['requests'] = _FAUX_REQ
    REQ_EXC = _FAUX_REQ.exceptions


# ===================================================================
# Mock classes
# ===================================================================

class MockDatabase:
    """Tracks config entries for testing cache/ETag storage."""

    def __init__(self):
        self._config: dict = {}
        self.closed = False

    def close(self):
        self.closed = True

    def set_config(self, scope, key, value):
        self._config[key] = value
        return True

    def get_config(self, scope, key, default=None):
        return self._config.get(key, default)

    def query(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        return {'rowcount': 1}

    def create_mod(self, name, slug, version, mc_version, loader,
                   download_url=None, file_hash=None):
        return 1

    def get_mod(self, slug):
        return None

    def list_mods(self, server_id=None, mc_version=None,
                  loader=None, enabled_only=False):
        return []

    def install_mod_to_server(self, mod_slug, server_id):
        return True


class MockVFS:
    """Simulates VFS for testing without real filesystem."""

    def __init__(self, db, vfs_root="vfs"):
        self.db = db
        self.vfs_root = vfs_root
        self._files: dict = {}
        self._dirs: set = set()

    def read(self, path):
        return self._files.get(path)

    def write(self, path, data, atomic=False, content_type=None,
              original_name=None, tags=None):
        self._files[path] = data
        dir_path = os.path.dirname(path)
        if dir_path:
            self._dirs.add(dir_path)
        return len(data)

    def delete(self, path):
        self._files.pop(path, None)
        return True

    def exists(self, path):
        return path in self._files or path in self._dirs

    def listdir(self, path):
        items = []
        prefix = path.rstrip('/') + '/'
        for p in self._files:
            if p.startswith(prefix):
                items.append({'path': p, 'type': 'file',
                             'size': len(self._files[p])})
        for d in self._dirs:
            if d.startswith(prefix) and d != path:
                items.append({'path': d, 'type': 'dir', 'size': 0})
        return items

    def mkdir(self, path):
        self._dirs.add(path)
        return True


# ===================================================================
# Helper: build a mock HTTP response
# ===================================================================

def _mock_response(json_data=None, status_code=200, content=b'',
                   headers=None, request_error=None):
    """Build a MagicMock that simulates a requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.content = content
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}

    if request_error is not None:
        resp.raise_for_status.side_effect = request_error
    elif status_code >= 400:
        try:
            resp.raise_for_status.side_effect = REQ_EXC.HTTPError(
                f"{status_code} Error", response=resp)
        except Exception:
            pass  # In case REQ_EXC.HTTPError doesn't accept 'response'
    else:
        resp.raise_for_status.return_value = None

    return resp


# ===================================================================
# Tests
# ===================================================================

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestETagStorage(unittest.TestCase):
    """Tests for ETag storage and retrieval helpers."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs)

    def test_etag_key_deterministic(self):
        """Same URL should produce the same ETag key."""
        url = "https://api.modrinth.com/v2/search"
        key1 = self.cache._etag_key(url)
        key2 = self.cache._etag_key(url)
        self.assertEqual(key1, key2)
        self.assertTrue(key1.startswith(ETAG_PREFIX))

    def test_etag_key_different_urls(self):
        """Different URLs should produce different ETag keys."""
        key1 = self.cache._etag_key("https://api.modrinth.com/v2/search")
        key2 = self.cache._etag_key("https://api.modrinth.com/v2/project/fabric-api")
        self.assertNotEqual(key1, key2)

    def test_store_and_retrieve_etag(self):
        """Stored ETag should be retrievable."""
        url = "https://api.modrinth.com/v2/search"
        etag = '"abc123def456"'
        self.cache._store_etag(url, etag)
        retrieved = self.cache._get_etag(url)
        self.assertEqual(retrieved, etag)

    def test_store_empty_etag(self):
        """Empty ETag should not be stored."""
        url = "https://api.modrinth.com/v2/search"
        self.cache._store_etag(url, "")
        retrieved = self.cache._get_etag(url)
        self.assertIsNone(retrieved)

    def test_store_none_etag(self):
        """None ETag should not be stored."""
        url = "https://api.modrinth.com/v2/search"
        self.cache._store_etag(url, None)
        retrieved = self.cache._get_etag(url)
        self.assertIsNone(retrieved)

    def test_get_etag_nonexistent(self):
        """Getting ETag for unknown URL should return None."""
        url = "https://api.modrinth.com/v2/project/unknown"
        retrieved = self.cache._get_etag(url)
        self.assertIsNone(retrieved)

    def test_etag_overwrite(self):
        """Storing a new ETag should overwrite the old one."""
        url = "https://api.modrinth.com/v2/search"
        self.cache._store_etag(url, "old-etag")
        self.cache._store_etag(url, "new-etag")
        retrieved = self.cache._get_etag(url)
        self.assertEqual(retrieved, "new-etag")

    def test_etag_key_sha256_length(self):
        """ETag key should be deterministic and reasonable length."""
        url = "https://example.com/very/long/path/with/many/segments"
        key = self.cache._etag_key(url)
        # Prefix (16) + sha256 prefix (16) = 32
        self.assertEqual(len(key), len(ETAG_PREFIX) + 16)


@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestCacheFreshness(unittest.TestCase):
    """Tests for cache timestamp and freshness helpers."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs)

    def test_store_and_retrieve_timestamp(self):
        """Stored timestamp should be retrievable as hours."""
        cache_key = "test_key_123"
        self.cache._store_cache_timestamp(cache_key)
        age = self.cache._get_cache_age_hours(cache_key)
        self.assertIsNotNone(age)
        self.assertAlmostEqual(age, 0.0, delta=0.01)  # Just stored, age ~0

    def test_cache_age_increases(self):
        """Cache age should increase over time."""
        cache_key = "age_test"
        self.cache._store_cache_timestamp(cache_key)
        age_before = self.cache._get_cache_age_hours(cache_key)
        time.sleep(0.01)
        age_after = self.cache._get_cache_age_hours(cache_key)
        self.assertGreater(age_after, age_before)

    def test_unknown_cache_key(self):
        """Unknown cache key should return None age."""
        age = self.cache._get_cache_age_hours("nonexistent_key")
        self.assertIsNone(age)

    def test_fresh_cache(self):
        """Recently stored cache should be fresh."""
        cache_key = "fresh_test"
        self.cache._store_cache_timestamp(cache_key)
        self.assertTrue(
            self.cache._is_cache_fresh(cache_key, max_hours=24))

    def test_stale_cache(self):
        """Cache with age > max_hours should not be fresh."""
        cache_key = "stale_test"
        # Store a timestamp in the past
        ts_key = self.cache._cache_time_key(cache_key)
        old_time = str(time.time() - 3600 * 48)  # 48 hours ago
        self.db.set_config(0, ts_key, old_time)
        self.assertFalse(
            self.cache._is_cache_fresh(cache_key, max_hours=24))

    def test_cache_key_with_special_chars(self):
        """Cache keys with special characters should work."""
        cache_key = "search_['fabric']_{'query': 'test'}"
        self.cache._store_cache_timestamp(cache_key)
        self.assertTrue(
            self.cache._is_cache_fresh(cache_key, max_hours=1))

    def test_different_ttls(self):
        """Different TTL values should work correctly."""
        cache_key = "ttl_test"
        self.cache._store_cache_timestamp(cache_key)
        # Zero TTL should always be stale (time has elapsed since store)
        self.assertFalse(
            self.cache._is_cache_fresh(cache_key, max_hours=0))
        # Longer TTL should be fresh
        self.assertTrue(
            self.cache._is_cache_fresh(cache_key, max_hours=24))


@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestRateLimiting(unittest.TestCase):
    """Tests for rate limit tracking and backoff."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs)

    def test_endpoint_key_modrinth(self):
        """Modrinth URLs should be grouped by endpoint."""
        url1 = f"{MODRINTH_API}/search?query=test"
        url2 = f"{MODRINTH_API}/search?query=other"
        url3 = f"{MODRINTH_API}/project/fabric-api"
        self.assertEqual(
            self.cache._endpoint_key(url1),
            self.cache._endpoint_key(url2))
        self.assertNotEqual(
            self.cache._endpoint_key(url1),
            self.cache._endpoint_key(url3))

    def test_no_backoff_initially(self):
        """With no rate limits recorded, should_backoff should return 0."""
        url = f"{MODRINTH_API}/search"
        self.assertEqual(self.cache._should_backoff(url), 0.0)

    def test_backoff_after_429(self):
        """After recording a rate limit, should_backoff should return > 0."""
        url = f"{MODRINTH_API}/search"
        self.cache._record_rate_limit(url)
        backoff = self.cache._should_backoff(url)
        self.assertGreater(backoff, 0.0)
        self.assertAlmostEqual(backoff, RATE_LIMIT_BASE_DELAY, delta=0.01)

    def test_backoff_exponential(self):
        """Multiple 429s should increase backoff exponentially."""
        url = f"{MODRINTH_API}/search"
        for i in range(3):
            self.cache._record_rate_limit(url)
        backoff = self.cache._should_backoff(url)
        expected = min(
            RATE_LIMIT_BASE_DELAY * (2 ** 2),  # retry_count=3 -> 2^2
            RATE_LIMIT_MAX_DELAY
        )
        self.assertAlmostEqual(backoff, expected, delta=0.1)

    def test_backoff_resets_after_cooldown(self):
        """After RATE_LIMIT_COOLDOWN seconds, backoff should reset."""
        url = f"{MODRINTH_API}/search"
        self.cache._record_rate_limit(url)

        # Simulate time passing by manipulating rate_limit_state
        endpoint = self.cache._endpoint_key(url)
        with self.cache._rl_lock:
            state = self.cache._rate_limit_state[endpoint]
            state['last_429'] = time.time() - RATE_LIMIT_COOLDOWN - 1

        backoff = self.cache._should_backoff(url)
        self.assertEqual(backoff, 0.0)
        # State should be cleared
        self.assertNotIn(endpoint, self.cache._rate_limit_state)

    def test_backoff_capped(self):
        """Backoff should be capped at RATE_LIMIT_MAX_DELAY."""
        url = f"{MODRINTH_API}/search"
        # Record many rate limits
        for i in range(20):
            self.cache._record_rate_limit(url)
        backoff = self.cache._should_backoff(url)
        self.assertLessEqual(backoff, RATE_LIMIT_MAX_DELAY)

    def test_different_endpoints_independent(self):
        """Rate limit state should be per-endpoint."""
        url1 = f"{MODRINTH_API}/search"
        url2 = f"{MODRINTH_API}/project/fabric-api"
        self.cache._record_rate_limit(url1)
        self.assertGreater(self.cache._should_backoff(url1), 0.0)
        self.assertEqual(self.cache._should_backoff(url2), 0.0)

    def test_rate_limit_aware_get_429_no_retry(self):
        """
        When _rate_limit_aware_get receives a 429, it should record
        the rate limit and retry (or exhaust retries).
        
        With RATE_LIMIT_MAX_RETRIES=5 and base_delay=1, the retries would
        take a while. Instead, we test that the function records the rate
        limit and handles it without crashing.
        """
        # Create a session that always returns 429
        session = MagicMock()
        resp_429 = _mock_response({}, status_code=429)
        session.get.return_value = resp_429

        with patch.object(ModCache, '_get_requests', return_value=session):
            result = self.cache._rate_limit_aware_get(
                f"{MODRINTH_API}/search", timeout=5)
            self.assertIsNone(result)
            # Should have recorded the rate limit
            self.assertGreater(
                self.cache._should_backoff(f"{MODRINTH_API}/search"), 0.0)

    def test_connection_error_returns_immediately(self):
        """
        Connection-level errors should return None immediately,
        without retry backoff sleep.
        """
        session = MagicMock()
        session.get.side_effect = REQ_EXC.ConnectionError("Connection refused")

        with patch.object(ModCache, '_get_requests', return_value=session):
            t0 = time.time()
            result = self.cache._rate_limit_aware_get(
                f"{MODRINTH_API}/search", timeout=5)
            elapsed = time.time() - t0
            # Should return quickly (under 1s, not 15s+)
            self.assertLess(elapsed, 1.0)
            self.assertIsNone(result)


@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestBackgroundValidation(unittest.TestCase):
    """Tests for validate_cache_background()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs)

    def test_validate_empty_cache(self):
        """Validating an empty cache should return zero counts."""
        result = self.cache.validate_cache_background(max_checks=10)
        self.assertEqual(result['checked'], 0)
        self.assertEqual(result['up_to_date'], 0)
        self.assertEqual(result['stale'], 0)
        self.assertEqual(result['errors'], 0)

    def test_validate_with_cached_data(self):
        """Validating should check cached VFS entries."""
        # Add a cached file to VFS
        self.vfs.write("/mod-cache/modrinth/fabric-api-1.20.4-fabric.jar",
                       b"mock_jar_data")
        self.vfs.mkdir("/mod-cache/modrinth")

        # Mock modrinth_get_latest to return a version
        with patch.object(ModCache, 'modrinth_get_latest',
                          return_value={'version_number': '0.92.0'}):
            result = self.cache.validate_cache_background(max_checks=10)
            self.assertGreaterEqual(result['checked'], 1)
            # Since we have no cached version info, it should report
            # 'errors' (couldn't compare) or 'stale'

    def test_validate_respects_max_checks(self):
        """max_checks parameter should limit the number of validations."""
        # Add multiple files
        for i in range(5):
            self.vfs.write(
                f"/mod-cache/modrinth/mod-{i}-1.20.4-fabric.jar",
                b"mock_data")
        self.vfs.mkdir("/mod-cache/modrinth")

        with patch.object(ModCache, 'modrinth_get_latest',
                          return_value={'version_number': '1.0.0'}):
            result = self.cache.validate_cache_background(max_checks=2)
            self.assertLessEqual(result['checked'], 2)


@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestCacheCleanup(unittest.TestCase):
    """Tests for clear_cache()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs)

    def test_clear_cache_no_op(self):
        """Clearing an empty cache should return 0."""
        result = self.cache.clear_cache(older_than_days=30)
        self.assertEqual(result, 0)

    def test_clear_cache_with_old_entries(self):
        """Old cached entries should be removed."""
        # Add a cached file
        self.vfs.write("/mod-cache/modrinth/old-mod-1.20.4-fabric.jar",
                       b"old_data")
        self.vfs.mkdir("/mod-cache/modrinth")

        # Set its cache timestamp to 100 days ago
        ts_key = self.cache._cache_time_key("modrinth-old-mod-1.20.4-fabric")
        self.db.set_config(0, ts_key, str(time.time() - 86400 * 100))

        # Set up cache metadata
        self.db.set_config(
            0, "mod_cache_modrinth-old-mod-1.20.4-fabric",
            '{"version": "0.1.0"}')

        result = self.cache.clear_cache(older_than_days=30)
        self.assertEqual(result, 1)
        self.assertIsNone(self.vfs.read(
            "/mod-cache/modrinth/old-mod-1.20.4-fabric.jar"))

    def test_clear_cache_keeps_fresh(self):
        """Recent entries should be kept when clear_cache runs."""
        self.vfs.write("/mod-cache/modrinth/fresh-mod-1.20.4-fabric.jar",
                       b"fresh_data")
        self.vfs.mkdir("/mod-cache/modrinth")
        ts_key = self.cache._cache_time_key("modrinth-fresh-mod-1.20.4-fabric")
        self.db.set_config(0, ts_key, str(time.time()))  # Just now

        result = self.cache.clear_cache(older_than_days=30)
        self.assertEqual(result, 0)
        self.assertIsNotNone(self.vfs.read(
            "/mod-cache/modrinth/fresh-mod-1.20.4-fabric.jar"))

    def test_clear_cache_with_zero_days(self):
        """Setting older_than_days=0 should remove everything."""
        self.vfs.write("/mod-cache/modrinth/test-mod-1.20.4-fabric.jar",
                       b"test_data")
        self.vfs.mkdir("/mod-cache/modrinth")
        ts_key = self.cache._cache_time_key("modrinth-test-mod-1.20.4-fabric")
        self.db.set_config(0, ts_key, str(time.time()))

        result = self.cache.clear_cache(older_than_days=0)
        self.assertEqual(result, 1)


@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestCacheStats(unittest.TestCase):
    """Tests for cache_stats()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs)

    def test_cache_stats_empty(self):
        """Empty cache should return zero stats."""
        stats = self.cache.cache_stats()
        self.assertIn('cached_mods', stats)
        self.assertEqual(stats['cached_mods'], 0)
        self.assertEqual(stats['total_estimated_bytes'], 0)

    def test_cache_stats_with_data(self):
        """Cache with mods should return non-zero stats."""
        self.vfs.write("/mod-cache/modrinth/mod-a-1.20.4-fabric.jar",
                       b"a" * 1000)
        self.vfs.mkdir("/mod-cache/modrinth")

        stats = self.cache.cache_stats()
        self.assertEqual(stats['cached_mods'], 1)
        self.assertEqual(stats['total_estimated_bytes'], 1000)


@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestConditionalRequests(unittest.TestCase):
    """Tests that ETags are passed as If-None-Match headers."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs)

    def test_etag_sent_as_header(self):
        """When an ETag is stored, it should be sent as If-None-Match."""
        url = f"{MODRINTH_API}/search"
        self.cache._store_etag(url, '"my-etag"')

        # Mock the session
        session = MagicMock()
        resp_200 = _mock_response({'hits': []})
        session.get.return_value = resp_200

        with patch.object(ModCache, '_get_requests', return_value=session):
            self.cache.modrinth_search('test')
            # Check that the ETag was sent
            call_args, call_kwargs = session.get.call_args
            headers = call_kwargs.get('headers', {})
            self.assertIn('If-None-Match', headers)
            self.assertEqual(headers['If-None-Match'], '"my-etag"')

    def test_no_etag_no_header(self):
        """When no ETag is stored, If-None-Match should not be sent."""
        session = MagicMock()
        resp_200 = _mock_response({'hits': []})
        session.get.return_value = resp_200

        with patch.object(ModCache, '_get_requests', return_value=session):
            self.cache.modrinth_search('test')
            call_args, call_kwargs = session.get.call_args
            headers = call_kwargs.get('headers', {})
            self.assertNotIn('If-None-Match', headers)

    def test_304_response_uses_cache(self):
        """A 304 response should trigger using cached data."""
        url = f"{MODRINTH_API}/search"
        # First, store some search results
        cache_key = 'search_test_{"query": "test", "limit": 20}'
        self.db.set_config(0, f"mod_cache_{cache_key}",
                           json.dumps([{'slug': 'cached-mod'}]))

        # Mock session to return 304
        session = MagicMock()
        resp_304 = _mock_response({}, status_code=304)
        session.get.return_value = resp_304

        with patch.object(ModCache, '_get_requests', return_value=session):
            results = self.cache.modrinth_search('test')
            # Should return cached data
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['slug'], 'cached-mod')

    def test_304_no_cache_returns_empty(self):
        """A 304 with no cached data should return empty list."""
        session = MagicMock()
        resp_304 = _mock_response({}, status_code=304)
        session.get.return_value = resp_304

        with patch.object(ModCache, '_get_requests', return_value=session):
            results = self.cache.modrinth_search('test')
            self.assertEqual(results, [])


@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestEndToEndFlow(unittest.TestCase):
    """End-to-end integration tests for the full mod cache flow."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs)

    def test_store_etag_then_reuse(self):
        """ETag should be stored after first request and used on second."""
        url = f"{MODRINTH_API}/search"

        session = MagicMock()
        # First response: 200 with ETag
        resp1 = _mock_response(
            {'hits': [{'slug': 'fabric-api'}]},
            headers={'ETag': '"etag-v1"'})
        # Second response: 304 Not Modified (from etag)
        resp2 = _mock_response({}, status_code=304)

        session.get.side_effect = [resp1, resp2]

        with patch.object(ModCache, '_get_requests', return_value=session):
            # First call — stores ETag
            r1 = self.cache.modrinth_search('fabric-api')
            self.assertEqual(len(r1), 1)
            self.assertEqual(r1[0]['slug'], 'fabric-api')

        # Verify ETag was stored
        stored_etag = self.cache._get_etag(url)
        self.assertEqual(stored_etag, '"etag-v1"')

        # Second call — uses ETag, gets 304
        with patch.object(ModCache, '_get_requests', return_value=session):
            r2 = self.cache.modrinth_search('fabric-api')
            # Should return cached results from first call
            self.assertEqual(len(r2), 1)
            self.assertEqual(r2[0]['slug'], 'fabric-api')

    def test_cache_then_fetch_project(self):
        """Project should be cached and retrievable."""
        url = f"{MODRINTH_API}/project/fabric-api"
        project_data = {
            'slug': 'fabric-api', 'title': 'Fabric API',
            'description': 'Core modding API',
        }

        session = MagicMock()
        resp = _mock_response(project_data, headers={'ETag': '"proj-v1"'})
        session.get.return_value = resp

        with patch.object(ModCache, '_get_requests', return_value=session):
            project = self.cache.modrinth_get_project('fabric-api')
            self.assertIsNotNone(project)
            self.assertEqual(project['slug'], 'fabric-api')

        # Check cached
        cache_key = "project_fabric-api"
        self.assertTrue(
            self.cache._is_cache_fresh(cache_key, PROJECT_CACHE_DAYS * 24))

        # Check ETag stored
        stored = self.cache._get_etag(url)
        self.assertEqual(stored, '"proj-v1"')


# ===================================================================
# Run
# ===================================================================

if __name__ == '__main__':
    unittest.main(verbosity=2)
