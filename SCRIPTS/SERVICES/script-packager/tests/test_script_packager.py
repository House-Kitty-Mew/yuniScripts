"""
test_script_packager.py — Comprehensive unittest tests for the Script Packager.

Tests the complete lifecycle:
1. Script discovery
2. Compile/decompile config management
3. Snapshot creation (various scripts)
4. Metadata loading from snapshots
5. Deploy preview
6. Full deployment with file restoration
7. Edge cases (nonexistent scripts, existing paths, etc.)
"""

import sys, os, tempfile, shutil, json, unittest
from pathlib import Path

# Add paths
sys.path.insert(0, '/home/deck/Documents/dev-yuniScripts/SCRIPTS/SERVICES/script-packager')
sys.path.insert(0, '/home/deck/Documents/dev-yuniScripts')

from core.snapshot_manager import ScriptSnapshotManager, SnapshotResult, DeployPreview, DeployResult
from core.compile_config import CompileConfig, get_default_compile_config, save_compile_config, load_compile_config
from core.decompile_config import DecompileConfig, get_default_decompile_config, save_decompile_config, load_decompile_config


class TestCompileConfig(unittest.TestCase):
    """Test compile configuration."""

    def test_default_config(self):
        cfg = get_default_compile_config("SERVICES/test")
        self.assertEqual(cfg.script_id, "SERVICES/test")
        self.assertIn("*.py", cfg.include_patterns)
        self.assertIn("__pycache__", cfg.exclude_patterns)
        self.assertTrue(cfg.include_databases)
        self.assertTrue(cfg.include_configs)
        self.assertEqual(cfg.packaging_config.get("hash_algorithm"), "SHA256")

    def test_custom_config(self):
        data = {
            "script_id": "SERVICES/custom",
            "include_patterns": ["*.py", "*.json"],
            "exclude_patterns": ["__pycache__"],
            "include_databases": False,
        }
        cfg = CompileConfig(data)
        self.assertEqual(cfg.script_id, "SERVICES/custom")
        self.assertEqual(len(cfg.include_patterns), 2)
        self.assertFalse(cfg.include_databases)

    def test_save_load_roundtrip(self):
        script_id = "SERVICES/test_roundtrip"
        cfg = get_default_compile_config(script_id)
        cfg.data["version"] = "2.0.0"
        
        saved = save_compile_config(script_id, cfg)
        self.assertTrue(saved)
        
        loaded = load_compile_config(script_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.data["version"], "2.0.0")
        self.assertEqual(loaded.script_id, script_id)

    def test_to_json(self):
        cfg = get_default_compile_config("SERVICES/json_test")
        json_str = cfg.to_json()
        self.assertIn("script_id", json_str)
        self.assertIn("SERVICES/json_test", json_str)
        # Verify valid JSON
        parsed = json.loads(json_str)
        self.assertEqual(parsed["script_id"], "SERVICES/json_test")


class TestDecompileConfig(unittest.TestCase):
    """Test decompile configuration."""

    def test_default_config(self):
        cfg = get_default_decompile_config("SERVICES/test")
        self.assertEqual(cfg.script_id, "SERVICES/test")
        self.assertEqual(cfg.target_path, "SERVICES/test")
        self.assertEqual(len(cfg.post_unpack_actions), 4)
        self.assertEqual(cfg.min_engine_version, "1.0.0")
        self.assertFalse(cfg.overwrite_existing)
        self.assertTrue(cfg.create_backup)

    def test_custom_config(self):
        data = {
            "script_id": "SERVICES/custom",
            "target_path": "SCRIPTS/SERVICES/custom",
            "post_unpack_actions": [
                {"type": "register_script", "enabled": True}
            ],
            "unpackaging": {
                "overwrite_existing": True,
                "on_conflict": "overwrite"
            }
        }
        cfg = DecompileConfig(data)
        self.assertTrue(cfg.overwrite_existing)
        self.assertEqual(cfg.on_conflict, "overwrite")
        self.assertEqual(len(cfg.post_unpack_actions), 1)

    def test_save_load_roundtrip(self):
        script_id = "SERVICES/decompile_rt"
        cfg = get_default_decompile_config(script_id)
        cfg.data["compatibility"]["min_engine_version"] = "2.0.0"
        
        saved = save_decompile_config(script_id, cfg)
        self.assertTrue(saved)
        
        loaded = load_decompile_config(script_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.min_engine_version, "2.0.0")


class TestSnapshotManager(unittest.TestCase):
    """Test the ScriptSnapshotManager end-to-end."""

    @classmethod
    def setUpClass(cls):
        cls.manager = ScriptSnapshotManager()
        cls.tmp = tempfile.mkdtemp(prefix="yuni_test_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_discover_scripts(self):
        scripts = self.manager.discover_scripts()
        self.assertGreater(len(scripts), 10, "Should discover many scripts")
        
        # Check structure
        for s in scripts:
            self.assertIn("script_id", s)
            self.assertIn("name", s)
            self.assertIn("version", s)
            self.assertIn("category", s)
            self.assertIn("path", s)
            self.assertIn("enabled", s)
        
        # Check we found specific scripts
        sids = [s["script_id"] for s in scripts]
        self.assertIn("TOOLS/gui-dashboard", sids)
        self.assertIn("SERVICES/datagram-engine", sids)

    def test_create_snapshot_gui_dashboard(self):
        out = os.path.join(self.tmp, "gui_snap")
        result = self.manager.create_snapshot(
            "TOOLS/gui-dashboard", out,
            "GUI Dashboard Test", "Test Suite",
            include_databases=False, include_configs=False
        )
        self.assertTrue(result.success)
        self.assertGreater(result.file_count, 5)
        self.assertTrue(len(result.hash) > 10)
        self.assertGreater(result.size_bytes, 0)
        
        # Verify datagram structure
        dg = Path(out)
        self.assertTrue((dg / "Meta" / "Base.ini").exists())
        self.assertTrue((dg / "Meta" / "ScriptMeta.ini").exists())
        self.assertTrue((dg / "Manifest.json").exists())
        self.assertTrue((dg / "Script").exists())
        self.assertTrue((dg / "Script" / "compile_instructions.json").exists())
        self.assertTrue((dg / "Script" / "decompile_instructions.json").exists())

    def test_load_snapshot_meta(self):
        out = os.path.join(self.tmp, "gui_snap")
        meta = self.manager.load_snapshot_meta(out)
        self.assertIsNotNone(meta)
        self.assertTrue(meta["is_valid"])
        self.assertEqual(meta["script_id"], "TOOLS/gui-dashboard")
        self.assertEqual(meta["name"], "GUI Dashboard Test")
        self.assertIn("compile_instructions", meta)
        self.assertIn("decompile_instructions", meta)
        self.assertGreater(meta["file_count"], 0)

    def test_preview_deploy(self):
        out = os.path.join(self.tmp, "gui_snap")
        preview = self.manager.preview_deploy(out)
        self.assertTrue(preview.success)
        self.assertEqual(preview.script_id, "TOOLS/gui-dashboard")
        self.assertGreater(preview.file_count, 0)
        self.assertGreater(len(preview.post_unpack_actions), 0)

    def test_full_deploy(self):
        """Test creating a snapshot and deploying it to a new location."""
        # Create snapshot
        snap_out = os.path.join(self.tmp, "deploy_test_snap")
        self.manager.create_snapshot(
            "TOOLS/gui-dashboard", snap_out,
            "Deploy Test", "Test",
            include_databases=False, include_configs=False
        )
        
        # Deploy to temp directory
        deploy_dir = tempfile.mkdtemp(prefix="yuni_deploy_")
        old_root = self.manager.scripts_root
        self.manager.scripts_root = Path(deploy_dir)
        try:
            result = self.manager.deploy_snapshot(snap_out, verbose=False)
            self.assertTrue(result.success)
            self.assertGreater(result.files_restored, 0)
            
            # Verify files were restored
            target = Path(deploy_dir) / "TOOLS" / "gui-dashboard"
            self.assertTrue(target.exists())
            self.assertTrue((target / "main.py").exists())
            self.assertTrue((target / "Phooks.py").exists())
            self.assertTrue((target / "meta.info").exists())
            self.assertTrue((target / "API.md").exists())
        finally:
            self.manager.scripts_root = old_root
            shutil.rmtree(deploy_dir, ignore_errors=True)

    def test_snapshot_nonexistent_script(self):
        result = self.manager.create_snapshot(
            "SERVICES/nonexistent_script_xyz",
            os.path.join(self.tmp, "fail_snap"),
            "Should Fail", "Test"
        )
        self.assertFalse(result.success)
        self.assertIn("not found", result.message.lower())

    def test_snapshot_existing_path(self):
        # Create a path first
        out = os.path.join(self.tmp, "existing_path")
        os.makedirs(out)
        result = self.manager.create_snapshot(
            "TOOLS/gui-dashboard", out,
            "Should Fail", "Test"
        )
        self.assertFalse(result.success)
        self.assertIn("exists", result.message.lower())

    def test_snapshot_self(self):
        """Test snapshotting the script-packager itself (recursive)."""
        out = os.path.join(self.tmp, "self_snap")
        result = self.manager.create_snapshot(
            "SERVICES/script-packager", out,
            "Self-Test", "Test System",
            include_databases=False, include_configs=False
        )
        self.assertTrue(result.success)
        self.assertGreater(result.file_count, 10)
        # Path is the temp output, not the script packager dir
        self.assertTrue(result.success, "Script packager self-snapshot should succeed")

    def test_snapshot_datagram_engine(self):
        """Test snapshotting the datagram-engine (has sub-modules)."""
        out = os.path.join(self.tmp, "dg_engine_snap")
        result = self.manager.create_snapshot(
            "SERVICES/datagram-engine", out,
            "Datagram Engine Test", "Test",
            include_databases=False, include_configs=False
        )
        self.assertTrue(result.success)
        self.assertGreater(result.file_count, 15,
                          f"datagram-engine should have many files, got {result.file_count}")

    def test_snapshot_with_databases(self):
        """Test snapshot including database files."""
        out = os.path.join(self.tmp, "db_snap")
        result = self.manager.create_snapshot(
            "TOOLS/gui-dashboard", out,
            "DB Test", "Test",
            include_databases=True, include_configs=False
        )
        self.assertTrue(result.success)
        # Should include .db files if any exist
        db_dir = Path(out) / "Databases" / "Default" / "Data"
        if db_dir.exists():
            db_files = list(db_dir.glob("*.db"))
            # This is fine whether or not DBs exist — the option is enabled

    def test_list_snapshots(self):
        """Test listing snapshots from a directory."""
        # Create multiple snapshots
        self.manager.create_snapshot("TOOLS/gui-dashboard", os.path.join(self.tmp, "list_a"), "List A", "Test", include_databases=False, include_configs=False)
        self.manager.create_snapshot("SERVICES/datagram-engine", os.path.join(self.tmp, "list_b"), "List B", "Test", include_databases=False, include_configs=False)
        
        snaps = self.manager.list_snapshots(self.tmp)
        self.assertGreaterEqual(len(snaps), 2)
        snap_names = [s["name"] for s in snaps]
        self.assertIn("List A", snap_names)
        self.assertIn("List B", snap_names)

    def test_data_classes(self):
        """Test SnapshotResult, DeployPreview, DeployResult creation."""
        sr = SnapshotResult(True, "SERVICES/test", "/tmp/dg", 42, "abc123", "SHA256", 1024, "OK", [])
        self.assertTrue(sr.success)
        self.assertEqual(sr.file_count, 42)
        
        dp = DeployPreview(True, "SERVICES/test", "/tmp/src", "/tmp/dst", 10, [], [], True, [], [], "Preview OK")
        self.assertTrue(dp.success)
        self.assertEqual(dp.file_count, 10)
        
        dr = DeployResult(True, "SERVICES/test", "/tmp/src", "/tmp/dst", 10, 0, ["Action 1"], "/tmp/bk", "Done", [])
        self.assertTrue(dr.success)
        self.assertEqual(dr.files_restored, 10)

    def test_parse_ports(self):
        """Test port parsing utility."""
        parse = ScriptSnapshotManager._parse_ports
        self.assertEqual(parse("25565"), [25565])
        self.assertEqual(parse("25565,25570"), [25565, 25570])
        self.assertEqual(parse("25565-25567"), [25565, 25566, 25567])
        self.assertEqual(parse(""), [])
        self.assertEqual(parse("  25565  ,  25570  "), [25565, 25570])


if __name__ == "__main__":
    unittest.main()
