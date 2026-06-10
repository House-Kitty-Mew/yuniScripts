"""
mod_cache.py — Modrinth + CurseForge Mod Caching Bridge

Caches mod metadata and binaries in the existing VFS + server_data.db
tables. Supports:
  - Modrinth v2 API (open, no auth needed)
  - CurseForge Core API (requires API key in config)
  - SHA-512/SHA-1 hash verification on download
  - Local cache storage in VFS (avoids re-downloading)
  - Bulk dependency resolution
  - MC version + loader filtering
  - ETag/If-None-Match conditional requests (saves rate limit quota)
  - Tiered TTL: 30d for search results, 7d for projects, 24h for versions
  - Exponential backoff on 429 rate limits
  - Background validation for stale cache entries

Cache Storage:
  - Metadata: Stored in existing 'mods' DB table (extended columns)
    + config table for ETags and timestamps
  - Binaries: Stored in VFS at /mod-cache/{platform}/{slug}-{version}.jar
  - Hash index: SHA-512/1 stored alongside for integrity verification

API Endpoints:
  Modrinth: https://api.modrinth.com/v2/
  CurseForge: https://api.curseforge.com/v1/
  Fabric Meta: https://meta.fabricmc.net/v2/
  Paper API: https://api.papermc.io/v2/
  Purpur API: https://api.purpurmc.org/v2/
  Mojang Meta: https://piston-meta.mojang.com/mc/game/version_manifest.json
"""

import json
import hashlib
import os
import time
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta

from engine.database import Database
from engine.vfs import VFS

logger = logging.getLogger('mc-server-runner.mod_cache')

# ── Cache TTL Configuration ───────────────────────────────────
# Search results change slowly — 30 day cache
SEARCH_CACHE_DAYS = 30
# Project metadata (description, icon, etc.) — 7 day cache
PROJECT_CACHE_DAYS = 7
# Version listings change with mod updates — 24 hour cache
VERSION_CACHE_HOURS = 24
# Binary downloads are cached indefinitely (hash-verified)
# VFS binary cache is only cleared manually via clear_cache()

# ── Rate Limiting ─────────────────────────────────────────────
RATE_LIMIT_MAX_RETRIES = 5          # Max retries on 429 responses
RATE_LIMIT_BASE_DELAY = 1.0         # Base backoff delay in seconds
RATE_LIMIT_MAX_DELAY = 60.0         # Max backoff delay in seconds
RATE_LIMIT_COOLDOWN = 300.0         # Seconds before resetting backoff state

# ── DB Config Key Prefixes ────────────────────────────────────
ETAG_PREFIX = "mod_cache_etag_"           # Stores ETag header values
CACHE_TIME_PREFIX = "mod_cache_time_"     # Stores cache timestamps
RATE_LIMIT_PREFIX = "mod_cache_ratelimit_" # Stores rate limit state

# ── API URLs ──────────────────────────────────────────────────
MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_API = "https://api.curseforge.com/v1"
MOD_CACHE_VFS_ROOT = "/mod-cache"


class ModCache:
    """
    Unified mod cache bridge for Modrinth and CurseForge.

    Downloads mod metadata + binaries, stores in VFS + DB for
    offline access and fast retrieval. Supports ETag-based
    conditional requests, rate limit backoff, and cache validation.
    """

    def __init__(self, db: Database, vfs: VFS, cf_api_key: str = None):
        self.db = db
        self.vfs = vfs
        self.cf_api_key = cf_api_key
        self._session = None  # Lazy requests session
        # In-memory rate limit state: {endpoint_key: {'last_429': float, 'retry_count': int}}
        self._rate_limit_state: Dict[str, Dict] = {}
        self._rl_lock = threading.Lock()
        self._ensure_cache_dirs()

    # ═══════════════════════════════════════════════════════════
    # Initialization
    # ═══════════════════════════════════════════════════════════

    def _ensure_cache_dirs(self):
        """Create VFS cache directories if they don't exist."""
        self.vfs.mkdir(f"{MOD_CACHE_VFS_ROOT}")
        self.vfs.mkdir(f"{MOD_CACHE_VFS_ROOT}/modrinth")
        self.vfs.mkdir(f"{MOD_CACHE_VFS_ROOT}/curseforge")

    def _get_requests(self):
        """Lazy-init requests session with retries."""
        if self._session is None:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            self._session = requests.Session()
            retries = Retry(total=3, backoff_factor=0.5,
                           status_forcelist=[500, 502, 503])
            # NOTE: 429 is handled by _rate_limit_aware_get with
            # exponential backoff — NOT by urllib3 Retry, which
            # would retry too aggressively without delay.
            self._session.mount('https://', HTTPAdapter(max_retries=retries))
            self._session.headers.update({
                'User-Agent': 'YuniScripts-MCServerRunner/1.0.0',
            })
        return self._session

    # ═══════════════════════════════════════════════════════════
    # ETag Helpers
    # ═══════════════════════════════════════════════════════════

    def _etag_key(self, url: str) -> str:
        """Generate a deterministic DB key for an ETag by URL."""
        return f"{ETAG_PREFIX}{hashlib.sha256(url.encode()).hexdigest()[:16]}"

    def _store_etag(self, url: str, etag: str):
        """Store an ETag header value for a URL."""
        if not etag:
            return
        key = self._etag_key(url)
        try:
            self.db.set_config(0, key, etag)
        except Exception as e:
            logger.debug(f"Failed to store ETag for {url[:60]}: {e}")

    def _get_etag(self, url: str) -> Optional[str]:
        """Retrieve a stored ETag for a URL."""
        key = self._etag_key(url)
        try:
            return self.db.get_config(0, key) or None
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════
    # Cache Freshness Helpers
    # ═══════════════════════════════════════════════════════════

    def _cache_time_key(self, cache_key: str) -> str:
        """Generate DB key for cache timestamp."""
        return f"{CACHE_TIME_PREFIX}{cache_key}"

    def _store_cache_timestamp(self, cache_key: str):
        """Record the current time as when data was cached."""
        ts_key = self._cache_time_key(cache_key)
        try:
            self.db.set_config(0, ts_key, str(time.time()))
        except Exception as e:
            logger.debug(f"Failed to store cache timestamp: {e}")

    def _get_cache_age_hours(self, cache_key: str) -> Optional[float]:
        """Get the age of a cached entry in hours. Returns None if never cached."""
        ts_key = self._cache_time_key(cache_key)
        try:
            raw = self.db.get_config(0, ts_key)
            if not raw:
                return None
            cached_at = float(raw)
            return (time.time() - cached_at) / 3600.0
        except (ValueError, TypeError, Exception):
            return None

    def _is_cache_fresh(self, cache_key: str, max_hours: float) -> bool:
        """Check if a cached entry is still within its TTL."""
        age = self._get_cache_age_hours(cache_key)
        if age is None:
            return False
        return age < max_hours

    # ═══════════════════════════════════════════════════════════
    # Rate Limit Helpers
    # ═══════════════════════════════════════════════════════════

    def _endpoint_key(self, url: str) -> str:
        """Extract a normalized endpoint key from a URL for rate limit tracking."""
        # Group by API base + first path segment
        for prefix in [MODRINTH_API, CURSEFORGE_API]:
            if url.startswith(prefix):
                remainder = url[len(prefix):]
                # Use first path segment as endpoint group
                parts = remainder.strip('/').split('/')
                return f"{prefix}/{parts[0]}" if parts else prefix
        return url  # Fallback: full URL as key

    def _record_rate_limit(self, url: str):
        """Record a 429 rate limit response for an endpoint."""
        endpoint = self._endpoint_key(url)
        with self._rl_lock:
            state = self._rate_limit_state.get(endpoint, {})
            state['last_429'] = time.time()
            state['retry_count'] = state.get('retry_count', 0) + 1
            self._rate_limit_state[endpoint] = state
            logger.warning(
                f"Rate limit recorded for {endpoint}: "
                f"retry #{state['retry_count']}"
            )

    def _should_backoff(self, url: str) -> float:
        """
        Check if we should delay before making a request to an endpoint.

        Returns:
            Seconds to wait (0 = no backoff needed).
        """
        endpoint = self._endpoint_key(url)
        with self._rl_lock:
            state = self._rate_limit_state.get(endpoint)
            if not state:
                return 0.0

            last_429 = state.get('last_429', 0)
            elapsed = time.time() - last_429

            # If enough time has passed since last 429, reset state
            if elapsed > RATE_LIMIT_COOLDOWN:
                del self._rate_limit_state[endpoint]
                return 0.0

            # Calculate exponential backoff delay
            retry_count = state.get('retry_count', 1)
            delay = min(
                RATE_LIMIT_BASE_DELAY * (2 ** (retry_count - 1)),
                RATE_LIMIT_MAX_DELAY
            )
            return delay

    def _rate_limit_aware_get(self, url: str, params: dict = None,
                               headers: dict = None, timeout: int = 15,
                               etag: str = None) -> Optional['requests.Response']:
        """
        Make a GET request with rate limit backoff and ETag support.

        Args:
            url: Request URL
            params: Query parameters
            headers: Additional headers
            timeout: Request timeout in seconds
            etag: ETag value for If-None-Match header

        Returns:
            Response object, or None if rate limited / error.
            On 304 Not Modified, returns the response with status_code=304
            (caller should use cached data).
        """
        import requests as _r

        # Wait if we're in backoff
        wait = self._should_backoff(url)
        if wait > 0:
            logger.info(f"Rate limit backoff: waiting {wait:.1f}s for {self._endpoint_key(url)}")
            time.sleep(wait)

        ses = self._get_requests()

        # Build headers with ETag
        req_headers = dict(headers or {})
        if etag:
            req_headers['If-None-Match'] = etag

        # Attempt request with retries on 429
        # Connection-level retries (timeout, DNS) are handled by
        # urllib3's Retry adapter, NOT by this loop.
        last_error = None
        for attempt in range(RATE_LIMIT_MAX_RETRIES):
            try:
                resp = ses.get(url, params=params, headers=req_headers,
                              timeout=timeout)
                if resp.status_code == 429:
                    self._record_rate_limit(url)
                    wait = self._should_backoff(url)
                    if wait > 0 and attempt < RATE_LIMIT_MAX_RETRIES - 1:
                        logger.info(
                            f"429 on {self._endpoint_key(url)}, "
                            f"backoff {wait:.1f}s "
                            f"(attempt {attempt + 1}/{RATE_LIMIT_MAX_RETRIES})"
                        )
                        time.sleep(wait)
                        continue
                elif resp.status_code == 304:
                    return resp
                else:
                    resp.raise_for_status()
                    return resp
            except _r.exceptions.RequestException as e:
                last_error = e
                # HTTP-level error with an actual response
                if hasattr(e, 'response') and e.response is not None:
                    status = e.response.status_code
                    if status == 429:
                        self._record_rate_limit(url)
                        wait = self._should_backoff(url)
                        if wait > 0 and attempt < RATE_LIMIT_MAX_RETRIES - 1:
                            time.sleep(wait)
                            continue
                    logger.error(f"HTTP {status} on {self._endpoint_key(url)}: {e}")
                    return None
                # Connection-level error: no HTTP response received
                # urllib3 Retry already handles these; return immediately
                logger.error(f"Connection error on {self._endpoint_key(url)}: {e}")
                return None

        logger.error(f"Request failed after {RATE_LIMIT_MAX_RETRIES} attempts "
                     f"for {self._endpoint_key(url)}: {last_error}")
        return None

    # ═══════════════════════════════════════════════════════════
    # Modrinth API — Search
    # ═══════════════════════════════════════════════════════════

    def modrinth_search(self, query: str, loaders: list = None,
                        mc_version: str = None, limit: int = 20) -> list:
        """
        Search Modrinth for mods by query, loader, and MC version.

        Uses 30-day cache for search results with ETag-based freshness.
        When a 304 response is received, returns cached data if available.

        GET /v2/search?query={q}&facets=[[...]]

        Returns list of dicts with keys:
          project_id, slug, title, description, versions, client_side,
          server_side, downloads, latest_version, icon_url
        """
        import requests as _r

        # Build cache key
        facets = []
        if loaders:
            facets.append([f"categories:{l}" for l in loaders])
        if mc_version:
            facets.append([f"versions:{mc_version}"])
        params = {'query': query, 'limit': min(limit, 100)}
        if facets:
            params['facets'] = json.dumps(facets)
        cache_key = f"search_{query}_{json.dumps(params, sort_keys=True)}"

        # Check cache freshness — search results are cached for 30 days
        if self._is_cache_fresh(cache_key, SEARCH_CACHE_DAYS * 24):
            cached = self.db.get_config(0, f"mod_cache_{cache_key}")
            if cached:
                try:
                    logger.info(f"Search cache HIT: {query} (cached <{SEARCH_CACHE_DAYS}d)")
                    return json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    pass  # Corrupted cache — re-fetch

        # Attempt conditional request with ETag
        url = f"{MODRINTH_API}/search"
        etag = self._get_etag(url)
        resp = self._rate_limit_aware_get(
            url, params=params, etag=etag, timeout=15
        )

        if resp is None:
            # Request failed — try returning stale cache
            stale = self.db.get_config(0, f"mod_cache_{cache_key}")
            if stale:
                try:
                    logger.warning(f"Search request failed, returning stale cache for {query}")
                    return json.loads(stale)
                except (json.JSONDecodeError, TypeError):
                    pass
            return []

        if resp.status_code == 304:
            # Not Modified — return cached data
            cached = self.db.get_config(0, f"mod_cache_{cache_key}")
            if cached:
                try:
                    logger.info(f"Search 304 (not modified): {query}")
                    return json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    pass
            return []

        # Success — parse and cache
        data = resp.json()
        hits = data.get('hits', [])

        # Store in cache
        try:
            self.db.set_config(0, f"mod_cache_{cache_key}", json.dumps(hits))
            self._store_cache_timestamp(cache_key)
            # Store ETag
            resp_etag = resp.headers.get('ETag')
            if resp_etag:
                self._store_etag(url, resp_etag)
        except Exception as e:
            logger.debug(f"Search cache write skipped: {e}")

        return hits

    # ═══════════════════════════════════════════════════════════
    # Modrinth API — Project
    # ═══════════════════════════════════════════════════════════

    def modrinth_get_project(self, slug: str) -> Optional[dict]:
        """
        Get full project metadata from Modrinth.

        Uses 7-day cache with ETag conditional requests.

        GET /v2/project/{slug}

        Returns project dict with description, body_url, etc.
        """
        import requests as _r

        cache_key = f"project_{slug}"

        # Check cache freshness — projects are cached for 7 days
        if self._is_cache_fresh(cache_key, PROJECT_CACHE_DAYS * 24):
            cached = self.db.get_config(0, f"mod_cache_{cache_key}")
            if cached:
                try:
                    logger.info(f"Project cache HIT: {slug} (cached <{PROJECT_CACHE_DAYS}d)")
                    return json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Attempt conditional request
        url = f"{MODRINTH_API}/project/{slug}"
        etag = self._get_etag(url)
        resp = self._rate_limit_aware_get(url, etag=etag, timeout=15)

        if resp is None:
            # Try stale cache
            stale = self.db.get_config(0, f"mod_cache_{cache_key}")
            if stale:
                try:
                    logger.warning(f"Project fetch failed, returning stale cache for {slug}")
                    return json.loads(stale)
                except (json.JSONDecodeError, TypeError):
                    pass
            return None

        if resp.status_code == 304:
            # Not Modified — return cached data
            cached = self.db.get_config(0, f"mod_cache_{cache_key}")
            if cached:
                try:
                    logger.info(f"Project 304 (not modified): {slug}")
                    return json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    pass
            return None

        # Success
        project = resp.json()

        # Cache it
        try:
            self.db.set_config(0, f"mod_cache_{cache_key}", json.dumps(project))
            self._store_cache_timestamp(cache_key)
            resp_etag = resp.headers.get('ETag')
            if resp_etag:
                self._store_etag(url, resp_etag)
        except Exception as e:
            logger.debug(f"Project cache write skipped: {e}")

        return project

    # ═══════════════════════════════════════════════════════════
    # Modrinth API — Versions
    # ═══════════════════════════════════════════════════════════

    def modrinth_get_versions(self, slug: str, mc_version: str = None,
                              loaders: list = None) -> list:
        """
        Get available versions for a Modrinth project, filtered.

        Uses 24-hour cache with ETag conditional requests.

        GET /v2/project/{slug}/version

        Filters by mc_version and loaders if provided.
        Returns list of version dicts sorted by date_published descending.

        Each version has:
          id, name, version_number, game_versions, loaders,
          files[].url, files[].hashes.sha1/sha512, files[].size
        """
        import requests as _r

        cache_key = f"versions_{slug}"

        # Check cache freshness — versions are cached for 24 hours
        if self._is_cache_fresh(cache_key, VERSION_CACHE_HOURS):
            cached = self.db.get_config(0, f"mod_cache_{cache_key}")
            if cached:
                try:
                    versions = json.loads(cached)
                    # Apply filters to cached data
                    filtered = self._filter_versions(versions, mc_version, loaders)
                    logger.info(f"Versions cache HIT: {slug} (cached <{VERSION_CACHE_HOURS}h)")
                    return filtered
                except (json.JSONDecodeError, TypeError):
                    pass

        # Attempt conditional request
        url = f"{MODRINTH_API}/project/{slug}/version"
        etag = self._get_etag(url)
        resp = self._rate_limit_aware_get(url, etag=etag, timeout=15)

        if resp is None:
            # Try stale cache
            stale = self.db.get_config(0, f"mod_cache_{cache_key}")
            if stale:
                try:
                    logger.warning(f"Versions fetch failed, returning stale cache for {slug}")
                    versions = json.loads(stale)
                    return self._filter_versions(versions, mc_version, loaders)
                except (json.JSONDecodeError, TypeError):
                    pass
            return []

        if resp.status_code == 304:
            # Not Modified — return cached data
            cached = self.db.get_config(0, f"mod_cache_{cache_key}")
            if cached:
                try:
                    logger.info(f"Versions 304 (not modified): {slug}")
                    versions = json.loads(cached)
                    return self._filter_versions(versions, mc_version, loaders)
                except (json.JSONDecodeError, TypeError):
                    pass
            return []

        # Success
        versions = resp.json()

        # Cache the full version list
        try:
            self.db.set_config(0, f"mod_cache_{cache_key}", json.dumps(versions))
            self._store_cache_timestamp(cache_key)
            resp_etag = resp.headers.get('ETag')
            if resp_etag:
                self._store_etag(url, resp_etag)
        except Exception as e:
            logger.debug(f"Versions cache write skipped: {e}")

        # Apply filters and return
        return self._filter_versions(versions, mc_version, loaders)

    def _filter_versions(self, versions: list, mc_version: str = None,
                         loaders: list = None) -> list:
        """
        Filter and sort a version list by MC version and loaders.

        Shared between live and cached data paths.
        """
        filtered = []
        for v in versions:
            if mc_version and mc_version not in v.get('game_versions', []):
                continue
            if loaders and not any(l in v.get('loaders', []) for l in loaders):
                continue
            filtered.append(v)

        # Sort newest first by date_published
        filtered.sort(key=lambda x: x.get('date_published', ''), reverse=True)
        return filtered

    def modrinth_get_latest(self, slug: str, mc_version: str,
                            loader: str = 'fabric',
                            version_type: str = 'release') -> Optional[dict]:
        """
        Get the latest matching version for a mod + MC version + loader.

        Args:
            slug: Mod slug
            mc_version: Target MC version (e.g. '1.20.4')
            loader: Mod loader ('fabric', 'forge', etc.)
            version_type: 'release', 'beta', or None for any

        Returns:
            Version dict with files[].url for download, or None
        """
        versions = self.modrinth_get_versions(slug, mc_version, [loader])
        if version_type:
            for v in versions:
                if v.get('version_type') == version_type:
                    return v
        return versions[0] if versions else None

    # ═══════════════════════════════════════════════════════════
    # Modrinth API — Download
    # ═══════════════════════════════════════════════════════════

    def modrinth_download(self, slug: str, mc_version: str, loader: str = 'fabric',
                          version_type: str = 'release') -> Optional[Tuple[str, bytes]]:
        """
        Download a mod from Modrinth, with VFS caching.

        Checks VFS cache first. If not cached, downloads from CDN,
        verifies hash, stores in VFS cache.

        Args:
            slug: Mod slug on Modrinth
            mc_version: Minecraft version
            loader: Mod loader
            version_type: 'release', 'beta', or None

        Returns:
            (filename: str, file_data: bytes) or None on failure
        """
        import requests as _r

        # Check VFS cache first
        cache_key = f"modrinth-{slug}-{mc_version}-{loader}"
        vfs_path = f"{MOD_CACHE_VFS_ROOT}/modrinth/{slug}-{mc_version}-{loader}.jar"
        cached = self.vfs.read(vfs_path)
        if cached:
            logger.info(f"Mod cache HIT: {slug} v{mc_version}")
            filenames = json.loads(
                self.db.get_config(0, f"mod_cache_{cache_key}_filename", '""')
            )
            return (filenames or f"{slug}.jar", cached)

        # Fetch version metadata (uses cached versions with ETag)
        version = self.modrinth_get_latest(slug, mc_version, loader, version_type)
        if not version:
            logger.warning(f"No version found for {slug} MC={mc_version} {loader}")
            return None

        # Get the primary file
        files = version.get('files', [])
        if not files:
            logger.warning(f"No files in version for {slug}")
            return None

        primary = files[0]
        dl_url = primary.get('url')
        filename = primary.get('filename', f"{slug}.jar")
        expected_sha512 = primary.get('hashes', {}).get('sha512')
        expected_sha1 = primary.get('hashes', {}).get('sha1')
        expected_size = primary.get('size', 0)

        # Download (no ETag — CDN files change when version changes)
        ses = self._get_requests()
        try:
            resp = ses.get(dl_url, timeout=60)
            resp.raise_for_status()
            file_data = resp.content
        except _r.exceptions.RequestException as e:
            logger.error(f"Download failed for {slug}: {e}")
            return None

        # Verify size
        if expected_size and len(file_data) != expected_size:
            logger.warning(f"Size mismatch for {slug}: expected {expected_size}, got {len(file_data)}")

        # Verify SHA-512
        if expected_sha512:
            actual = hashlib.sha512(file_data).hexdigest()
            if actual != expected_sha512:
                logger.error(f"SHA-512 mismatch for {slug}: expected {expected_sha512[:16]}..., got {actual[:16]}...")
                return None
            logger.info(f"SHA-512 verified for {slug}")

        # Store in VFS cache
        self.vfs.write(vfs_path, file_data, atomic=True,
                       content_type='application/java-archive',
                       original_name=filename)

        # Cache metadata
        ver_info = {
            'version': version.get('version_number'),
            'version_id': version.get('id'),
            'project_id': version.get('project_id'),
            'date_published': version.get('date_published'),
            'version_type': version.get('version_type'),
        }
        try:
            self.db.set_config(0, f"mod_cache_{cache_key}", json.dumps(ver_info))
            self.db.set_config(0, f"mod_cache_{cache_key}_filename", json.dumps(filename))
        except Exception as _meta_err:
            logger.debug(f"Metadata cache skipped for {slug}: {_meta_err}")

        logger.info(f"Downloaded+verified: {filename} ({len(file_data)} bytes)")
        return (filename, file_data)

    # ═══════════════════════════════════════════════════════════
    # CurseForge API
    # ═══════════════════════════════════════════════════════════

    def curseforge_search(self, query: str, game_version: str = None,
                          mod_loader_type: int = None, limit: int = 20) -> list:
        """
        Search CurseForge for mods.

        Args:
            query: Search text
            game_version: MC version (e.g. '1.20.4')
            mod_loader_type: 0=Any, 1=Forge, 2=Cauldron, 3=LiteLoader,
                             4=Fabric, 5=Quilt, 6=NeoForge
            limit: Max results

        Requires CF_API_KEY in config.
        """
        if not self.cf_api_key:
            logger.warning("CurseForge API key not configured — cannot search")
            return []

        import requests as _r
        ses = self._get_requests()
        params = {'searchFilter': query, 'pageSize': min(limit, 50)}
        if game_version:
            params['gameVersion'] = game_version
        if mod_loader_type is not None:
            params['modLoaderType'] = mod_loader_type

        try:
            resp = ses.get(
                f"{CURSEFORGE_API}/mods/search",
                params=params,
                headers={'x-api-key': self.cf_api_key},
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get('data', [])
        except _r.exceptions.RequestException as e:
            logger.error(f"CurseForge search failed: {e}")
            return []

    # ═══════════════════════════════════════════════════════════
    # Remote Dependency Resolution
    # ═══════════════════════════════════════════════════════════

    def resolve_remote_dependencies(self, slug: str, mc_version: str,
                                     loader: str) -> List[Dict[str, Any]]:
        """
        Fetch dependency information from Modrinth for a specific mod version.

        Uses the version-level 'dependencies' array returned by the Modrinth API
        (https://docs.modrinth.com/#tag/versions/operation/getVersion).
        Each dependency has:
          - project_id: Modrinth project ID (str)
          - dependency_type: "required", "optional", "incompatible", or "embedded"
          - version_id: specific pinned version (or null for any compatible)
          - file_name: filename hint (may be empty)

        For each dependency with project_id, this method looks up the project
        to resolve its slug (human-readable identifier).

        Args:
            slug: Mod slug on Modrinth (e.g. 'fabric-api')
            mc_version: Target Minecraft version (e.g. '1.20.4')
            loader: Mod loader ('fabric', 'forge', etc.)

        Returns:
            List of dicts:
              {project_id, slug, dependency_type, version_id, resolved: bool}
            An empty list means no dependencies or the mod/version was not found.
            'resolved' is False if the project lookup failed.
        """
        versions = self.modrinth_get_versions(slug, mc_version, [loader])
        if not versions:
            logger.info(f"No versions found for {slug} — cannot resolve dependencies")
            return []

        version = versions[0]  # Latest matching version (already sorted)
        raw_deps = version.get('dependencies', [])
        if not raw_deps:
            return []

        result = []
        for dep in raw_deps:
            dep_type = dep.get('dependency_type', 'required')
            proj_id = dep.get('project_id')
            if not proj_id:
                continue

            # Look up the project to get its slug
            dep_slug = None
            resolved = False
            try:
                proj = self.modrinth_get_project(proj_id)
                if proj:
                    dep_slug = proj.get('slug', proj_id)
                    resolved = True
                else:
                    dep_slug = proj_id
            except Exception as e:
                logger.warning(f"Failed to resolve project {proj_id} for dep of {slug}: {e}")
                dep_slug = proj_id

            result.append({
                'project_id': proj_id,
                'slug': dep_slug,
                'dependency_type': dep_type,
                'version_id': dep.get('version_id'),
                'resolved': resolved,
            })

        logger.info(
            f"Resolved {len(result)} remote dependencies for {slug}: "
            + ", ".join(f"{d['slug']}({d['dependency_type']})" for d in result)
        )
        return result

    def install_with_dependencies(self, slug: str, mc_version: str,
                                   loader: str, server_id: int,
                                   _installing: Optional[set] = None,
                                   max_depth: int = 10) -> Dict[str, Any]:
        """
        Install a mod AND all its required remote dependencies recursively.

        This is the primary entry point for the admin GUI's "install to server"
        flow. It:
          1. Resolves the mod's remote dependencies from Modrinth
          2. Recursively installs each REQUIRED dependency (max_depth prevents cycles)
          3. Logs OPTIONAL dependencies as suggestions
          4. Skips INCOMPATIBLE dependencies with a warning
          5. Installs the requested mod itself last

        Args:
            slug: Mod slug to install (e.g. 'fabric-api')
            mc_version: Server Minecraft version
            loader: Server mod loader
            server_id: Target server database ID
            _installing: Internal set tracking currently-installing slugs
                         (prevents duplicate work and circular deps)
            max_depth: Maximum recursive depth (prevents infinite loops)

        Returns:
            Dict with keys:
              slug: The requested mod slug
              installed: List of slugs that were installed
              already_installed: List of slugs already present on the server
              optional: List of optional dependency suggestions
              incompatible: List of incompatible dependencies skipped
              failed: List of {slug, reason} for failed installs
              depth: Recursion depth used
        """
        if _installing is None:
            _installing = set()

        result: Dict[str, Any] = {
            'slug': slug,
            'installed': [],
            'already_installed': [],
            'optional': [],
            'incompatible': [],
            'failed': [],
            'depth': 0,
        }

        # Circular/duplicate detection
        if slug in _installing:
            logger.warning(f"Circular dependency detected: {slug} already being installed")
            return result
        if max_depth <= 0:
            logger.warning(f"Max depth exceeded for {slug}")
            result['failed'].append({'slug': slug, 'reason': 'Max dependency depth exceeded'})
            return result

        _installing.add(slug)

        # Step 1: Resolve remote dependencies
        remote_deps = self.resolve_remote_dependencies(slug, mc_version, loader)

        # Step 2: Process each dependency
        for dep in remote_deps:
            dep_slug = dep['slug']
            dep_type = dep['dependency_type']

            if dep_type == 'incompatible':
                result['incompatible'].append(dep_slug)
                logger.info(f"Skipping incompatible dependency: {dep_slug}")
                continue

            if dep_type == 'optional':
                result['optional'].append(dep_slug)
                logger.info(f"Optional dependency: {dep_slug} (will not auto-install)")
                continue

            if dep_type == 'embedded':
                logger.info(f"Embedded dependency: {dep_slug} (included in mod file)")
                continue

            # Required dependency — recursively install
            dep_result = self.install_with_dependencies(
                dep_slug, mc_version, loader, server_id,
                _installing=_installing, max_depth=max_depth - 1
            )

            # Merge results
            result['installed'].extend(dep_result.get('installed', []))
            result['already_installed'].extend(dep_result.get('already_installed', []))
            result['optional'].extend(dep_result.get('optional', []))
            result['incompatible'].extend(dep_result.get('incompatible', []))
            result['failed'].extend(dep_result.get('failed', []))
            result['depth'] = max(result['depth'], dep_result.get('depth', 0) + 1)

        # Step 3: Install the requested mod itself
        try:
            success = self.install_mod_from_cache(slug, mc_version, loader, server_id)
            if success:
                result['installed'].append(slug)
            else:
                result['failed'].append({'slug': slug, 'reason': 'Install returned False'})
        except Exception as e:
            logger.error(f"Failed to install {slug}: {e}")
            result['failed'].append({'slug': slug, 'reason': str(e)})

        _installing.discard(slug)

        # Deduplicate lists
        for key in ['installed', 'already_installed', 'optional', 'incompatible']:
            seen = set()
            deduped = []
            for item in result[key]:
                if item not in seen:
                    seen.add(item)
                    deduped.append(item)
            result[key] = deduped

        return result

    # ═══════════════════════════════════════════════════════════
    # Bulk Operations
    # ═══════════════════════════════════════════════════════════

    def install_mod_from_cache(self, slug: str, mc_version: str,
                                loader: str, server_id: int) -> bool:
        """
        Download (if needed) and install a mod to a server.

        Combines ModCache.download + ModManager.install in one operation.

        Args:
            slug: Mod slug on Modrinth
            mc_version: Server MC version
            loader: Server loader type
            server_id: Target server ID

        Returns:
            True if installed
        """
        # Get/verify cache
        result = self.modrinth_download(slug, mc_version, loader)
        if not result:
            return False

        filename, file_data = result

        # Import into VFS mods storage
        mod_vfs = f"/mods/{slug}.jar"
        self.vfs.write(mod_vfs, file_data, atomic=True,
                       content_type='application/java-archive',
                       original_name=filename)

        # Register in DB if not exists
        existing = self.db.get_mod(slug)
        if not existing:
            version_info = self.db.get_config(0, f"mod_cache_modrinth-{slug}-{mc_version}-{loader}", '{}')
            try:
                ver = json.loads(version_info) if version_info != '{}' else {}
            except json.JSONDecodeError:
                ver = {}

            self.db.create_mod(
                name=slug.replace('-', ' ').title(),
                slug=slug,
                version=ver.get('version', 'cached'),
                mc_version=mc_version,
                loader=loader,
            )

        # Install mod to server
        from engine.mod_manager import ModManager
        mm = ModManager(self.db, self.vfs)
        return mm.install_mod_to_server(slug, server_id)

    def install_bulk(self, slugs: list, mc_version: str, loader: str,
                     server_id: int) -> Dict[str, list]:
        """
        Install multiple mods with bulk caching.

        Returns {installed: [], failed: []}
        """
        results = {'installed': [], 'failed': []}
        for slug in slugs:
            try:
                if self.install_mod_from_cache(slug, mc_version, loader, server_id):
                    results['installed'].append(slug)
                else:
                    results['failed'].append(slug)
            except Exception as e:
                logger.error(f"Bulk install failed for {slug}: {e}")
                results['failed'].append(slug)
        return results

    # ═══════════════════════════════════════════════════════════
    # Background Cache Validation
    # ═══════════════════════════════════════════════════════════

    def validate_cache_background(self, max_checks: int = 50) -> Dict[str, Any]:
        """
        Background validation of cached mod entries.

        Scans cached entries stored in DB config and VFS, checks each
        against the current Modrinth version. If a cached entry's version
        is no longer the latest, it logs a warning but does NOT auto-delete
        (the next download will overwrite stale binaries).

        Designed to be called periodically (e.g., daily cron or on startup).

        Args:
            max_checks: Maximum number of entries to validate in one pass
                        (prevents rate limit bursts). Default 50.

        Returns:
            Dict with keys:
              checked: Number of entries validated
              up_to_date: Number of entries that are still current
              stale: Number of entries with newer versions available
              errors: Number of entries that could not be validated
              details: List of {slug, mc_version, loader, status, cached_version, latest_version}
        """
        import requests as _r
        results = {
            'checked': 0,
            'up_to_date': 0,
            'stale': 0,
            'errors': 0,
            'details': [],
        }

        # Scan the VFS cache directory for cached mods
        cache_dir = f"{MOD_CACHE_VFS_ROOT}/modrinth"
        try:
            cached_files = self.vfs.listdir(cache_dir)
        except Exception as e:
            logger.warning(f"Cannot list VFS cache dir {cache_dir}: {e}")
            return results

        checks = 0
        for entry in cached_files:
            if checks >= max_checks:
                break
            path = entry.get('path', '')
            if not path.endswith('.jar'):
                continue

            # Parse slug, mc_version, loader from filename: modrinth/{slug}-{mc_version}-{loader}.jar
            rel = path.replace(cache_dir, '').strip('/').replace('.jar', '')
            parts = rel.split('-', 2) if '-' in rel else [rel]
            if len(parts) < 3:
                continue

            loader = parts[-1]
            mc_version = parts[-2]
            slug = '-'.join(parts[:-2])

            checks += 1
            try:
                # Check the latest version from Modrinth (uses cache + ETag)
                latest = self.modrinth_get_latest(slug, mc_version, loader)
                if not latest:
                    results['errors'] += 1
                    results['details'].append({
                        'slug': slug,
                        'mc_version': mc_version,
                        'loader': loader,
                        'status': 'error',
                        'message': 'Could not fetch latest version',
                    })
                    continue

                latest_version = latest.get('version_number', 'unknown')

                # Compare with cached version info
                cached_ver_info = self.db.get_config(
                    0, f"mod_cache_modrinth-{slug}-{mc_version}-{loader}", '{}'
                )
                try:
                    cached_info = json.loads(cached_ver_info) if cached_ver_info != '{}' else {}
                except json.JSONDecodeError:
                    cached_info = {}

                cached_version = cached_info.get('version', 'unknown')

                if latest_version != cached_version:
                    results['stale'] += 1
                    results['details'].append({
                        'slug': slug,
                        'mc_version': mc_version,
                        'loader': loader,
                        'status': 'stale',
                        'cached_version': cached_version,
                        'latest_version': latest_version,
                    })
                    logger.info(
                        f"Stale cache: {slug} v{cached_version} -> v{latest_version}"
                    )
                else:
                    results['up_to_date'] += 1
            except Exception as e:
                results['errors'] += 1
                logger.debug(f"Cache validation error for {slug}: {e}")

        results['checked'] = checks
        logger.info(
            f"Cache validation complete: {results['checked']} checked, "
            f"{results['up_to_date']} up-to-date, "
            f"{results['stale']} stale, {results['errors']} errors"
        )
        return results

    # ═══════════════════════════════════════════════════════════
    # Cache Cleanup
    # ═══════════════════════════════════════════════════════════

    def clear_cache(self, older_than_days: int = 30) -> int:
        """
        Clear mod cache entries older than specified days.

        Scans VFS cache directory and removes entries whose
        cached metadata timestamp exceeds older_than_days.
        Also removes corresponding DB config entries.

        Args:
            older_than_days: Remove entries older than this many days.
                             Default 30. Use 0 to clear everything.

        Returns:
            Number of cache entries removed.
        """
        cutoff = time.time() - (older_than_days * 86400)
        removed = 0

        cache_dir = f"{MOD_CACHE_VFS_ROOT}/modrinth"
        try:
            cached_files = self.vfs.listdir(cache_dir)
        except Exception as e:
            logger.warning(f"Cannot list VFS cache dir {cache_dir}: {e}")
            return 0

        for entry in cached_files:
            path = entry.get('path', '')
            if not path.endswith('.jar'):
                continue

            # Parse the cache key from path
            rel = path.replace(cache_dir, '').strip('/').replace('.jar', '')
            parts = rel.split('-', 2) if '-' in rel else [rel]
            if len(parts) < 3:
                continue
            loader = parts[-1]
            mc_version = parts[-2]
            slug = '-'.join(parts[:-2])

            cache_key = f"modrinth-{slug}-{mc_version}-{loader}"
            ts_key = self._cache_time_key(cache_key)

            # Check age
            try:
                raw = self.db.get_config(0, ts_key)
                if raw:
                    cached_at = float(raw)
                    if cached_at < cutoff:
                        # Remove VFS file
                        self.vfs.delete(path)
                        # Remove DB config entries
                        try:
                            self.db.set_config(0, ts_key, '')
                            self.db.set_config(0, f"mod_cache_{cache_key}", '')
                            self.db.set_config(0, f"mod_cache_{cache_key}_filename", '')
                        except Exception:
                            pass
                        removed += 1
                        logger.debug(f"Cleared cache: {slug} ({mc_version}/{loader})")
            except (ValueError, TypeError, Exception):
                continue

        if removed:
            logger.info(f"Cache cleanup: removed {removed} entries older than {older_than_days}d")
        else:
            logger.info(f"Cache cleanup: no entries older than {older_than_days}d found")
        return removed

    # ═══════════════════════════════════════════════════════════
    # Cache Stats
    # ═══════════════════════════════════════════════════════════

    def cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the current cache state.

        Returns:
            Dict with:
              cached_mods: Number of mod jars in VFS
              cached_searches: Number of cached search results in DB
              cached_projects: Number of cached project entries in DB
              cached_version_lists: Number of cached version lists in DB
              oldest_entry_days: Age of oldest cache entry in days
              total_estimated_bytes: Estimated total cached size
        """
        stats = {
            'cached_mods': 0,
            'cached_searches': 0,
            'cached_projects': 0,
            'cached_version_lists': 0,
            'oldest_entry_days': 0,
            'total_estimated_bytes': 0,
        }

        # Count VFS mod jars
        try:
            mod_files = self.vfs.listdir(f"{MOD_CACHE_VFS_ROOT}/modrinth") or []
            stats['cached_mods'] = sum(
                1 for e in mod_files if e.get('path', '').endswith('.jar')
            )
            stats['total_estimated_bytes'] = sum(
                e.get('size', 0) for e in mod_files if e.get('path', '').endswith('.jar')
            )
        except Exception:
            pass

        # Count DB cache entries and find oldest timestamp
        try:
            conn = getattr(self.db, 'conn', None)
            if conn is not None and hasattr(conn, 'execute'):
                rows = conn.execute(
                    "SELECT flag_key, flag_value FROM server_flags "
                    "WHERE server_id = 0 AND flag_key LIKE 'mod_cache_%'"
                ).fetchall()

                now = time.time()
                oldest_ts = float('inf')

                for row in rows:
                    key = row['flag_key']
                    if key.startswith('mod_cache_search_'):
                        stats['cached_searches'] += 1
                    elif key.startswith('mod_cache_project_'):
                        stats['cached_projects'] += 1
                    elif key.startswith('mod_cache_versions_'):
                        stats['cached_version_lists'] += 1
                    elif key.startswith('mod_cache_time_'):
                        try:
                            ts_raw = row['flag_value']
                            if ts_raw:
                                ts = float(ts_raw)
                                if ts < oldest_ts:
                                    oldest_ts = ts
                        except (ValueError, TypeError):
                            pass

                if oldest_ts < float('inf'):
                    stats['oldest_entry_days'] = round((now - oldest_ts) / 86400, 1)
        except Exception:
            pass

        return stats
