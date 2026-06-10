"""
config.py — Unified configuration system for AH extensions.

Provides a shared ``ExtensionConfig`` class that handles:
  - Default values (fallback if missing from file)
  - JSON file loading (``config.json`` in the extension's directory)
  - Runtime overrides (for testing / debugging)
  - Hot-reload support (re-read from disk)
  - Dictionary-style access (``config["key"]`` or ``config.get("key", default)``)
  - Registry of all extension configs (for introspection / dashboard)

Replaces the per-extension ad-hoc config loading (``load_config()`` in
SIMULATED_PEOPLE, ``tr_config.py`` in SIMULATED_TRADE).

Usage:
    from EXTENSIONS._shared.config import ExtensionConfig

    DEFAULTS = {
        "enabled": True,
        "interval_minutes": 60,
        "max_items": 100,
    }

    cfg = ExtensionConfig("SIMULATED_PEOPLE", DEFAULTS)
    cfg.load()  # reads config.json from this file's directory

    enabled = cfg["enabled"]          # dict-style access
    interval = cfg.get("interval_minutes", 60)  # with default
    cfg.set("debug", True)            # runtime override
    cfg.reload()                      # hot-reload from disk
"""

import json, os, threading, logging
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


# ── Global config registry ─────────────────────────────────────────
_config_registry: Dict[str, "ExtensionConfig"] = {}
_registry_lock = threading.Lock()


def get_config(name: str) -> Optional["ExtensionConfig"]:
    """Retrieve a registered config by extension name."""
    with _registry_lock:
        return _config_registry.get(name)


def list_configs() -> Dict[str, "ExtensionConfig"]:
    """Return a snapshot of all registered configs."""
    with _registry_lock:
        return dict(_config_registry)


def reload_all() -> Dict[str, Any]:
    """Hot-reload all registered configs from disk.

    Returns a dict of extension_name -> reload status.
    """
    results = {}
    with _registry_lock:
        names = list(_config_registry.keys())
    for name in names:
        cfg = get_config(name)
        if cfg:
            try:
                results[name] = cfg.reload()
            except Exception as e:
                results[name] = f"error: {e}"
    return results


# ═══════════════════════════════════════════════════════════════════
# ExtensionConfig class
# ═══════════════════════════════════════════════════════════════════

class ExtensionConfig:
    """
    Configuration for a single extension.

    Loads from ``config.json`` in the extension's directory, merged
    over a defaults dict.  Supports runtime overrides and hot-reload.

    Thread-safe for reads.  Writes lock per instance.
    """

    def __init__(
        self,
        extension_name: str,
        defaults: Dict[str, Any],
        config_dir: Optional[str] = None,
        auto_register: bool = True,
    ):
        """
        Args:
            extension_name: Unique name (e.g. "SIMULATED_PEOPLE").
            defaults: Dict of default values.
            config_dir: Path to extension directory.  Defaults to
                        ``EXTENSIONS/<name>/`` relative to this file.
            auto_register: If True, register in the global registry.
        """
        self._name = extension_name
        self._defaults = dict(defaults)
        self._overrides: Dict[str, Any] = {}
        self._lock = threading.Lock()

        if config_dir is None:
            # Assume this file is at EXTENSIONS/_shared/config.py
            config_dir = str(
                Path(__file__).resolve().parent.parent / extension_name
            )
        self._config_dir = config_dir
        self._config_path = os.path.join(self._config_dir, "config.json")
        self._file_data: Dict[str, Any] = {}

        if auto_register:
            with _registry_lock:
                _config_registry[extension_name] = self

    # ── Public API ───────────────────────────────────────────────

    def load(self, path: Optional[str] = None) -> "ExtensionConfig":
        """Load config from JSON file.

        Args:
            path: Path to JSON file.  Defaults to ``config.json``
                  in the extension's directory.

        Returns:
            self for chaining.
        """
        load_path = path or self._config_path
        if os.path.isfile(load_path):
            try:
                with open(load_path, "r") as f:
                    self._file_data = json.load(f)
                log.info(f"Config {self._name}: loaded from {load_path}")
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"Config {self._name}: failed to load {load_path}: {e}")
                self._file_data = {}
        else:
            log.info(f"Config {self._name}: no config.json at {load_path}, using defaults")
            self._file_data = {}
        return self

    def reload(self) -> Dict[str, Any]:
        """Hot-reload config from disk (preserves runtime overrides).

        Returns:
            Summary of changes.
        """
        old_data = dict(self._file_data)
        self.load()
        new_data = dict(self._file_data)

        added = [k for k in new_data if k not in old_data]
        removed = [k for k in old_data if k not in new_data]
        changed = [k for k in new_data if k in old_data and new_data[k] != old_data[k]]

        summary = {"added": added, "removed": removed, "changed": changed}
        if any((added, removed, changed)):
            log.info(f"Config {self._name}: reload detected changes: {summary}")
        return summary

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value.

        Resolution order:
          1. Runtime override (set via ``set()``)
          2. File value (from ``config.json``)
          3. Default value (from constructor)

        Args:
            key: Config key.
            default: Fallback if key not found anywhere.

        Returns:
            The resolved value, or ``default``.
        """
        with self._lock:
            if key in self._overrides:
                return self._overrides[key]
        # No lock needed for reads from immutable-ish dicts
        if key in self._file_data:
            return self._file_data[key]
        if key in self._defaults:
            return self._defaults[key]
        return default

    def __getitem__(self, key: str) -> Any:
        """Dict-style access, raises KeyError if missing."""
        val = self.get(key)
        # This is used when we know we need it for setup.
        if val is None and key not in self._defaults and key not in self._file_data and key not in self._overrides:
            raise KeyError(f"Config '{self._name}' has no key '{key}'")
        return val

    def __contains__(self, key: str) -> bool:
        return key in self._defaults or key in self._file_data or key in self._overrides

    def set(self, key: str, value: Any) -> None:
        """Set a runtime override (highest priority).

        Does NOT write to disk.  Use ``save()`` for that.
        """
        with self._lock:
            self._overrides[key] = value

    def set_many(self, mapping: Dict[str, Any]) -> None:
        """Set multiple runtime overrides atomically."""
        with self._lock:
            self._overrides.update(mapping)

    def save(self, path: Optional[str] = None) -> None:
        """Save current config (file values + overrides) to disk."""
        save_path = path or self._config_path
        merged = dict(self._defaults)
        merged.update(self._file_data)
        with self._lock:
            merged.update(self._overrides)
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "w") as f:
                json.dump(merged, f, indent=2)
            log.info(f"Config {self._name}: saved to {save_path}")
            # Reload from file so file_data matches disk
            self._file_data = merged
        except OSError as e:
            log.error(f"Config {self._name}: failed to save: {e}")

    def clear_overrides(self) -> None:
        """Remove all runtime overrides (revert to file + defaults)."""
        with self._lock:
            self._overrides.clear()

    def as_dict(self) -> Dict[str, Any]:
        """Return the complete resolved config as a dict."""
        merged = dict(self._defaults)
        merged.update(self._file_data)
        with self._lock:
            merged.update(self._overrides)
        return merged

    # ── Properties ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._name

    @property
    def config_path(self) -> str:
        return self._config_path

    @property
    def has_file(self) -> bool:
        return bool(self._file_data)
