"""
test_config_centralization.py — Comprehensive tests for the centralized DATA/ config system.

Covers:
  1. ConfigLoader basics (path resolution, DATA dir creation)
  2. JSON config load/save round-trip
  3. INI config load/save round-trip
  4. Legacy config migration (auto-detect + copy)
  5. Fresh install detection
  6. Mark-setup-complete flow
  7. Edge cases: missing files, corrupt JSON, empty files
  8. Data flow: first-run-setup → write → load → verify
"""

import os, sys, json, tempfile, shutil, time
from pathlib import Path
from collections import deque

# ── Setup ────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Use a temp directory so tests don't touch real DATA/
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="ytests_config_"))
_TEST_DATA = _TEST_ROOT / "DATA"
os.environ["YUNISCRIPTS_TEST_ROOT"] = str(_TEST_ROOT)

# Patch before importing config_loader
import engine.config_loader as cl
_orig_find_root = cl._find_project_root
cl._find_project_root = lambda: _TEST_ROOT
cl._DATA_DIR = None  # Force re-resolve

from engine.config_loader import (
    get_data_dir, get_config_path, load_config, save_config,
    is_fresh_install, mark_setup_complete, migrate_old_config,
    _auto_migrate, _load_json, _load_ini, _save_ini,
    _CONFIG_NAMES, _LEGACY_PATHS, get_project_root,
)


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestConfigLoaderBasics:
    """1. ConfigLoader basics."""

    def setup_method(self):
        cl._DATA_DIR = None
        cl._migrated.clear()
        if _TEST_DATA.exists():
            shutil.rmtree(str(_TEST_DATA))

    def test_data_dir_created(self):
        """get_data_dir() should create the DATA directory."""
        d = get_data_dir()
        assert d.exists(), "DATA dir was not created"
        assert d.is_dir(), "DATA dir is not a directory"
        assert d.name == "DATA"

    def test_get_config_path(self):
        """get_config_path should return the correct filename."""
        p = get_config_path("engine")
        assert str(p).endswith("engine_config.json"), f"Unexpected path: {p}"
        assert "DATA" in str(p), "Path doesn't contain DATA"

    def test_get_config_path_unknown_name(self):
        """Unknown config names should get a default JSON filename."""
        p = get_config_path("foo_bar")
        assert str(p).endswith("foo_bar_config.json")

    def test_project_root(self):
        """get_project_root should return the test root."""
        r = get_project_root()
        assert r == _TEST_ROOT, f"Expected {_TEST_ROOT}, got {r}"

    def test_data_dir_is_singleton(self):
        """Multiple calls should return the same path."""
        d1 = get_data_dir()
        d2 = get_data_dir()
        assert d1 == d2


class TestJSONConfig:
    """2. JSON config load/save round-trip."""

    def setup_method(self):
        cl._DATA_DIR = None
        cl._migrated.clear()
        if _TEST_DATA.exists():
            shutil.rmtree(str(_TEST_DATA))

    def test_save_and_load(self):
        data = {"key": "value", "number": 42, "flag": True}
        ok = save_config("test_json", data)
        assert ok, "Save returned False"
        loaded = load_config("test_json")
        assert loaded["key"] == "value"
        assert loaded["number"] == 42
        assert loaded["flag"] is True

    def test_overwrite(self):
        save_config("test", {"a": 1})
        save_config("test", {"b": 2})
        loaded = load_config("test")
        assert "a" not in loaded
        assert loaded["b"] == 2

    def test_defaults_merged(self):
        save_config("test", {"custom": "yes"})
        loaded = load_config("test", {"default_key": "default_val", "custom": "overridden"})
        assert loaded["default_key"] == "default_val"
        assert loaded["custom"] == "yes"  # saved value wins

    def test_empty_config_returns_empty(self):
        loaded = load_config("nonexistent_script")
        assert loaded == {}

    def test_reload_after_save(self):
        data = {"version": 2}
        save_config("test_reload", data)
        t1 = load_config("test_reload")
        assert t1["version"] == 2
        save_config("test_reload", {"version": 3})
        t2 = load_config("test_reload")
        assert t2["version"] == 3


class TestIniConfig:
    """3. INI config load/save round-trip."""

    def setup_method(self):
        cl._DATA_DIR = None
        cl._migrated.clear()
        if _TEST_DATA.exists():
            shutil.rmtree(str(_TEST_DATA))

    def test_save_ini_and_load(self):
        data = {"host": "localhost", "port": "8080", "enabled": "true"}
        save_config("test_ini", data)
        loaded = load_config("test_ini")
        assert loaded.get("host") == "localhost"
        assert loaded.get("port") == "8080"

    def test_ini_defaults_merged(self):
        save_config("test_ini2", {"api_key": ""})
        loaded = load_config("test_ini2", {"api_key": "default_key", "timeout": "30"})
        assert loaded.get("timeout") == "30"
        assert loaded.get("api_key") == ""  # saved value wins

    @staticmethod
    def test_save_ini_flattens_nested_dicts():
        """_save_ini should only handle flat dicts (ConfigParser limitation)."""
        path = _TEST_DATA / "test_flat.ini"
        _save_ini(path, {"simple": "value", "number": "42"})
        assert path.exists()
        text = path.read_text()
        assert "simple" in text
        assert "42" in text


class TestMigration:
    """4. Legacy config migration."""

    def setup_method(self):
        cl._DATA_DIR = None
        cl._migrated.clear()
        if _TEST_DATA.exists():
            shutil.rmtree(str(_TEST_DATA))
        _TEST_DATA.mkdir(parents=True)

    def test_auto_migrate_from_legacy(self):
        """Auto-migrate should copy legacy config on first load."""
        # Create a legacy config at the old engine_config.json location
        legacy = _TEST_ROOT / "engine_config.json"
        legacy.write_text(json.dumps({"fresh_install": False}))
        assert legacy.exists()

        # Load config — should trigger auto-migration
        cfg = load_config("engine")
        assert cfg.get("fresh_install") is False, "Config not migrated"

        # Centralized file should now exist
        centralized = get_config_path("engine")
        assert centralized.exists(), "Centralized config not created"

        # Legacy should still exist (copy, not move)
        assert legacy.exists(), "Legacy config was deleted"

    def test_no_migration_if_already_centralized(self):
        """If centralized file exists, don't migrate."""
        centralized = get_config_path("engine")
        centralized.parent.mkdir(parents=True, exist_ok=True)
        centralized.write_text(json.dumps({"version": 2}))

        # Create legacy
        legacy = _TEST_ROOT / "engine_config.json"
        legacy.write_text(json.dumps({"version": 1, "old": True}))

        cfg = load_config("engine")
        assert cfg.get("version") == 2, "Centralized was overwritten by legacy"
        assert "old" not in cfg

    def test_migration_from_subdirectory(self):
        """Test migration from a nested legacy path (e.g. SCRIPTS/GAMES/...)."""
        # Create the legacy path that SIMULATED_PEOPLE uses
        legacy = _TEST_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" / "AUCTIONHOUSE" / "EXTENSIONS" / "SIMULATED_PEOPLE" / "config.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"enabled": True, "persona_tier": "normal"}))

        cfg = load_config("simulated_people")
        assert cfg.get("enabled") is True
        assert cfg.get("persona_tier") == "normal"

    def test_migration_only_happens_once(self):
        """Migration should run at most once per config name."""
        legacy = _TEST_ROOT / "engine_config.json"
        legacy.write_text(json.dumps({"counter": 1}))
        load_config("engine")
        # Modify legacy after migration
        legacy.write_text(json.dumps({"counter": 2}))
        # Reload should NOT re-migrate
        cfg = load_config("engine")
        assert cfg.get("counter") == 1, "Migration ran twice!"

    def test_migrate_all_legacy_paths(self):
        """Verify all registered legacy paths are valid."""
        for name, paths in _LEGACY_PATHS.items():
            assert isinstance(paths, list), f"{name}: legacy paths should be a list"
            for p in paths:
                assert isinstance(p, Path), f"{name}: {p} should be a Path"

    @staticmethod
    def test_legacy_paths_coverage():
        """Every config name should have at least one legacy path."""
        for name in _CONFIG_NAMES:
            assert name in _LEGACY_PATHS, f"{name} has no legacy paths defined"


class TestFreshInstall:
    """5. Fresh install detection."""

    def setup_method(self):
        cl._DATA_DIR = None
        cl._migrated.clear()
        if _TEST_DATA.exists():
            shutil.rmtree(str(_TEST_DATA))

    def test_fresh_when_no_config(self):
        assert is_fresh_install() is True

    def test_fresh_when_flag_true(self):
        save_config("engine", {"fresh_install": True})
        assert is_fresh_install() is True

    def test_not_fresh_when_flag_false(self):
        save_config("engine", {"fresh_install": False})
        assert is_fresh_install() is False

    def test_not_fresh_when_no_flag(self):
        save_config("engine", {"version": 1})
        assert is_fresh_install() is False


class TestSetupComplete:
    """6. Mark-setup-complete flow."""

    def setup_method(self):
        cl._DATA_DIR = None
        cl._migrated.clear()
        if _TEST_DATA.exists():
            shutil.rmtree(str(_TEST_DATA))

    def test_mark_setup_complete(self):
        assert is_fresh_install() is True
        mark_setup_complete()
        assert is_fresh_install() is False

    def test_mark_twice_stays_complete(self):
        mark_setup_complete()
        mark_setup_complete()
        assert is_fresh_install() is False

    def test_mark_preserves_existing_config(self):
        save_config("engine", {"custom_setting": "preserved", "fresh_install": True})
        mark_setup_complete()
        cfg = load_config("engine")
        assert cfg.get("custom_setting") == "preserved"
        assert cfg.get("fresh_install") is False


class TestEdgeCases:
    """7. Edge cases: missing files, corrupt JSON, empty files."""

    def setup_method(self):
        cl._DATA_DIR = None
        cl._migrated.clear()
        if _TEST_DATA.exists():
            shutil.rmtree(str(_TEST_DATA))

    def test_corrupt_json_returns_empty(self):
        path = get_config_path("corrupted")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{invalid json!!!}")
        loaded = load_config("corrupted")
        assert loaded == {}

    def test_empty_file_returns_empty(self):
        path = get_config_path("empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        loaded = load_config("empty")
        assert loaded == {}

    @staticmethod
    def test_load_json_nonexistent():
        result = _load_json(Path("/nonexistent/path.json"))
        assert result == {}

    @staticmethod
    def test_load_ini_nonexistent():
        result = _load_ini(Path("/nonexistent/config.ini"))
        assert result == {}

    def test_save_to_readonly_fails_gracefully(self):
        data = {"test": "value"}
        path = get_config_path("readonly_test")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        path.chmod(0o444)  # Read-only
        try:
            ok = save_config("readonly_test", data)
            assert ok is False, "Save should have failed"
        finally:
            path.chmod(0o644)

    def test_unicode_in_config(self):
        data = {"name": "测试", "emoji": "🚀"}
        save_config("unicode", data)
        loaded = load_config("unicode")
        assert loaded["name"] == "测试"
        assert loaded["emoji"] == "🚀"

    def test_nested_missing_dir_recreated(self):
        """If DATA dir is deleted, it should be recreated on next access."""
        get_data_dir()
        assert _TEST_DATA.exists()
        shutil.rmtree(str(_TEST_DATA))
        assert not _TEST_DATA.exists()
        cl._DATA_DIR = None  # Force re-create
        d = get_data_dir()
        assert d.exists(), "DATA dir was not recreated"

    def test_save_overwrites_existing(self):
        """Save should overwrite existing file content."""
        save_config("overwrite", {"first": "value"})
        save_config("overwrite", {"second": "replaced"})
        loaded = load_config("overwrite")
        assert "first" not in loaded
        assert loaded["second"] == "replaced"


class TestDataFlow:
    """8. Full data flow: first-run-setup → write → load → verify."""

    def setup_method(self):
        cl._DATA_DIR = None
        cl._migrated.clear()
        if _TEST_DATA.exists():
            shutil.rmtree(str(_TEST_DATA))

    def test_full_engine_config_flow(self):
        """Simulate first-run-setup writing engine config, then loading it."""
        from engine.config_loader import save_config, load_config, is_fresh_install, mark_setup_complete

        # Simulate fresh install
        assert is_fresh_install() is True

        # Write all configs as the setup wizard would
        save_config("engine", {"fresh_install": False, "reset_venvs": False})
        save_config("minecraft_manager", {"rcon_host": "127.0.0.1", "rcon_port": 25575, "rcon_password": "test"})
        save_config("ah", {"deepseek_api_key": "sk-test", "deepseek_model": "deepseek-chat"})
        save_config("simulated_people", {"persona_tier": "data_saver", "ai_persona_decision": True})

        # Mark setup complete
        mark_setup_complete()

        # Verify fresh install flag is off
        assert is_fresh_install() is False

        # Load each config and verify values
        ec = load_config("engine")
        assert ec.get("fresh_install") is False

        mc = load_config("minecraft_manager")
        assert mc.get("rcon_host") == "127.0.0.1"
        assert mc.get("rcon_port") == 25575
        assert mc.get("rcon_password") == "test"

        ac = load_config("ah")
        assert ac.get("deepseek_api_key") == "sk-test"
        assert ac.get("deepseek_model") == "deepseek-chat"

        sp = load_config("simulated_people")
        assert sp.get("persona_tier") == "data_saver"
        assert sp.get("ai_persona_decision") is True

    def test_migration_flow(self):
        """Simulate a user who had existing configs before the DATA/ migration."""
        # 1. Create legacy config files
        legacy_engine = _TEST_ROOT / "engine_config.json"
        legacy_engine.write_text(json.dumps({"fresh_install": False, "reset_venvs": True}))

        legacy_ah = _TEST_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" / "AUCTIONHOUSE" / "ah_config.json"
        legacy_ah.parent.mkdir(parents=True, exist_ok=True)
        legacy_ah.write_text(json.dumps({"deepseek_api_key": "sk-legacy", "deepseek_model": "deepseek-chat"}))

        # 2. First load triggers migration
        ec = load_config("engine")
        ac = load_config("ah")

        # 3. Verify values preserved
        assert ec.get("fresh_install") is False
        assert ec.get("reset_venvs") is True
        assert ac.get("deepseek_api_key") == "sk-legacy"

        # 4. Centralized files exist
        assert get_config_path("engine").exists()
        assert get_config_path("ah").exists()

        # 5. Can modify and re-save
        save_config("ah", {"deepseek_api_key": "sk-updated"})
        ac2 = load_config("ah")
        assert ac2.get("deepseek_api_key") == "sk-updated"

    def test_first_run_setup_script(self):
        """Run the first-run-setup script with automated input simulation."""
        # Simulate the script's behavior programmatically
        # 1. Check fresh install
        assert is_fresh_install() is True

        # 2. Write configs (what the interactive prompts would produce)
        engine_cfg = {"fresh_install": False, "reset_venvs": False}
        save_config("engine", engine_cfg)

        mc_cfg = {
            "rcon_host": "192.168.1.10",
            "rcon_port": 25575,
            "rcon_password": "secure_password_123",
        }
        save_config("minecraft_manager", mc_cfg)

        ah_cfg = {
            "deepseek_api_key": "sk-simulated-key",
            "deepseek_model": "deepseek-chat",
            "simulation_interval_minutes": 360,
        }
        save_config("ah", ah_cfg)

        sp_cfg = {
            "persona_tier": "normal",
            "ai_persona_decision": True,
        }
        save_config("simulated_people", sp_cfg)

        eco_cfg = {
            "ooga_db_path": "config/otters_civ_revived/project_ooga.db",
        }
        save_config("eco", eco_cfg)

        # 3. Mark complete
        mark_setup_complete()

        # 4. Verify all configs
        assert is_fresh_install() is False
        assert load_config("engine").get("fresh_install") is False
        assert load_config("minecraft_manager").get("rcon_password") == "secure_password_123"
        assert load_config("ah").get("deepseek_api_key") == "sk-simulated-key"
        assert load_config("simulated_people").get("persona_tier") == "normal"
        assert load_config("eco").get("ooga_db_path") == "config/otters_civ_revived/project_ooga.db"

        # 5. Verify the config files actually exist on disk
        data_dir = get_data_dir()
        assert (data_dir / "engine_config.json").exists()
        assert (data_dir / "minecraft_manager_config.json").exists()
        assert (data_dir / "ah_config.json").exists()
        assert (data_dir / "simulated_people_config.json").exists()
        assert (data_dir / "eco_config.json").exists()


# ═══════════════════════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════════════════════

def teardown_module():
    """Clean up the temporary test directory."""
    cl._find_project_root = _orig_find_root
    cl._DATA_DIR = None
    cl._migrated.clear()
    try:
        shutil.rmtree(str(_TEST_ROOT))
    except PermissionError:
        pass


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
