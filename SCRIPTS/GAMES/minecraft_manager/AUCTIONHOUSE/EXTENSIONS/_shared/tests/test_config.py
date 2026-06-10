"""
test_config.py — Tests for the unified ExtensionConfig system.

Tests:
  - Default values
  - JSON file loading
  - Runtime overrides
  - Resolution priority (override > file > default)
  - Dict-style access
  - Hot reload
  - Global registry
  - Config save/load round-trip
  - Thread safety
  - Missing key handling
"""

import sys, os, json, tempfile, threading, time
from unittest import TestCase, main
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED_DIR = os.path.dirname(_HERE)
_EXT_DIR = os.path.dirname(_SHARED_DIR)
_AH_DIR = os.path.dirname(_EXT_DIR)
for p in [_AH_DIR, _EXT_DIR, _SHARED_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from EXTENSIONS._shared.config import ExtensionConfig, get_config, list_configs, reload_all
from EXTENSIONS._shared.config import _config_registry

# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

class TestExtensionConfigDefaults(TestCase):
    """Default values work correctly."""

    def test_defaults_used_when_no_file(self):
        """Config returns defaults when no config.json exists."""
        from EXTENSIONS._shared.config import ExtensionConfig

        cfg = ExtensionConfig("test_no_file", {
            "enabled": True,
            "count": 42,
            "name": "default_name",
        }, config_dir="/tmp/nonexistent_dir_xyz", auto_register=False)

        self.assertTrue(cfg.get("enabled"))
        self.assertEqual(cfg.get("count"), 42)
        self.assertEqual(cfg.get("name"), "default_name")

    def test_get_with_fallback(self):
        """get() with explicit fallback for missing keys."""
        cfg = ExtensionConfig("test_fallback", {"a": 1},
                              config_dir="/tmp/nonexistent_xyz",
                              auto_register=False)
        self.assertEqual(cfg.get("a"), 1)
        self.assertEqual(cfg.get("missing", "fallback"), "fallback")
        self.assertIsNone(cfg.get("also_missing"))

    def test_dict_access(self):
        """__getitem__ returns config values."""
        cfg = ExtensionConfig("test_dict", {"key": "val"},
                              config_dir="/tmp/nonexistent_xyz",
                              auto_register=False)
        self.assertEqual(cfg["key"], "val")

    def test_dict_access_missing_raises(self):
        """__getitem__ on missing key raises KeyError."""
        cfg = ExtensionConfig("test_missing", {},
                              config_dir="/tmp/nonexistent_xyz",
                              auto_register=False)
        with self.assertRaises(KeyError):
            _ = cfg["nonexistent"]

    def test_contains(self):
        """__contains__ works."""
        cfg = ExtensionConfig("test_contains", {"a": 1},
                              config_dir="/tmp/nonexistent_xyz",
                              auto_register=False)
        self.assertIn("a", cfg)
        self.assertNotIn("b", cfg)


class TestExtensionConfigFile(TestCase):
    """JSON file loading."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmp_dir, "config.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_config(self, data: dict):
        with open(self.cfg_path, "w") as f:
            json.dump(data, f)

    def test_file_overrides_defaults(self):
        """File values override defaults."""
        self._write_config({"count": 99, "name": "from_file"})
        cfg = ExtensionConfig("test_file", {"count": 1, "name": "default", "extra": True},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        self.assertEqual(cfg.get("count"), 99)
        self.assertEqual(cfg.get("name"), "from_file")
        self.assertTrue(cfg.get("extra"))  # from defaults

    def test_file_extends_defaults(self):
        """New keys from file are available even if not in defaults."""
        self._write_config({"new_key": "new_value"})
        cfg = ExtensionConfig("test_extend", {"existing": "yes"},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        self.assertEqual(cfg.get("existing"), "yes")
        self.assertEqual(cfg.get("new_key"), "new_value")

    def test_missing_file_uses_defaults(self):
        """If no config.json exists, defaults are used."""
        cfg = ExtensionConfig("test_missing_file", {"a": 1},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        self.assertEqual(cfg.get("a"), 1)

    def test_invalid_json_file_uses_defaults(self):
        """Invalid JSON file logs warning and uses defaults."""
        with open(self.cfg_path, "w") as f:
            f.write("{invalid json}")
        cfg = ExtensionConfig("test_bad_json", {"a": 1},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        self.assertEqual(cfg.get("a"), 1)


class TestExtensionConfigOverrides(TestCase):
    """Runtime overrides."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmp_dir, "config.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_override_has_highest_priority(self):
        """Runtime overrides beat file values and defaults."""
        with open(self.cfg_path, "w") as f:
            json.dump({"key": "from_file", "keep": "file_val"}, f)

        cfg = ExtensionConfig("test_priority", {"key": "from_defaults"},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        cfg.set("key", "from_override")

        self.assertEqual(cfg.get("key"), "from_override")
        self.assertEqual(cfg.get("keep"), "file_val")  # unchanged

    def test_set_many_atomic(self):
        """set_many() updates multiple overrides atomically."""
        cfg = ExtensionConfig("test_many", {"a": 1, "b": 2, "c": 3},
                              config_dir="/tmp/nonexistent_xyz",
                              auto_register=False)
        cfg.set_many({"a": 10, "b": 20})
        self.assertEqual(cfg["a"], 10)
        self.assertEqual(cfg["b"], 20)
        self.assertEqual(cfg["c"], 3)  # unchanged by overrides

    def test_clear_overrides(self):
        """clear_overrides() removes all runtime overrides."""
        cfg = ExtensionConfig("test_clear", {"key": "default"},
                              config_dir="/tmp/nonexistent_xyz",
                              auto_register=False)
        cfg.set("key", "override")
        self.assertEqual(cfg.get("key"), "override")
        cfg.clear_overrides()
        self.assertEqual(cfg.get("key"), "default")

    def test_as_dict_includes_all_layers(self):
        """as_dict() returns merged defaults + file + overrides."""
        cfg = ExtensionConfig("test_asdict", {"a": 1, "b": 2},
                              config_dir="/tmp/nonexistent_xyz",
                              auto_register=False)
        cfg.set("b", 99)
        cfg.set("c", 100)
        d = cfg.as_dict()
        self.assertEqual(d["a"], 1)
        self.assertEqual(d["b"], 99)
        self.assertEqual(d["c"], 100)


class TestExtensionConfigReload(TestCase):
    """Hot-reload functionality."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmp_dir, "config.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_reload_detects_file_changes(self):
        """reload() picks up disk changes."""
        with open(self.cfg_path, "w") as f:
            json.dump({"key": "old"}, f)

        cfg = ExtensionConfig("test_reload", {},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        self.assertEqual(cfg.get("key"), "old")

        # Change file externally
        with open(self.cfg_path, "w") as f:
            json.dump({"key": "new"}, f)

        summary = cfg.reload()
        self.assertIn("changed", summary)
        self.assertIn("key", summary["changed"])
        self.assertEqual(cfg.get("key"), "new")

    def test_reload_added_key_detected(self):
        """reload() detects newly added keys."""
        with open(self.cfg_path, "w") as f:
            json.dump({"a": 1}, f)

        cfg = ExtensionConfig("test_added", {},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        self.assertIsNone(cfg.get("b"))

        with open(self.cfg_path, "w") as f:
            json.dump({"a": 1, "b": 2}, f)

        summary = cfg.reload()
        self.assertIn("added", summary)
        self.assertIn("b", summary["added"])
        self.assertEqual(cfg.get("b"), 2)

    def test_overrides_survive_reload(self):
        """Runtime overrides are preserved after reload."""
        with open(self.cfg_path, "w") as f:
            json.dump({"file_key": "file_val", "shared": "from_file"}, f)

        cfg = ExtensionConfig("test_survive", {},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        cfg.set("override_key", "override_val")
        cfg.set("shared", "from_override")

        cfg.reload()

        # Override still has priority
        self.assertEqual(cfg.get("override_key"), "override_val")
        self.assertEqual(cfg.get("shared"), "from_override")


class TestExtensionConfigRegistry(TestCase):
    """Global config registry."""

    def setUp(self):
        # Clear registry between tests
        from EXTENSIONS._shared.config import _config_registry
        _config_registry.clear()

    def tearDown(self):
        from EXTENSIONS._shared.config import _config_registry
        _config_registry.clear()

    def test_auto_registration(self):
        """Configurations register themselves by default."""
        from EXTENSIONS._shared.config import ExtensionConfig, get_config

        cfg = ExtensionConfig("auto_reg_test", {"key": "val"},
                              config_dir="/tmp/nonexistent_xyz")
        retrieved = get_config("auto_reg_test")
        self.assertIs(retrieved, cfg)

    def test_get_config_returns_none_for_unknown(self):
        """get_config() returns None for unregistered names."""
        from EXTENSIONS._shared.config import get_config
        self.assertIsNone(get_config("nonexistent"))

    def test_list_configs(self):
        """list_configs() returns all registered configs."""
        from EXTENSIONS._shared.config import ExtensionConfig, list_configs

        c1 = ExtensionConfig("ext_a", {}, config_dir="/tmp/x", auto_register=True)
        c2 = ExtensionConfig("ext_b", {}, config_dir="/tmp/x", auto_register=True)

        configs = list_configs()
        self.assertIn("ext_a", configs)
        self.assertIn("ext_b", configs)

    def test_reload_all(self):
        """reload_all() triggers reload on all registered configs."""
        from EXTENSIONS._shared.config import ExtensionConfig, reload_all

        c1 = ExtensionConfig("rall_a", {"a": 1}, config_dir="/tmp/x", auto_register=True)
        c2 = ExtensionConfig("rall_b", {"b": 2}, config_dir="/tmp/y", auto_register=True)

        results = reload_all()
        self.assertIn("rall_a", results)
        self.assertIn("rall_b", results)


class TestExtensionConfigSave(TestCase):
    """Save to disk."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_writes_defaults_and_overrides(self):
        """save() writes merged config to disk."""
        cfg = ExtensionConfig("test_save", {"a": 1, "b": 2},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.set("b", 99)
        cfg.set("c", 100)
        cfg.save()

        with open(os.path.join(self.tmp_dir, "config.json")) as f:
            saved = json.load(f)

        self.assertEqual(saved["a"], 1)
        self.assertEqual(saved["b"], 99)
        self.assertEqual(saved["c"], 100)


class TestExtensionConfigThreadSafety(TestCase):
    """Concurrent access safety."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmp_dir, "config.json")
        with open(self.cfg_path, "w") as f:
            json.dump({"read_key": "file_val"}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_concurrent_reads_writes(self):
        """Concurrent set/get doesn't corrupt data."""
        cfg = ExtensionConfig("test_thread", {"a": 0},
                              config_dir=self.tmp_dir, auto_register=False)
        cfg.load()
        errors = []
        barrier = threading.Barrier(5)
        stop = threading.Event()

        def worker(name):
            barrier.wait()
            try:
                for i in range(100):
                    cfg.set(f"w_{name}_{i}", i)
                    _ = cfg.get(f"w_{name}_{i}")
                    _ = cfg.as_dict()
            except Exception as e:
                errors.append(f"{name}: {e}")

        threads = [threading.Thread(target=worker, args=(f"t{j}",))
                   for j in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")


if __name__ == "__main__":
    main()
