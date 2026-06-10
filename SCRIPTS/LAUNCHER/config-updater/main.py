#!/usr/bin/env python3
"""
Config Updater – validates and applies system configuration changes.

This script is a placeholder/reference implementation for the YuniScripts engine.
It demonstrates how to read config from DATA/, validate it, and apply changes.

Customize the VALIDATORS and APPLIERS dictionaries to fit your needs.
"""

import json
import sys
import os
import time
from pathlib import Path
from configparser import ConfigParser

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from engine.gui_api_client import GuiApiClient

# ── GUI wire-up ──────────────────────────────────────────────────
gui = GuiApiClient('LAUNCHER/config-updater', 'Config Updater')
gui.register_tab([
    {'type': 'label', 'id': 'status', 'title': 'Status', 'default': 'Unknown'},
    {'type': 'label', 'id': 'last_sync', 'title': 'Last Sync', 'default': 'Never'},
    {'type': 'label', 'id': 'config_path', 'title': 'Config Path', 'default': ''},
    {'type': 'label', 'id': 'valid_keys', 'title': 'Valid Keys', 'default': '0'},
    {'type': 'label', 'id': 'errors', 'title': 'Errors', 'default': 'None'},
    {'type': 'message', 'id': 'activity', 'title': 'Activity', 'default': ''},
])

_start_time = time.time()
_config_status = {"state": "Unknown", "errors": [], "valid_count": 0, "cfg_path": ""}
_activity_log = []

def _append_activity(msg):
    ts = time.strftime("%H:%M:%S")
    _activity_log.append(f"[{ts}] {msg}")
    if len(_activity_log) > 100:
        _activity_log.pop(0)

def _get_gui_data():
    uptime_str = f"{int(time.time() - _start_time)}s"
    return {
        'status': _config_status.get("state", "Unknown"),
        'last_sync': uptime_str,
        'config_path': _config_status.get("cfg_path", ""),
        'valid_keys': str(_config_status.get("valid_count", 0)),
        'errors': str(_config_status.get("errors", ["None"])[0]) if _config_status.get("errors") else "None",
        'activity': "\n".join(_activity_log[-25:]),
    }

from engine.config_loader import get_config_path, load_config

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "DATA"
CONFIG_FILE = get_config_path("config_updater")

# ---- Validation rules ----
# Each entry maps a config section+key to a validation function.
# The function receives the raw string value and returns (is_valid, normalized_value_or_error).
VALIDATORS = {}


def register_validator(section, key, func):
    """Register a validator for a config key."""
    if section not in VALIDATORS:
        VALIDATORS[section] = {}
    VALIDATORS[section][key] = func


def validate_port(value):
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return True, port
        return False, "Port must be between 1 and 65535"
    except ValueError:
        return False, f"Not a valid port number: {value}"


def validate_non_empty(value):
    if value.strip():
        return True, value.strip()
    return False, "Value must not be empty"


def validate_boolean(value):
    low = value.strip().lower()
    if low in ("true", "yes", "1", "on"):
        return True, True
    if low in ("false", "no", "0", "off"):
        return True, False
    return False, f"Not a boolean: {value}"


# Register example validators
register_validator("server", "port", validate_port)
register_validator("server", "host", validate_non_empty)
register_validator("logging", "enabled", validate_boolean)


def load_config():
    """Load and parse the INI config file."""
    if not CONFIG_FILE.exists():
        print(f"Config file not found: {CONFIG_FILE}")
        return None
    config = ConfigParser()
    config.read(CONFIG_FILE)
    return config


def validate_config(config):
    """Run all registered validators against the config."""
    errors = []
    normalized = {}

    for section in config.sections():
        normalized[section] = {}
        for key, raw_value in config.items(section):
            if section in VALIDATORS and key in VALIDATORS[section]:
                is_valid, result = VALIDATORS[section][key](raw_value)
                if is_valid:
                    normalized[section][key] = result
                else:
                    errors.append(f"[{section}] {key}: {result}")
            else:
                # No validator registered – pass through as string
                normalized[section][key] = raw_value.strip()

    return normalized, errors


def apply_config(normalized):
    """Apply the validated configuration changes.

    Override this function with your actual config application logic.
    """
    print("Applying configuration changes...")
    for section, keys in normalized.items():
        print(f"  [{section}]")
        for key, value in keys.items():
            print(f"    {key} = {value}")
    print("Configuration applied successfully.")


def main():
    global _config_status
    _config_status["cfg_path"] = str(CONFIG_FILE)
    _append_activity("Config Updater starting...")

    gui.on_data_request(_get_gui_data)

    config = load_config()
    if config is None:
        _config_status["state"] = "No Config"
        _append_activity("No configuration file found — exiting.")
        print("No configuration to apply. Exiting.")
        gui.close()
        sys.exit(0)

    _config_status["state"] = "Validating"
    _append_activity(f"Loaded config from {CONFIG_FILE}")
    normalized, errors = validate_config(config)

    if errors:
        _config_status["state"] = "Validation Failed"
        _config_status["errors"] = errors
        _config_status["valid_count"] = len(normalized)
        for err in errors:
            _append_activity(f"ERROR: {err}")
        print("Validation errors:")
        for err in errors:
            print(f"  - {err}")
        print("Config not applied.")
        gui.close()
        sys.exit(1)

    _config_status["state"] = "Applied"
    _config_status["valid_count"] = len(normalized)
    _config_status["errors"] = []
    _append_activity(f"Validation passed — {len(normalized)} section(s) OK")
    _append_activity("Applying configuration...")

    print("Validation passed.")
    apply_config(normalized)
    _append_activity("Configuration applied successfully.")
    print("Done.")
    gui.close()
    print("SHUTDOWN_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
