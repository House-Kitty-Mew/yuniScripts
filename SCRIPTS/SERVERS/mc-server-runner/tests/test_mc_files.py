"""
test_mc_files.py - Comprehensive tests for Minecraft file acquisition.

Tests: IntegrityChecker, DownloadCache, MojangDownloader (simulated),
FabricDownloader (simulated), ForgeDownloader (simulated), file placement,
cache integration, error handling, edge cases.

Usage:
    python -m tests.test_mc_files  (from project root)
    python -m unittest tests.test_mc_files
"""

import os
import sys
import json
import time
import hashlib
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import downloader module directly (matches pattern in test_dynamic_deps.py)
_ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
sys.path.insert(0, str(_ENGINE_DIR))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "downloader", str(_ENGINE_DIR / "downloader.py")
)
_dl = importlib.util.module_from_spec(_spec)
sys.modules["downloader"] = _dl
_spec.loader.exec_module(_dl)

# Module aliases
DownloadResult = _dl.DownloadResult
VersionManifestEntry = _dl.VersionManifestEntry
IntegrityChecker = _dl.IntegrityChecker
IntegrityCheckError = _dl.IntegrityCheckError
DownloadCache = _dl.DownloadCache
DownloadError = _dl.DownloadError
BaseDownloader = _dl.BaseDownloader
MojangDownloader = _dl.MojangDownloader
FabricDownloader = _dl.FabricDownloader
ForgeDownloader = _dl.ForgeDownloader
MANIFEST_URL = _dl.MANIFEST_URL

# ===================================================================
#  Helper utilities
# ===================================================================


def _make_temp_dir() -> str:
    return tempfile.mkdtemp(prefix="mc_files_test_")


def _fake_jar_data(size: int = 1024) -> bytes:
    """Generate fake JAR-like data (starts with PK zip header)."""
    return b"PK" + os.urandom(size - 2)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()
# ===================================================================
#  Test: IntegrityChecker
# ===================================================================


class TestIntegrityChecker(unittest.TestCase):
    """Test IntegrityChecker static methods."""

    def test_sha256_returns_64_hex_chars(self):
        data = b"hello world"
        digest = IntegrityChecker.sha256(data)
        self.assertEqual(len(digest), 64)

    def test_sha1_returns_40_hex_chars(self):
        data = b"hello world"
        digest = IntegrityChecker.sha1(data)
        self.assertEqual(len(digest), 40)

    def test_sha256_deterministic(self):
        data = b"deterministic test data"
        self.assertEqual(IntegrityChecker.sha256(data), IntegrityChecker.sha256(data))

    def test_sha256_file_matches_bytes(self):
        data = b"file integrity test data"
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            tmp_path = f.name
        try:
            bytes_digest = IntegrityChecker.sha256(data)
            file_digest = IntegrityChecker.sha256_file(tmp_path)
            self.assertEqual(bytes_digest, file_digest)
        finally:
            os.unlink(tmp_path)

    def test_sha256_empty_bytes(self):
        digest = IntegrityChecker.sha256(b"")
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_verify_sha256_correct(self):
        data = b"verify me"
        h = IntegrityChecker.sha256(data)
        self.assertTrue(IntegrityChecker.verify_sha256(data, h))

    def test_verify_sha256_incorrect(self):
        data = b"verify me"
        self.assertFalse(IntegrityChecker.verify_sha256(data, "00" * 32))

    def test_verify_size_correct(self):
        self.assertTrue(IntegrityChecker.verify_size(b"12345", 5))

    def test_verify_size_incorrect(self):
        self.assertFalse(IntegrityChecker.verify_size(b"12345", 10))

    def test_assert_integrity_passes_all(self):
        data = b"all good"
        IntegrityChecker.assert_integrity(data, expected_sha256=_sha256(data), expected_sha1=_sha1(data), expected_size=len(data))

    def test_assert_integrity_raises_on_sha256_mismatch(self):
        with self.assertRaises(IntegrityCheckError):
            IntegrityChecker.assert_integrity(b"good", expected_sha256="00" * 32)

    def test_assert_integrity_raises_on_sha1_mismatch(self):
        with self.assertRaises(IntegrityCheckError):
            IntegrityChecker.assert_integrity(b"good", expected_sha1="00" * 20)

    def test_assert_integrity_raises_on_size_mismatch(self):
        with self.assertRaises(IntegrityCheckError):
            IntegrityChecker.assert_integrity(b"good", expected_size=999)

    def test_assert_integrity_none_checks_skip(self):
        IntegrityChecker.assert_integrity(b"any data")

    def test_assert_file_integrity_passes(self):
        data = b"file check"
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            tmp_path = f.name
        try:
            IntegrityChecker.assert_file_integrity(tmp_path, expected_sha256=_sha256(data), expected_size=len(data))
        finally:
            os.unlink(tmp_path)

    def test_assert_file_integrity_raises_on_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            IntegrityChecker.assert_file_integrity("/nonexistent/path/file.jar")

    def test_assert_file_integrity_raises_on_bad_hash(self):
        data = b"original content"
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            tmp_path = f.name
        try:
            with self.assertRaises(IntegrityCheckError):
                IntegrityChecker.assert_file_integrity(tmp_path, expected_sha256="00" * 32)
        finally:
            os.unlink(tmp_path)
# ===================================================================
#  Test: DownloadCache
# ===================================================================


class TestDownloadCache(unittest.TestCase):
    """Test the DownloadCache filesystem-based caching system."""

    def setUp(self):
        self._cache_dir = _make_temp_dir()
        self.cache = DownloadCache(self._cache_dir, ttl_seconds=3600)

    def tearDown(self):
        shutil.rmtree(self._cache_dir, ignore_errors=True)

    def test_put_and_get_roundtrip(self):
        data = _fake_jar_data(512)
        self.cache.put("vanilla-1.20.4", data)
        retrieved = self.cache.get("vanilla-1.20.4")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved, data)

    def test_get_missing_key_returns_none(self):
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_get_after_evict_returns_none(self):
        self.cache.put("test-key", _fake_jar_data(128))
        self.cache.evict("test-key")
        self.assertIsNone(self.cache.get("test-key"))

    def test_put_stores_metadata(self):
        data = _fake_jar_data(256)
        self.cache.put("key1", data, version="1.20.4", source="mojang")
        entry = self.cache.get_entry("key1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["version"], "1.20.4")
        self.assertEqual(entry["source"], "mojang")

    def test_put_returns_absolute_path(self):
        path = self.cache.put("key-path", _fake_jar_data(100))
        self.assertTrue(os.path.isabs(path))
        self.assertTrue(os.path.isfile(path))

    def test_expired_entry_returns_none(self):
        short_cache = DownloadCache(self._cache_dir, ttl_seconds=0)
        short_cache.put("expire-key", _fake_jar_data(64))
        import time; time.sleep(0.01)
        self.assertIsNone(short_cache.get("expire-key"))

    def test_valid_entry_within_ttl(self):
        self.cache.put("valid-key", _fake_jar_data(128))
        self.assertIsNotNone(self.cache.get("valid-key"))

    def test_has_returns_true_for_valid_entry(self):
        self.cache.put("has-key", _fake_jar_data(100))
        self.assertTrue(self.cache.has("has-key"))

    def test_has_returns_false_for_missing(self):
        self.assertFalse(self.cache.has("no-such-key"))

    def test_evict_returns_true_for_existing(self):
        self.cache.put("evict-me", _fake_jar_data(64))
        self.assertTrue(self.cache.evict("evict-me"))

    def test_evict_returns_false_for_nonexistent(self):
        self.assertFalse(self.cache.evict("does-not-exist"))

    def test_evict_preserves_file(self):
        data = _fake_jar_data(128)
        path = self.cache.put("preserve-file", data)
        self.cache.evict("preserve-file")
        self.assertTrue(os.path.isfile(path))

    def test_clear_removes_all_entries(self):
        self.cache.put("a", _fake_jar_data(64))
        self.cache.put("b", _fake_jar_data(64))
        self.cache.put("c", _fake_jar_data(64))
        self.assertEqual(self.cache.clear(), 3)
        self.assertEqual(len(self.cache._index), 0)

    def test_clear_older_than(self):
        self.cache.put("new", _fake_jar_data(64))
        import time; time.sleep(0.5)
        self.cache.put("old", _fake_jar_data(64))
        cleared = self.cache.clear(older_than_seconds=0.25)
        self.assertEqual(cleared, 1)
        self.assertIsNotNone(self.cache.get("old"))
        self.assertIsNone(self.cache.get("new"))

    def test_stats_empty_cache(self):
        stats = self.cache.stats()
        self.assertEqual(stats["total_entries"], 0)
        self.assertEqual(stats["total_size_bytes"], 0)

    def test_stats_with_entries(self):
        self.cache.put("stats-key", _fake_jar_data(200))
        stats = self.cache.stats()
        self.assertEqual(stats["total_entries"], 1)
        self.assertEqual(stats["valid_entries"], 1)
        self.assertEqual(stats["total_size_bytes"], 200)

    def test_cache_hit_verifies_integrity(self):
        data = _fake_jar_data(256)
        path = self.cache.put("integrity-key", data)
        with open(path, "wb") as f:
            f.write(b"TAMPERED DATA")
        self.assertIsNone(self.cache.get("integrity-key"))

    def test_cache_missing_file_evicts(self):
        data = _fake_jar_data(64)
        path = self.cache.put("missing-file-key", data)
        os.unlink(path)
        self.assertIsNone(self.cache.get("missing-file-key"))

    def test_index_persists_across_cache_instances(self):
        self.cache.put("persist-key", _fake_jar_data(128), version="1.0")
        cache2 = DownloadCache(self._cache_dir, ttl_seconds=3600)
        retrieved = cache2.get("persist-key")
        self.assertIsNotNone(retrieved)
        self.assertEqual(cache2.get_entry("persist-key")["version"], "1.0")

    def test_index_survives_corrupt_json(self):
        with open(self.cache.index_path, "w") as f:
            f.write("NOT VALID JSON{{{{")
        cache2 = DownloadCache(self._cache_dir)
        self.assertEqual(len(cache2._index), 0)
# ===================================================================
#  Test: BaseDownloader - File Placement
# ===================================================================


class TestBaseDownloaderPlacement(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = _make_temp_dir()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_place_file_creates_dir(self):
        nested = os.path.join(self._tmp_dir, "a", "b", "c")
        path = BaseDownloader.place_file(b"nested dir test", nested, "server.jar")
        self.assertTrue(os.path.isdir(nested))
        self.assertTrue(os.path.isfile(path))

    def test_place_file_returns_abs_path(self):
        path = BaseDownloader.place_file(b"test", self._tmp_dir, "test.jar")
        self.assertTrue(os.path.isabs(path))

    def test_place_file_overwrite_default(self):
        path1 = BaseDownloader.place_file(b"v1", self._tmp_dir, "overwrite.jar")
        path2 = BaseDownloader.place_file(b"v2", self._tmp_dir, "overwrite.jar")
        self.assertEqual(path1, path2)
        with open(path2, "rb") as f:
            self.assertEqual(f.read(), b"v2")

    def test_place_file_no_overwrite_appends_suffix(self):
        path1 = BaseDownloader.place_file(b"first", self._tmp_dir, "no-overwrite.jar", overwrite=False)
        path2 = BaseDownloader.place_file(b"second", self._tmp_dir, "no-overwrite.jar", overwrite=False)
        self.assertNotEqual(path1, path2)
        self.assertTrue(os.path.isfile(path1))
        self.assertTrue(os.path.isfile(path2))

    def test_place_file_writes_correct_data(self):
        data = os.urandom(4096)
        path = BaseDownloader.place_file(data, self._tmp_dir, "exact.jar")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), data)

# ===================================================================
#  Test: MojangDownloader - Manifest Parsing
# ===================================================================


class TestMojangDownloaderManifest(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = _make_temp_dir()
        self.dl = MojangDownloader()
        self.sample_manifest = {
            "latest": {"release": "1.20.4", "snapshot": "24w14a"},
            "versions": [
                {"id": "1.20.4", "type": "release",
                 "url": "https://example.com/v1/1.20.4.json",
                 "time": "2024-06-01T12:00:00+00:00", "sha1": "aaa"},
                {"id": "1.20.2", "type": "release",
                 "url": "https://example.com/v1/1.20.2.json",
                 "time": "2024-05-01T12:00:00+00:00", "sha1": "bbb"},
                {"id": "24w14a", "type": "snapshot",
                 "url": "https://example.com/v1/24w14a.json",
                 "time": "2024-04-01T12:00:00+00:00", "sha1": "ccc"},
                {"id": "old-alpha-1", "type": "old_alpha",
                 "url": "https://example.com/v1/old-alpha-1.json",
                 "time": "2023-01-01T12:00:00+00:00", "sha1": "ddd"},
            ],
        }

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch.object(MojangDownloader, "_http_get_json")
    def test_fetch_manifest_returns_dict(self, mock_get):
        mock_get.return_value = self.sample_manifest
        manifest = self.dl.fetch_manifest()
        self.assertEqual(manifest["latest"]["release"], "1.20.4")

    @patch.object(MojangDownloader, "_http_get_json")
    def test_list_versions_release_only(self, mock_get):
        mock_get.return_value = self.sample_manifest
        entries = self.dl.list_versions(release_type="release")
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertEqual(e.release_type, "release")

    @patch.object(MojangDownloader, "_http_get_json")
    def test_list_versions_all(self, mock_get):
        mock_get.return_value = self.sample_manifest
        entries = self.dl.list_versions(release_type=None)
        self.assertEqual(len(entries), 4)

    @patch.object(MojangDownloader, "_http_get_json")
    def test_resolve_version_finds_exact(self, mock_get):
        mock_get.return_value = self.sample_manifest
        entry = self.dl.resolve_version("1.20.4")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.id, "1.20.4")
        self.assertEqual(entry.sha1, "aaa")

    @patch.object(MojangDownloader, "_http_get_json")
    def test_resolve_version_returns_none_for_missing(self, mock_get):
        mock_get.return_value = self.sample_manifest
        entry = self.dl.resolve_version("99.99.99")
        self.assertIsNone(entry)

    @patch.object(MojangDownloader, "_http_get_json")
    def test_resolve_version_latest(self, mock_get):
        mock_get.return_value = self.sample_manifest
        entry = self.dl.resolve_version("latest")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.id, "1.20.4")

    @patch.object(MojangDownloader, "_http_get_json")
    def test_resolve_version_latest_no_latest(self, mock_get):
        mock_get.return_value = {"versions": []}
        entry = self.dl.resolve_version("latest")
        self.assertIsNone(entry)
# ===================================================================
#  Test: MojangDownloader - Download Simulation
# ===================================================================


class TestMojangDownloaderDownload(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = _make_temp_dir()
        self.cache = DownloadCache(os.path.join(self._tmp_dir, "cache"))
        self.dl = MojangDownloader(cache=self.cache)
        self.server_jar_data = _fake_jar_data(2048)
        self.sample_version_json = {
            "id": "1.20.4",
            "downloads": {
                "server": {
                    "url": "https://example.com/dl/server.jar",
                    "sha1": _sha1(self.server_jar_data),
                    "size": 2048,
                }
            },
        }
        self.sample_manifest = {
            "latest": {"release": "1.20.4"},
            "versions": [
                {"id": "1.20.4", "type": "release",
                 "url": "https://example.com/v1.json",
                 "time": "2024-06-01T12:00:00+00:00", "sha1": "abc"},
            ],
        }

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch.object(MojangDownloader, "_http_get_json")
    @patch.object(MojangDownloader, "_http_get")
    def test_download_server_success(self, mock_get, mock_get_json):
        mock_get_json.side_effect = [self.sample_manifest, self.sample_version_json]
        mock_get.return_value = self.server_jar_data
        result = self.dl.download_server("1.20.4", dest_dir=self._tmp_dir)
        self.assertTrue(result.success)
        self.assertEqual(result.version, "1.20.4")
        self.assertFalse(result.cached)
        jar_path = os.path.join(self._tmp_dir, "server.jar")
        self.assertTrue(os.path.isfile(jar_path))

    @patch.object(MojangDownloader, "_http_get_json")
    def test_download_server_version_not_found(self, mock_get_json):
        mock_get_json.return_value = {"latest": {"release": "1.20.4"}, "versions": []}
        result = self.dl.download_server("99.99.99")
        self.assertFalse(result.success)
        self.assertIn("not found", result.error.lower())

    @patch.object(MojangDownloader, "_http_get_json")
    @patch.object(MojangDownloader, "_http_get")
    def test_download_server_integrity_fail(self, mock_get, mock_get_json):
        mock_get_json.side_effect = [self.sample_manifest, self.sample_version_json]
        mock_get.return_value = b"CORRUPTED DATA"
        result = self.dl.download_server("1.20.4")
        self.assertFalse(result.success)
        self.assertIn("integrity", result.message.lower())

    @patch.object(MojangDownloader, "_http_get_json")
    @patch.object(MojangDownloader, "_http_get")
    def test_download_server_caches_result(self, mock_get, mock_get_json):
        mock_get_json.side_effect = [self.sample_manifest, self.sample_version_json]
        mock_get.return_value = self.server_jar_data
        r1 = self.dl.download_server("1.20.4")
        self.assertFalse(r1.cached)
        r2 = self.dl.download_server("1.20.4")
        self.assertTrue(r2.cached)

    @patch.object(MojangDownloader, "_http_get_json")
    @patch.object(MojangDownloader, "_http_get")
    def test_download_server_no_server_info(self, mock_get, mock_get_json):
        version_no_server = {"id": "1.20.4", "downloads": {}}
        mock_get_json.side_effect = [self.sample_manifest, version_no_server]
        result = self.dl.download_server("1.20.4")
        self.assertFalse(result.success)
        self.assertIn("no server download", result.error.lower())

    @patch.object(MojangDownloader, "_http_get_json")
    @patch.object(MojangDownloader, "_http_get")
    def test_download_server_verifies_sha1(self, mock_get, mock_get_json):
        mock_get_json.side_effect = [self.sample_manifest, self.sample_version_json]
        mock_get.return_value = self.server_jar_data
        result = self.dl.download_server("1.20.4")
        self.assertTrue(result.success)
        self.assertEqual(result.hash_sha1, _sha1(self.server_jar_data))

    @patch.object(MojangDownloader, "_http_get_json")
    @patch.object(MojangDownloader, "_http_get")
    def test_download_server_with_custom_filename(self, mock_get, mock_get_json):
        mock_get_json.side_effect = [self.sample_manifest, self.sample_version_json]
        mock_get.return_value = self.server_jar_data
        result = self.dl.download_server("1.20.4", dest_dir=self._tmp_dir, filename="my-server.jar")
        self.assertTrue(result.success)
        self.assertTrue(os.path.isfile(os.path.join(self._tmp_dir, "my-server.jar")))

# ===================================================================
#  Test: FabricDownloader
# ===================================================================


class TestFabricDownloader(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = _make_temp_dir()
        self.cache = DownloadCache(os.path.join(self._tmp_dir, "cache"))
        self.dl = FabricDownloader(cache=self.cache)
        self.sample_loader_meta = [
            {
                "loader": {"version": "0.15.11"},
                "intermediary": {"version": "0.1.0"},
                "launcherMeta": {"min_java_version": 17},
            },
            {
                "loader": {"version": "0.15.10"},
                "intermediary": {"version": "0.1.0"},
                "launcherMeta": {"min_java_version": 17},
            },
        ]
        self.fake_jar = _fake_jar_data(4096)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch.object(FabricDownloader, "_http_get_json")
    def test_list_loader_versions(self, mock_json):
        mock_json.return_value = self.sample_loader_meta
        entries = self.dl.list_loader_versions("1.20.4")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["loader"]["version"], "0.15.11")

    @patch.object(FabricDownloader, "_http_get_json")
    def test_resolve_loader_returns_latest(self, mock_json):
        mock_json.return_value = self.sample_loader_meta
        entry = self.dl.resolve_loader("1.20.4")
        self.assertEqual(entry["loader"]["version"], "0.15.11")

    @patch.object(FabricDownloader, "_http_get_json")
    def test_resolve_loader_none_for_missing(self, mock_json):
        mock_json.return_value = []
        entry = self.dl.resolve_loader("1.0.0")
        self.assertIsNone(entry)

    def test_build_server_launch_url(self):
        url = self.dl.build_server_launch_url("1.20.4", "0.15.11")
        self.assertIn("fabric-server-launch-0.15.11.jar", url)
        self.assertIn("maven.fabricmc.net", url)

    @patch.object(FabricDownloader, "_http_get_json")
    @patch.object(FabricDownloader, "_http_get")
    def test_download_loader_success(self, mock_get, mock_json):
        mock_json.return_value = self.sample_loader_meta
        mock_get.return_value = self.fake_jar
        result = self.dl.download_loader("1.20.4", dest_dir=self._tmp_dir)
        self.assertTrue(result.success)
        self.assertIn("0.15.11", result.version)
        self.assertTrue(os.path.isfile(os.path.join(self._tmp_dir, "server.jar")))

    @patch.object(FabricDownloader, "_http_get_json")
    @patch.object(FabricDownloader, "_http_get")
    def test_download_loader_with_specific_version(self, mock_get, mock_json):
        mock_json.return_value = self.sample_loader_meta
        mock_get.return_value = self.fake_jar
        result = self.dl.download_loader("1.20.4", loader_version="0.15.10")
        self.assertIn("0.15.10", result.version)

    @patch.object(FabricDownloader, "_http_get_json")
    def test_download_loader_no_loader_found(self, mock_json):
        mock_json.return_value = []
        result = self.dl.download_loader("1.0.0")
        self.assertFalse(result.success)
        self.assertIn("no fabric loader", result.error.lower())

    @patch.object(FabricDownloader, "_http_get_json")
    @patch.object(FabricDownloader, "_http_get")
    def test_download_loader_invalid_jar(self, mock_get, mock_json):
        mock_json.return_value = self.sample_loader_meta
        mock_get.return_value = b"NOT A ZIP FILE"
        result = self.dl.download_loader("1.20.4")
        self.assertFalse(result.success)
        self.assertIn("pk header", result.error.lower())

    @patch.object(FabricDownloader, "_http_get_json")
    @patch.object(FabricDownloader, "_http_get")
    def test_download_loader_caches(self, mock_get, mock_json):
        mock_json.return_value = self.sample_loader_meta
        mock_get.return_value = self.fake_jar
        r1 = self.dl.download_loader("1.20.4")
        self.assertFalse(r1.cached)
        r2 = self.dl.download_loader("1.20.4")
        self.assertTrue(r2.cached)
# ===================================================================
#  Test: ForgeDownloader
# ===================================================================


class TestForgeDownloader(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = _make_temp_dir()
        self.cache = DownloadCache(os.path.join(self._tmp_dir, "cache"))
        self.dl = ForgeDownloader(cache=self.cache)
        self.fake_jar = _fake_jar_data(8192)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_build_installer_url(self):
        url = self.dl.build_installer_url("1.20.4", "49.0.2")
        self.assertIn("forge-1.20.4-49.0.2-installer.jar", url)
        self.assertIn("maven.minecraftforge.net", url)

    def test_build_server_url(self):
        url = self.dl.build_server_url("1.20.4", "49.0.2")
        self.assertIn("forge-1.20.4-49.0.2-server.jar", url)

    def test_parse_maven_versions_empty(self):
        self.assertEqual(ForgeDownloader._parse_maven_versions("", "1.20.4"), [])

    def test_parse_maven_versions_valid(self):
        xml = """<?xml version="1.0"?><metadata><versioning><versions>
<version>1.20.4-49.0.2</version>
<version>1.20.4-49.0.1</version>
</versions></versioning></metadata>"""
        versions = ForgeDownloader._parse_maven_versions(xml, "1.20.4")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0], "49.0.2")

    def test_parse_maven_versions_sorts_descending(self):
        xml = """<metadata><versioning><versions>
<version>1.20.4-49.0.0</version>
<version>1.20.4-49.0.2</version>
<version>1.20.4-49.0.1</version>
</versions></versioning></metadata>"""
        self.assertEqual(ForgeDownloader._parse_maven_versions(xml, "1.20.4"),
                         ["49.0.2", "49.0.1", "49.0.0"])

    @patch.object(ForgeDownloader, "_http_get")
    @patch.object(ForgeDownloader, "_http_get_json")
    def test_list_forge_versions_from_xml(self, mock_json, mock_get):
        xml = """<metadata><versioning><versions>
<version>1.20.4-49.0.2</version>
</versions></versioning></metadata>"""
        mock_get.return_value = xml.encode("utf-8")
        versions = self.dl.list_forge_versions("1.20.4")
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0], "49.0.2")

    @patch.object(ForgeDownloader, "_http_get")
    @patch.object(ForgeDownloader, "_http_get_json")
    def test_resolve_latest(self, mock_json, mock_get):
        xml = """<metadata><versioning><versions>
<version>1.20.4-49.0.2</version>
<version>1.20.4-49.0.1</version>
</versions></versioning></metadata>"""
        mock_get.return_value = xml.encode("utf-8")
        self.assertEqual(self.dl.resolve_latest("1.20.4"), "49.0.2")

    @patch.object(ForgeDownloader, "_http_get")
    @patch.object(ForgeDownloader, "_http_get_json")
    def test_download_installer_success(self, mock_json, mock_get):
        xml = """<metadata><versioning><versions>
<version>1.20.4-49.0.2</version>
</versions></versioning></metadata>"""
        mock_get.side_effect = [xml.encode("utf-8"), self.fake_jar]
        result = self.dl.download_installer("1.20.4", dest_dir=self._tmp_dir)
        self.assertTrue(result.success)
        self.assertIn("49.0.2", result.version)
        self.assertTrue(os.path.isfile(os.path.join(self._tmp_dir, "forge-installer.jar")))

    @patch.object(ForgeDownloader, "_http_get")
    @patch.object(ForgeDownloader, "_http_get_json")
    def test_download_installer_with_specific_version(self, mock_json, mock_get):
        mock_get.return_value = self.fake_jar
        result = self.dl.download_installer("1.20.4", forge_version="49.0.1", dest_dir=self._tmp_dir)
        self.assertTrue(result.success)

    @patch.object(ForgeDownloader, "_http_get")
    @patch.object(ForgeDownloader, "_http_get_json")
    def test_download_installer_no_version(self, mock_json, mock_get):
        mock_get.side_effect = ForgeDownloader._http_get.__wrapped__ if hasattr(ForgeDownloader._http_get, "__wrapped__") else DownloadError("No data")
        # Actually, let mock_get raise on XML fetch and mock_json return empty
        mock_get.side_effect = DownloadError("No XML")
        mock_json.return_value = {}
        result = self.dl.download_installer("2.0.0")
        self.assertFalse(result.success)
        self.assertIn("no forge version", result.error.lower())

    @patch.object(ForgeDownloader, "_http_get")
    @patch.object(ForgeDownloader, "_http_get_json")
    def test_download_installer_invalid_jar(self, mock_json, mock_get):
        xml = """<metadata><versioning><versions>
<version>1.20.4-49.0.2</version>
</versions></versioning></metadata>"""
        mock_get.side_effect = [xml.encode("utf-8"), b"NOT A JAR"]
        result = self.dl.download_installer("1.20.4")
        self.assertFalse(result.success)
        self.assertIn("pk header", result.error.lower())

    def test_download_installer_caches(self):
        """When forge_version is specified, cache should work."""
        # Create a non-mocked test that verifies cache behavior
        # Manually populate cache first
        jar_data = _fake_jar_data(8192)
        self.cache.put("forge:1.20.4:49.0.2", jar_data, version="1.20.4-49.0.2")
        # Now the download should be served from cache without any HTTP calls
        result = self.dl.download_installer("1.20.4", forge_version="49.0.2")
        self.assertTrue(result.success)
        self.assertTrue(result.cached)
# ===================================================================
#  Test: DownloadResult
# ===================================================================


class TestDownloadResult(unittest.TestCase):
    def test_default_values(self):
        r = DownloadResult()
        self.assertFalse(r.success)
        self.assertEqual(r.url, "")
        self.assertEqual(r.file_size, 0)
        self.assertEqual(r.hash_sha256, "")

    def test_to_dict_serializes_all_fields(self):
        r = DownloadResult(
            success=True, url="https://example.com/test.jar",
            local_path="/tmp/test.jar", file_size=1024,
            hash_sha256="a" * 64, hash_sha1="b" * 40,
            version="1.0", cached=True,
            message="OK", error="",
        )
        d = r.to_dict()
        self.assertEqual(d["success"], True)
        self.assertEqual(d["url"], "https://example.com/test.jar")
        self.assertEqual(d["file_size"], 1024)

# ===================================================================
#  Test: Cache Integration
# ===================================================================


class TestCacheIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = _make_temp_dir()
        self.cache = DownloadCache(os.path.join(self._tmp_dir, "cache"))
        self.mojang = MojangDownloader(cache=self.cache)
        self.fabric = FabricDownloader(cache=self.cache)
        self.forge = ForgeDownloader(cache=self.cache)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_mojang_cache_key_is_consistent(self):
        self.assertEqual(self.mojang._cache_key("vanilla", "1.20.4"), "vanilla:1.20.4")

    def test_fabric_cache_key_is_consistent(self):
        self.assertEqual(self.fabric._cache_key("fabric", "1.20.4", "0.15.11"), "fabric:1.20.4:0.15.11")

    def test_forge_cache_key_is_consistent(self):
        self.assertEqual(self.forge._cache_key("forge", "1.20.4", "49.0.2"), "forge:1.20.4:49.0.2")

    def test_cache_isolation_between_downloaders(self):
        m_key = self.mojang._cache_key("vanilla", "1.20.4")
        f_key = self.fabric._cache_key("fabric", "1.20.4", "0.15.11")
        self.assertNotEqual(m_key, f_key)

    def test_cache_works_without_cache(self):
        self.assertIsNone(MojangDownloader(cache=None).cache)
        self.assertIsNone(FabricDownloader(cache=None).cache)
        self.assertIsNone(ForgeDownloader(cache=None).cache)

    def test_can_set_cache_after_construction(self):
        dl = MojangDownloader()
        self.assertIsNone(dl.cache)
        dl.cache = self.cache
        self.assertIsNotNone(dl.cache)

# ===================================================================
#  Test: Error Handling
# ===================================================================


class TestErrorHandling(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = _make_temp_dir()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch.object(MojangDownloader, "_http_get_json")
    def test_mojang_manifest_fetch_failure(self, mock_json):
        mock_json.side_effect = DownloadError("Connection refused")
        with self.assertRaises(DownloadError):
            MojangDownloader().fetch_manifest()

    def test_download_error_is_exception(self):
        with self.assertRaises(DownloadError):
            raise DownloadError("test")

    def test_integrity_check_error_is_exception(self):
        with self.assertRaises(IntegrityCheckError):
            raise IntegrityCheckError("hash mismatch")

    @patch.object(MojangDownloader, "_http_get_json")
    @patch.object(MojangDownloader, "_http_get")
    def test_network_failure_during_download(self, mock_get, mock_json):
        sample_manifest = {
            "latest": {"release": "1.20.4"},
            "versions": [{"id": "1.20.4", "type": "release",
                         "url": "https://ex.com/v1.json", "time": "", "sha1": ""}],
        }
        sample_version = {
            "id": "1.20.4",
            "downloads": {"server": {"url": "https://ex.com/dl", "sha1": "aaa", "size": 100}},
        }
        mock_json.side_effect = [sample_manifest, sample_version]
        mock_get.side_effect = DownloadError("Connection reset")
        result = MojangDownloader().download_server("1.20.4")
        self.assertFalse(result.success)
        self.assertIn("connection", result.error.lower())

# ===================================================================
#  Test: Edge Cases
# ===================================================================


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = _make_temp_dir()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_cache_with_empty_data(self):
        cache = DownloadCache(os.path.join(self._tmp_dir, "empty-cache"))
        cache.put("empty", b"")
        self.assertEqual(cache.get("empty"), b"")

    def test_integrity_checker_empty_data(self):
        IntegrityChecker.assert_integrity(b"")
        self.assertEqual(len(IntegrityChecker.sha256(b"")), 64)

    def test_cache_ttl_zero_immediate_expiry(self):
        cache = DownloadCache(os.path.join(self._tmp_dir, "zero-ttl"), ttl_seconds=0)
        cache.put("instant-expire", b"data")
        import time; time.sleep(0.01)
        self.assertIsNone(cache.get("instant-expire"))

    def test_multiple_cache_entries(self):
        cache = DownloadCache(os.path.join(self._tmp_dir, "multi-cache"))
        for i in range(100):
            cache.put(f"key-{i}", os.urandom(100))
        self.assertEqual(cache.stats()["total_entries"], 100)

    def test_download_result_json_serialization(self):
        r = DownloadResult(success=True, version="1.20.4", file_size=1234)
        d = r.to_dict()
        parsed = json.loads(json.dumps(d))
        self.assertEqual(parsed["version"], "1.20.4")

    def test_version_manifest_entry_dataclass(self):
        entry = VersionManifestEntry(
            id="1.20.4", release_type="release",
            url="https://example.com/1.20.4.json",
            time="2024-01-01T00:00:00Z", sha1="abc123",
        )
        self.assertEqual(entry.id, "1.20.4")
        self.assertEqual(entry.sha1, "abc123")

    def test_verify_disabled(self):
        dl = MojangDownloader()
        with patch.object(dl, "_http_get_json") as mock_json:
            with patch.object(dl, "_http_get") as mock_get:
                mock_json.side_effect = [
                    {"latest": {"release": "1.20.4"},
                     "versions": [{"id": "1.20.4", "type": "release",
                                  "url": "https://ex.com/v1.json", "time": "", "sha1": ""}]},
                    {"id": "1.20.4",
                     "downloads": {"server": {"url": "https://ex.com/dl", "sha1": "abc", "size": 100}}},
                ]
                mock_get.return_value = b"CORRUPTED DATA"
                result = dl.download_server("1.20.4", verify=False)
                self.assertTrue(result.success)

# ===================================================================
#  Entry point
# ===================================================================

if __name__ == "__main__":
    unittest.main()
