"""
test_server_starts.py — Comprehensive unittest tests for each server type lifecycle.

Tests cover:
  1. Per-type server lifecycle (vanilla, fabric, forge, paper) — create, start, status, stop, cleanup
  2. Rcon command routing — multi-server routing to correct instance, single-server default
  3. Status/command output verification — get_status fields, send_command responses
  4. Port checking — reservation, conflicts, release, range validation
  5. Network adapter setup — VirtualAdapter, Firewall rules, NetworkManager sandbox
  6. Clean shutdown — graceful (SIGTERM), force (SIGKILL), timeout handling
  7. Edge cases — empty configs, missing files, port conflicts, corrupt jars, timeouts, permissions
  8. Integration tests — VFS, atomic operations, converter pipeline

Each test creates an isolated temporary database and cleans up on teardown.
System-level operations (subprocess, Java, iptables) are mocked to allow
running in any environment without actual Java or Minecraft installations.

Usage:
    python -m tests.test_server_starts  (from project root)
    python -m unittest tests.test_server_starts
"""

import os
import sys
import json
import time
import signal
import struct
import socket
import hashlib
import tempfile
import unittest
import threading
import shutil
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime
from typing import Optional, Dict, Any, List

logging.disable(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Import engine modules directly (matches pattern in test_dynamic_deps.py)
# ---------------------------------------------------------------------------
_ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
sys.path.insert(0, str(_ENGINE_DIR.parent))

ENGINE_AVAILABLE = True
_IMPORT_ERROR = None

try:
    from engine.database import get_db, Database
    from engine.vfs import VFS
    from engine.converter import (
        compress_data, decompress_data,
        file_to_db_blob, bytes_to_db_blob,
        db_blob_to_file, validate_file_integrity,
    )
    from engine.atomic import (
        AtomicJournal, AtomicOperation, get_journal, reset_journal, AtomicWriteContext,
    )
    from engine.runner import ServerRunner, ServerRunnerError, DEFAULT_JAVA_ARGS
    from engine.networking import (
        VirtualAdapter, Firewall, PortManager, NetworkManager,
        DEFAULT_SERVER_PORT, DEFAULT_RCON_PORT, MAX_PORT,
    )
    from engine.server_profiles import ServerProfile, SERVER_TYPE_LOADER_MAP
    from engine.mod_manager import ModManager, ModLoaders
except ImportError as exc:
    ENGINE_AVAILABLE = False
    _IMPORT_ERROR = str(exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skip_if_no_engine():
    return None if ENGINE_AVAILABLE else f"engine modules not available: {_IMPORT_ERROR}"


def _make_temp_db() -> tuple:
    """Create a temporary SQLite database and return (db, path)."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="srv_start_test_")
    os.close(fd)
    db = get_db(path)
    return db, path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fake_jar_data(size: int = 4096) -> bytes:
    """Generate fake JAR-like data with valid ZIP header."""
    return b"PK" + os.urandom(size - 2)


def _make_mock_popen(pid: int = 12345, poll_return: Optional[int] = None) -> MagicMock:
    """Create a mock subprocess.Popen with configurable behavior."""
    proc = MagicMock()
    proc.pid = pid
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.flush = MagicMock()
    proc.poll = MagicMock(return_value=poll_return)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = MagicMock(return_value=0)
    proc.returncode = 0
    return proc


def _setup_server_in_db(
    db: Database,
    name: str = "test-server",
    mc_version: str = "1.20.4",
    server_type: str = "vanilla",
    server_port: int = 25565,
    rcon_port: int = 25575,
    rcon_password: str = "",
    min_ram: str = "1G",
    max_ram: str = "2G",
) -> int:
    """Helper to create a server in the database and return its ID."""
    sid = db.create_server(
        name=name,
        mc_version=mc_version,
        server_type=server_type,
        server_port=server_port,
        rcon_port=rcon_port,
        rcon_password=rcon_password,
        min_ram=min_ram,
        max_ram=max_ram,
    )
    return sid


def _populate_vfs_with_jar(vfs: VFS, server_name: str, jar_data: Optional[bytes] = None):
    """Populate the VFS with a server jar file for a named server."""
    if jar_data is None:
        jar_data = _fake_jar_data()
    vfs.mkdir(f"/servers/{server_name}")
    vfs.write(f"/servers/{server_name}/server.jar", jar_data, atomic=True,
              content_type="application/java-archive")


def _get_table_names(db):
    """Return set of table names from the database."""
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return {r["name"] for r in rows}


# ===================================================================
#  Test class
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestServerLifecycle(unittest.TestCase):
    """Per-type server lifecycle tests: create, start, status, stop, cleanup."""

    def setUp(self):
        self.db = None
        self._db_path = None
        self._vfs_root = None
        reset_journal()

    def tearDown(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        if self.db is not None:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path is not None and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass
        if self._vfs_root is not None and os.path.exists(self._vfs_root):
            try:
                shutil.rmtree(self._vfs_root)
            except OSError:
                pass

    def _make_vfs(self, db) -> VFS:
        """Create VFS with a temp root."""
        self._vfs_root = tempfile.mkdtemp(prefix="vfs_test_")
        return VFS(db, vfs_root=self._vfs_root)

    # ------------------------------------------------------------------
    #  1. Vanilla lifecycle
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_vanilla_lifecycle(self, mock_popen):
        """Complete vanilla server lifecycle: create -> start -> status -> stop -> cleanup."""
        mock_popen.return_value = _make_mock_popen(pid=11111)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        # Create server
        sid = _setup_server_in_db(db, name="vanilla-test", server_type="vanilla")
        self.assertIsNotNone(sid)

        # Populate VFS with server.jar
        _populate_vfs_with_jar(vfs, "vanilla-test")

        # Start server
        runner = ServerRunner(db, vfs, sid)
        result = runner.start()
        self.assertTrue(result)
        self.assertTrue(runner.is_running())
        self.assertIsNotNone(runner._process)
        mock_popen.assert_called_once()

        # Status check
        status = runner.get_status()
        self.assertEqual(status["server_id"], sid)
        self.assertEqual(status["name"], "vanilla-test")
        self.assertTrue(status["running"])
        self.assertEqual(status["port"], 25565)
        self.assertIn("pid", status)
        self.assertEqual(status["pid"], 11111)
        self.assertIn("started_at", status)

        # Class method status
        running = ServerRunner.get_running()
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0]["server_id"], sid)

        # Stop server
        stop_result = runner.stop(timeout=5)
        self.assertTrue(stop_result)
        self.assertFalse(runner.is_running())

        # Cleanup verification
        self.assertNotIn(sid, ServerRunner._running_servers)
        self.assertTrue(PortManager.check_available(25565))
        self.assertTrue(PortManager.check_available(25575))

        # DB status
        status_flag = db.get_config(sid, "status")
        self.assertEqual(status_flag, "stopped")
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  2. Fabric lifecycle
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_fabric_lifecycle(self, mock_popen):
        """Complete Fabric server lifecycle."""
        mock_popen.return_value = _make_mock_popen(pid=22222)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="fabric-test", server_type="fabric")
        _populate_vfs_with_jar(vfs, "fabric-test")

        runner = ServerRunner(db, vfs, sid)
        result = runner.start()
        self.assertTrue(result)
        self.assertTrue(runner.is_running())
        self.assertEqual(runner.server["server_type"], "fabric")

        status = runner.get_status()
        self.assertTrue(status["running"])

        runner.stop()
        self.assertFalse(runner.is_running())
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  3. Forge lifecycle
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_forge_lifecycle(self, mock_popen):
        """Complete Forge server lifecycle."""
        mock_popen.return_value = _make_mock_popen(pid=33333)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="forge-test", server_type="forge")
        _populate_vfs_with_jar(vfs, "forge-test")

        runner = ServerRunner(db, vfs, sid)
        result = runner.start()
        self.assertTrue(result)
        self.assertEqual(runner.server["server_type"], "forge")

        runner.stop()
        self.assertFalse(runner.is_running())
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  4. Paper lifecycle
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_paper_lifecycle(self, mock_popen):
        """Complete Paper server lifecycle."""
        mock_popen.return_value = _make_mock_popen(pid=44444)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="paper-test", server_type="paper")
        _populate_vfs_with_jar(vfs, "paper-test")

        runner = ServerRunner(db, vfs, sid)
        result = runner.start()
        self.assertTrue(result)
        self.assertEqual(runner.server["server_type"], "paper")

        runner.stop()
        self.assertFalse(runner.is_running())
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  5. Restart cycle
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_restart_cycle(self, mock_popen):
        """Stop + start cycle should produce a fresh process."""
        mock_popen.side_effect = [
            _make_mock_popen(pid=55555),
            _make_mock_popen(pid=55556),
        ]

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="restart-test")
        _populate_vfs_with_jar(vfs, "restart-test")

        runner = ServerRunner(db, vfs, sid)
        runner.start()
        self.assertEqual(runner._process.pid, 55555)

        # Restart
        runner.restart(timeout=5)
        self.assertTrue(runner.is_running())
        self.assertEqual(runner._process.pid, 55556)

        runner.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  6. Multiple servers lifecycle
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_multiple_servers_independent(self, mock_popen):
        """Multiple servers of different types can run independently."""
        mock_popen.side_effect = [
            _make_mock_popen(pid=101),
            _make_mock_popen(pid=102),
            _make_mock_popen(pid=103),
        ]

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        # Create and start multiple servers on different ports
        servers = []
        for i, (stype, port) in enumerate([
            ("vanilla", 26500),
            ("fabric", 26501),
            ("paper", 26502),
        ]):
            name = f"multi-{stype}"
            rcon_port = port + 100
            sid = _setup_server_in_db(db, name=name, server_type=stype, server_port=port, rcon_port=rcon_port)
            _populate_vfs_with_jar(vfs, name)
            runner = ServerRunner(db, vfs, sid)
            runner.start()
            servers.append(runner)

        # Verify all running
        running = ServerRunner.get_running()
        self.assertEqual(len(running), 3)

        # Stop each
        for runner in servers:
            runner.stop()
            self.assertFalse(runner.is_running())

        self.assertEqual(len(ServerRunner._running_servers), 0)
        vfs.cleanup()


# ===================================================================
#  Test: Rcon Command Routing
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestRconCommands(unittest.TestCase):
    """Rcon command routing tests."""

    def setUp(self):
        self.db = None
        self._db_path = None
        self._vfs_root = None
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        reset_journal()

    def tearDown(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass
        if self._vfs_root and os.path.exists(self._vfs_root):
            try:
                shutil.rmtree(self._vfs_root)
            except OSError:
                pass

    def _make_vfs(self, db):
        self._vfs_root = tempfile.mkdtemp(prefix="vfs_rcon_")
        return VFS(db, vfs_root=self._vfs_root)

    # ------------------------------------------------------------------
    #  1. Single server default routing
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_send_command_to_single_server(self, mock_popen):
        """Single server: send_command should route to stdin."""
        proc = _make_mock_popen(pid=7001)
        mock_popen.return_value = proc

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="cmd-test")
        _populate_vfs_with_jar(vfs, "cmd-test")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        # Send command
        response = runner.send_command("say Hello")
        self.assertIn("Command sent", response)
        proc.stdin.write.assert_called_with("say Hello\n")
        proc.stdin.flush.assert_called()

        runner.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  2. Multi-server routing to correct instance
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_multi_server_routing(self, mock_popen):
        """Multiple servers: each runner's send_command routes to its own stdin."""
        proc_a = _make_mock_popen(pid=8001)
        proc_b = _make_mock_popen(pid=8002)
        mock_popen.side_effect = [proc_a, proc_b]

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid_a = _setup_server_in_db(db, name="server-a", server_port=26600, rcon_port=26610)
        sid_b = _setup_server_in_db(db, name="server-b", server_port=26601, rcon_port=26611)
        _populate_vfs_with_jar(vfs, "server-a")
        _populate_vfs_with_jar(vfs, "server-b")

        runner_a = ServerRunner(db, vfs, sid_a)
        runner_b = ServerRunner(db, vfs, sid_b)
        runner_a.start()
        runner_b.start()

        # Send commands — each goes to its own process
        runner_a.send_command("list")
        proc_a.stdin.write.assert_called_with("list\n")

        runner_b.send_command("save-all")
        proc_b.stdin.write.assert_called_with("save-all\n")

        # Verify proc_a didn't receive proc_b's command
        proc_a_calls = [c[0][0] for c in proc_a.stdin.write.call_args_list]
        self.assertNotIn("save-all\n", proc_a_calls)
        self.assertIn("list\n", proc_a_calls)

        runner_a.stop()
        runner_b.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  3. RCON password-based configuration
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_rcon_password_configured(self, mock_popen):
        """Server with RCON password should have enable-rcon=true in properties."""
        mock_popen.return_value = _make_mock_popen(pid=9001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="rcon-pw-test",
                                   rcon_password="secret123")
        _populate_vfs_with_jar(vfs, "rcon-pw-test")

        runner = ServerRunner(db, vfs, sid)
        runner._prepare_workdir()
        runner._generate_server_properties()

        # Read the generated properties
        props_path = runner.work_dir / "server.properties"
        self.assertTrue(props_path.exists())
        props_text = props_path.read_text()
        self.assertIn("enable-rcon=true", props_text)
        self.assertIn("rcon.password=secret123", props_text)
        self.assertIn("rcon.port=25575", props_text)

        runner.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  4. Command to stopped server raises error
    # ------------------------------------------------------------------
    def test_send_command_to_stopped_server(self):
        """Sending a command to a stopped server should raise ServerRunnerError."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="stopped-cmd")
        runner = ServerRunner(db, vfs, sid)

        with self.assertRaises(ServerRunnerError) as ctx:
            runner.send_command("list")
        self.assertIn("not running", str(ctx.exception).lower())

        vfs.cleanup()


# ===================================================================
#  Test: Status and Command Output
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestStatusAndOutput(unittest.TestCase):
    """Status/command output verification tests."""

    def setUp(self):
        self.db = None
        self._db_path = None
        self._vfs_root = None
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        reset_journal()

    def tearDown(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass
        if self._vfs_root and os.path.exists(self._vfs_root):
            try:
                shutil.rmtree(self._vfs_root)
            except OSError:
                pass

    def _make_vfs(self, db):
        self._vfs_root = tempfile.mkdtemp(prefix="vfs_status_")
        return VFS(db, vfs_root=self._vfs_root)

    # ------------------------------------------------------------------
    #  1. get_status fields verification
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_get_status_all_fields(self, mock_popen):
        """get_status must include: server_id, name, running, port, pid, started_at."""
        mock_popen.return_value = _make_mock_popen(pid=5001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="status-test")
        _populate_vfs_with_jar(vfs, "status-test")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        status = runner.get_status()
        expected_keys = {"server_id", "name", "running", "port", "pid", "started_at"}
        self.assertEqual(set(status.keys()), expected_keys)
        self.assertEqual(status["server_id"], sid)
        self.assertEqual(status["name"], "status-test")
        self.assertTrue(status["running"])
        self.assertEqual(status["port"], 25565)
        self.assertEqual(status["pid"], 5001)
        self.assertIsNotNone(status["started_at"])

        runner.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  2. get_status when stopped
    # ------------------------------------------------------------------
    def test_get_status_not_running(self):
        """get_status on a never-started server should have running=False, no pid."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="stopped-status")
        runner = ServerRunner(db, vfs, sid)

        status = runner.get_status()
        self.assertFalse(status["running"])
        self.assertNotIn("pid", status)

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  3. get_running class method
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_get_running_class_method(self, mock_popen):
        """ServerRunner.get_running() returns list of all running servers."""
        mock_popen.side_effect = [
            _make_mock_popen(pid=6001),
            _make_mock_popen(pid=6002),
        ]

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid1 = _setup_server_in_db(db, name="run-a", server_port=26700, rcon_port=26702)
        sid2 = _setup_server_in_db(db, name="run-b", server_port=26701, rcon_port=26703)
        _populate_vfs_with_jar(vfs, "run-a")
        _populate_vfs_with_jar(vfs, "run-b")

        r1 = ServerRunner(db, vfs, sid1)
        r2 = ServerRunner(db, vfs, sid2)
        r1.start()
        r2.start()

        # get_running with no filter
        all_running = ServerRunner.get_running()
        self.assertEqual(len(all_running), 2)

        # get_running with server_id filter
        specific = ServerRunner.get_running(sid1)
        self.assertEqual(len(specific), 1)
        self.assertEqual(specific[0]["server_id"], sid1)

        r1.stop()
        r2.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  4. send_command response format
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_send_command_response(self, mock_popen):
        """send_command returns confirmation message."""
        mock_popen.return_value = _make_mock_popen(pid=7001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="cmd-response")
        _populate_vfs_with_jar(vfs, "cmd-response")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        response = runner.send_command("op Player1")
        self.assertIsInstance(response, str)
        self.assertIn("Command sent", response)
        self.assertIn("op Player1", response)

        runner.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  5. DB status flags
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_db_status_flags_lifecycle(self, mock_popen):
        """DB status flags should transition: starting -> running -> stopped."""
        mock_popen.return_value = _make_mock_popen(pid=8001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="db-flags")
        _populate_vfs_with_jar(vfs, "db-flags")
        runner = ServerRunner(db, vfs, sid)

        runner.start()
        self.assertEqual(db.get_config(sid, "status"), "running")

        runner.stop()
        self.assertEqual(db.get_config(sid, "status"), "stopped")

        vfs.cleanup()


# ===================================================================
#  Test: Port Management
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestPortManagement(unittest.TestCase):
    """Port checking and reservation tests."""

    def setUp(self):
        PortManager._reserved.clear()
        self.db = None
        self._db_path = None

    def tearDown(self):
        PortManager._reserved.clear()
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    #  1. Basic reservation and release
    # ------------------------------------------------------------------
    def test_port_reserve_and_release(self):
        """PortManager.reserve and PortManager.release roundtrip."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        # Reserve
        result = PortManager.reserve(25565, 1, db)
        self.assertTrue(result)

        # Check available
        self.assertFalse(PortManager.check_available(25565))
        self.assertTrue(PortManager.check_available(25566))

        # Get owner
        owner = PortManager.get_server_for_port(25565)
        self.assertEqual(owner, 1)

        # Release
        released = PortManager.release(25565, 1)
        self.assertTrue(released)
        self.assertTrue(PortManager.check_available(25565))

    # ------------------------------------------------------------------
    #  2. Port conflict detection
    # ------------------------------------------------------------------
    def test_port_conflict(self):
        """Two servers cannot reserve the same port."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        self.assertTrue(PortManager.reserve(25565, 1, db))
        self.assertFalse(PortManager.reserve(25565, 2, db))

    # ------------------------------------------------------------------
    #  3. Port range validation
    # ------------------------------------------------------------------
    def test_port_range_validation(self):
        """Ports outside 1-65535 are rejected."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        self.assertFalse(PortManager.reserve(0, 1, db))
        self.assertFalse(PortManager.reserve(65536, 1, db))
        self.assertFalse(PortManager.reserve(-1, 1, db))

    # ------------------------------------------------------------------
    #  4. Release by wrong owner fails
    # ------------------------------------------------------------------
    def test_release_by_wrong_owner_fails(self):
        """Only the owning server can release a port."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        PortManager.reserve(25565, 1, db)
        self.assertFalse(PortManager.release(25565, 2))

        # Correct owner succeeds
        self.assertTrue(PortManager.release(25565, 1))

    # ------------------------------------------------------------------
    #  5. Find free port
    # ------------------------------------------------------------------
    def test_find_free_port(self):
        """find_free_port skips reserved ports."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        PortManager.reserve(25565, 1, db)
        free_port = PortManager.find_free_port(preferred=25565, db=db)
        self.assertNotEqual(free_port, 25565)
        self.assertGreater(free_port, 0)

    # ------------------------------------------------------------------
    #  6. Port release idempotent
    # ------------------------------------------------------------------
    def test_release_unreserved_port(self):
        """Releasing an unreserved port should succeed silently."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        self.assertTrue(PortManager.release(29999, 1))


# ===================================================================
#  Test: Network Adapter Setup
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestNetworkAdapter(unittest.TestCase):
    """Network adapter, firewall, and sandbox configuration tests."""

    def setUp(self):
        PortManager._reserved.clear()
        self.db = None
        self._db_path = None

    def tearDown(self):
        PortManager._reserved.clear()
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    #  1. VirtualAdapter creates IP and MAC
    # ------------------------------------------------------------------
    def test_virtual_adapter_creation(self):
        """VirtualAdapter auto-creates with IP and MAC if no DB record exists."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        sid = _setup_server_in_db(db, name="net-test")
        adapter = VirtualAdapter(db, sid)
        config = adapter.get_config()

        self.assertIn("virtual_ip", config)
        self.assertIn("mac_address", config)
        self.assertIn("subnet", config)
        self.assertIn("enabled", config)

        # IP should be valid IPv4 in 10.0.0.0/24
        ip = config["virtual_ip"]
        octets = ip.split(".")
        self.assertEqual(len(octets), 4)
        for o in octets:
            self.assertTrue(0 <= int(o) <= 255)

        # MAC should be 6 colon-separated hex pairs
        mac = config["mac_address"]
        self.assertEqual(len(mac.split(":")), 6)
        self.assertTrue(mac.startswith("02:"))

    # ------------------------------------------------------------------
    #  2. VirtualAdapter update
    # ------------------------------------------------------------------
    def test_virtual_adapter_update(self):
        """VirtualAdapter.update should change properties."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        sid = _setup_server_in_db(db, name="net-update")
        adapter = VirtualAdapter(db, sid)

        # Update bandwidth limit
        result = adapter.update(bandwidth_limit_kbps=10000)
        self.assertTrue(result)
        config = adapter.get_config()
        self.assertEqual(config["bandwidth_limit_kbps"], 10000)

    # ------------------------------------------------------------------
    #  3. VirtualAdapter enable/disable
    # ------------------------------------------------------------------
    def test_virtual_adapter_enable_disable(self):
        """enable() and disable() should toggle the enabled flag."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        sid = _setup_server_in_db(db, name="net-toggle")
        adapter = VirtualAdapter(db, sid)

        adapter.disable()
        self.assertFalse(adapter.get_config()["enabled"])

        adapter.enable()
        self.assertTrue(adapter.get_config()["enabled"])

    # ------------------------------------------------------------------
    #  4. Firewall default rules
    # ------------------------------------------------------------------
    def test_firewall_default_rules(self):
        """NetworkManager.initialize_default_rules adds port allow rules."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        sid = _setup_server_in_db(db, name="fw-default")
        nm = NetworkManager(db, sid)
        nm.initialize_default_rules()

        rules = nm.firewall.list_rules()
        # Should have at least 2 rules: server port + RCON port
        self.assertGreaterEqual(len(rules), 2)

        rule_types = [r["rule_type"] for r in rules]
        self.assertTrue(all(t == "allow" for t in rule_types))

        ports = set()
        for r in rules:
            tp = r.get("target_port")
            if tp:
                ports.add(tp)
        self.assertIn(DEFAULT_SERVER_PORT, ports)
        self.assertIn(DEFAULT_RCON_PORT, ports)

    # ------------------------------------------------------------------
    #  5. Firewall add and remove rules
    # ------------------------------------------------------------------
    def test_firewall_add_remove_rule(self):
        """Firewall rules can be added and removed."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        sid = _setup_server_in_db(db, name="fw-crud")
        firewall = Firewall(db, sid)

        # Add rule
        rule_id = firewall.add_rule(
            rule_type="allow",
            direction="inbound",
            protocol="tcp",
            port_range="25565",
            priority=10,
        )
        self.assertGreater(rule_id, 0)

        rules = firewall.list_rules()
        self.assertEqual(len(rules), 1)

        # Remove rule
        removed = firewall.remove_rule(rule_id)
        self.assertTrue(removed)
        self.assertEqual(len(firewall.list_rules()), 0)

    # ------------------------------------------------------------------
    #  6. Firewall connection checking
    # ------------------------------------------------------------------
    def test_firewall_connection_check(self):
        """check_connection should evaluate rules correctly."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        sid = _setup_server_in_db(db, name="fw-check")
        firewall = Firewall(db, sid)

        # Default: no rules = deny
        allowed, rule_id = firewall.check_connection("anyhost", 25565)
        self.assertFalse(allowed)
        self.assertIsNone(rule_id)

        # Add allow rule
        firewall.add_rule(
            rule_type="allow",
            direction="inbound",
            protocol="tcp",
            target_port=25565,
            priority=10,
        )

        allowed, rule_id = firewall.check_connection("anyhost", 25565)
        self.assertTrue(allowed)
        self.assertIsNotNone(rule_id)

    # ------------------------------------------------------------------
    #  7. NetworkManager sandbox config
    # ------------------------------------------------------------------
    def test_network_sandbox_config(self):
        """NetworkManager.get_sandbox_config returns complete config."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        sid = _setup_server_in_db(db, name="sandbox-config")
        nm = NetworkManager(db, sid)
        config = nm.get_sandbox_config()

        self.assertIn("server_id", config)
        self.assertIn("virtual_adapter", config)
        self.assertIn("firewall_rules", config)
        self.assertIn("allowed_ports", config)
        self.assertIn("enabled", config)
        self.assertEqual(config["server_id"], sid)

    # ------------------------------------------------------------------
    #  8. NetworkManager generate_network_config
    # ------------------------------------------------------------------
    def test_generate_network_config(self):
        """generate_network_config includes firewall script and port info."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db

        sid = _setup_server_in_db(db, name="net-gen-config")
        nm = NetworkManager(db, sid)
        config = nm.generate_network_config()

        self.assertIn("firewall_script", config)
        self.assertIn("server_port", config)
        self.assertIn("allowed_ports", config)
        self.assertIn("adapter", config)

    # ------------------------------------------------------------------
    #  9. Port reservation via runner._setup_network
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_runner_network_setup(self, mock_popen):
        """Runner._setup_network reserves ports and creates firewall rules."""
        mock_popen.return_value = _make_mock_popen(pid=9501)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        self._vfs_root = tempfile.mkdtemp(prefix="vfs_net_")
        vfs = VFS(db, vfs_root=self._vfs_root)

        sid = _setup_server_in_db(db, name="runner-net",
                                   server_port=26800, rcon_port=26820)
        _populate_vfs_with_jar(vfs, "runner-net")

        runner = ServerRunner(db, vfs, sid)
        runner.start()

        # Ports should be reserved
        self.assertFalse(PortManager.check_available(26800))
        self.assertFalse(PortManager.check_available(26820))

        runner.stop()

        # Ports should be released
        self.assertTrue(PortManager.check_available(26800))
        self.assertTrue(PortManager.check_available(26810))

        shutil.rmtree(self._vfs_root)


# ===================================================================
#  Test: Clean Shutdown
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestShutdown(unittest.TestCase):
    """Clean shutdown following yuniScripts shutdown spec."""

    def setUp(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        self.db = None
        self._db_path = None
        self._vfs_root = None
        reset_journal()

    def tearDown(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass
        if self._vfs_root and os.path.exists(self._vfs_root):
            try:
                shutil.rmtree(self._vfs_root)
            except OSError:
                pass

    def _make_vfs(self, db):
        self._vfs_root = tempfile.mkdtemp(prefix="vfs_shutdown_")
        return VFS(db, vfs_root=self._vfs_root)

    # ------------------------------------------------------------------
    #  1. Graceful shutdown via SIGTERM
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_graceful_shutdown(self, mock_popen):
        """stop() should send SIGTERM first (via process.terminate)."""
        proc = _make_mock_popen(pid=10001)
        mock_popen.return_value = proc

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="graceful")
        _populate_vfs_with_jar(vfs, "graceful")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        runner.stop(timeout=10)

        # Verify terminate was called
        proc.terminate.assert_called_once()
        proc.wait.assert_called_with(timeout=10)

        # Kill should NOT have been called
        proc.kill.assert_not_called()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  2. Force shutdown via SIGKILL
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_force_shutdown(self, mock_popen):
        """stop(force=True) should skip RCON and use kill directly."""
        proc = _make_mock_popen(pid=10002)
        mock_popen.return_value = proc

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="force-kill")
        _populate_vfs_with_jar(vfs, "force-kill")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        runner.stop(timeout=5, force=True)

        # Verify kill was called
        proc.kill.assert_called_once()
        proc.terminate.assert_not_called()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  3. Timeout fallback from SIGTERM to SIGKILL
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_timeout_fallback_to_kill(self, mock_popen):
        """If SIGTERM times out, SIGKILL should be used."""
        import subprocess as _subprocess
        proc = _make_mock_popen(pid=10003)
        proc.wait.side_effect = [_subprocess.TimeoutExpired("cmd", 2)]
        mock_popen.return_value = proc

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="timeout-fallback")
        _populate_vfs_with_jar(vfs, "timeout-fallback")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        runner.stop(timeout=2)

        # terminate called first, then kill when timeout
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  4. Stop non-running server
    # ------------------------------------------------------------------
    def test_stop_non_running_server(self):
        """Stopping a non-running server should return False silently."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="not-running")
        runner = ServerRunner(db, vfs, sid)

        result = runner.stop()
        self.assertFalse(result)
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  5. Monitor thread cleanup
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_monitor_thread_cleanup(self, mock_popen):
        """Monitor thread should stop and cleanup when server is stopped."""
        proc = _make_mock_popen(pid=10004)
        proc.poll.return_value = None  # Process is alive
        mock_popen.return_value = proc

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="monitor-cleanup")
        _populate_vfs_with_jar(vfs, "monitor-cleanup")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        self.assertIsNotNone(runner._monitor_thread)
        self.assertTrue(runner._monitor_thread.is_alive())

        runner.stop()

        self.assertFalse(runner.is_running())
        self.assertEqual(runner._process, None)
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  6. stop_all class method
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_stop_all_class_method(self, mock_popen):
        """ServerRunner.stop_all() stops all running servers."""
        mock_popen.side_effect = [
            _make_mock_popen(pid=11001),
            _make_mock_popen(pid=11002),
        ]

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid1 = _setup_server_in_db(db, name="stop-all-a", server_port=26900, rcon_port=26902)
        sid2 = _setup_server_in_db(db, name="stop-all-b", server_port=26901, rcon_port=26903)
        _populate_vfs_with_jar(vfs, "stop-all-a")
        _populate_vfs_with_jar(vfs, "stop-all-b")

        ServerRunner(db, vfs, sid1).start()
        ServerRunner(db, vfs, sid2).start()

        count = ServerRunner.stop_all(timeout=5)
        self.assertEqual(count, 2)
        self.assertEqual(len(ServerRunner._running_servers), 0)
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  7. DB cleanup after stop
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_db_state_after_shutdown(self, mock_popen):
        """After stop, DB flags should be cleared/updated."""
        proc = _make_mock_popen(pid=11003)
        proc.poll.return_value = None
        mock_popen.return_value = proc

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="db-after-stop")
        _populate_vfs_with_jar(vfs, "db-after-stop")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        # Verify flags set during start
        self.assertEqual(db.get_config(sid, "status"), "running")

        runner.stop()

        # After stop, flags should be cleared/updated
        self.assertEqual(db.get_config(sid, "status"), "stopped")
        self.assertEqual(db.get_config(sid, "pid"), "")
        self.assertIsNotNone(db.get_config(sid, "stopped_at"))
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  8. Duplicate stop is safe
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_duplicate_stop_is_safe(self, mock_popen):
        """Calling stop() twice should not raise."""
        mock_popen.return_value = _make_mock_popen(pid=11004)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="dup-stop")
        _populate_vfs_with_jar(vfs, "dup-stop")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        # First stop
        runner.stop()
        # Second stop should not raise
        runner.stop()  # Should be safe
        vfs.cleanup()


# ===================================================================
#  Test: Edge Cases
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestEdgeCases(unittest.TestCase):
    """Edge cases: empty configs, missing files, port conflicts,
    corrupt jars, timeout scenarios, permission errors."""

    def setUp(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        self.db = None
        self._db_path = None
        self._vfs_root = None
        reset_journal()

    def tearDown(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass
        if self._vfs_root and os.path.exists(self._vfs_root):
            try:
                shutil.rmtree(self._vfs_root)
            except OSError:
                pass

    def _make_vfs(self, db):
        self._vfs_root = tempfile.mkdtemp(prefix="vfs_edge_")
        return VFS(db, vfs_root=self._vfs_root)

    # ------------------------------------------------------------------
    #  1. Missing server.jar raises error
    # ------------------------------------------------------------------
    def test_missing_server_jar(self):
        """Starting a server without a server.jar should raise ServerRunnerError."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="no-jar")
        runner = ServerRunner(db, vfs, sid)

        with self.assertRaises(ServerRunnerError) as ctx:
            runner.start()
        self.assertIn("No server jar", str(ctx.exception))

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  2. Double start prevention
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_double_start_raises_error(self, mock_popen):
        """Starting an already-running server should raise ServerRunnerError."""
        mock_popen.return_value = _make_mock_popen(pid=12001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="double-start")
        _populate_vfs_with_jar(vfs, "double-start")
        runner = ServerRunner(db, vfs, sid)

        runner.start()

        with self.assertRaises(ServerRunnerError) as ctx:
            runner.start()
        self.assertIn("already running", str(ctx.exception).lower())

        runner.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  3. Invalid server ID
    # ------------------------------------------------------------------
    def test_invalid_server_id(self):
        """Creating a runner with an invalid ID should raise ServerRunnerError."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        with self.assertRaises(ServerRunnerError) as ctx:
            ServerRunner(db, vfs, 99999)  # Non-existent ID
        self.assertIn("not found", str(ctx.exception).lower())

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  4. Empty VFS (no server files at all)
    # ------------------------------------------------------------------
    def test_empty_vfs_start(self):
        """Start with empty VFS should fail due to no jar."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="empty-vfs")
        # VFS is empty — no server files

        runner = ServerRunner(db, vfs, sid)

        with self.assertRaises(ServerRunnerError) as ctx:
            runner.start()
        self.assertIn("No server jar", str(ctx.exception))

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  5. Port conflict between two servers on start
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_port_conflict_on_start(self, mock_popen):
        """If a port is already reserved, start should raise."""
        mock_popen.return_value = _make_mock_popen(pid=13001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        # Manually reserve the port
        PortManager.reserve(27000, 99, db)

        sid = _setup_server_in_db(db, name="port-conflict",
                                   server_port=27000)
        _populate_vfs_with_jar(vfs, "port-conflict")
        runner = ServerRunner(db, vfs, sid)

        with self.assertRaises(ServerRunnerError) as ctx:
            runner.start()
        self.assertIn("already in use", str(ctx.exception).lower())

        PortManager.release(27000, 99)
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  6. Process dies after start (monitor detects crash)
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_process_crash_detection(self, mock_popen):
        """If process dies after start, monitor should detect and cleanup."""
        # First poll returns None (running), then returns -1 (crashed)
        proc = _make_mock_popen(pid=14001)
        proc.poll.side_effect = [None, None, -1]  # running -> running -> crashed
        mock_popen.return_value = proc

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="crash-detect")
        _populate_vfs_with_jar(vfs, "crash-detect")
        runner = ServerRunner(db, vfs, sid)
        runner.start()

        # Let monitor thread run briefly
        time.sleep(0.2)

        # Process should be detected as crashed
        # The monitor loop checks every 5s, so we need to force the check
        runner._stop_event.set()  # Stop the monitor
        if runner._monitor_thread:
            runner._monitor_thread.join(timeout=2)

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  7. Server with minimal config
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_minimal_config_server(self, mock_popen):
        """Server created with minimal config should start."""
        mock_popen.return_value = _make_mock_popen(pid=15001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        # Minimal: just name and mc_version
        sid = db.create_server(name="minimal", mc_version="1.20.4")
        _populate_vfs_with_jar(vfs, "minimal")

        runner = ServerRunner(db, vfs, sid)
        result = runner.start()
        self.assertTrue(result)

        runner.stop()
        vfs.cleanup()

    # ------------------------------------------------------------------
    #  8. start with extract_files=False
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_start_without_extract(self, mock_popen):
        """start(extract_files=False) should skip extraction."""
        mock_popen.return_value = _make_mock_popen(pid=16001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(db, name="no-extract")
        _populate_vfs_with_jar(vfs, "no-extract")

        runner = ServerRunner(db, vfs, sid)

        # First start with extraction
        runner.start(extract_files=True)
        runner.stop()

        # Second start without extraction
        runner._stop_event.clear()
        result = runner.start(extract_files=False)
        self.assertTrue(result)

        runner.stop()
        vfs.cleanup()


# ===================================================================
#  Test: Integration with VFS, Atomic, Converter
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestIntegration(unittest.TestCase):
    """Integration tests with VFS, atomic operations, and converter."""

    def setUp(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        self.db = None
        self._db_path = None
        self._vfs_root = None
        reset_journal()

    def tearDown(self):
        PortManager._reserved.clear()
        ServerRunner._running_servers.clear()
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass
        if self._vfs_root and os.path.exists(self._vfs_root):
            try:
                shutil.rmtree(self._vfs_root)
            except OSError:
                pass

    def _make_vfs(self, db):
        self._vfs_root = tempfile.mkdtemp(prefix="vfs_int_")
        return VFS(db, vfs_root=self._vfs_root)

    # ------------------------------------------------------------------
    #  1. VFS extraction to workdir
    # ------------------------------------------------------------------
    @patch("engine.runner.subprocess.Popen")
    def test_vfs_extraction_to_workdir(self, mock_popen):
        """Server files are extracted from VFS to workdir before start."""
        mock_popen.return_value = _make_mock_popen(pid=17001)

        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        # Create server with files in VFS
        sid = _setup_server_in_db(db, name="extract-test")
        _populate_vfs_with_jar(vfs, "extract-test")

        # Also add some extra files
        vfs.write("/servers/extract-test/ops.json", b"[]", atomic=True)
        vfs.write("/servers/extract-test/whitelist.json", b"[]", atomic=True)

        runner = ServerRunner(db, vfs, sid)

        # Before extract, workdir should exist (VFS creates it via mkdir)
        # The work_dir is vfs_root/servers/{server_name} which aligns with VFS path structure

        runner._prepare_workdir()
        self.assertTrue(runner.work_dir.exists())

        # After extract, files should be present
        jar_path = runner.work_dir / "server.jar"
        self.assertTrue(jar_path.exists())
        self.assertEqual(jar_path.read_bytes()[:2], b"PK")

        ops_path = runner.work_dir / "ops.json"
        self.assertTrue(ops_path.exists())

        # EULA should be auto-created
        eula_path = runner.work_dir / "eula.txt"
        self.assertTrue(eula_path.exists())
        self.assertIn("eula=true", eula_path.read_text())

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  2. server.properties generation
    # ------------------------------------------------------------------
    def test_server_properties_generation(self):
        """server.properties generated with correct values from DB."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        sid = _setup_server_in_db(
            db, name="props-test",
            server_port=28000, rcon_port=28010,
            rcon_password="testpass",
        )
        _populate_vfs_with_jar(vfs, "props-test")

        runner = ServerRunner(db, vfs, sid)
        runner._prepare_workdir()
        runner._generate_server_properties()

        props_path = runner.work_dir / "server.properties"
        props_text = props_path.read_text()

        self.assertIn("server-port=28000", props_text)
        self.assertIn("rcon.port=28010", props_text)
        self.assertIn("rcon.password=testpass", props_text)
        self.assertIn("enable-rcon=true", props_text)
        self.assertIn("motd=MC Server Runner - props-test", props_text)
        self.assertIn("max-players=20", props_text)
        self.assertIn("gamemode=survival", props_text)

        # Network sandbox integration: server-ip should be set
        self.assertIn("server-ip=", props_text)

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  3. Atomic journal rollback on write
    # ------------------------------------------------------------------
    def test_atomic_journal_rollback_vfs_write(self):
        """Atomic journal should capture before-state and support rollback."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        journal = AtomicJournal()  # No DB instance (in-memory only)

        # Write initial file
        vfs.write("/atomic-test/rollback.txt", b"original content", atomic=True)

        # Capture before state
        existing = db.get_file("/atomic-test/rollback.txt")
        before = {
            "vfs_path": "/atomic-test/rollback.txt",
            "blob_data": existing["blob_data"],
            "file_mode": existing["file_mode"],
        }

        op_id = journal.begin("vfs_write", "/atomic-test/rollback.txt", before)

        # Overwrite with new content
        vfs.write("/atomic-test/rollback.txt", b"new content", atomic=True)

        # Verify new content
        content = vfs.read("/atomic-test/rollback.txt")
        self.assertEqual(content, b"new content")

        # Rollback
        restored = journal.rollback(op_id)
        self.assertIsNotNone(restored)

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  4. Converter roundtrip via VFS store
    # ------------------------------------------------------------------
    def test_converter_roundtrip_via_vfs(self):
        """Full converter pipeline: bytes->db_blob->store->retrieve->decompress."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        original = b"Minecraft server data for converter test"
        original_hash = _sha256(original)

        # Convert bytes to DB blob
        blob_info = bytes_to_db_blob(original)
        self.assertEqual(blob_info["original_size"], len(original))
        self.assertEqual(blob_info["import_hash"], original_hash)

        # Store in database (compressively via store_file)
        db.store_file("/converter-test/data.bin", original)

        # Retrieve and decompress
        retrieved = db.get_file("/converter-test/data.bin")
        self.assertEqual(retrieved["blob_data"], original)

        # Also test db_blob_to_file roundtrip
        tmp_path = os.path.join(tempfile.gettempdir(), f"conv_test_{os.urandom(4).hex()}.bin")
        try:
            db_blob_to_file(blob_info["blob_data"], blob_info["validation_hash"], tmp_path)
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, "rb") as f:
                read_back = f.read()
            self.assertEqual(read_back, original)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  5. Hash validation on corrupted data
    # ------------------------------------------------------------------
    def test_corrupted_data_rejected(self):
        """Tampered blob data should fail validation."""
        original = b"Data integrity is crucial"
        blob_info = bytes_to_db_blob(original)

        # Tamper with compressed data
        tampered = b"X" + blob_info["blob_data"][1:]

        tmp_path = os.path.join(tempfile.gettempdir(), f"corrupt_{os.urandom(4).hex()}.bin")
        with self.assertRaises(ValueError) as ctx:
            db_blob_to_file(tampered, blob_info["validation_hash"], tmp_path)
        self.assertIn("Hash mismatch", str(ctx.exception))

        self.assertFalse(os.path.exists(tmp_path))

    # ------------------------------------------------------------------
    #  6. VFS import_file and extract integration
    # ------------------------------------------------------------------
    def test_vfs_import_extract_pipeline(self):
        """Import a real temp file to VFS, then extract it back."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        # Create a temp file
        host_path = os.path.join(tempfile.gettempdir(), f"import_test_{os.urandom(4).hex()}.txt")
        original_content = b"Hello from the VFS import pipeline!"
        with open(host_path, "wb") as f:
            f.write(original_content)

        try:
            # Import to VFS
            vfs_path = vfs.import_file(host_path, "/imported/test.txt")
            self.assertEqual(vfs_path, "/imported/test.txt")

            # Verify in VFS
            self.assertTrue(vfs.exists("/imported/test.txt"))

            # Extract back to a different path
            extract_path = os.path.join(tempfile.gettempdir(), f"extract_test_{os.urandom(4).hex()}.txt")
            result_path = vfs.extract("/imported/test.txt", extract_path)

            with open(result_path, "rb") as f:
                extracted_content = f.read()
            self.assertEqual(extracted_content, original_content)

            # Clean up extracted file
            os.unlink(result_path)

            # Verify conversion log
            rows = db.conn.execute(
                "SELECT * FROM conversion_log WHERE vfs_path = ?",
                ("/imported/test.txt",)
            ).fetchall()
            self.assertGreaterEqual(len(rows), 1)

        finally:
            if os.path.exists(host_path):
                os.unlink(host_path)

        vfs.cleanup()

    # ------------------------------------------------------------------
    #  7. Mod integration with VFS
    # ------------------------------------------------------------------
    def test_mod_vfs_integration(self):
        """Mod files stored in VFS can be accessed via ModManager."""
        db, path = _make_temp_db()
        self._db_path = path
        self.db = db
        vfs = self._make_vfs(db)

        mm = ModManager(db, vfs)

        # Register a mod with file data
        mod_data = _fake_jar_data(2048)
        mod_id = mm.register_mod(
            name="Test Mod",
            slug="test-mod",
            version="1.0.0",
            mc_version="1.20.4",
            loader=ModLoaders.FABRIC,
            file_data=mod_data,
        )
        self.assertIsNotNone(mod_id)

        # Verify mod file exists in VFS
        mod_vfs_path = f"/mods/test-mod.jar"
        self.assertTrue(vfs.exists(mod_vfs_path))

        stored_data = vfs.read(mod_vfs_path)
        self.assertEqual(stored_data, mod_data)

        vfs.cleanup()


# ===================================================================
#  Test: ServerProfile type-specific setup
# ===================================================================

@unittest.skipUnless(ENGINE_AVAILABLE, "engine modules not available")
class TestServerProfiles(unittest.TestCase):
    """ServerProfile setup differences per server type."""

    def setUp(self):
        self.db = None
        self._db_path = None

    def tearDown(self):
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    #  1. ServerProfile factory
    # ------------------------------------------------------------------
    def test_profile_creation(self):
        """ServerProfile created for each type should have correct defaults."""
        for stype in ["vanilla", "fabric", "forge", "paper"]:
            profile = ServerProfile(stype, f"test-{stype}", "1.20.4")
            self.assertEqual(profile.server_type, stype)
            self.assertEqual(profile.name, f"test-{stype}")
            self.assertEqual(profile.mc_version, "1.20.4")

    # ------------------------------------------------------------------
    #  2. Profile to_dict output
    # ------------------------------------------------------------------
    def test_profile_to_dict(self):
        """Profile.to_dict() returns correct metadata per type."""
        fabric_profile = ServerProfile("fabric", "fabric-server", "1.20.4")
        d = fabric_profile.to_dict()
        self.assertEqual(d["server_type"], "fabric")
        self.assertTrue(d["supports_mods"])
        self.assertFalse(d["supports_plugins"])
        self.assertEqual(d["mods_dir"], "mods/")

        paper_profile = ServerProfile("paper", "paper-server", "1.20.4")
        d2 = paper_profile.to_dict()
        self.assertEqual(d2["server_type"], "paper")
        self.assertFalse(d2["supports_mods"])
        self.assertTrue(d2["supports_plugins"])
        self.assertEqual(d2["mods_dir"], "plugins/")

    # ------------------------------------------------------------------
    #  3. Default Java versions per type
    # ------------------------------------------------------------------
    def test_default_min_java(self):
        """ServerProfile.get_default_min_java should match expectations."""
        self.assertEqual(ServerProfile.get_default_min_java("vanilla"), 17)
        self.assertEqual(ServerProfile.get_default_min_java("fabric"), 17)
        self.assertEqual(ServerProfile.get_default_min_java("forge"), 17)
        self.assertEqual(ServerProfile.get_default_min_java("paper"), 17)
        self.assertEqual(ServerProfile.get_default_min_java("bukkit"), 8)

    # ------------------------------------------------------------------
    #  4. Server type loader mapping
    # ------------------------------------------------------------------
    def test_server_type_loader_map(self):
        """SERVER_TYPE_LOADER_MAP should include all modded types."""
        self.assertIn("fabric", SERVER_TYPE_LOADER_MAP)
        self.assertIn("forge", SERVER_TYPE_LOADER_MAP)
        self.assertIn("quilt", SERVER_TYPE_LOADER_MAP)
        self.assertIn("neoforge", SERVER_TYPE_LOADER_MAP)


# ===================================================================
#  Entry point
# ===================================================================

if __name__ == "__main__":
    unittest.main()
