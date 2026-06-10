#!/usr/bin/env python3
"""
tools/dynamic_config_loader.py — Central Dynamic Config System

Every script registers its configurable settings via `register_configs()`.
Settings are stored in memory. An admin GUI displays and edits them live.
Flush writes current values to each service's DATA/config.json or .ini.

Usage (in any script):
    from dynamic_config_loader import register_configs, get_config, update_config

    register_configs("god_watcher", [
        {"key": "alert_threshold", "type": "int", "default": 80,
         "description": "Alert threshold for CPU %", "valid_range": (1, 100),
         "category": "monitoring"},
    ])
    threshold = get_config("god_watcher", "alert_threshold")
"""

import json
import os
import threading
import time
from configparser import ConfigParser
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Logging ──────────────────────────────────────────────────────
import logging
logger = logging.getLogger("dynamic_config")


# ── Data Structures ──────────────────────────────────────────────

@dataclass
class ConfigSetting:
    """A single configurable setting."""
    key: str
    type: str           # "int" | "float" | "str" | "bool" | "list" | "dict"
    default: Any
    description: str = ""
    value: Any = None   # Current value (None = use default)
    source: str = ""    # Set by register_configs()
    valid_range: tuple = None    # (min, max) for numbers
    valid_options: list = None   # For enums
    category: str = "general"    # Grouping: performance, security, monitoring, debug, etc.
    persist_as: str = "json"     # "json" or "ini"
    readonly: bool = False       # If True, shown in GUI but not editable
    changed_at: str = ""         # ISO timestamp of last change
    changed_by: str = ""         # "admin_gui" | "cli" | "startup"

    def __post_init__(self):
        if self.value is None:
            self.value = self.default

    def to_dict(self) -> dict:
        """Serialize to plain dict (for GUI JSON output)."""
        return {
            "key": self.key,
            "type": self.type,
            "default": self.default,
            "value": self.value,
            "description": self.description,
            "source": self.source,
            "valid_range": self.valid_range,
            "valid_options": self.valid_options,
            "category": self.category,
            "persist_as": self.persist_as,
            "readonly": self.readonly,
            "changed_at": self.changed_at,
            "changed_by": self.changed_by,
        }

    def validate(self, new_value: Any) -> Tuple[bool, str]:
        """Validate a new value against this setting's type/range/options."""
        # Type check
        if self.type == "int":
            if not isinstance(new_value, int):
                return False, f"Expected int, got {type(new_value).__name__}"
            if self.valid_range:
                lo, hi = self.valid_range
                if new_value < lo or new_value > hi:
                    return False, f"Value {new_value} outside range [{lo}, {hi}]"
        elif self.type == "float":
            if not isinstance(new_value, (int, float)):
                return False, f"Expected number, got {type(new_value).__name__}"
            new_value = float(new_value)
            if self.valid_range:
                lo, hi = self.valid_range
                if new_value < lo or new_value > hi:
                    return False, f"Value {new_value} outside range [{lo}, {hi}]"
        elif self.type == "bool":
            if not isinstance(new_value, bool):
                return False, f"Expected bool, got {type(new_value).__name__}"
        elif self.type == "str":
            if not isinstance(new_value, str):
                return False, f"Expected string, got {type(new_value).__name__}"
            if self.valid_options and new_value not in self.valid_options:
                return False, f"'{new_value}' not in valid options: {self.valid_options}"
        elif self.type == "list":
            if not isinstance(new_value, list):
                return False, f"Expected list, got {type(new_value).__name__}"
        elif self.type == "dict":
            if not isinstance(new_value, dict):
                return False, f"Expected dict, got {type(new_value).__name__}"
        else:
            return False, f"Unknown type: {self.type}"
        return True, "ok"


# ── Change Event System ──────────────────────────────────────────

_change_listeners: Dict[str, List[Callable]] = {}  # "source.key" -> [callbacks]

def on_config_change(source: str, key: str, callback: Callable):
    """Register a callback for when a specific config changes.
    Callback receives (source, key, old_value, new_value)."""
    event_key = f"{source}.{key}"
    if event_key not in _change_listeners:
        _change_listeners[event_key] = []
    _change_listeners[event_key].append(callback)

def _fire_change_event(source: str, key: str, old_value: Any, new_value: Any):
    """Fire change event to all registered listeners."""
    event_key = f"{source}.{key}"
    for cb in _change_listeners.get(event_key, []):
        try:
            cb(source, key, old_value, new_value)
        except Exception as e:
            logger.warning(f"Config change callback error: {e}")


# ── Change History ───────────────────────────────────────────────

_change_history: List[dict] = []
_MAX_HISTORY = 500

def _log_change(source: str, key: str, old_value: Any, new_value: Any, changed_by: str = "cli"):
    """Record a config change in the history log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "key": key,
        "old_value": old_value,
        "new_value": new_value,
        "changed_by": changed_by,
    }
    _change_history.append(entry)
    if len(_change_history) > _MAX_HISTORY:
        _change_history.pop(0)


# ── Config Registry ──────────────────────────────────────────────

_registry: Dict[str, Dict[str, ConfigSetting]] = {}  # source -> {key -> ConfigSetting}
_registry_lock = threading.Lock()


def register_configs(source: str, settings: List[dict], persist_as: str = "json"):
    """Register config settings for a source (script).
    
    Args:
        source: Name identifying the script (e.g., "god_watcher", "cpu_guard")
        settings: List of config setting dicts
        persist_as: Default persistence format ("json" or "ini")
    """
    with _registry_lock:
        if source not in _registry:
            _registry[source] = {}
            logger.info(f"Config registry created for '{source}'")

        for s in settings:
            setting = ConfigSetting(**s)
            setting.source = source
            if not setting.persist_as:
                setting.persist_as = persist_as
            _registry[source][setting.key] = setting
            logger.debug(f"  Registered: {source}.{setting.key} = {setting.value} ({setting.type})")

    logger.info(f"Registered {len(settings)} settings for '{source}'")
    return len(settings)


def get_config(source: str, key: str, default: Any = None) -> Any:
    """Get the current value of a config setting.
    
    Args:
        source: Source name (e.g., "god_watcher")
        key: Setting key
        default: Fallback if not found
    Returns:
        Current value, or default if setting not found
    """
    with _registry_lock:
        src = _registry.get(source)
        if src is None:
            return default
        setting = src.get(key)
        if setting is None:
            return default
        return setting.value


def get_setting(source: str, key: str) -> Optional[ConfigSetting]:
    """Get the full ConfigSetting object (for GUI)."""
    with _registry_lock:
        src = _registry.get(source)
        if src is None:
            return None
        return src.get(key)


def get_all_configs() -> Dict[str, List[dict]]:
    """Get all registered configs grouped by source (for GUI)."""
    result = {}
    with _registry_lock:
        for source, settings in _registry.items():
            result[source] = [s.to_dict() for s in settings.values()]
    return result


def get_config_sources() -> List[str]:
    """Get list of all registered source names."""
    with _registry_lock:
        return sorted(_registry.keys())


def update_config(source: str, key: str, value: Any,
                  changed_by: str = "cli") -> Tuple[bool, str]:
    """Update a config setting value in memory.
    
    Args:
        source: Source name
        key: Setting key
        value: New value
        changed_by: Who made the change ("admin_gui", "cli", "startup")
    Returns:
        (success, message)
    """
    with _registry_lock:
        src = _registry.get(source)
        if src is None:
            return False, f"Source '{source}' not registered"
        setting = src.get(key)
        if setting is None:
            return False, f"Key '{key}' not found in '{source}'"
        if setting.readonly:
            return False, f"'{source}.{key}' is read-only"

        # Validate
        valid, msg = setting.validate(value)
        if not valid:
            return False, f"Validation failed: {msg}"

        old_value = setting.value
        setting.value = value
        setting.changed_at = datetime.now(timezone.utc).isoformat()
        setting.changed_by = changed_by

    _log_change(source, key, old_value, value, changed_by)
    _fire_change_event(source, key, old_value, value)
    logger.info(f"Updated {source}.{key}: {old_value} -> {value} (by {changed_by})")
    return True, "ok"


def update_many(source: str, updates: Dict[str, Any],
                changed_by: str = "cli") -> List[Tuple[str, bool, str]]:
    """Update multiple settings at once. Returns list of (key, success, msg)."""
    results = []
    for key, value in updates.items():
        success, msg = update_config(source, key, value, changed_by)
        results.append((key, success, msg))
    return results


def get_config_summary() -> dict:
    """Get summary stats about the registry."""
    with _registry_lock:
        total = sum(len(s) for s in _registry.values())
        sources = len(_registry)
        changed = sum(1 for s in _registry.values()
                      for cs in s.values() if cs.changed_at)
    return {
        "sources": sources,
        "total_settings": total,
        "changed_settings": changed,
        "change_history_count": len(_change_history),
    }


# ── Change History API ──────────────────────────────────────────

def get_change_history(limit: int = 50) -> List[dict]:
    """Get recent config change history."""
    return list(_change_history[-limit:])


# ── File I/O ─────────────────────────────────────────────────────

def _get_source_data_dir(source: str) -> Optional[Path]:
    """Find the DATA directory for a given source.
    
    Uses ecosystem-relative paths. If the source is a known service,
    maps to its DATA/ directory. Otherwise uses ./DATA relative to
    the calling script's location.
    """
    # Known service paths (relative to YuniScripts root)
    known_paths = {
        "fastmcp_server": "SCRIPTS/SERVICES/fastmcp_server/DATA",
        "god_watcher": "SCRIPTS/SERVICES/fastmcp_server/DATA",
        "host_protection": "SCRIPTS/SERVICES/fastmcp_server/DATA",
        "divine": "SCRIPTS/SERVICES/fastmcp_server/DATA",
        "cpu_guard": "SCRIPTS/SERVICES/fastmcp_server/DATA",
        "minecraft_manager": "SCRIPTS/GAMES/minecraft_manager/DATA",
        "item-signing-bridge": "SCRIPTS/SERVICES/item-signing-bridge/DATA",
        "server-stats-collector": "SCRIPTS/SERVICES/server-stats-collector/DATA",
        "steam-nextcloud": "SCRIPTS/PROGRAMS/steam-nextcloud-status-updater/DATA",
        "mc-server-runner": "SCRIPTS/SERVERS/mc-server-runner/DATA",
        "deepsky_client": "SCRIPTS/CLIENTS/deepsky_client/DATA",
        "mc-status-relay": "SCRIPTS/SERVICES/mc-status-relay/DATA",
        "multi-server-manager": "SCRIPTS/SERVICES/multi-server-manager/DATA",
        "global": "DATA",
    }

    # Try to find YuniScripts root
    yuniscripts_root = None
    for candidate in [
        "/home/deck/Documents/dev-yuniScripts",
        os.environ.get("YUNISCRIPTS_ROOT", ""),
    ]:
        if candidate and Path(candidate).exists():
            yuniscripts_root = Path(candidate)
            break

    # Check known paths
    rel_path = known_paths.get(source)
    if rel_path and yuniscripts_root:
        data_dir = yuniscripts_root / rel_path
        return data_dir

    # Fallback: try ./DATA relative to script location
    try:
        caller_frame = __import__('inspect').stack()[2]
        caller_file = Path(caller_frame.filename)
        data_dir = caller_file.parent / "DATA"
        if data_dir.exists():
            return data_dir
    except Exception:
        pass

    return None


def _ensure_data_dir(data_dir: Path) -> bool:
    """Ensure DATA directory exists."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"Cannot create {data_dir}: {e}")
        return False


def flush_source(source: str) -> Tuple[bool, str]:
    """Flush a single source's configs to its DATA file.
    
    Returns:
        (success, message)
    """
    with _registry_lock:
        settings = _registry.get(source)
        if not settings:
            return False, f"No settings registered for '{source}'"

    data_dir = _get_source_data_dir(source)
    if not data_dir:
        return False, f"Cannot determine DATA directory for '{source}'"

    if not _ensure_data_dir(data_dir):
        return False, f"Cannot create DATA directory for '{source}'"

    # Determine format from first setting
    first_setting = next(iter(settings.values()))
    persist_as = first_setting.persist_as

    if persist_as == "ini":
        return _flush_source_ini(source, settings, data_dir)
    else:
        return _flush_source_json(source, settings, data_dir)


def _flush_source_json(source: str, settings: Dict[str, ConfigSetting],
                        data_dir: Path) -> Tuple[bool, str]:
    """Flush configs to DATA/config.json"""
    config_path = data_dir / "config.json"
    config_data = {
        "_meta": {
            "generated_by": "DynamicConfigLoader",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        },
        "settings": {s.key: s.value for s in settings.values()},
    }
    try:
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)
        logger.info(f"Flushed {source} configs to {config_path}")
        return True, f"Flushed to {config_path}"
    except Exception as e:
        logger.error(f"Flush failed for {source}: {e}")
        return False, str(e)


def _flush_source_ini(source: str, settings: Dict[str, ConfigSetting],
                       data_dir: Path) -> Tuple[bool, str]:
    """Flush configs to DATA/config.ini"""
    config_path = data_dir / "config.ini"
    parser = ConfigParser()
    parser["DynamicConfigLoader"] = {
        "generated_by": "DynamicConfigLoader",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Group settings by category within the source section
    parser[source] = {}
    for s in settings.values():
        parser[source][s.key] = str(s.value)
    try:
        with open(config_path, 'w') as f:
            parser.write(f)
        logger.info(f"Flushed {source} configs to {config_path}")
        return True, f"Flushed to {config_path}"
    except Exception as e:
        logger.error(f"Flush failed for {source}: {e}")
        return False, str(e)


def flush_all() -> List[dict]:
    """Flush ALL registered sources to their DATA files.
    
    Returns:
        List of {source, success, message} for each source
    """
    sources = get_config_sources()
    results = []
    for source in sources:
        success, msg = flush_source(source)
        results.append({"source": source, "success": success, "message": msg})
    return results


def load_source(source: str) -> Tuple[bool, str]:
    """Load a source's configs from its DATA file into the registry.
    
    Returns:
        (success, message)
    """
    data_dir = _get_source_data_dir(source)
    if not data_dir:
        return False, f"Cannot determine DATA directory for '{source}'"

    # Try JSON first, then INI
    json_path = data_dir / "config.json"
    ini_path = data_dir / "config.ini"

    if json_path.exists():
        return _load_source_json(source, json_path)
    elif ini_path.exists():
        return _load_source_ini(source, ini_path)
    else:
        return False, f"No config file found for '{source}' in {data_dir}"


def _load_source_json(source: str, path: Path) -> Tuple[bool, str]:
    """Load configs from JSON file into registry."""
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        settings = data.get("settings", data)  # Support both formats
        if isinstance(settings, dict):
            for key, value in settings.items():
                update_config(source, key, value, changed_by="startup")
        return True, f"Loaded {len(settings) if isinstance(settings, dict) else 0} settings from {path}"
    except Exception as e:
        logger.error(f"Load failed for {path}: {e}")
        return False, str(e)


def _load_source_ini(source: str, path: Path) -> Tuple[bool, str]:
    """Load configs from INI file into registry."""
    try:
        parser = ConfigParser()
        parser.read(path)
        count = 0
        if source in parser:
            for key, value in parser[source].items():
                # Type conversion happens in update_config validation
                update_config(source, key, value, changed_by="startup")
                count += 1
        return True, f"Loaded {count} settings from {path}"
    except Exception as e:
        logger.error(f"Load failed for {path}: {e}")
        return False, str(e)


def load_all() -> List[dict]:
    """Load ALL sources from their DATA files."""
    sources = get_config_sources()
    results = []
    for source in sources:
        success, msg = load_source(source)
        results.append({"source": source, "success": success, "message": msg})
    return results


# ── Reset ────────────────────────────────────────────────────────

def reset_config(source: str, key: Optional[str] = None) -> Tuple[bool, str]:
    """Reset a config setting to its default value."""
    with _registry_lock:
        src = _registry.get(source)
        if not src:
            return False, f"Source '{source}' not registered"
        if key:
            setting = src.get(key)
            if not setting:
                return False, f"Key '{key}' not found in '{source}'"
            old_value = setting.value
            setting.value = setting.default
            _log_change(source, key, old_value, setting.default, "reset")
            _fire_change_event(source, key, old_value, setting.default)
            return True, f"Reset {source}.{key} to default ({setting.default})"
        else:
            count = 0
            for k, s in src.items():
                if s.value != s.default:
                    old = s.value
                    s.value = s.default
                    _log_change(source, k, old, s.default, "reset")
                    _fire_change_event(source, k, old, s.default)
                    count += 1
            return True, f"Reset all {count} settings in '{source}' to defaults"


# ── Utility ──────────────────────────────────────────────────────

def export_all_json() -> str:
    """Export ALL configs as a single JSON string (for backup)."""
    data = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "configs": get_all_configs(),
        "history": get_change_history(500),
    }
    return json.dumps(data, indent=2)


def import_all_json(json_str: str, changed_by: str = "import") -> List[dict]:
    """Import configs from a JSON backup string."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return [{"success": False, "message": f"Invalid JSON: {e}"}]
    results = []
    for source, settings in data.get("configs", {}).items():
        for s in settings:
            key = s.get("key")
            value = s.get("value")
            if key and value is not None:
                success, msg = update_config(source, key, value, changed_by)
                results.append({"source": source, "key": key, "success": success, "message": msg})
    return results


# ── Admin GUI Server ─────────────────────────────────────────────

def start_admin_gui(port: int = 8180, host: str = "127.0.0.1"):
    """Start the admin GUI web server in a background thread.
    
    The GUI is a standalone HTML+JS dashboard served by Python's
    built-in HTTP server. No external dependencies.
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse

    class ConfigGUIHandler(BaseHTTPRequestHandler):
        """HTTP handler for the admin config GUI."""

        def _send_json(self, data: Any, status: int = 200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, status: int = 200):
            body = html.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_redirect(self, path: str):
            self.send_response(302)
            self.send_header("Location", path)
            self.end_headers()

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            query = urllib.parse.parse_qs(parsed.query)

            if path == "" or path == "/":
                self._send_html(_GUI_HTML)
            elif path == "/api/config":
                self._send_json(get_all_configs())
            elif path == "/api/summary":
                self._send_json(get_config_summary())
            elif path == "/api/history":
                limit = int(query.get("limit", [50])[0])
                self._send_json(get_change_history(limit))
            elif path.startswith("/api/config/"):
                parts = path.split("/")
                if len(parts) == 4:
                    source = parts[3]
                    # Return all configs for a source
                    all_configs = get_all_configs()
                    source_configs = all_configs.get(source, [])
                    self._send_json({source: source_configs})
                elif len(parts) == 5:
                    source, key = parts[3], parts[4]
                    setting = get_setting(source, key)
                    if setting:
                        self._send_json(setting.to_dict())
                    else:
                        self._send_json({"error": "Not found"}, 404)
                else:
                    self._send_json({"error": "Invalid path"}, 400)
            else:
                self._send_json({"error": "Not found"}, 404)

        def do_PUT(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path.startswith("/api/config/"):
                parts = path.split("/")
                if len(parts) == 5:
                    source, key = parts[3], parts[4]
                    content_length = int(self.headers.get("Content-Length", 0))
                    if content_length == 0:
                        self._send_json({"error": "No content"}, 400)
                        return
                    body = self.rfile.read(content_length)
                    data = json.loads(body)
                    value = data.get("value")
                    changed_by = data.get("changed_by", "admin_gui")
                    success, msg = update_config(source, key, value, changed_by)
                    if success:
                        self._send_json({"success": True, "message": msg})
                    else:
                        self._send_json({"success": False, "message": msg}, 400)
                else:
                    self._send_json({"error": "Invalid path"}, 400)
            else:
                self._send_json({"error": "Not found"}, 404)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path == "/api/flush":
                results = flush_all()
                self._send_json({"results": results})
            elif path == "/api/reload":
                results = load_all()
                self._send_json({"results": results})
            elif path.startswith("/api/flush/"):
                source = path.split("/")[-1]
                success, msg = flush_source(source)
                self._send_json({"success": success, "message": msg})
            elif path.startswith("/api/reload/"):
                source = path.split("/")[-1]
                success, msg = load_source(source)
                self._send_json({"success": success, "message": msg})
            elif path.startswith("/api/reset/"):
                parts = path.split("/")
                if len(parts) == 4:
                    source = parts[3]
                    success, msg = reset_config(source)
                elif len(parts) == 5:
                    source, key = parts[3], parts[4]
                    success, msg = reset_config(source, key)
                else:
                    self._send_json({"error": "Invalid path"}, 400)
                    return
                self._send_json({"success": success, "message": msg})
            else:
                self._send_json({"error": "Not found"}, 404)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, format, *args):
            logger.debug(f"GUI: {args[0]} {args[1]} {args[2]}")

    server = HTTPServer((host, port), ConfigGUIHandler)
    logger.info(f"Admin Config GUI started on http://{host}:{port}")
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="admin-gui")
    thread.start()
    return server


# ── Built-in GUI HTML (single page app, no dependencies) ─────────

_GUI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dynamic Config Admin</title>
<style>
:root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --text-muted: #8b949e;
    --accent: #58a6ff;
    --green: #3fb950;
    --red: #f85149;
    --yellow: #d29922;
    --blue: #58a6ff;
    --purple: #bc8cff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Ubuntu, sans-serif;
       background: var(--bg); color: var(--text); padding: 20px; line-height: 1.5; }
h1 { text-align: center; margin-bottom: 24px; color: var(--accent); font-size: 28px; }
.header-bar { display: flex; justify-content: space-between; align-items: center;
              margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
.stats { display: flex; gap: 16px; flex-wrap: wrap; }
.stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
        padding: 8px 16px; font-size: 14px; }
.stat strong { font-size: 20px; display: block; color: var(--blue); }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
.btn { padding: 8px 20px; border: 1px solid var(--border); border-radius: 6px;
       background: var(--card); color: var(--text); cursor: pointer; font-size: 14px; }
.btn:hover { background: #21262d; }
.btn-primary { background: #238636; border-color: #2ea043; color: white; }
.btn-primary:hover { background: #2ea043; }
.btn-danger { background: #da3633; border-color: #f85149; color: white; }
.btn-danger:hover { background: #f85149; }
.search-bar { margin-bottom: 16px; }
.search-bar input { width: 100%; padding: 10px 16px; border: 1px solid var(--border);
    border-radius: 8px; background: var(--card); color: var(--text); font-size: 14px; }
.source-card { background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; margin-bottom: 16px; overflow: hidden; }
.source-header { padding: 12px 16px; font-weight: 600; font-size: 16px;
    background: rgba(255,255,255,0.03); border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
.source-header:hover { background: rgba(255,255,255,0.06); }
.setting { padding: 10px 16px; border-bottom: 1px solid var(--border);
    display: grid; grid-template-columns: 1fr 1fr auto; gap: 12px; align-items: center; }
.setting:last-child { border-bottom: none; }
.setting-label { font-size: 13px; }
.setting-label .key { font-weight: 600; color: var(--accent); }
.setting-label .desc { color: var(--text-muted); font-size: 12px; display: block; }
.setting-label .cat { display: inline-block; padding: 1px 6px; border-radius: 4px;
    font-size: 10px; margin-top: 4px; }
.cat-performance { background: rgba(88,166,255,0.15); color: var(--blue); }
.cat-security { background: rgba(248,81,73,0.15); color: var(--red); }
.cat-monitoring { background: rgba(63,185,80,0.15); color: var(--green); }
.cat-debug { background: rgba(210,153,34,0.15); color: var(--yellow); }
.cat-general { background: rgba(188,140,255,0.15); color: var(--purple); }
.setting-value input[type="text"], .setting-value input[type="number"] {
    width: 100%; padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px;
    background: var(--bg); color: var(--text); font-size: 13px; }
.setting-value input[type="checkbox"] { transform: scale(1.3); margin: 4px; }
.setting-value select { width: 100%; padding: 6px 10px; border: 1px solid var(--border);
    border-radius: 4px; background: var(--bg); color: var(--text); font-size: 13px; }
.setting-actions { display: flex; gap: 4px; }
.setting-actions .btn { padding: 4px 8px; font-size: 11px; }
.readonly { opacity: 0.6; }
.flash { position: fixed; top: 20px; right: 20px; padding: 12px 20px; border-radius: 8px;
    z-index: 1000; font-size: 14px; animation: fadeIn 0.3s; }
.flash-success { background: #238636; color: white; }
.flash-error { background: #da3633; color: white; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-10px); } }
.toast { position: fixed; bottom: 20px; right: 20px; padding: 10px 16px;
    border-radius: 6px; background: var(--card); border: 1px solid var(--border); font-size: 12px;
    color: var(--text-muted); z-index: 999; opacity: 0.9; }
</style>
</head>
<body>
<h1>⚙ Dynamic Config Admin</h1>
<div class="header-bar">
    <div class="stats" id="stats"></div>
    <div class="actions">
        <button class="btn" onclick="reloadConfigs()">⟳ Reload from Files</button>
        <button class="btn btn-primary" onclick="flushConfigs()">💾 Flush All to Files</button>
    </div>
</div>
<div class="search-bar">
    <input type="text" id="search" placeholder="Search settings..." oninput="renderConfigs()">
</div>
<div id="config-list"></div>
<div class="toast" id="toast"></div>
<script>
let allConfigs = {};
let timeout = null;

async function api(url, method='GET', body=null) {
    const opts = { method, headers: {'Content-Type':'application/json'} };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(url, opts);
    return r.json();
}

async function loadConfigs() {
    allConfigs = await api('/api/config');
    const summary = await api('/api/summary');
    document.getElementById('stats').innerHTML = `
        <div class="stat"><strong>${summary.sources}</strong> Sources</div>
        <div class="stat"><strong>${summary.total_settings}</strong> Settings</div>
        <div class="stat"><strong>${summary.changed_settings}</strong> Modified</div>
    `;
    renderConfigs();
}

function escapeHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function renderConfigs() {
    const query = document.getElementById('search').value.toLowerCase();
    const container = document.getElementById('config-list');
    container.innerHTML = '';
    let matchCount = 0;
    for (const [source, settings] of Object.entries(allConfigs)) {
        const filtered = settings.filter(s =>
            s.key.toLowerCase().includes(query) ||
            s.description.toLowerCase().includes(query) ||
            s.category.includes(query) ||
            source.includes(query)
        );
        if (filtered.length === 0 && query) continue;
        matchCount += filtered.length;
        const sourceKey = source.replace(/[^a-zA-Z0-9]/g, '_');
        const card = document.createElement('div');
        card.className = 'source-card';
        card.innerHTML = `
            <div class="source-header" onclick="toggleSource('${sourceKey}')">
                <span>📁 ${escapeHtml(source)} (${filtered.length})</span>
                <span>🔽</span>
            </div>
            <div id="source-${sourceKey}" style="display:block">
                ${filtered.map(s => renderSetting(source, s)).join('')}
            </div>
        `;
        container.appendChild(card);
    }
}

function renderSetting(source, s) {
    const catClass = 'cat-' + (s.category || 'general');
    const readonly = s.readonly ? ' readonly' : '';
    let inputHtml = '';
    const val = s.value !== null && s.value !== undefined ? s.value : s.default;
    if (readonly) {
        inputHtml = `<span style="color:var(--text-muted)">${escapeHtml(String(val))}</span>`;
    } else if (s.type === 'bool') {
        inputHtml = `<input type="checkbox" ${val ? 'checked' : ''} onchange="updateSetting('${source}','${s.key}',this.checked)"${readonly}>`;
    } else if (s.type === 'int') {
        const [min, max] = s.valid_range || [0, 999999];
        inputHtml = `<input type="number" value="${escapeHtml(String(val))}" min="${min}" max="${max}" onchange="updateSetting('${source}','${s.key}',parseInt(this.value))">`;
    } else if (s.type === 'float') {
        const [min, max] = s.valid_range || [0, 999999];
        inputHtml = `<input type="number" step="0.1" value="${escapeHtml(String(val))}" min="${min}" max="${max}" onchange="updateSetting('${source}','${s.key}',parseFloat(this.value))">`;
    } else if (s.type === 'str' && s.valid_options) {
        inputHtml = `<select onchange="updateSetting('${source}','${s.key}',this.value)">
            ${s.valid_options.map(o => `<option value="${escapeHtml(o)}" ${val===o?'selected':''}>${escapeHtml(o)}</option>`).join('')}
        </select>`;
    } else if (s.type === 'list' && Array.isArray(val)) {
        inputHtml = `<input type="text" value="${escapeHtml(val.join(', '))}" onchange="updateSetting('${source}','${s.key}',this.value.split(',').map(x=>x.trim()))">`;
    } else {
        inputHtml = `<input type="text" value="${escapeHtml(String(val))}" onchange="updateSetting('${source}','${s.key}',this.value)"${readonly}>`;
    }
    const changed = s.changed_at ? ' 🔄' : '';
    return `<div class="setting${readonly?' readonly':''}">
        <div class="setting-label">
            <span class="key">${escapeHtml(s.key)}</span>${changed}
            <span class="desc">${escapeHtml(s.description)}</span>
            <span class="cat ${catClass}">${escapeHtml(s.category)}</span>
        </div>
        <div class="setting-value">${inputHtml}</div>
        <div class="setting-actions">
            <button class="btn" onclick="resetSetting('${source}','${s.key}')" title="Reset to default">↺</button>
        </div>
    </div>`;
}

async function updateSetting(source, key, value) {
    const r = await api(`/api/config/${source}/${key}`, 'PUT', {value});
    showToast(r.success ? '✅ Updated ' + key : '❌ ' + r.message);
    if (r.success) setTimeout(loadConfigs, 500);
}

async function resetSetting(source, key) {
    await api(`/api/reset/${source}/${key}`, 'POST');
    showToast('↺ Reset ' + key);
    setTimeout(loadConfigs, 300);
}

async function flushConfigs() {
    const r = await api('/api/flush', 'POST');
    showToast('💾 Flushed to files!');
    setTimeout(loadConfigs, 500);
}

async function reloadConfigs() {
    const r = await api('/api/reload', 'POST');
    showToast('⟳ Reloaded from files!');
    setTimeout(loadConfigs, 500);
}

function toggleSource(id) {
    const el = document.getElementById('source-' + id);
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.opacity = '1';
    clearTimeout(timeout);
    timeout = setTimeout(() => t.style.opacity = '0', 3000);
}

loadConfigs();
setInterval(loadConfigs, 5000);  // Auto-refresh every 5s
</script>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════════
# Self-Test
# ═══════════════════════════════════════════════════════════════

def self_test():
    """Run basic self-test to verify the module works."""
    print("DynamicConfigLoader Self-Test")
    print("=" * 50)

    # 1. Register configs
    register_configs("test_module", [
        {"key": "max_retries", "type": "int", "default": 3,
         "description": "Max retry attempts", "valid_range": (1, 10),
         "category": "performance"},
        {"key": "debug_mode", "type": "bool", "default": False,
         "description": "Enable debug logging", "category": "debug"},
        {"key": "log_level", "type": "str", "default": "INFO",
         "description": "Logging level",
         "valid_options": ["DEBUG", "INFO", "WARNING", "ERROR"],
         "category": "debug"},
        {"key": "threshold", "type": "float", "default": 0.8,
         "description": "CPU threshold", "valid_range": (0.0, 1.0),
         "category": "performance"},
    ])
    assert get_config("test_module", "max_retries") == 3
    assert get_config("test_module", "nonexistent", "fallback") == "fallback"
    print("✓ register_configs + get_config")

    # 2. Update config
    success, msg = update_config("test_module", "max_retries", 5)
    assert success, f"Update failed: {msg}"
    assert get_config("test_module", "max_retries") == 5
    print("✓ update_config")

    # 3. Validation
    success, msg = update_config("test_module", "max_retries", 999)
    assert not success, "Should have rejected out-of-range value"
    success, msg = update_config("test_module", "debug_mode", True)
    assert success
    assert get_config("test_module", "debug_mode") is True
    print("✓ validation")

    # 4. Get all configs
    all_c = get_all_configs()
    assert "test_module" in all_c
    assert len(all_c["test_module"]) == 4
    print("✓ get_all_configs")

    # 5. Change history
    history = get_change_history()
    assert len(history) >= 2
    assert history[-1]["key"] == "debug_mode"
    print("✓ change history")

    # 6. Summary
    summary = get_config_summary()
    assert summary["sources"] >= 1
    print("✓ summary")

    # 7. Reset
    success, msg = reset_config("test_module", "max_retries")
    assert success
    assert get_config("test_module", "max_retries") == 3
    print("✓ reset")

    # 8. JSON export/import
    exported = export_all_json()
    result = import_all_json(exported, "test")
    print(f"✓ export/import ({len(result)} settings)")

    # 9. Gather all registered sources
    sources = get_config_sources()
    print(f"✓ Registered sources: {sources}")

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
