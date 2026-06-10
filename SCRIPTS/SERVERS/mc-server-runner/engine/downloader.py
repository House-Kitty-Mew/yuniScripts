"""
downloader.py -- Minecraft Server File Acquisition Module

Centralized download and verification for Minecraft server JARs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mc-server-runner.downloader")

# ===================================================================
#  Data Classes
# ===================================================================


@dataclass
class DownloadResult:
    """Result of a single file download operation."""
    success: bool = False
    url: str = ""
    local_path: str = ""
    file_size: int = 0
    hash_sha256: str = ""
    hash_sha1: str = ""
    version: str = ""
    cached: bool = False
    message: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "url": self.url,
            "local_path": self.local_path,
            "file_size": self.file_size,
            "hash_sha256": self.hash_sha256,
            "hash_sha1": self.hash_sha1,
            "version": self.version,
            "cached": self.cached,
            "message": self.message,
            "error": self.error,
        }


@dataclass
class VersionManifestEntry:
    """A single entry in the Mojang version manifest."""
    id: str
    release_type: str
    url: str
    time: str
    sha1: str = ""

# ===================================================================
#  IntegrityChecker
# ===================================================================


class IntegrityCheckError(Exception):
    """Raised when a file integrity check fails."""
    pass


class IntegrityChecker:
    """File integrity verification with SHA-256, SHA-1, and size."""

    @staticmethod
    def sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def sha1(data: bytes) -> str:
        return hashlib.sha1(data).hexdigest()

    @staticmethod
    def sha256_file(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def sha1_file(path: str) -> str:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def verify_sha256(data: bytes, expected: str) -> bool:
        return IntegrityChecker.sha256(data).lower() == expected.lower()

    @staticmethod
    def verify_sha1(data: bytes, expected: str) -> bool:
        return IntegrityChecker.sha1(data).lower() == expected.lower()

    @staticmethod
    def verify_size(data: bytes, expected_size: int) -> bool:
        return len(data) == expected_size

    @staticmethod
    def assert_integrity(
        data: bytes,
        expected_sha256: Optional[str] = None,
        expected_sha1: Optional[str] = None,
        expected_size: Optional[int] = None,
    ) -> None:
        if expected_size is not None and len(data) != expected_size:
            raise IntegrityCheckError(
                f"Size mismatch: expected {expected_size}, got {len(data)}"
            )
        if expected_sha256 is not None:
            actual = IntegrityChecker.sha256(data)
            if actual.lower() != expected_sha256.lower():
                raise IntegrityCheckError(
                    f"SHA-256 mismatch: expected {expected_sha256[:16]}..., "
                    f"got {actual[:16]}..."
                )
        if expected_sha1 is not None:
            actual = IntegrityChecker.sha1(data)
            if actual.lower() != expected_sha1.lower():
                raise IntegrityCheckError(
                    f"SHA-1 mismatch: expected {expected_sha1[:16]}..., "
                    f"got {actual[:16]}..."
                )

    @staticmethod
    def assert_file_integrity(
        path: str,
        expected_sha256: Optional[str] = None,
        expected_sha1: Optional[str] = None,
        expected_size: Optional[int] = None,
    ) -> None:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        stat = os.stat(path)
        if expected_size is not None and stat.st_size != expected_size:
            raise IntegrityCheckError(
                f"Size mismatch for {path}: expected {expected_size}, got {stat.st_size}"
            )
        if expected_sha256 is not None:
            actual = IntegrityChecker.sha256_file(path)
            if actual.lower() != expected_sha256.lower():
                raise IntegrityCheckError(
                    f"SHA-256 mismatch for {path}: expected "
                    f"{expected_sha256[:16]}..., got {actual[:16]}..."
                )
        if expected_sha1 is not None:
            actual = IntegrityChecker.sha1_file(path)
            if actual.lower() != expected_sha1.lower():
                raise IntegrityCheckError(
                    f"SHA-1 mismatch for {path}: expected "
                    f"{expected_sha1[:16]}..., got {actual[:16]}..."
                )
# ===================================================================
#  DownloadCache
# ===================================================================


class DownloadCache:
    """Filesystem-based cache for downloaded MC server files.

    Stores files in a local directory with JSON index for metadata.
    Supports TTL expiry, integrity verification on cache hit, and
    atomic index writes.
    """

    DEFAULT_TTL_SECONDS = 86400 * 7

    def __init__(self, cache_dir: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._cache_dir = os.path.abspath(cache_dir)
        self._index_path = os.path.join(self._cache_dir, "index.json")
        self._ttl = ttl_seconds
        self._index: Dict[str, Dict[str, Any]] = {}
        os.makedirs(self._cache_dir, exist_ok=True)
        self._load_index()

    @property
    def cache_dir(self) -> str:
        return self._cache_dir

    @property
    def index_path(self) -> str:
        return self._index_path

    def get(self, key: str) -> Optional[bytes]:
        entry = self._index.get(key)
        if entry is None:
            return None
        if time.time() - entry.get("timestamp", 0) > self._ttl:
            logger.debug("Cache TTL expired for key=%s", key)
            self._evict(key)
            return None
        entry_path = entry.get("path")
        if not entry_path or not os.path.isfile(entry_path):
            logger.debug("Cache file missing for key=%s", key)
            self._evict(key)
            return None
        expected_hash = entry.get("sha256")
        if expected_hash:
            try:
                IntegrityChecker.assert_file_integrity(entry_path, expected_sha256=expected_hash)
            except (IntegrityCheckError, FileNotFoundError):
                logger.warning("Cache integrity fail for key=%s - evicting", key)
                self._evict(key)
                return None
        with open(entry_path, "rb") as f:
            data = f.read()
        logger.debug("Cache HIT: key=%s (%d bytes)", key, len(data))
        return data

    def put(self, key: str, data: bytes, **metadata: Any) -> str:
        digest = IntegrityChecker.sha256(data)
        filename = f"{digest}.jar"
        filepath = os.path.join(self._cache_dir, filename)
        if not os.path.isfile(filepath):
            tmp_path = filepath + ".tmp"
            with open(tmp_path, "wb") as f:
                f.write(data)
            os.replace(tmp_path, filepath)
        self._index[key] = {
            "path": filepath,
            "sha256": digest,
            "timestamp": time.time(),
            "file_size": len(data),
            **metadata,
        }
        self._save_index()
        return filepath

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def evict(self, key: str) -> bool:
        return self._evict(key)

    def clear(self, older_than_seconds: Optional[int] = None) -> int:
        if older_than_seconds is None:
            count = len(self._index)
            self._index.clear()
            self._save_index()
            return count
        cutoff = time.time() - older_than_seconds
        to_evict = [k for k, v in self._index.items()
                    if v.get("timestamp", 0) < cutoff]
        for k in to_evict:
            self._evict(k)
        return len(to_evict)

    def stats(self) -> Dict[str, Any]:
        total_size = 0
        valid = 0
        expired = 0
        now = time.time()
        for entry in self._index.values():
            if now - entry.get("timestamp", 0) > self._ttl:
                expired += 1
            else:
                valid += 1
            total_size += entry.get("file_size", 0)
        return {
            "cache_dir": self._cache_dir,
            "total_entries": len(self._index),
            "valid_entries": valid,
            "expired_entries": expired,
            "total_size_bytes": total_size,
            "ttl_seconds": self._ttl,
        }

    def get_entry(self, key: str) -> Optional[Dict[str, Any]]:
        return self._index.get(key)

    def _load_index(self) -> None:
        if os.path.isfile(self._index_path):
            try:
                with open(self._index_path, "r") as f:
                    self._index = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load cache index: %s", exc)
                self._index = {}
        else:
            self._index = {}

    def _save_index(self) -> None:
        tmp_path = self._index_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(self._index, f, indent=2)
        os.replace(tmp_path, self._index_path)

    def _evict(self, key: str) -> bool:
        if key in self._index:
            del self._index[key]
            self._save_index()
            return True
        return False
# ===================================================================
#  BaseDownloader
# ===================================================================


class DownloadError(Exception):
    pass


class BaseDownloader:
    """Base class with HTTP helpers, cache integration, and file placement."""

    USER_AGENT = "YuniScripts-MCServerRunner/1.0.0"

    def __init__(self, cache: Optional[DownloadCache] = None):
        self._cache = cache
        self._session = None

    @property
    def cache(self) -> Optional[DownloadCache]:
        return self._cache

    @cache.setter
    def cache(self, value: Optional[DownloadCache]) -> None:
        self._cache = value

    def _get_session(self):
        if self._session is None:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            self._session = requests.Session()
            retries = Retry(total=3, backoff_factor=0.5,
                           status_forcelist=[429, 500, 502, 503])
            self._session.mount("https://", HTTPAdapter(max_retries=retries))
            self._session.headers.update({"User-Agent": self.USER_AGENT})
        return self._session

    def _http_get(self, url: str, timeout: int = 30) -> bytes:
        import requests as _r
        session = self._get_session()
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.content
        except _r.exceptions.RequestException as exc:
            raise DownloadError(f"HTTP GET {url} failed: {exc}") from exc

    def _http_get_json(self, url: str, timeout: int = 30) -> Any:
        data = self._http_get(url, timeout=timeout)
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            raise DownloadError(f"Invalid JSON from {url}: {exc}") from exc

    def _cache_key(self, *parts: str) -> str:
        return ":".join(parts)

    def _check_cache(self, key: str) -> Optional[bytes]:
        if self._cache is None:
            return None
        return self._cache.get(key)

    def _write_cache(self, key: str, data: bytes, **metadata: Any) -> str:
        if self._cache is not None:
            return self._cache.put(key, data, **metadata)
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jar", prefix="mc_dl_")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(data)
        return path

    @staticmethod
    def place_file(data: bytes, dest_dir: str, filename: str, overwrite: bool = True) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, filename)
        if os.path.exists(dest_path) and not overwrite:
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(dest_dir, f"{base}_{counter}{ext}")
                counter += 1
        with open(dest_path, "wb") as f:
            f.write(data)
        logger.info("Placed file: %s (%d bytes)", dest_path, len(data))
        return os.path.abspath(dest_path)

    @staticmethod
    def place_in_vfs(vfs, data: bytes, vfs_path: str, filename: str = "server.jar") -> bool:
        vfs.write(vfs_path, data, atomic=True,
                  content_type="application/java-archive",
                  original_name=filename)
        logger.info("Placed file in VFS: %s (%d bytes)", vfs_path, len(data))
        return True

    @staticmethod
    def _make_result(
        success: bool, url: str, data: Optional[bytes] = None,
        local_path: str = "", version: str = "",
        cached: bool = False, message: str = "",
        error: str = "", hash_sha1: str = "",
    ) -> DownloadResult:
        return DownloadResult(
            success=success, url=url,
            local_path=local_path,
            file_size=len(data) if data else 0,
            hash_sha256=IntegrityChecker.sha256(data) if data else "",
            hash_sha1=hash_sha1 or (IntegrityChecker.sha1(data) if data else ""),
            version=version, cached=cached,
            message=message, error=error,
        )
# ===================================================================
#  MojangDownloader
# ===================================================================

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest.json"


class MojangDownloader(BaseDownloader):
    """Downloads vanilla Minecraft server JARs from Mojang manifest."""

    def __init__(self, cache: Optional[DownloadCache] = None):
        super().__init__(cache=cache)

    def fetch_manifest(self) -> Dict[str, Any]:
        return self._http_get_json(MANIFEST_URL)

    def list_versions(self, release_type: str = "release") -> List[VersionManifestEntry]:
        manifest = self.fetch_manifest()
        entries = []
        for v in manifest.get("versions", []):
            if release_type is not None and v.get("type") != release_type:
                continue
            entries.append(VersionManifestEntry(
                id=v["id"], release_type=v.get("type", ""),
                url=v.get("url", ""), time=v.get("time", ""),
                sha1=v.get("sha1", ""),
            ))
        return entries

    def resolve_version(self, mc_version: str) -> Optional[VersionManifestEntry]:
        manifest = self.fetch_manifest()
        if mc_version.lower() == "latest":
            latest_id = manifest.get("latest", {}).get("release", "")
            if not latest_id:
                return None
            mc_version = latest_id
        for v in manifest.get("versions", []):
            if v["id"] == mc_version:
                return VersionManifestEntry(
                    id=v["id"], release_type=v.get("type", ""),
                    url=v.get("url", ""), time=v.get("time", ""),
                    sha1=v.get("sha1", ""),
                )
        return None

    def fetch_version_json(self, version_entry: VersionManifestEntry) -> Dict[str, Any]:
        return self._http_get_json(version_entry.url)

    def download_server(self, mc_version: str, dest_dir: Optional[str] = None,
                       filename: str = "server.jar", verify: bool = True) -> DownloadResult:
        url = MANIFEST_URL
        cache_key = self._cache_key("vanilla", mc_version)
        if self._cache:
            cached = self._check_cache(cache_key)
            if cached is not None:
                local_path = ""
                if dest_dir:
                    local_path = self.place_file(cached, dest_dir, filename)
                else:
                    entry = self._cache.get_entry(cache_key)
                    local_path = entry["path"] if entry else ""
                return self._make_result(
                    success=True, url=url, data=cached,
                    local_path=local_path, version=mc_version,
                    cached=True, message="Served from cache",
                )
        version_entry = self.resolve_version(mc_version)
        if version_entry is None:
            return self._make_result(
                success=False, url=url,
                error=f"MC version {mc_version!r} not found in Mojang manifest",
                message=f"Version {mc_version} not available",
            )
        try:
            version_json = self.fetch_version_json(version_entry)
        except DownloadError as exc:
            return self._make_result(
                success=False, url=version_entry.url, error=str(exc),
                message=f"Failed to fetch version JSON for {mc_version}",
            )
        downloads = version_json.get("downloads", {})
        server_info = downloads.get("server")
        if server_info is None:
            return self._make_result(
                success=False, url=version_entry.url,
                error=f"No server download for MC version {mc_version}",
                message="Server JAR not available for this version",
                version=mc_version,
            )
        dl_url = server_info.get("url", "")
        expected_sha1 = server_info.get("sha1", "")
        expected_size = server_info.get("size")
        try:
            jar_data = self._http_get(dl_url, timeout=120)
        except DownloadError as exc:
            return self._make_result(
                success=False, url=dl_url, error=str(exc),
                message=f"Download failed for vanilla {mc_version}",
                version=mc_version,
            )
        if verify:
            try:
                IntegrityChecker.assert_integrity(
                    jar_data,
                    expected_sha1=expected_sha1,
                    expected_size=expected_size,
                )
            except IntegrityCheckError as exc:
                return self._make_result(
                    success=False, url=dl_url, data=jar_data,
                    error=str(exc),
                    message=f"Integrity check failed for vanilla {mc_version}",
                    version=mc_version, hash_sha1=expected_sha1,
                )
        local_path = self._write_cache(
            cache_key, jar_data,
            version=mc_version, url=dl_url, server_type="vanilla",
        )
        if dest_dir:
            local_path = self.place_file(jar_data, dest_dir, filename)
        return self._make_result(
            success=True, url=dl_url, data=jar_data,
            local_path=local_path, version=mc_version,
            message=f"Downloaded vanilla server.jar for {mc_version} ({len(jar_data)} bytes)",
            hash_sha1=expected_sha1,
        )
# ===================================================================
#  FabricDownloader
# ===================================================================

FABRIC_META_API = "https://meta.fabricmc.net/v2"
FABRIC_MAVEN = "https://maven.fabricmc.net"


class FabricDownloader(BaseDownloader):
    """Downloads Fabric server launcher JARs."""

    def __init__(self, cache: Optional[DownloadCache] = None):
        super().__init__(cache=cache)

    def list_loader_versions(self, mc_version: str) -> List[Dict[str, Any]]:
        url = f"{FABRIC_META_API}/versions/loader/{mc_version}"
        return self._http_get_json(url)

    def resolve_loader(self, mc_version: str) -> Optional[Dict[str, Any]]:
        entries = self.list_loader_versions(mc_version)
        if not entries:
            return None
        return entries[0]

    def build_server_launch_url(self, mc_version: str, loader_version: str) -> str:
        return (
            f"{FABRIC_MAVEN}/net/fabricmc/"
            f"fabric-server-launch/{loader_version}/"
            f"fabric-server-launch-{loader_version}.jar"
        )

    def download_loader(self, mc_version: str, loader_version: Optional[str] = None,
                        dest_dir: Optional[str] = None, filename: str = "server.jar",
                        verify: bool = True) -> DownloadResult:
        url = FABRIC_META_API
        if loader_version is None:
            entry = self.resolve_loader(mc_version)
            if entry is None:
                return self._make_result(
                    success=False, url=url,
                    error=f"No Fabric loader found for MC {mc_version}",
                    message=f"Fabric loader unavailable for {mc_version}",
                )
            loader_version = entry["loader"]["version"]
        cache_key = self._cache_key("fabric", mc_version, loader_version)
        if self._cache:
            cached = self._check_cache(cache_key)
            if cached is not None:
                local_path = ""
                if dest_dir:
                    local_path = self.place_file(cached, dest_dir, filename)
                else:
                    entry_meta = self._cache.get_entry(cache_key)
                    local_path = entry_meta["path"] if entry_meta else ""
                return self._make_result(
                    success=True, url=url, data=cached,
                    local_path=local_path,
                    version=f"{mc_version}+loader.{loader_version}",
                    cached=True, message="Served from cache",
                )
        dl_url = self.build_server_launch_url(mc_version, loader_version)
        try:
            jar_data = self._http_get(dl_url, timeout=120)
        except DownloadError as exc:
            return self._make_result(
                success=False, url=dl_url, error=str(exc),
                message=f"Fabric download failed: {mc_version} loader {loader_version}",
                version=f"{mc_version}+loader.{loader_version}",
            )
        if verify:
            if len(jar_data) < 4 or jar_data[:2] != b"PK":
                return self._make_result(
                    success=False, url=dl_url, data=jar_data,
                    error="Downloaded file is not a valid JAR (no PK header)",
                    message="Fabric JAR integrity check failed",
                    version=f"{mc_version}+loader.{loader_version}",
                )
        local_path = self._write_cache(
            cache_key, jar_data,
            version=f"{mc_version}+loader.{loader_version}",
            url=dl_url, loader_version=loader_version,
            server_type="fabric",
        )
        if dest_dir:
            local_path = self.place_file(jar_data, dest_dir, filename)
        return self._make_result(
            success=True, url=dl_url, data=jar_data,
            local_path=local_path,
            version=f"{mc_version}+loader.{loader_version}",
            message=f"Downloaded Fabric launcher for {mc_version} (loader {loader_version}, {len(jar_data)} bytes)",
        )

# ===================================================================
#  ForgeDownloader
# ===================================================================

FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"


class ForgeDownloader(BaseDownloader):
    """Downloads Forge installer JARs from Forge Maven."""

    def __init__(self, cache: Optional[DownloadCache] = None):
        super().__init__(cache=cache)

    def list_forge_versions(self, mc_version: str) -> List[str]:
        metadata_url = f"{FORGE_MAVEN}/maven-metadata.xml"
        xml_text = ""
        try:
            xml_text = self._http_get(metadata_url, timeout=30).decode("utf-8")
        except DownloadError:
            pass
        versions = self._parse_maven_versions(xml_text, mc_version)
        if versions:
            return versions
        fallback_url = ("https://files.minecraftforge.net/net/minecraftforge/"
                        "forge/promotions_slim.json")
        try:
            promotions = self._http_get_json(fallback_url, timeout=15)
        except DownloadError:
            return []
        result = []
        for key in promotions:
            if key.startswith(f"{mc_version}-"):
                result.append(key)
        result.sort(reverse=True)
        return result

    @staticmethod
    def _parse_maven_versions(xml_text: str, mc_version: str) -> List[str]:
        if not xml_text.strip():
            return []
        versions = []
        pattern = re.compile(rf"<version>{re.escape(mc_version)}-([^<]+)</version>")
        for match in pattern.finditer(xml_text):
            versions.append(match.group(1))
        alt_pattern = re.compile(r"<version>([^<]+)</version>")
        for match in alt_pattern.finditer(xml_text):
            ver = match.group(1)
            if ver.startswith(f"{mc_version}-"):
                forge_ver = ver[len(mc_version) + 1:]
                if forge_ver not in versions:
                    versions.append(forge_ver)
        return sorted(set(versions), reverse=True)

    def resolve_latest(self, mc_version: str) -> Optional[str]:
        versions = self.list_forge_versions(mc_version)
        if not versions:
            return None
        return versions[0]

    def build_installer_url(self, mc_version: str, forge_version: str) -> str:
        return (
            f"{FORGE_MAVEN}/{mc_version}-{forge_version}/"
            f"forge-{mc_version}-{forge_version}-installer.jar"
        )

    def build_server_url(self, mc_version: str, forge_version: str) -> str:
        return (
            f"{FORGE_MAVEN}/{mc_version}-{forge_version}/"
            f"forge-{mc_version}-{forge_version}-server.jar"
        )

    def download_installer(self, mc_version: str, forge_version: Optional[str] = None,
                           dest_dir: Optional[str] = None,
                           filename: str = "forge-installer.jar",
                           verify: bool = True) -> DownloadResult:
        url = FORGE_MAVEN
        if forge_version is None:
            forge_version = self.resolve_latest(mc_version)
            if forge_version is None:
                return self._make_result(
                    success=False, url=url,
                    error=f"No Forge version found for MC {mc_version}",
                    message=f"Forge unavailable for {mc_version}",
                )
        cache_key = self._cache_key("forge", mc_version, forge_version)
        if self._cache:
            cached = self._check_cache(cache_key)
            if cached is not None:
                local_path = ""
                if dest_dir:
                    local_path = self.place_file(cached, dest_dir, filename)
                else:
                    entry_meta = self._cache.get_entry(cache_key)
                    local_path = entry_meta["path"] if entry_meta else ""
                return self._make_result(
                    success=True, url=url, data=cached,
                    local_path=local_path,
                    version=f"{mc_version}-{forge_version}",
                    cached=True, message="Served from cache",
                )
        dl_url = self.build_installer_url(mc_version, forge_version)
        try:
            jar_data = self._http_get(dl_url, timeout=120)
        except DownloadError as exc:
            return self._make_result(
                success=False, url=dl_url, error=str(exc),
                message=f"Forge download failed for {mc_version}-{forge_version}",
                version=f"{mc_version}-{forge_version}",
            )
        if verify:
            if len(jar_data) < 4 or jar_data[:2] != b"PK":
                return self._make_result(
                    success=False, url=dl_url, data=jar_data,
                    error="Downloaded file is not a valid JAR (no PK header)",
                    message="Forge installer integrity check failed",
                    version=f"{mc_version}-{forge_version}",
                )
        local_path = self._write_cache(
            cache_key, jar_data,
            version=f"{mc_version}-{forge_version}",
            url=dl_url, forge_version=forge_version,
            server_type="forge",
        )
        if dest_dir:
            local_path = self.place_file(jar_data, dest_dir, filename)
        return self._make_result(
            success=True, url=dl_url, data=jar_data,
            local_path=local_path,
            version=f"{mc_version}-{forge_version}",
            message=f"Downloaded Forge installer for {mc_version}-{forge_version} ({len(jar_data)} bytes)",
        )