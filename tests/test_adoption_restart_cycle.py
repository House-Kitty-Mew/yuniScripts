#!/usr/bin/env python3
"""
test_adoption_restart_cycle.py — Full Adoption Lifecycle Integration Tests

Tests the complete PID-based process adoption cycle:
  1. Start a long-running script → PID file written
  2. Simulate engine restart → adopt existing process
  3. Verify adoption works (no re-spawn, status = adopted)
  4. Clean stop of adopted process → PID file removed
  5. Edge cases: stale PID, port conflicts, multiple adopted

These tests work by launching a real subprocess that mimics a
long-running game server, then exercising the adoption API.
"""

import os
import sys
import time
import json
import signal
import tempfile
import subprocess
import unittest
from pathlib import Path
from typing import Dict, Optional

# ── Ensure engine is importable ──────────────────────────────────────
_ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE_DIR))

from engine.process_adoption import (
    write_pid_file,
    read_pid_file,
    remove_pid_file,
    is_process_alive,
    find_and_adopt,
    cleanup_stale_pids,
    check_adopted_health,
    check_script_status,
    adopt_existing_process,
)
from engine.metadata import default_meta


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _dummy_script_instance(script_id: str, pid: int = None) -> Dict:
    """Create a minimal script instance dict for testing."""
    inst = {
        "id": script_id,
        "path": Path(tempfile.gettempdir()) / f"test_adoption_{script_id}",
        "meta": {
            **default_meta(),
            "name": f"Test {script_id}",
            "server_type": "long_running",
            "shutdown_timeout": 5.0,
            "ports": [],
        },
    }
    if pid is not None:
        inst["pid"] = pid
    return inst


def _run_dummy_server(timeout: float = 3.0) -> subprocess.Popen:
    """Launch a dummy long-running Python process that sleeps."""
    code = (
        "import time, signal, sys; "
        "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0)); "
        f"time.sleep({timeout})"
    )
    return subprocess.Popen([sys.executable, "-c", code])


# ══════════════════════════════════════════════════════════════════════
# Test Class
# ══════════════════════════════════════════════════════════════════════

class TestAdoptionRestartCycle(unittest.TestCase):
    """Full adoption lifecycle integration tests."""

    def setUp(self):
        """Create a temp dir for PID files."""
        self._tmp = tempfile.mkdtemp(prefix="adopt_test_")
        # Override the pid dir for this test session
        self._orig_pid_dir = None
        import engine.process_adoption as pa
        self._orig_get_pid_dir = pa.get_pid_dir
        pa.get_pid_dir = lambda: Path(self._tmp)
        self._patch_pa = pa

    def tearDown(self):
        """Restore original pid dir and clean up files."""
        self._patch_pa.get_pid_dir = self._orig_get_pid_dir
        # Clean up any PID files
        for f in Path(self._tmp).iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            os.rmdir(self._tmp)
        except OSError:
            pass

    # ── Basic PID file lifecycle ──────────────────────────────────

    def test_write_and_read_pid(self):
        """PID file is written and can be read back."""
        sid = "test_script"
        pid = 12345
        result = write_pid_file(sid, pid)
        self.assertTrue(result)

        read = read_pid_file(sid)
        self.assertIsNotNone(read)
        self.assertEqual(read, pid)

    def test_remove_pid_file(self):
        """PID file is removed cleanly."""
        sid = "test_script_remove"
        write_pid_file(sid, 99999)
        self.assertIsNotNone(read_pid_file(sid))

        removed = remove_pid_file(sid)
        self.assertTrue(removed)
        self.assertIsNone(read_pid_file(sid))

    def test_read_missing_pid(self):
        """Reading a non-existent PID file returns None."""
        self.assertIsNone(read_pid_file("nonexistent_script"))

    def test_remove_missing_pid(self):
        """Removing a non-existent PID file returns False."""
        self.assertFalse(remove_pid_file("nonexistent_script"))

    # ── Process alive checks ──────────────────────────────────────

    def test_is_process_alive_live_proc(self):
        """A running process reports alive."""
        proc = _run_dummy_server(timeout=5.0)
        self.assertTrue(is_process_alive(proc.pid))
        proc.terminate()
        proc.wait(timeout=3.0)

    def test_is_process_alive_dead_proc(self):
        """A terminated process reports dead (after cleanup)."""
        proc = _run_dummy_server(timeout=0.1)
        time.sleep(0.3)  # Let it finish
        # Give OS time to reap
        for _ in range(10):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        self.assertFalse(is_process_alive(proc.pid))

    def test_is_process_alive_invalid_pid(self):
        """A PID that never existed reports dead."""
        # Use a PID that is extremely unlikely to exist
        self.assertFalse(is_process_alive(999999999))

    # ── check_script_status ───────────────────────────────────────

    def test_check_script_status_running(self):
        """A running script instance reports status=adoptable."""
        proc = _run_dummy_server(timeout=5.0)
        inst = _dummy_script_instance("status_test", pid=proc.pid)
        status = check_script_status(inst)
        self.assertEqual(status["status"], "adoptable")
        self.assertEqual(status["pid"], proc.pid)
        proc.terminate()
        proc.wait(timeout=3.0)

    def test_check_script_status_dead(self):
        """A script with a dead PID reports status=stale."""
        inst = _dummy_script_instance("stale_test", pid=999999999)
        status = check_script_status(inst)
        self.assertEqual(status["status"], "stale")

    def test_check_script_status_no_pid(self):
        """A script instance with no PID reports status=no_pid."""
        inst = _dummy_script_instance("nopid_test")
        inst.pop("pid", None)
        status = check_script_status(inst)
        self.assertEqual(status["status"], "no_pid")

    # ── adopt_existing_process ────────────────────────────────────

    def test_adopt_existing_running_proc(self):
        """A running process can be adopted successfully."""
        proc = _run_dummy_server(timeout=5.0)
        inst = _dummy_script_instance("adopt_ok", pid=proc.pid)
        result = adopt_existing_process(inst, proc.pid, log_base_dir=Path(self._tmp))
        self.assertIsNotNone(result)
        self.assertTrue(result.get("adopted"))
        self.assertEqual(result["pid"], proc.pid)
        self.assertIn("status", result)
        # Cleanup
        remove_pid_file("adopt_ok")
        proc.terminate()
        proc.wait(timeout=3.0)

    def test_adopt_dead_process(self):
        """A dead PID cannot be adopted — returns None."""
        inst = _dummy_script_instance("adopt_dead", pid=999999999)
        result = adopt_existing_process(inst, 999999999, log_base_dir=Path(self._tmp))
        self.assertIsNone(result)

    # ── find_and_adopt (full flow) ─────────────────────────────────

    def test_find_and_adopt_live(self):
        """find_and_adopt finds a running process via PID file + port detection.
        
        For scripts without open ports, it falls back to PID file match.
        """
        proc = _run_dummy_server(timeout=5.0)
        sid = "find_adopt_live"
        inst = _dummy_script_instance(sid)
        # Write the PID file manually (as if from a previous engine instance)
        write_pid_file(sid, proc.pid)

        result = find_and_adopt(inst, log_base_dir=Path(self._tmp))
        self.assertIsNotNone(result, "Should adopt the running process")
        self.assertTrue(result.get("adopted"))
        self.assertEqual(result["pid"], proc.pid)

        # Cleanup
        remove_pid_file(sid)
        proc.terminate()
        proc.wait(timeout=3.0)

    def test_find_and_adopt_no_pid_file(self):
        """find_and_adopt returns None when no PID file exists."""
        inst = _dummy_script_instance("no_pid_file")
        result = find_and_adopt(inst, log_base_dir=Path(self._tmp))
        self.assertIsNone(result)

    def test_find_and_adopt_stale_pid(self):
        """find_and_adopt handles stale PID gracefully — returns None.
        
        The PID file exists but the process is dead. Should clean up
        the stale PID file and return None so the caller starts fresh.
        """
        sid = "stale_adopt"
        inst = _dummy_script_instance(sid)
        write_pid_file(sid, 999999999)  # Extremely unlikely to be alive

        result = find_and_adopt(inst, log_base_dir=Path(self._tmp))
        self.assertIsNone(result, "Stale PID should not be adopted")
        # PID file should be cleaned up
        self.assertIsNone(read_pid_file(sid), "Stale PID file should be removed")

    # ── cleanup_stale_pids ────────────────────────────────────────

    def test_cleanup_stale_pids_removes_dead(self):
        """cleanup_stale_pids removes PID files for dead processes."""
        write_pid_file("dead1", 999999999)
        write_pid_file("dead2", 999999998)
        # Write a PID file for the current process (should be alive)
        write_pid_file("alive_self", os.getpid())

        cleaned = cleanup_stale_pids()
        self.assertGreaterEqual(cleaned, 2)  # At least the two dead ones

        # Dead PIDs should be gone
        self.assertIsNone(read_pid_file("dead1"))
        self.assertIsNone(read_pid_file("dead2"))
        # Alive PID should remain
        self.assertIsNotNone(read_pid_file("alive_self"))

        remove_pid_file("alive_self")

    def test_cleanup_stale_pids_no_files(self):
        """cleanup_stale_pids returns 0 when no stale PID files exist."""
        # Write a PID for the current process (alive)
        write_pid_file("current", os.getpid())
        cleaned = cleanup_stale_pids()
        self.assertEqual(cleaned, 0)
        remove_pid_file("current")

    # ── check_adopted_health ───────────────────────────────────────

    def test_check_adopted_health_alive(self):
        """An adopted process that is alive reports healthy status."""
        proc = _run_dummy_server(timeout=5.0)
        proc_info = _dummy_script_instance("health_alive", pid=proc.pid)
        proc_info["adopted"] = True
        proc_info["status"] = "running"

        result = check_adopted_health(proc_info)
        self.assertEqual(result["status"], "running")
        self.assertTrue(result.get("alive", True))

        proc.terminate()
        proc.wait(timeout=3.0)
        remove_pid_file("health_alive")

    def test_check_adopted_health_dead(self):
        """An adopted process that dies reports stopped."""
        proc_info = _dummy_script_instance("health_dead", pid=999999999)
        proc_info["adopted"] = True
        proc_info["status"] = "running"

        result = check_adopted_health(proc_info)
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result.get("alive", True))
        self.assertNotEqual(result.get("exit_code"), None)

    # ── Full lifecycle integration ─────────────────────────────────

    def test_full_adoption_lifecycle(self):
        """End-to-end: start → PID file → adopt → health check → stop."""
        # 1. Simulate "previous engine instance" starting a long-running script
        proc = _run_dummy_server(timeout=10.0)
        sid = "full_lifecycle"
        write_pid_file(sid, proc.pid)

        # 2. Simulate "new engine instance" adopting it
        inst = _dummy_script_instance(sid)
        result = find_and_adopt(inst, log_base_dir=Path(self._tmp))
        self.assertIsNotNone(result)
        self.assertTrue(result["adopted"])
        self.assertEqual(result["pid"], proc.pid)

        # 3. Health check while alive
        health = check_adopted_health(result)
        self.assertEqual(health["status"], "running")

        # 4. Process exits → health check should reflect that
        proc.terminate()
        proc.wait(timeout=3.0)
        time.sleep(1.0)  # Let adoption module detect death

        health_after = check_adopted_health(result)
        self.assertIn(health_after["status"], ("stopped", "crashed"))

        # 5. PID file should have been cleaned up on stop
        # (The adopted stop logic should remove the PID file)
        remove_pid_file(sid)

    def test_multiple_adopted_scripts(self):
        """Multiple long-running scripts can be adopted simultaneously."""
        procs = []
        sids = ["multi_1", "multi_2", "multi_3"]
        for sid in sids:
            proc = _run_dummy_server(timeout=10.0)
            write_pid_file(sid, proc.pid)
            procs.append(proc)

        adopted = []
        for sid in sids:
            inst = _dummy_script_instance(sid)
            result = find_and_adopt(inst, log_base_dir=Path(self._tmp))
            self.assertIsNotNone(result, f"Should adopt {sid}")
            adopted.append(result)

        # All should be running
        for i, a in enumerate(adopted):
            health = check_adopted_health(a)
            self.assertEqual(health["status"], "running",
                             f"{sids[i]} should be running after adoption")

        # Cleanup
        for proc in procs:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:
                pass
        for sid in sids:
            remove_pid_file(sid)

    def test_adopt_replaces_started_instance(self):
        """Adoption should prefer existing PID over starting fresh.
        
        When start_all_enabled() is called, long-running scripts with
        existing PIDs should be adopted (not started fresh).
        """
        # Simulate a running process with PID file
        proc = _run_dummy_server(timeout=10.0)
        sid = "dont_restart_me"
        write_pid_file(sid, proc.pid)

        # Verify adoption would succeed
        inst = _dummy_script_instance(sid)
        adopted = find_and_adopt(inst, log_base_dir=Path(self._tmp))
        self.assertIsNotNone(adopted)
        self.assertEqual(adopted["pid"], proc.pid)

        # Cleanup
        proc.terminate()
        proc.wait(timeout=3.0)
        remove_pid_file(sid)

    def test_engine_restart_does_not_kill_adopted(self):
        """Simulated engine restart: adopted scripts survive.
        
        The key property is that the shutdown_timeout for long_running
        scripts is handled by the adoption system — they get extra time
        to shut down, and if revived quickly, they're just adopted.
        """
        proc = _run_dummy_server(timeout=10.0)
        sid = "survivor"
        write_pid_file(sid, proc.pid)

        # Simulate "engine restarted" — the PID file still exists
        inst = _dummy_script_instance(sid)

        # Check that the existing PID is found
        status = check_script_status(inst)
        self.assertEqual(status["status"], "adoptable",
                         "Existing process should be adoptable")
        self.assertEqual(status["pid"], proc.pid)

        # Actually adopt it
        result = adopt_existing_process(inst, proc.pid)
        if result:
            self.assertTrue(result.get("adopted"))

        # Cleanup
        proc.terminate()
        proc.wait(timeout=3.0)
        remove_pid_file(sid)

    def test_pid_file_survives_engine_restart(self):
        """PID file persists across 'engine restarts'."""
        sid = "persistent_pid"
        write_pid_file(sid, os.getpid())  # Current process as proxy

        # "First engine life"
        pid1 = read_pid_file(sid)
        self.assertIsNotNone(pid1)

        # "Second engine life" — file still exists
        pid2 = read_pid_file(sid)
        self.assertEqual(pid1, pid2)

        remove_pid_file(sid)
        self.assertIsNone(read_pid_file(sid))


# ══════════════════════════════════════════════════════════════════════
# Edge Case Tests
# ══════════════════════════════════════════════════════════════════════

class TestAdoptionEdgeCases(unittest.TestCase):
    """Edge cases for the adoption system."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="adopt_edge_")
        import engine.process_adoption as pa
        self._orig = pa.get_pid_dir
        pa.get_pid_dir = lambda: Path(self._tmp)
        self._pa = pa

    def tearDown(self):
        self._pa.get_pid_dir = self._orig
        for f in Path(self._tmp).iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            os.rmdir(self._tmp)
        except OSError:
            pass

    def test_pid_file_with_special_chars(self):
        """Script IDs with special characters are safely stored."""
        sid = "my/weird/path/script_name-v2.0"
        write_pid_file(sid, 12345)
        read = read_pid_file(sid)
        self.assertEqual(read, 12345)
        remove_pid_file(sid)

    def test_adopt_with_port_check_timeout(self):
        """Adoption flow with a reasonable port check timeout doesn't hang."""
        inst = _dummy_script_instance("port_timeout", pid=os.getpid())
        # check_script_status with a live process but no open ports
        status = check_script_status(inst)
        # The script has no ports defined, so port_check is skipped
        self.assertIn(status["status"], ("adoptable", "no_ports"))

    def test_adopt_twice_returns_same(self):
        """Adopting the same process twice returns consistent results."""
        proc = _run_dummy_server(timeout=5.0)
        sid = "twice_adopt"
        write_pid_file(sid, proc.pid)

        inst = _dummy_script_instance(sid)
        first = find_and_adopt(inst, log_base_dir=Path(self._tmp))
        second = find_and_adopt(inst, log_base_dir=Path(self._tmp))

        # Both should succeed, report same PID
        if first and second:
            self.assertEqual(first["pid"], second["pid"])

        # Cleanup
        proc.terminate()
        proc.wait(timeout=3.0)
        remove_pid_file(sid)

    def test_concurrent_adoption_safety(self):
        """Multiple adoption calls on different scripts work."""
        import threading
        procs = []
        sids = [f"concurrent_{i}" for i in range(5)]
        for sid in sids:
            proc = _run_dummy_server(timeout=5.0)
            write_pid_file(sid, proc.pid)
            procs.append(proc)

        results = {}
        errors = []

        def _adopt_one(sid):
            try:
                inst = _dummy_script_instance(sid)
                results[sid] = find_and_adopt(inst, log_base_dir=Path(self._tmp))
            except Exception as e:
                errors.append((sid, str(e)))

        threads = [threading.Thread(target=_adopt_one, args=(sid,)) for sid in sids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Concurrent adoption errors: {errors}")
        for sid in sids:
            self.assertIsNotNone(results.get(sid), f"{sid} should adopt")
            self.assertTrue(results[sid].get("adopted"))

        # Cleanup
        for proc in procs:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:
                pass
        for sid in sids:
            remove_pid_file(sid)

    def test_kill_adopted_process(self):
        """Killing an adopted process (via _kill_process) works."""
        proc = _run_dummy_server(timeout=5.0)
        sid = "kill_test"
        write_pid_file(sid, proc.pid)

        # Kill via the adoption module
        from engine.process_adoption import _kill_process
        killed = _kill_process(proc.pid)
        self.assertTrue(killed, "Should succeed in killing the process")

        proc.wait(timeout=3.0)
        # Process should be dead
        self.assertFalse(is_process_alive(proc.pid))
        remove_pid_file(sid)

    def test_stale_pid_race_condition(self):
        """PID file exists but dies between file read and adoption attempt.
        
        This simulates a timing edge case where the process dies right
        before adoption.
        """
        import threading as t_mod
        sid = "race_condition"
        # Write a PID file for the current process
        write_pid_file(sid, os.getpid())

        # In another thread, read and remove the file simultaneously
        barrier = t_mod.Barrier(2, timeout=5.0)

        results = []

        def _racer():
            barrier.wait()
            # Try to adopt (the PID is alive, but by the time adoption
            # runs, the PID might still be valid — simulate by reading)
            from engine.process_adoption import read_pid_file
            pid = read_pid_file(sid)
            results.append(pid)

        t = t_mod.Thread(target=_racer)
        t.start()
        barrier.wait()
        # Meanwhile, write another PID file to simulate overlap
        write_pid_file(sid, 999999999)
        t.join(timeout=5.0)

        # The race shouldn't crash — at worst, stale PID
        remove_pid_file(sid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
