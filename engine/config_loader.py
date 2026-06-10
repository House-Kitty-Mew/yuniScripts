"""
config_loader.py — Unified config management for all YuniScripts.

Centralizes ALL configuration files into PROJECT_ROOT/DATA/ with unique
per-script filenames.  Auto-creates the DATA directory, provides
load/save helpers, and auto-migrates legacy config paths on first access.

Usage:
    from engine.config_loader import load_config, save_config, get_data_dir, migrate_old_config

    # Load a script's config (auto-returns {} if missing)
    cfg = load_config("minecraft_manager")

    # Save config
    save_config("minecraft_manager", {"rcon_host": "..."})

    # Get the data dir path
    data_dir = get_data_dir()

    # Validate config at startup (surfaces missing keys early)
    from engine.config_loader import assert_valid_config, register_config_schema
    register_config_schema("minecraft_manager", required=["rcon_host"])
    assert_valid_config("minecraft_manager", cfg)
"""

import json, os, threading, shutil
from pathlib import Path
from configparser import ConfigParser
from typing import Optional, Any

# ── Globals ─────────────────────────────────────────────────────────

_PROJECT_ROOT: Optional[Path] = None
_DATA_DIR: Optional[Path] = None
_lock = threading.Lock()

# ── Path resolution ─────────────────────────────────────────────────

def _find_project_root() -> Path:
    """Find the project root (where start.py / main.py lives)."""
    # Check common locations.  The engine/ directory is at the project root.
    candidates = [
        Path(__file__).resolve().parent.parent,           # engine/ -> root
        Path(os.getcwd()),                                 # cwd
    ]
    for c in candidates:
        if (c / "start.py").exists() or (c / "main.py").exists():
            return c
    return Path(os.getcwd()).resolve()


def get_project_root() -> Path:
    """Return the absolute project root path."""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = _find_project_root()
    return _PROJECT_ROOT


def get_data_dir() -> Path:
    """Return the centralized DATA directory, creating it if needed."""
    global _DATA_DIR
    if _DATA_DIR is None:
        _DATA_DIR = get_project_root() / "DATA"
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


# ── Config filename mapping ────────────────────────────────────────
# Each script registers its config name and the legacy path(s) to
# auto-migrate from on first load.

_CONFIG_NAMES = {
    "engine":                   "engine_config.json",
    "minecraft_manager":        "minecraft_manager_config.json",
    "ah":                       "ah_config.json",
    "simulated_people":         "simulated_people_config.json",
    "eco":                      "eco_config.json",
    "item_signing_bridge":      "item_signing_bridge_config.json",
    "script_packager": [
        Path("SCRIPTS/SERVICES/script-packager/DATA/config.json"),
    ],
    "server_stats_collector":   "server_stats_collector_config.json",
    "steam_nextcloud":          "steam_nextcloud_config.ini",
    "config_updater":           "config_updater.ini",
    "sign_item":                "sign_item_config.json",
}

_LEGACY_PATHS = {
    "engine": [
        Path("engine_config.json"),
    ],
    "ah": [
        Path("SCRIPTS/GAMES/minecraft_manager/AUCTIONHOUSE/ah_config.json"),
    ],
    "minecraft_manager": [
        Path("SCRIPTS/GAMES/minecraft_manager/DATA/config.json"),
    ],
    "simulated_people": [
        Path("SCRIPTS/GAMES/minecraft_manager/AUCTIONHOUSE/EXTENSIONS/SIMULATED_PEOPLE/config.json"),
    ],
    "eco": [
        Path("SCRIPTS/GAMES/minecraft_manager/ECO_BRIDGE/eco_config.json"),
    ],
    "item_signing_bridge": [
        Path("SCRIPTS/SERVICES/item-signing-bridge/DATA/config.json"),
    ],
    "script_packager": [
        Path("SCRIPTS/SERVICES/script-packager/DATA/config.json"),
    ],
    "server_stats_collector": [
        Path("SCRIPTS/SERVICES/server-stats-collector/DATA/config.json"),
    ],
    "steam_nextcloud": [
        Path("SCRIPTS/PROGRAMS/steam-nextcloud-status-updater/DATA/config.ini"),
    ],
    "config_updater": [
        Path("SCRIPTS/LAUNCHER/config-updater/DATA/main.ini"),
    ],
    "sign_item": [
        Path("minescript/sign_item_config.json"),
    ],
}


def get_config_path(name: str) -> Path:
    """Return the centralized config path for a given script name.

    Args:
        name: Short script identifier (e.g. "minecraft_manager", "eco")

    Returns:
        Absolute Path to the config file in DATA/
    """
    filename = _CONFIG_NAMES.get(name, f"{name}_config.json")
    return get_data_dir() / filename


# ── Loading ─────────────────────────────────────────────────────────

def load_config(name: str, default: Optional[dict] = None) -> dict:
    """Load a script's config from the centralized DATA directory.

    On first load, auto-migrates from any legacy paths that exist.
    Creates an empty config file if none exists and no defaults provided.

    Args:
        name: Short script identifier (e.g. "minecraft_manager")
        default: Default values to merge with loaded config

    Returns:
        Dict of config values
    """
    config_path = get_config_path(name)
    data: dict = {}

    # 1. Try migration from legacy paths on first access
    _auto_migrate(name, config_path)

    # 2. Load from centralized path
    if config_path.exists():
        if config_path.suffix == ".ini":
            data = _load_ini(config_path)
        else:
            data = _load_json(config_path)

    # 3. Merge defaults
    if default:
        merged = dict(default)
        merged.update(data)
        data = merged

    return data


def save_config(name: str, data: dict) -> bool:
    """Save a script's config to the centralized DATA directory.

    Args:
        name: Short script identifier
        data: Dict of config values to save

    Returns:
        True if saved successfully
    """
    config_path = get_config_path(name)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _lock:
            if config_path.suffix == ".ini":
                _save_ini(config_path, data)
            else:
                with open(config_path, "w") as f:
                    json.dump(data, f, indent=2)
        return True
    except (IOError, OSError, json.JSONDecodeError) as e:
        print(f"[config_loader] Failed to save '{name}': {e}")
        return False


# ── Auto-migration from legacy paths ────────────────────────────────

_migrated: set = set()


def _auto_migrate(name: str, target_path: Path) -> None:
    """Migrate legacy config to centralized DATA/ — runs once per name.

    Thread-safe: both the _migrated check and the file copy are under _lock
    so concurrent first-access calls cannot double-migrate.
    """
    with _lock:
        if name in _migrated or target_path.exists():
            return

        project_root = get_project_root()
        legacy_list = _LEGACY_PATHS.get(name, [])

        for legacy_rel in legacy_list:
            legacy_abs = project_root / legacy_rel
            if legacy_abs.exists() and legacy_abs.is_file():
                try:
                    # Copy the legacy file to the new location
                    shutil.copy2(str(legacy_abs), str(target_path))
                    print(f"[config_loader] Migrated '{name}' config: {legacy_rel} -> DATA/")
                    _migrated.add(name)
                    return  # Stop at first successful migration
                except (IOError, OSError, shutil.Error) as e:
                    print(f"[config_loader] Migration error for '{name}': {e}")


def migrate_old_config(name: str) -> bool:
    """Explicitly trigger migration for a named config.

    Can be called by scripts during their initialization to force
    legacy config migration.

    Returns:
        True if migration happened
    """
    config_path = get_config_path(name)
    old_name = _migrated.copy()
    _auto_migrate(name, config_path)
    return name not in old_name


# ── Low-level helpers ───────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _load_ini(path: Path) -> dict:
    """Load an INI file and flatten it into a single dict."""
    config = ConfigParser()
    config.read(str(path))
    result = {}
    for section in config.sections():
        for key, value in config.items(section):
            result[key] = value
    return result


def _save_ini(path: Path, data: dict, section: str = "settings") -> None:
    """Save a flat dict as a single-section INI file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    config = ConfigParser()
    config[section] = {}
    for key, value in data.items():
        config[section][key] = str(value)
    with open(path, "w") as f:
        config.write(f)


# ── Config validation ───────────────────────────────────────────────

# Per-service required keys and type schemas.
# Keys in "required" must exist and be non-None.
# Keys in "types" are checked via isinstance() if provided.
_CONFIG_SCHEMAS: dict[str, dict] = {}


def register_config_schema(name: str, required: list[str] | None = None,
                            types: dict[str, type] | None = None) -> None:
    """Register a config schema for a named script/service.

    Args:
        name: Script identifier (e.g. 'item_signing_bridge').
        required: List of keys that must be present and non-None.
        types: Dict mapping key -> expected type (e.g. {'port': int}).
    """
    _CONFIG_SCHEMAS[name] = {
        "required": required or [],
        "types": types or {},
    }


def validate_config(name: str, data: dict) -> list[str]:
    """Validate a loaded config dict against its registered schema.

    Args:
        name: Script identifier matching a previous register_config_schema() call.
        data: The loaded config dict to validate.

    Returns:
        A list of error messages (empty = valid).
    """
    errors: list[str] = []
    schema = _CONFIG_SCHEMAS.get(name)
    if schema is None:
        return []  # No schema registered — skip validation

    for key in schema["required"]:
        if key not in data or data[key] is None:
            errors.append(f"Missing required config key '{key}' for '{name}'")

    for key, expected_type in schema["types"].items():
        if key in data and data[key] is not None:
            if not isinstance(data[key], expected_type):
                errors.append(
                    f"Config key '{key}' for '{name}' should be {expected_type.__name__}, "
                    f"got {type(data[key]).__name__}"
                )

    return errors


def assert_valid_config(name: str, data: dict) -> dict:
    """Validate config and print warnings. Returns the data dict unchanged.

    Call this after loading config in any script's main() to surface
    missing keys at startup instead of crashing at runtime with KeyError.
    """
    errors = validate_config(name, data)
    if errors:
        for err in errors:
            print(f"[config] ⚠ {err}")
    return data

def is_fresh_install() -> bool:
    """Check if this is a fresh install by looking for engine_config."""
    ec_path = get_config_path("engine")
    if not ec_path.exists():
        return True
    try:
        cfg = _load_json(ec_path)
        return cfg.get("fresh_install", False)
    except Exception:
        return True


def mark_setup_complete() -> None:
    """Mark the first-run setup as complete by clearing fresh_install flag."""
    cfg = load_config("engine", {})
    cfg["fresh_install"] = False
    save_config("engine", cfg)
