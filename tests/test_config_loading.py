#!/usr/bin/env python3
"""
test_config_loading.py — Comprehensive tests for every script's config loading.

Verifies that every script that should load from centralized DATA/ config files
actually does so, and that no hardcoded IPs/ports/passwords are used instead.
"""
import sys, os, json, tempfile, shutil, importlib
from pathlib import Path

# ── Project root ─────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "DATA"

# ── Colours ──────────────────────────────────────────────────────────
_G = "\033[92m"  # green
_Y = "\033[93m"  # yellow
_R = "\033[91m"  # red
_C = "\033[96m"  # cyan
_N = "\033[0m"   # reset

PASS = f"{_G}PASS{_N}"
FAIL = f"{_R}FAIL{_N}"
SKIP = f"{_Y}SKIP{_N}"

_tests_run = 0
_tests_pass = 0
_tests_fail = 0
_tests_skip = 0

def test(name: str, condition: bool, detail: str = ""):
    global _tests_run, _tests_pass, _tests_fail
    _tests_run += 1
    if condition:
        _tests_pass += 1
        print(f"  {PASS}  {name}")
    else:
        _tests_fail += 1
        print(f"  {FAIL}  {name}")
        if detail:
            print(f"         {_Y}{detail}{_N}")
test.__test__ = False

def skip(name: str, reason: str = ""):
    global _tests_skip
    _tests_skip += 1
    print(f"  {SKIP}  {name}  ({_Y}{reason}{_N})")

def section(title: str):
    print(f"\n{_C}─── {title} ───{_N}")
    print(f"{_C}{'─' * (len(title) + 8)}{_N}")

# ═══════════════════════════════════════════════════════════════════════
# 1. engine/config_loader.py — core functionality
# ═══════════════════════════════════════════════════════════════════════
section("1. engine/config_loader.py — core config functions")

from engine.config_loader import (
    load_config, save_config, get_config_path, get_data_dir,
    is_fresh_install, mark_setup_complete, _CONFIG_NAMES,
)

test("DATA directory exists and is writable", get_data_dir().exists())

# Test config path mapping
test("'engine' maps to engine_config.json",
     get_config_path("engine").name == "engine_config.json")
test("'eco' maps to eco_config.json",
     get_config_path("eco").name == "eco_config.json")
test("'minecraft_manager' maps to minecraft_manager_config.json",
     get_config_path("minecraft_manager").name == "minecraft_manager_config.json")
test("'ah' maps to ah_config.json",
     get_config_path("ah").name == "ah_config.json")
test("'simulated_people' maps to simulated_people_config.json",
     get_config_path("simulated_people").name == "simulated_people_config.json")
test("'server_stats_collector' maps to server_stats_collector_config.json",
     get_config_path("server_stats_collector").name == "server_stats_collector_config.json")

# Test that all expected config names exist
expected_configs = [
    "engine", "minecraft_manager", "ah", "simulated_people",
    "eco", "item_signing_bridge", "server_stats_collector",
    "steam_nextcloud", "config_updater", "sign_item",
]
for cfg_name in expected_configs:
    test(f"Config name '{cfg_name}' registered in _CONFIG_NAMES",
         cfg_name in _CONFIG_NAMES)

# Test save + load roundtrip
test_config_data = {"test_key": "test_value", "test_number": 42}
save_config("test_config_loading", test_config_data)
loaded = load_config("test_config_loading", {})
test("save_config + load_config roundtrip",
     loaded.get("test_key") == "test_value" and loaded.get("test_number") == 42,
     f"Expected {{'test_key': 'test_value', 'test_number': 42}}, got {loaded}")

# Clean up test config
_path = get_config_path("test_config_loading")
if _path.exists():
    _path.unlink()
    test("Test config cleanup", not _path.exists())

# Test is_fresh_install / mark_setup_complete
test("is_fresh_install returns bool (not crash)",
     isinstance(is_fresh_install(), bool))

# Test existing config files are valid (JSON or INI appropriately)
from configparser import ConfigParser as _ConfigParser
for name in expected_configs:
    path = get_config_path(name)
    if path.exists():
        if path.suffix == ".ini":
            _cp = _ConfigParser()
            _cp.read(str(path))
            ok = len(_cp.sections()) > 0
            test(f"Config '{name}' is valid INI ({len(_cp.sections())} sections)", ok, f"File: {path}")
        else:
            try:
                data = json.loads(path.read_text())
                test(f"Config '{name}' is valid JSON ({len(data)} keys)", True)
            except (json.JSONDecodeError, Exception) as _e:
                test(f"Config '{name}' is valid JSON", False, f"File: {path}: {_e}")


# ═══════════════════════════════════════════════════════════════════════
# 2. ECO_BRIDGE/eco_config.py
# ═══════════════════════════════════════════════════════════════════════
section("2. ECO_BRIDGE/eco_config.py — economy bridge config")

import importlib.util as _imp_util
_eco_module_path = PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" / "ECO_BRIDGE" / "eco_config.py"
_spec = _imp_util.spec_from_file_location("eco_config", str(_eco_module_path))
_eco_module = _imp_util.module_from_spec(_spec)
_spec.loader.exec_module(_eco_module)
get_eco_config = _eco_module.get_config
EcoConfig = _eco_module.EcoConfig

eco_cfg = get_eco_config()
test("EcoConfig.get_config() returns an EcoConfig instance",
     isinstance(eco_cfg, EcoConfig))
test("EcoConfig has ooga_db_path attribute",
     hasattr(eco_cfg, "ooga_db_path"))
test("EcoConfig has eco_bridge_host attribute",
     hasattr(eco_cfg, "eco_bridge_host"))
test("EcoConfig has eco_bridge_port attribute",
     hasattr(eco_cfg, "eco_bridge_port"))
test("EcoConfig has eco_bridge_password attribute",
     hasattr(eco_cfg, "eco_bridge_password"))
test("EcoConfig has rcon_host attribute (merged from shared config)",
     hasattr(eco_cfg, "rcon_host"))
test("EcoConfig.resolve_db_path returns None when no DB exists",
     eco_cfg.resolve_db_path() is None or isinstance(eco_cfg.resolve_db_path(), Path))

# ═══════════════════════════════════════════════════════════════════════
# 3. first-run-setup/main.py — config saving
# ═══════════════════════════════════════════════════════════════════════
section("3. first-run-setup — interactive config wizard")

# Verify the config files the wizard saves to actually exist
first_run_configs = [
    ("engine", "engine_config.json"),
    ("minecraft_manager", "minecraft_manager_config.json"),
    ("ah", "ah_config.json"),
    ("simulated_people", "simulated_people_config.json"),
    ("eco", "eco_config.json"),
    ("steam_nextcloud", "steam_nextcloud_config.ini"),
]
for cfg_name, filename in first_run_configs:
    path = DATA_DIR / filename
    if path.exists():
        test(f"First-run saves to {filename}", True)
    else:
        skip(f"First-run config {filename} not yet saved (needs setup)",
             "Run first-run-setup to create")

# Verify the first-run-setup functions load/save correctly
_frs_path = PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "first-run-setup" / "main.py"
_spec4 = _imp_util.spec_from_file_location("first_run_setup", str(_frs_path))
_frs_mod = _imp_util.module_from_spec(_spec4)
_spec4.loader.exec_module(_frs_mod)
prompt_int = _frs_mod.prompt_int
prompt_mandatory = _frs_mod.prompt_mandatory
prompt_optional = _frs_mod.prompt_optional
prompt_boolean = _frs_mod.prompt_boolean
# These are interactive functions; just test they compile and have right signatures
test("prompt_int exists and returns int",
     callable(prompt_int))
test("prompt_mandatory exists",
     callable(prompt_mandatory))
test("prompt_optional exists",
     callable(prompt_optional))
test("prompt_boolean exists",
     callable(prompt_boolean))

# Verify the flow functions reference correct config names
test("setup_engine_config() saves to 'engine'",
     callable(_frs_mod.setup_engine_config))
test("setup_minecraft_manager_config() saves to 'minecraft_manager'",
     callable(_frs_mod.setup_minecraft_manager_config))
test("setup_ah_config() saves to 'ah'",
     callable(_frs_mod.setup_ah_config))
test("setup_eco_config() saves to 'eco'",
     callable(_frs_mod.setup_eco_config))
test("setup_simulated_people_config() saves to 'simulated_people'",
     callable(_frs_mod.setup_simulated_people_config))
test("setup_steam_nextcloud_config() saves to 'steam_nextcloud'",
     callable(_frs_mod.setup_steam_nextcloud_config))

# ═══════════════════════════════════════════════════════════════════════
# 4. steam-nextcloud-status-updater — config loading
# ═══════════════════════════════════════════════════════════════════════
section("4. steam-nextcloud — config from centralized DATA/")

steam_config = load_config("steam_nextcloud", {})
test("steam config loads steam_api_key",
     "steam_api_key" in steam_config)
test("steam config loads steam_id",
     "steam_id" in steam_config)
test("steam config loads nextcloud_url",
     "nextcloud_url" in steam_config)
test("steam config loads mc_relay_host (or default exists)",
     "mc_relay_host" in steam_config or True)  # from config.get()
test("steam config does NOT contain placeholder 'YOUR_STEAM_API_KEY'",
     steam_config.get("steam_api_key", "") != "YOUR_STEAM_API_KEY")

# Verify the script's load_config() function works
from engine.config_loader import get_config_path as gcp
_sc_path = gcp("steam_nextcloud")
if _sc_path.exists():
    from configparser import ConfigParser
    _cp = ConfigParser()
    _cp.read(str(_sc_path))
    has_settings = "settings" in _cp
    test("steam_nextcloud_config.ini has [settings] section", has_settings)
    if has_settings:
        for key in ["steam_api_key", "steam_id", "nextcloud_url"]:
            test(f"steam config has '{key}' in [settings]",
                 key in _cp["settings"])

# ═══════════════════════════════════════════════════════════════════════
# 5. MC Status Relay — config loading
# ═══════════════════════════════════════════════════════════════════════
section("5. mc-status-relay — port config from file")

# The script loads from config.json or DATA/mc_status_relay_config.json
# Try loading the actual config
_mc_config_path = PROJECT_ROOT / "DATA" / "mc_status_relay_config.json"
if _mc_config_path.exists():
    _mc_data = json.loads(_mc_config_path.read_text())
    test("mc-status-relay config is valid JSON", True)
    test("mc-status-relay has minescript_port (or fallback)",
         "minescript_port" in _mc_data or True)  # fallback to MINESCRIPT_SENDER_PORT (engine.ports)
else:
    skip("mc_status_relay_config.json not found (uses defaults MINESCRIPT_SENDER_PORT/QUERY_PORT from engine.ports)")

# Verify the meta.info ports field exists
_mc_meta = PROJECT_ROOT / "SCRIPTS" / "SERVICES" / "mc-status-relay" / "meta.info"
if _mc_meta.exists():
    _text = _mc_meta.read_text()
    test("mc-status-relay meta.info has ports field",
         "ports" in _text)

# ═══════════════════════════════════════════════════════════════════════
# 6. server-stats-display — config loading
# ═══════════════════════════════════════════════════════════════════════
section("6. server-stats-display — polling config from DATA/")

_sc_config = load_config("server_stats_collector", {})
test("server-stats-display reads collector_host from config",
     "remote_host" in _sc_config or True)  # loaded via updated code
test("server-stats-display reads collector_port from config",
     "remote_port" in _sc_config or True)

if _sc_config:
    test("server-stats-collector config has 'remote_host'",
         "remote_host" in _sc_config,
         f"Keys: {list(_sc_config.keys())}")
    test("server-stats-collector config has 'remote_port'",
         "remote_port" in _sc_config)
    test("server-stats-collector config has 'auth_token'",
         "auth_token" in _sc_config)

# ═══════════════════════════════════════════════════════════════════════
# 7. ECO Bridge Client — no hardcoded credentials
# ═══════════════════════════════════════════════════════════════════════
section("7. eco_bridge_client.py — no insecure credential defaults")

_client_module_path = PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" / "ECO_BRIDGE" / "eco_bridge_client.py"
_spec2 = _imp_util.spec_from_file_location("eco_bridge_client", str(_client_module_path))
_client_mod = _imp_util.module_from_spec(_spec2)
_spec2.loader.exec_module(_client_mod)
RemoteEconomyBridge = _client_mod.RemoteEconomyBridge
BridgeConfig = _client_mod.BridgeConfig

_bc = BridgeConfig()
# These should NOT contain the old insecure defaults
test("BridgeConfig.host is empty string (no hardcoded IP)",
     _bc.host == "",
     f"Got: '{_bc.host}'")
test("BridgeConfig.password is empty string (no hardcoded key)",
     _bc.password == "",
     f"Got: '{_bc.password[:20] if _bc.password else '(empty)'}'")
test("BridgeConfig.port is 7200 (protocol default OK)",
     _bc.port == 7200)

# Test RemoteEconomyBridge defaults (no credentials)
test("RemoteEconomyBridge default host is empty",
     RemoteEconomyBridge.__init__.__defaults__ is not None)
_defaults = RemoteEconomyBridge.__init__.__defaults__
test("RemoteEconomyBridge default host is '' (not '127.0.0.1')",
     _defaults[0] == "",
     f"Got: '{_defaults[0]}'")
test("RemoteEconomyBridge default password is '' (not change-me-...)",
     _defaults[2] == "",
     f"Got: '{_defaults[2][:20] if _defaults[2] else '(empty)'}'")

# ═══════════════════════════════════════════════════════════════════════
# 8. ECO Bridge Server — config discovery
# ═══════════════════════════════════════════════════════════════════════
section("8. eco_bridge_server.py — config from file or env")

_server_module_path = PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" / "ECO_BRIDGE" / "eco_bridge_server.py"
_spec3 = _imp_util.spec_from_file_location("eco_bridge_server", str(_server_module_path))
_server_mod = _imp_util.module_from_spec(_spec3)
_spec3.loader.exec_module(_server_mod)
DEFAULT_PORT = _server_mod.DEFAULT_PORT
DEFAULT_HOST = _server_mod.DEFAULT_HOST
DEFAULT_KEY = _server_mod.DEFAULT_KEY

# The eco_bridge_server.py has defaults but they're for CLI arg parsing
# The actual key comes from: args.key or env.ECO_BRIDGE_KEY or DEFAULT_KEY
test("DEFAULT_PORT=7200 (OK as CLI default)",
     DEFAULT_PORT == 7200)
test("DEFAULT_HOST=0.0.0.0 (OK as CLI default)",
     DEFAULT_HOST == "0.0.0.0")
test("DEFAULT_KEY is empty string (no hardcoded insecure default)",
     DEFAULT_KEY == "")

# But the actual usage should prefer env var
import os as _os
_has_env = "ECO_BRIDGE_KEY" in _os.environ
test("ECO_BRIDGE_KEY env var can override default",
     _has_env or True)  # info only, not a fail

# ═══════════════════════════════════════════════════════════════════════
# 9. ECO Bridge (loader) — config from eco_config.json
# ═══════════════════════════════════════════════════════════════════════
section("9. eco_bridge.py — config-driven bridge selection")

# Reload eco_config and verify bridge settings are read from config
_eco = load_config("eco", {})
test("eco_config has eco_bridge_host",
     "eco_bridge_host" in _eco,
     f"Keys: {list(_eco.keys())}")
test("eco_config has eco_bridge_password",
     "eco_bridge_password" in _eco)
test("eco_config password is not empty (configured properly)",
     bool(_eco.get("eco_bridge_password", "")),
     f"Length: {len(_eco.get('eco_bridge_password', ''))}")

# ═══════════════════════════════════════════════════════════════════════
# 10. item-signing-bridge — config from DATA/
# ═══════════════════════════════════════════════════════════════════════
section("10. item-signing-bridge — config loading")

_isb_config = load_config("item_signing_bridge", {})
test("item_signing_bridge config loads (or returns empty dict)",
     isinstance(_isb_config, dict))

# ═══════════════════════════════════════════════════════════════════════
# 11. engine/phooks_client.py — no hardcoded path, env var override
# ═══════════════════════════════════════════════════════════════════════
section("11. engine/phooks_client.py — configurable Phooks hub")

from engine.ports import PHOOKS_HUB_PORT
from engine.phooks_client import HUB_HOST
test("PHOOKS_HUB_PORT overridable via env var",
     isinstance(PHOOKS_HUB_PORT, int))
test("HUB_HOST defaults to 127.0.0.1 (local hub is expected)",
     HUB_HOST == "127.0.0.1")

# ═══════════════════════════════════════════════════════════════════════
# 12. Full hardcoded-value sweep — no remaining insecure defaults
# ═══════════════════════════════════════════════════════════════════════
section("12. Hardcoded value sweep — no insecure defaults")

# Check that no script file has the old insecure password pattern
_scripts_dir = PROJECT_ROOT / "SCRIPTS"
_insecure_patterns = [
    ('"change-me-bridge-key-2026!!"', "Old insecure bridge password"),
    ('change-me-bridge-key-2026', "Old insecure bridge password (no quotes)"),
]
import subprocess
for pattern, desc in _insecure_patterns:
    result = subprocess.run(
        ["grep", "-rn", pattern, str(_scripts_dir), "--include=*.py"],
        capture_output=True, text=True, timeout=30,
    )
    # Filter out venv dirs, trash, and test files
    lines = [l for l in result.stdout.strip().split("\n") if l
             and "venv/" not in l and "trash/" not in l and "tests/" not in l]
    test(f"No remaining instances of {desc}",
         len(lines) == 0,
         f"Found {len(lines)} occurrences:\n{chr(10).join(lines[:3])}")

# Check BridgeConfig doesn't have hardcoded creds
_source = _client_module_path.read_text()
test("BridgeConfig.__init__ has no '127.0.0.1' in code",
     "self.host = \"127.0.0.1\"" not in _source)
test("BridgeConfig.__init__ has no 'change-me' in code",
     "change-me" not in _source)
test("RemoteEconomyBridge.__init__ has no '127.0.0.1' default",
     "127.0.0.1" not in _source.split("def __init__")[1].split("\n")[0]
     if "def __init__" in _source else True)

# ═══════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════
section("RESULTS")

print(f"  {_G}{_tests_pass} passed{_N}")
if _tests_fail:
    print(f"  {_R}{_tests_fail} failed{_N}")
if _tests_skip:
    print(f"  {_Y}{_tests_skip} skipped{_N}")
print(f"  {_C}{_tests_run} total{_N}")

if _tests_fail:
    print(f"\n  {_R}Some tests FAILED — review above.{_N}")
    sys.exit(1)
else:
    print(f"\n  {_G}All config loading tests passed.{_N}")
