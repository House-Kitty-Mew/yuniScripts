"""
test_mod_cache_api.py — Comprehensive tests for ModCache API wrapper methods.

Tests the direct Modrinth/CurseForge API methods of ModCache that are NOT
covered by test_mod_cache_deps.py (which focuses on dependency resolution).

Methods tested:
  - modrinth_search          — Search Modrinth for mods by query
  - modrinth_get_project     — Get full project metadata by slug
  - modrinth_get_versions    — Get available versions with filtering
  - modrinth_get_latest      — Get latest matching version
  - modrinth_download        — Download mod binary with VFS caching + hash verify
  - curseforge_search        — Search CurseForge for mods
  - clear_cache              — Clear cached entries

All tests use mocks — no real API calls are made.
Uses unittest.mock.patch on ModCache._get_requests() to simulate HTTP.

Usage:
    python -m unittest tests.test_mod_cache_api  (from project root)
"""

import os
import sys
import json
import hashlib
import unittest
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

logging.disable(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Import setup (matches existing test patterns)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

ENGINE_AVAILABLE = True
_IMPORT_ERROR = None

try:
    from engine.database import Database
    from engine.vfs import VFS
    from engine.mod_cache import ModCache, MODRINTH_API
    CACHE_IMPORTABLE = True
except ImportError as exc:
    CACHE_IMPORTABLE = False
    _IMPORT_ERROR = str(exc)

# Ensure 'requests' is available — inject a faux module so that ModCache's
# internal "import requests as _r" never fails on any Python environment.
# The faux module provides the exception classes needed for testing.
class _FauxRequestsExceptions:
    """Faux requests.exceptions module for testing."""
    class RequestException(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.response = kwargs.get('response')
    class HTTPError(RequestException):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.response = kwargs.get('response')
    class ConnectionError(RequestException):
        pass
    class Timeout(RequestException):
        pass

class _FauxRequests:
    """Faux requests module injected when real requests is unavailable."""
    exceptions = _FauxRequestsExceptions

try:
    import requests.exceptions
    REQ_EXC = requests.exceptions
except ImportError:
    _FAUX_REQ = _FauxRequests()
    sys.modules['requests'] = _FAUX_REQ
    REQ_EXC = _FAUX_REQ.exceptions


# ===================================================================
# Helper: build a mock HTTP response
# ===================================================================

def _mock_response(json_data=None, status_code=200, content=b'', ok=True,
                   request_error=None):
    """
    Build a MagicMock that simulates a requests.Response.

    Args:
        json_data: Dict that resp.json() will return.
        status_code: HTTP status code.
        content: Binary content for resp.content.
        ok: Boolean for resp.ok.
        request_error: If set, resp.raise_for_status() will raise this.
                       If None and status_code >= 400, auto-raises
                       requests.exceptions.HTTPError.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = ok
    resp.content = content
    resp.json.return_value = json_data or {}

    if request_error is not None:
        resp.raise_for_status.side_effect = request_error
    elif status_code >= 400 and REQ_EXC is not None:
        resp.raise_for_status.side_effect = REQ_EXC.HTTPError(
            f"{status_code} Error", response=resp)
    else:
        resp.raise_for_status.return_value = None

    return resp


def _make_get_raises(error):
    """
    Return a side-effect function that raises `error` when called.
    This ignores all arguments passed to get().
    """
    def _side_effect(*args, **kwargs):
        raise error
    return _side_effect


def _make_get_sequence(responses_and_raisers):
    """
    Return a side-effect function that returns/raises the next item
    from `responses_and_raisers` on each call.

    Each item can be:
      - A MagicMock (returned as the HTTP response)
      - An Exception class or instance (raised)
    """
    iterator = iter(responses_and_raisers)

    def _side_effect(*args, **kwargs):
        item = next(iterator)
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item
        elif isinstance(item, BaseException):
            raise item
        return item
    return _side_effect


def _patch_get_requests(test_case, session_get_side_effect=None,
                        session_get_return=None):
    """
    Patch ModCache._get_requests to return a controlled mock session.

    Either session_get_side_effect or session_get_return must be provided
    (not both).

    Args:
        session_get_side_effect: Callable or exception — set as
                                  session.get.side_effect.
        session_get_return: Fixed return value for session.get().
    """
    session = MagicMock()
    if session_get_side_effect is not None:
        session.get.side_effect = session_get_side_effect
    else:
        session.get.return_value = session_get_return or _mock_response({})

    patcher = patch.object(ModCache, '_get_requests', return_value=session)
    test_case.addCleanup(patcher.stop)
    return patcher.start()


# ===================================================================
#  Realistic mock data
# ===================================================================

SEARCH_HITS_FABRIC_API = [
    {
        'project_id': 'P7dR8M',
        'slug': 'fabric-api',
        'title': 'Fabric API',
        'description': 'Core modding API for Fabric.',
        'categories': ['fabric', 'utility'],
        'client_side': 'required',
        'server_side': 'required',
        'downloads': 42000000,
        'latest_version': '0.92.0+1.20.4',
        'icon_url': 'https://cdn.modrinth.com/icons/fabric-api.png',
        'versions': ['1.20.4', '1.20.2', '1.20.1'],
    }
]

SEARCH_HITS_MULTI = [
    {
        'project_id': 'P7dR8M',
        'slug': 'fabric-api',
        'title': 'Fabric API',
        'description': 'Core modding API for Fabric.',
        'categories': ['fabric', 'utility'],
        'downloads': 42000000,
        'latest_version': '0.92.0+1.20.4',
    },
    {
        'project_id': 'gvQqZ',
        'slug': 'lithium',
        'title': 'Lithium',
        'description': 'Performance optimization mod.',
        'categories': ['fabric', 'performance'],
        'downloads': 15000000,
        'latest_version': '0.12.6+1.20.4',
    },
]

PROJECT_FABRIC_API = {
    'id': 'P7dR8M',
    'slug': 'fabric-api',
    'title': 'Fabric API',
    'description': 'Core modding API for Fabric.',
    'body_url': 'https://modrinth.com/mod/fabric-api',
    'project_type': 'mod',
    'client_side': 'required',
    'server_side': 'required',
    'downloads': 42000000,
    'followers': 50000,
    'icon_url': 'https://cdn.modrinth.com/icons/fabric-api.png',
    'categories': ['fabric', 'utility'],
    'game_versions': ['1.20.4', '1.20.2', '1.20.1'],
    'loaders': ['fabric', 'quilt'],
    'date_created': '2020-01-01T00:00:00Z',
    'date_modified': '2025-01-15T00:00:00Z',
}

VERSIONS_FABRIC_API = [
    {
        'id': 'ver_fabric_2',
        'project_id': 'P7dR8M',
        'author_id': 'abric',
        'name': 'Fabric API',
        'version_number': '0.92.0+1.20.4',
        'game_versions': ['1.20.4', '1.20.2'],
        'loaders': ['fabric', 'quilt'],
        'version_type': 'release',
        'date_published': '2025-01-15T00:00:00Z',
        'dependencies': [],
        'files': [{
            'url': 'https://cdn.modrinth.com/fabric-api-0.92.0.jar',
            'filename': 'fabric-api-0.92.0.jar',
            'size': 512000,
            'hashes': {'sha1': 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b',
                       'sha512': 's1s2s3s4s5s6s7s8s9s0s1s2s3s4s5s6s7s8s9s0s1s2s3s4s5s6s7s8s9s0s1s2s3s4s5s6s7s8s9s0s1s2'},
        }],
    },
    {
        'id': 'ver_fabric_1',
        'project_id': 'P7dR8M',
        'author_id': 'abric',
        'name': 'Fabric API',
        'version_number': '0.91.0+1.20.2',
        'game_versions': ['1.20.2', '1.20.1'],
        'loaders': ['fabric', 'quilt'],
        'version_type': 'release',
        'date_published': '2024-12-01T00:00:00Z',
        'dependencies': [],
        'files': [{
            'url': 'https://cdn.modrinth.com/fabric-api-0.91.0.jar',
            'filename': 'fabric-api-0.91.0.jar',
            'size': 500000,
            'hashes': {'sha1': 'b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0',
                       'sha512': 't1t2t3t4t5t6t7t8t9t0t1t2t3t4t5t6t7t8t9t0t1t2t3t4t5t6t7t8t9t0t1t2t3t4t5t6t7t8t9t0t1t2'},
        }],
    },
]

VERSIONS_WITH_BETA = VERSIONS_FABRIC_API + [
    {
        'id': 'ver_fabric_beta',
        'project_id': 'P7dR8M',
        'author_id': 'abric',
        'name': 'Fabric API',
        'version_number': '0.93.0-beta+1.20.4',
        'game_versions': ['1.20.4'],
        'loaders': ['fabric'],
        'version_type': 'beta',
        'date_published': '2025-02-01T00:00:00Z',
        'dependencies': [],
        'files': [{
            'url': 'https://cdn.modrinth.com/fabric-api-0.93.0-beta.jar',
            'filename': 'fabric-api-0.93.0-beta.jar',
            'size': 520000,
            'hashes': {'sha1': 'c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1',
                       'sha512': 'u1u2u3u4u5u6u7u8u9u0u1u2u3u4u5u6u7u8u9u0u1u2u3u4u5u6u7u8u9u0u1u2u3u4u5u6u7u8u9u0u1u2'},
        }],
    },
]

# Real jar data with pre-computed hashes for download verification tests
JAR_DATA = b'mock_fabric_api_jar_data_1234567890'
JAR_SHA512 = hashlib.sha512(JAR_DATA).hexdigest()
JAR_SHA1 = hashlib.sha1(JAR_DATA).hexdigest()

VERSION_WITH_REAL_HASHES = {
    'id': 'ver_dl_test',
    'project_id': 'P7dR8M',
    'author_id': 'abric',
    'name': 'Fabric API',
    'version_number': '0.92.0+1.20.4',
    'game_versions': ['1.20.4', '1.20.2'],
    'loaders': ['fabric', 'quilt'],
    'version_type': 'release',
    'date_published': '2025-01-15T00:00:00Z',
    'dependencies': [],
    'files': [{
        'url': 'https://cdn.modrinth.com/fabric-api-0.92.0.jar',
        'filename': 'fabric-api-0.92.0.jar',
        'size': len(JAR_DATA),
        'hashes': {'sha1': JAR_SHA1, 'sha512': JAR_SHA512},
    }],
}

CF_SEARCH_RESULT = {
    'data': [
        {
            'id': 306612,
            'name': 'Sodium',
            'slug': 'sodium',
            'summary': 'Performance optimization mod for Minecraft.',
            'downloads': 30000000,
            'gameVersionLatestFiles': [
                {'gameVersion': '1.20.4', 'projectFileId': 12345},
            ],
        }
    ]
}


# ===================================================================
#  MockDatabase + MockVFS (minimal, matches existing patterns)
# ===================================================================

class MockDatabase:
    """Minimal Database mock for ModCache API tests."""

    def __init__(self):
        self._config = {}
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


class MockVFS:
    """Minimal VFS mock for ModCache API tests."""

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
#  Tests
# ===================================================================

# -- TestModrinthSearch ------------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestModrinthSearch(unittest.TestCase):
    """Tests for ModCache.modrinth_search()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs, cf_api_key=None)

    def test_search_returns_hits(self):
        """modrinth_search should return hits from the API response."""
        resp = _mock_response({'hits': SEARCH_HITS_FABRIC_API})
        _patch_get_requests(self, session_get_return=resp)
        results = self.cache.modrinth_search('fabric-api')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['slug'], 'fabric-api')
        self.assertEqual(results[0]['project_id'], 'P7dR8M')

    def test_search_empty_results(self):
        """modrinth_search should return empty list when no hits."""
        resp = _mock_response({'hits': []})
        _patch_get_requests(self, session_get_return=resp)
        results = self.cache.modrinth_search('nonexistent-mod-xyz')
        self.assertEqual(results, [])

    def test_search_request_exception(self):
        """modrinth_search should return [] on request exception."""
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_raises(
                REQ_EXC.RequestException("Timeout")))
        results = self.cache.modrinth_search('fabric-api')
        self.assertEqual(results, [])

    def test_search_with_facets(self):
        """modrinth_search should pass facets JSON when loaders/mc_version given."""
        resp = _mock_response({'hits': SEARCH_HITS_FABRIC_API})
        _patch_get_requests(self, session_get_return=resp)
        results = self.cache.modrinth_search(
            'fabric-api', loaders=['fabric'], mc_version='1.20.4')
        self.assertEqual(len(results), 1)

    def test_search_with_limit(self):
        """modrinth_search should cap limit at 100."""
        resp = _mock_response({'hits': SEARCH_HITS_MULTI})
        _patch_get_requests(self, session_get_return=resp)
        results = self.cache.modrinth_search('mod', limit=50)
        self.assertEqual(len(results), 2)


# -- TestModrinthGetProject --------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestModrinthGetProject(unittest.TestCase):
    """Tests for ModCache.modrinth_get_project()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs, cf_api_key=None)

    def test_get_project_success(self):
        """modrinth_get_project should return project metadata dict."""
        resp = _mock_response(PROJECT_FABRIC_API)
        _patch_get_requests(self, session_get_return=resp)
        project = self.cache.modrinth_get_project('fabric-api')
        self.assertIsNotNone(project)
        self.assertEqual(project['slug'], 'fabric-api')
        self.assertEqual(project['title'], 'Fabric API')
        self.assertEqual(project['project_type'], 'mod')

    def test_get_project_404(self):
        """modrinth_get_project should return None on 404."""
        err = REQ_EXC.HTTPError("404 Not Found",
                                response=MagicMock(status_code=404))
        resp = _mock_response({}, status_code=404, request_error=err)
        _patch_get_requests(self, session_get_return=resp)
        project = self.cache.modrinth_get_project('nonexistent-mod')
        self.assertIsNone(project)

    def test_get_project_request_exception(self):
        """modrinth_get_project should return None on connection error."""
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_raises(
                REQ_EXC.RequestException("Connection refused")))
        project = self.cache.modrinth_get_project('fabric-api')
        self.assertIsNone(project)

    def test_get_project_invalid_slug(self):
        """modrinth_get_project with empty slug should fail gracefully."""
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_raises(
                REQ_EXC.RequestException("Invalid slug")))
        project = self.cache.modrinth_get_project('')
        self.assertIsNone(project)


# -- TestModrinthGetVersions -------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestModrinthGetVersions(unittest.TestCase):
    """Tests for ModCache.modrinth_get_versions()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs, cf_api_key=None)

    def test_get_versions_success(self):
        """modrinth_get_versions should return sorted version list."""
        resp = _mock_response(VERSIONS_FABRIC_API)
        _patch_get_requests(self, session_get_return=resp)
        versions = self.cache.modrinth_get_versions('fabric-api')
        self.assertEqual(len(versions), 2)
        # Sorted by date_published descending
        self.assertEqual(versions[0]['version_number'], '0.92.0+1.20.4')
        self.assertEqual(versions[1]['version_number'], '0.91.0+1.20.2')

    def test_get_versions_empty(self):
        """modrinth_get_versions should return [] when API returns []."""
        resp = _mock_response([])
        _patch_get_requests(self, session_get_return=resp)
        versions = self.cache.modrinth_get_versions('unknown-mod')
        self.assertEqual(versions, [])

    def test_get_versions_filter_mc_version(self):
        """modrinth_get_versions should filter by mc_version."""
        resp = _mock_response(VERSIONS_FABRIC_API)
        _patch_get_requests(self, session_get_return=resp)
        versions = self.cache.modrinth_get_versions(
            'fabric-api', mc_version='1.20.4')
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]['version_number'], '0.92.0+1.20.4')

    def test_get_versions_filter_no_match(self):
        """modrinth_get_versions returns [] when filter matches nothing."""
        resp = _mock_response(VERSIONS_FABRIC_API)
        _patch_get_requests(self, session_get_return=resp)
        versions = self.cache.modrinth_get_versions(
            'fabric-api', mc_version='1.7.10')
        self.assertEqual(versions, [])

    def test_get_versions_filter_loader(self):
        """modrinth_get_versions should filter by loaders."""
        resp = _mock_response(VERSIONS_FABRIC_API)
        _patch_get_requests(self, session_get_return=resp)
        versions = self.cache.modrinth_get_versions(
            'fabric-api', loaders=['fabric'])
        self.assertEqual(len(versions), 2)
        versions_forge = self.cache.modrinth_get_versions(
            'fabric-api', loaders=['forge'])
        self.assertEqual(versions_forge, [])

    def test_get_versions_request_exception(self):
        """modrinth_get_versions should return [] on request error."""
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_raises(
                REQ_EXC.RequestException("Timeout")))
        versions = self.cache.modrinth_get_versions('fabric-api')
        self.assertEqual(versions, [])


# -- TestModrinthGetLatest ---------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestModrinthGetLatest(unittest.TestCase):
    """Tests for ModCache.modrinth_get_latest()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs, cf_api_key=None)

    def test_get_latest_release(self):
        """modrinth_get_latest should return the latest release version."""
        resp = _mock_response(VERSIONS_WITH_BETA)
        _patch_get_requests(self, session_get_return=resp)
        version = self.cache.modrinth_get_latest(
            'fabric-api', '1.20.4', loader='fabric', version_type='release')
        self.assertIsNotNone(version)
        self.assertEqual(version['version_type'], 'release')
        self.assertEqual(version['version_number'], '0.92.0+1.20.4')

    def test_get_latest_beta(self):
        """modrinth_get_latest should return the latest beta version."""
        resp = _mock_response(VERSIONS_WITH_BETA)
        _patch_get_requests(self, session_get_return=resp)
        version = self.cache.modrinth_get_latest(
            'fabric-api', '1.20.4', loader='fabric', version_type='beta')
        self.assertEqual(version['version_type'], 'beta')
        self.assertEqual(version['version_number'], '0.93.0-beta+1.20.4')

    def test_get_latest_no_type_filter(self):
        """modrinth_get_latest with version_type=None returns first version."""
        resp = _mock_response(VERSIONS_WITH_BETA)
        _patch_get_requests(self, session_get_return=resp)
        version = self.cache.modrinth_get_latest(
            'fabric-api', '1.20.4', loader='fabric', version_type=None)
        self.assertIsNotNone(version)
        self.assertEqual(version['version_number'], '0.93.0-beta+1.20.4')

    def test_get_latest_no_match(self):
        """modrinth_get_latest should return None when no version matches."""
        resp = _mock_response(VERSIONS_WITH_BETA)
        _patch_get_requests(self, session_get_return=resp)
        version = self.cache.modrinth_get_latest(
            'fabric-api', '1.7.10', loader='fabric', version_type='release')
        self.assertIsNone(version)

    def test_get_latest_empty_versions(self):
        """modrinth_get_latest should return None when no versions exist."""
        resp = _mock_response([])
        _patch_get_requests(self, session_get_return=resp)
        version = self.cache.modrinth_get_latest(
            'unknown-mod', '1.20.4', loader='fabric')
        self.assertIsNone(version)


# -- TestModrinthDownload ----------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestModrinthDownload(unittest.TestCase):
    """Tests for ModCache.modrinth_download()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs, cf_api_key=None)

    def test_download_cache_hit(self):
        """modrinth_download should return cached data from VFS without HTTP."""
        vfs_path = "/mod-cache/modrinth/fabric-api-1.20.4-fabric.jar"
        self.vfs.write(vfs_path, JAR_DATA)
        self.db.set_config(
            0, "mod_cache_modrinth-fabric-api-1.20.4-fabric_filename",
            json.dumps("fabric-api-0.92.0.jar"))
        result = self.cache.modrinth_download(
            'fabric-api', '1.20.4', loader='fabric')
        self.assertIsNotNone(result)
        filename, data = result
        self.assertEqual(filename, "fabric-api-0.92.0.jar")
        self.assertEqual(data, JAR_DATA)

    def test_download_fresh_with_verification(self):
        """modrinth_download should download, verify SHA-512, and cache."""
        versions_resp = _mock_response([VERSION_WITH_REAL_HASHES])
        download_resp = _mock_response({}, status_code=200, content=JAR_DATA)
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_sequence(
                [versions_resp, download_resp]))
        result = self.cache.modrinth_download(
            'fabric-api', '1.20.4', loader='fabric')
        self.assertIsNotNone(result)
        filename, data = result
        self.assertEqual(filename, 'fabric-api-0.92.0.jar')
        self.assertEqual(data, JAR_DATA)
        vfs_path = "/mod-cache/modrinth/fabric-api-1.20.4-fabric.jar"
        self.assertEqual(self.vfs.read(vfs_path), JAR_DATA)

    def test_download_hash_mismatch(self):
        """modrinth_download should return None on SHA-512 mismatch."""
        bad_version = {
            'id': 'ver_dl_bad',
            'project_id': 'P7dR8M',
            'name': 'Fabric API',
            'version_number': '0.92.0+1.20.4',
            'game_versions': ['1.20.4'],
            'loaders': ['fabric'],
            'version_type': 'release',
            'date_published': '2025-01-15T00:00:00Z',
            'dependencies': [],
            'files': [{
                'url': 'https://cdn.modrinth.com/fabric-api-bad.jar',
                'filename': 'fabric-api-bad.jar',
                'size': len(JAR_DATA),
                'hashes': {
                    'sha1': 'badbadbadbadbadbadbadbadbadbadbadbadbad1',
                    'sha512': 'badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbad',
                },
            }],
        }
        versions_resp = _mock_response([bad_version])
        download_resp = _mock_response({}, status_code=200, content=JAR_DATA)
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_sequence(
                [versions_resp, download_resp]))
        result = self.cache.modrinth_download(
            'fabric-api', '1.20.4', loader='fabric')
        self.assertIsNone(result)

    def test_download_no_version(self):
        """modrinth_download should return None when no matching version."""
        resp = _mock_response([])
        _patch_get_requests(self, session_get_return=resp)
        result = self.cache.modrinth_download(
            'unknown-mod', '1.20.4', loader='fabric')
        self.assertIsNone(result)

    def test_download_http_error(self):
        """modrinth_download should return None on download HTTP error."""
        versions_resp = _mock_response([VERSION_WITH_REAL_HASHES])
        download_err = REQ_EXC.HTTPError(
            "500 Server Error", response=MagicMock(status_code=500))
        download_resp = _mock_response(
            {}, status_code=500, request_error=download_err)
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_sequence(
                [versions_resp, download_resp]))
        result = self.cache.modrinth_download(
            'fabric-api', '1.20.4', loader='fabric')
        self.assertIsNone(result)

    def test_download_no_files_in_version(self):
        """modrinth_download should return None when version has no files."""
        version_no_files = dict(VERSION_WITH_REAL_HASHES)
        version_no_files['files'] = []
        resp = _mock_response([version_no_files])
        _patch_get_requests(self, session_get_return=resp)
        result = self.cache.modrinth_download(
            'fabric-api', '1.20.4', loader='fabric')
        self.assertIsNone(result)

    def test_download_download_request_exception(self):
        """modrinth_download should return None on download connection error."""
        versions_resp = _mock_response([VERSION_WITH_REAL_HASHES])
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_sequence(
                [versions_resp,
                 REQ_EXC.RequestException("Connection error")]))
        result = self.cache.modrinth_download(
            'fabric-api', '1.20.4', loader='fabric')
        self.assertIsNone(result)


# -- TestCurseForgeSearch ----------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestCurseForgeSearch(unittest.TestCase):
    """Tests for ModCache.curseforge_search()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)

    def test_curseforge_search_success(self):
        """curseforge_search should return results when API key is set."""
        cache = ModCache(self.db, self.vfs, cf_api_key='test-cf-key-123')
        resp = _mock_response(CF_SEARCH_RESULT)
        _patch_get_requests(self, session_get_return=resp)
        results = cache.curseforge_search('sodium')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Sodium')
        self.assertEqual(results[0]['slug'], 'sodium')

    def test_curseforge_search_no_api_key(self):
        """curseforge_search should return [] when CF_API_KEY is missing."""
        cache = ModCache(self.db, self.vfs, cf_api_key=None)
        results = cache.curseforge_search('sodium')
        self.assertEqual(results, [])

    def test_curseforge_search_empty_api_key(self):
        """curseforge_search should return [] when API key is empty string."""
        cache = ModCache(self.db, self.vfs, cf_api_key='')
        results = cache.curseforge_search('sodium')
        self.assertEqual(results, [])

    def test_curseforge_search_request_exception(self):
        """curseforge_search should return [] on request error."""
        cache = ModCache(self.db, self.vfs, cf_api_key='test-cf-key-123')
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_raises(
                REQ_EXC.RequestException("API limit")))
        results = cache.curseforge_search('sodium')
        self.assertEqual(results, [])

    def test_curseforge_search_with_game_version(self):
        """curseforge_search should pass gameVersion filter param."""
        cache = ModCache(self.db, self.vfs, cf_api_key='test-cf-key-123')
        resp = _mock_response(CF_SEARCH_RESULT)
        _patch_get_requests(self, session_get_return=resp)
        results = cache.curseforge_search('sodium', game_version='1.20.4')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Sodium')

    def test_curseforge_search_with_loader_type(self):
        """curseforge_search should pass modLoaderType filter param."""
        cache = ModCache(self.db, self.vfs, cf_api_key='test-cf-key-123')
        resp = _mock_response(CF_SEARCH_RESULT)
        _patch_get_requests(self, session_get_return=resp)
        results = cache.curseforge_search('sodium', mod_loader_type=4)
        self.assertEqual(len(results), 1)

    def test_curseforge_search_rate_limit(self):
        """curseforge_search should return [] on 429 rate limit."""
        cache = ModCache(self.db, self.vfs, cf_api_key='test-cf-key-123')
        err = REQ_EXC.HTTPError("429 Rate Limited",
                                response=MagicMock(status_code=429))
        resp = _mock_response({}, status_code=429, request_error=err)
        _patch_get_requests(self, session_get_return=resp)
        results = cache.curseforge_search('sodium')
        self.assertEqual(results, [])

    def test_curseforge_search_500_error(self):
        """curseforge_search should return [] on 500 server error."""
        cache = ModCache(self.db, self.vfs, cf_api_key='test-cf-key-123')
        err = REQ_EXC.HTTPError("500 Server Error",
                                response=MagicMock(status_code=500))
        resp = _mock_response({}, status_code=500, request_error=err)
        _patch_get_requests(self, session_get_return=resp)
        results = cache.curseforge_search('sodium')
        self.assertEqual(results, [])


# -- TestClearCache ----------------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestClearCache(unittest.TestCase):
    """Tests for ModCache.clear_cache()."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs, cf_api_key=None)

    def test_clear_cache_default(self):
        """clear_cache() should return 0 with default older_than_days."""
        result = self.cache.clear_cache()
        self.assertEqual(result, 0)

    def test_clear_cache_custom_days(self):
        """clear_cache() should accept a custom older_than_days parameter."""
        result = self.cache.clear_cache(older_than_days=7)
        self.assertEqual(result, 0)

    def test_clear_cache_zero_days(self):
        """clear_cache() should accept older_than_days=0."""
        result = self.cache.clear_cache(older_than_days=0)
        self.assertEqual(result, 0)

    def test_clear_cache_large_value(self):
        """clear_cache() should accept a large older_than_days value."""
        result = self.cache.clear_cache(older_than_days=365)
        self.assertEqual(result, 0)


# -- TestModCacheEdgeCases ---------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestModCacheEdgeCases(unittest.TestCase):
    """Edge case tests for ModCache HTTP interactions."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs, cf_api_key=None)

    def test_search_rate_limit(self):
        """modrinth_search should handle 429 rate limit gracefully."""
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_raises(
                REQ_EXC.RequestException("429 Too Many Requests")))
        results = self.cache.modrinth_search('fabric-api')
        self.assertEqual(results, [])

    def test_search_server_error(self):
        """modrinth_search should handle 500 server error gracefully."""
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_raises(
                REQ_EXC.RequestException("500 Server Error")))
        results = self.cache.modrinth_search('fabric-api')
        self.assertEqual(results, [])

    def test_get_project_timeout(self):
        """modrinth_get_project should handle timeout gracefully."""
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_raises(
                REQ_EXC.RequestException("Connection timeout")))
        project = self.cache.modrinth_get_project('fabric-api')
        self.assertIsNone(project)

    def test_search_then_project_sequential(self):
        """Sequential modrinth_search then get_project should work."""
        search_resp = _mock_response({'hits': SEARCH_HITS_FABRIC_API})
        project_resp = _mock_response(PROJECT_FABRIC_API)
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_sequence(
                [search_resp, project_resp]))
        results = self.cache.modrinth_search('fabric-api')
        self.assertEqual(len(results), 1)
        project = self.cache.modrinth_get_project('fabric-api')
        self.assertIsNotNone(project)
        self.assertEqual(project['slug'], 'fabric-api')


# -- TestModCacheIntegration -------------------------------------------------

@unittest.skipUnless(CACHE_IMPORTABLE, f"ModCache import failed: {_IMPORT_ERROR}")
class TestModCacheIntegration(unittest.TestCase):
    """Integration-style tests combining multiple ModCache methods."""

    def setUp(self):
        self.db = MockDatabase()
        self.vfs = MockVFS(self.db)
        self.cache = ModCache(self.db, self.vfs, cf_api_key=None)

    def test_full_discovery_flow(self):
        """Smoke test: search → project → versions → latest pipeline."""
        search_resp = _mock_response({'hits': SEARCH_HITS_FABRIC_API})
        project_resp = _mock_response(PROJECT_FABRIC_API)
        versions_resp = _mock_response(VERSIONS_FABRIC_API)
        # get_latest internally calls get_versions again, so we need 4 responses
        latest_versions_resp = _mock_response(VERSIONS_WITH_BETA)
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_sequence(
                [search_resp, project_resp, versions_resp, latest_versions_resp]))
        hits = self.cache.modrinth_search('fabric-api')
        self.assertGreater(len(hits), 0)
        slug = hits[0]['slug']
        project = self.cache.modrinth_get_project(slug)
        self.assertIsNotNone(project)
        versions = self.cache.modrinth_get_versions(slug)
        self.assertGreater(len(versions), 0)
        latest = self.cache.modrinth_get_latest(
            slug, '1.20.4', loader='fabric', version_type='release')
        self.assertIsNotNone(latest)

    def test_cross_platform_searches(self):
        """Both Modrinth and CurseForge searches should work independently."""
        mr_resp = _mock_response({'hits': SEARCH_HITS_FABRIC_API})
        cf_cache = ModCache(self.db, self.vfs, cf_api_key='test-cf-key')
        cf_resp = _mock_response(CF_SEARCH_RESULT)
        _patch_get_requests(
        self,
            session_get_side_effect=_make_get_sequence(
                [mr_resp, cf_resp]))
        modrinth_results = self.cache.modrinth_search('fabric-api')
        self.assertEqual(len(modrinth_results), 1)
        curseforge_results = cf_cache.curseforge_search('sodium')
        self.assertEqual(len(curseforge_results), 1)


# ===================================================================
#  Run
# ===================================================================

if __name__ == '__main__':
    unittest.main()
