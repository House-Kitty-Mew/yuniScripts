#!/usr/bin/env python3
"""Comprehensive unit tests for engine/process_wrapper.py.

Tests 10 edge cases as specified:
  1. _venv_python returns correct path for linux and windows
  2. _get_python falls back to meta.python_path if venv missing
  3. _build_command constructs correct command list
  4. Restart backoff caps at 30s for normal scripts
  5. Restart backoff caps at 60s for server-type scripts
  6. _create_proc_info has all required keys
  7. Per-script shutdown timeout from meta
  8. SHUTDOWN_COMPLETE detection in drain_pipe
  9. Thread safety of _RESTART_LOG under concurrent access
  10. _drain_pipe handles UTF-8 decode errors

Uses unittest.TestCase (NOT pytest).  Mocks subprocess.Popen.
"""

import sys
import os
import io
import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import (
    MagicMock,
    PropertyMock,
    patch,
    mock_open,
    call,
    ANY,
)

# ---------------------------------------------------------------------------
# Path setup so the engine package is importable
# ---------------------------------------------------------------------------
_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Import process_wrapper normally — engine sub-modules (ports.py,
# process_adoption.py) are real files on disk and import without
# side-effects.  We use unittest.mock.patch in individual tests where
# mocking is needed (e.g. to prevent actual PID file writes).
# ---------------------------------------------------------------------------
import engine.process_wrapper as _pw


# ===========================================================================
# Helper: build a minimal valid script_instance dict
# ===========================================================================
def _make_script_instance(
    sid: str = "test_script",
    entry_point: str = "main.py",
    restart_policy: str = "on-failure",
    server_type: str = "normal",
    enabled: bool = True,
    python_path: str = "/usr/bin/python3",
    venv_path: str = None,
    args: list = None,
    shutdown_timeout: float = 5.0,
    **extra_meta,
) -> dict:
    return {
        "id": sid,
        "path": Path("/fake/project"),
        "meta": {
            "entry_point": entry_point,
            "restart_policy": restart_policy,
            "server_type": server_type,
            "enabled": enabled,
            "python_path": python_path,
            "args": args or [],
            "shutdown_timeout": shutdown_timeout,
            **extra_meta,
        },
        "venv_path": Path(venv_path) if venv_path else None,
    }


# ===========================================================================
# Tests
# ===========================================================================
class TestVenvPython(unittest.TestCase):
    """1. _venv_python returns correct path for linux and windows."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_linux_path(self):
        """On Linux, _venv_python returns <venv>/bin/python."""
        with patch("sys.platform", "linux"):
            result = self.pw._venv_python(Path("/opt/venvs/myenv"))
        expected = Path("/opt/venvs/myenv/bin/python")
        self.assertEqual(result, expected)

    def test_windows_path(self):
        """On Windows, _venv_python returns <venv>\\Scripts\\python.exe."""
        with patch("sys.platform", "win32"):
            result = self.pw._venv_python(Path("C:\\venvs\\myenv"))
        # On Linux, PosixPath keeps backslashes in initial path but uses /
        # for pathlib joins. Check the structural components instead.
        self.assertIn('Scripts', result.parts)
        self.assertIn('python.exe', result.parts)
        self.assertEqual(result.suffix, '.exe')

    def test_windows_forward_slash_path(self):
        """Windows path with forward slashes still resolves correctly."""
        with patch("sys.platform", "win32"):
            result = self.pw._venv_python(Path("C:/venvs/myenv"))
        expected = Path("C:/venvs/myenv/Scripts/python.exe")
        self.assertEqual(result, expected)


class TestGetPython(unittest.TestCase):
    """2. _get_python falls back to meta.python_path if venv missing."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_venv_path_exists(self):
        """When venv_path exists, the venv python is returned."""
        inst = _make_script_instance(
            venv_path="/opt/venvs/myenv",
            python_path="/usr/bin/python3",
        )
        fake_python = Path("/opt/venvs/myenv/bin/python")
        with patch.object(Path, "exists", return_value=True):
            result = self.pw._get_python(inst)
        self.assertEqual(result, str(fake_python))

    def test_venv_path_none(self):
        """When venv_path is None, use meta.python_path."""
        inst = _make_script_instance(
            venv_path=None,
            python_path="/usr/bin/python3",
        )
        result = self.pw._get_python(inst)
        self.assertEqual(result, "/usr/bin/python3")

    def test_venv_path_missing_on_disk(self):
        """When venv_path doesn't exist on disk, fallback to meta.python_path."""
        inst = _make_script_instance(
            venv_path="/opt/venvs/missing",
            python_path="/usr/bin/python3",
        )
        with patch.object(Path, "exists", return_value=False):
            result = self.pw._get_python(inst)
        self.assertEqual(result, "/usr/bin/python3")

    def test_venv_path_key_absent(self):
        """When 'venv_path' key is entirely absent, use meta.python_path."""
        inst = _make_script_instance(python_path="/usr/bin/python3")
        del inst["venv_path"]
        result = self.pw._get_python(inst)
        self.assertEqual(result, "/usr/bin/python3")


class TestBuildCommand(unittest.TestCase):
    """3. _build_command constructs correct command list."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_basic_command(self):
        """Command = [python, entry_point] with no extra args."""
        inst = _make_script_instance(
            python_path="/usr/bin/python3",
            entry_point="server.py",
            args=[],
        )
        with patch.object(Path, "exists", return_value=True):
            cmd = self.pw._build_command(inst)
        self.assertEqual(cmd, ["/usr/bin/python3", "/fake/project/server.py"])

    def test_with_args(self):
        """Command includes extra arguments from meta."""
        inst = _make_script_instance(
            python_path="/usr/bin/python3",
            entry_point="bot.py",
            args=["--port", "8080", "--verbose"],
        )
        with patch.object(Path, "exists", return_value=True):
            cmd = self.pw._build_command(inst)
        self.assertEqual(
            cmd,
            ["/usr/bin/python3", "/fake/project/bot.py", "--port", "8080", "--verbose"],
        )

    def test_venv_python_in_command(self):
        """When venv exists, venv python is used in the command."""
        inst = _make_script_instance(
            venv_path="/opt/venvs/boten",
            python_path="/usr/bin/python3",
            entry_point="runner.py",
        )
        with patch.object(Path, "exists", return_value=True):
            cmd = self.pw._build_command(inst)
        self.assertEqual(
            cmd,
            ["/opt/venvs/boten/bin/python", "/fake/project/runner.py"],
        )

    def test_command_is_list_of_strings(self):
        """All elements in the command list are strings."""
        inst = _make_script_instance(args=["-c", "print(1)"])
        with patch.object(Path, "exists", return_value=True):
            cmd = self.pw._build_command(inst)
        for item in cmd:
            self.assertIsInstance(item, str)


class TestRestartBackoffCaps(unittest.TestCase):
    """4 & 5. Restart backoff caps at 30s for normal, 60s for server."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()
        self.pw._BACKOFF_CAP_NORMAL = 30.0
        self.pw._BACKOFF_CAP_SERVER = 60.0

    def _prime_log(self, sid: str, count: int, runtime: float = 0.0):
        """Pre-populate _RESTART_LOG so we skip warm-up calls."""
        with self.pw._RESTART_LOCK:
            self.pw._RESTART_LOG[sid] = {
                "count": count,
                "last_start": 0.0,
                "last_restart": 0.0,
                "last_run_duration": runtime,
            }

    def test_normal_cap_at_30s(self):
        """_restart_backoff caps at _BACKOFF_CAP_NORMAL (30s)."""
        self._prime_log("norm_script", count=6, runtime=0.0)
        # 0.5 * 2^6 = 32.0 > 30.0, so cap applies
        with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
            delay = self.pw._restart_backoff("norm_script", "normal")
        self.assertEqual(delay, 30.0)

    def test_normal_cap_not_reached_below_threshold(self):
        """Before the cap, delay follows exponential growth."""
        self._prime_log("norm_script", count=3, runtime=0.0)
        # 0.5 * 2^3 = 4.0 < 30.0
        with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
            delay = self.pw._restart_backoff("norm_script", "normal")
        self.assertEqual(delay, 4.0)

    def test_server_cap_at_60s(self):
        """_restart_backoff caps at _BACKOFF_CAP_SERVER (60s) for server scripts."""
        self._prime_log("srv_script", count=7, runtime=0.0)
        # 0.5 * 2^7 = 64.0 > 60.0, so cap applies
        with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
            delay = self.pw._restart_backoff("srv_script", "long_running")
        self.assertEqual(delay, 60.0)

    def test_server_cap_applies_for_long_running(self):
        """Server-type cap applies to 'long_running' scripts."""
        self._prime_log("lr_script", count=7, runtime=0.0)
        with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
            delay = self.pw._restart_backoff("lr_script", "long_running")
        self.assertEqual(delay, 60.0)

    def test_server_cap_applies_for_critical(self):
        """Server-type cap applies to 'critical' scripts."""
        self._prime_log("crit_script", count=7, runtime=0.0)
        with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
            delay = self.pw._restart_backoff("crit_script", "critical")
        self.assertEqual(delay, 60.0)

    def test_normal_cap_30s_after_many_calls(self):
        """After enough calls, normal backoff is capped at 30s."""
        # Simulate reaching count=7 (0.5*128=64 -> capped at 30)
        self._prime_log("cap_test", count=7, runtime=0.0)
        with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
            delay = self.pw._restart_backoff("cap_test", "normal")
        self.assertEqual(delay, 30.0)

    def test_stable_run_resets_backoff_count(self):
        """If last_run_duration > 10s, the count resets to 0."""
        self._prime_log("stable_sid", count=10, runtime=15.0)
        with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
            delay = self.pw._restart_backoff("stable_sid", "normal")
        # count reset to 0 → delay = 0.5
        self.assertEqual(delay, 0.5)

    def test_negative_return_when_still_in_backoff(self):
        """Returns negative seconds remaining if backoff delay hasn't elapsed."""
        with self.pw._RESTART_LOCK:
            self.pw._RESTART_LOG["waiting"] = {
                "count": 2,          # delay would be 2.0s
                "last_start": 995.0,
                "last_restart": 997.0,
                "last_run_duration": 0.0,
            }
        # monotonic = 998.0, elapsed = 998 - 997 = 1.0 < delay 2.0
        with patch("engine.process_wrapper.time.monotonic", return_value=998.0):
            result = self.pw._restart_backoff("waiting", "normal")
        self.assertLess(result, 0)  # negative = still waiting
        self.assertAlmostEqual(result, -1.0, places=1)  # 1.0s remaining


class TestCreateProcInfo(unittest.TestCase):
    """6. _create_proc_info has all required keys."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_all_required_keys_present(self):
        """_create_proc_info returns a dict with all expected keys."""
        inst = _make_script_instance()
        info = self.pw._create_proc_info(inst)

        required_keys = {
            "pid",
            "process",
            "status",
            "exit_code",
            "restart_count",
            "last_start_time",
            "script_id",
            "log_handle",
            "log_path",
            "stdout_thread",
            "stderr_thread",
            "server_type",
            "shutdown_timeout",
            "shutdown_detected",
        }
        self.assertEqual(set(info.keys()), required_keys)

    def test_initial_status_is_stopped(self):
        """Initial status is STATUS_STOPPED."""
        info = self.pw._create_proc_info(_make_script_instance())
        self.assertEqual(info["status"], self.pw.STATUS_STOPPED)

    def test_initial_pid_is_none(self):
        """Initial pid is None."""
        info = self.pw._create_proc_info(_make_script_instance())
        self.assertIsNone(info["pid"])

    def test_initial_restart_count_zero(self):
        """Initial restart_count is 0."""
        info = self.pw._create_proc_info(_make_script_instance())
        self.assertEqual(info["restart_count"], 0)

    def test_shutdown_detected_is_event(self):
        """shutdown_detected is a threading.Event instance."""
        info = self.pw._create_proc_info(_make_script_instance())
        self.assertIsInstance(info["shutdown_detected"], threading.Event)

    def test_script_id_matches(self):
        """script_id matches the input script instance id."""
        info = self.pw._create_proc_info(_make_script_instance(sid="my_bot"))
        self.assertEqual(info["script_id"], "my_bot")

    def test_server_type_defaults_to_normal(self):
        """server_type defaults to 'normal' if not in meta."""
        inst = _make_script_instance()
        info = self.pw._create_proc_info(inst)
        self.assertEqual(info["server_type"], "normal")

    def test_server_type_from_meta(self):
        """server_type is pulled from meta when present."""
        inst = _make_script_instance(server_type="long_running")
        info = self.pw._create_proc_info(inst)
        self.assertEqual(info["server_type"], "long_running")


class TestShutdownTimeout(unittest.TestCase):
    """7. Per-script shutdown timeout from meta."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_default_timeout_is_5s(self):
        """When meta has no shutdown_timeout, default is 5.0s."""
        inst = _make_script_instance()
        # Remove shutdown_timeout from meta
        del inst["meta"]["shutdown_timeout"]
        info = self.pw._create_proc_info(inst)
        self.assertEqual(info["shutdown_timeout"], 5.0)

    def test_custom_timeout_from_meta(self):
        """shutdown_timeout from meta is reflected in proc_info."""
        inst = _make_script_instance(shutdown_timeout=15.0)
        info = self.pw._create_proc_info(inst)
        self.assertEqual(info["shutdown_timeout"], 15.0)

    def test_zero_timeout(self):
        """Zero timeout is accepted."""
        inst = _make_script_instance(shutdown_timeout=0.0)
        info = self.pw._create_proc_info(inst)
        self.assertEqual(info["shutdown_timeout"], 0.0)

    def test_large_timeout(self):
        """Large timeout values pass through."""
        inst = _make_script_instance(shutdown_timeout=300.0)
        info = self.pw._create_proc_info(inst)
        self.assertEqual(info["shutdown_timeout"], 300.0)

    def test_shutdown_timeout_used_in_stop_script(self):
        """stop_script uses the per-script timeout from proc_info."""
        inst = _make_script_instance(shutdown_timeout=42.0)
        proc_info = self.pw._create_proc_info(inst)
        proc_info["status"] = self.pw.STATUS_RUNNING

        # Mock a process so stop_script doesn't bail early
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        proc_info["process"] = mock_proc

        with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
            result = self.pw.stop_script(proc_info)

        # The timeout should have been passed to proc.wait()
        # mock_proc.wait should have been called with timeout=42.0
        wait_calls = mock_proc.wait.call_args_list
        # First wait call should be with timeout=42.0
        self.assertIn(
            call(timeout=42.0),
            wait_calls,
            msg="stop_script should call proc.wait(timeout=42.0) from proc_info",
        )
        self.assertEqual(result["status"], self.pw.STATUS_STOPPED)


class TestDrainPipeShutdownDetection(unittest.TestCase):
    """8. SHUTDOWN_COMPLETE detection in drain_pipe."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_shutdown_complete_sets_event(self):
        """When SHUTDOWN_COMPLETE is read, shutdown_event.set() is called."""
        pipe_data = [b"line1\n", b"SHUTDOWN_COMPLETE\n", b"line3\n"]
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = pipe_data

        log_handle = io.StringIO()
        shutdown_event = threading.Event()

        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event)

        self.assertTrue(shutdown_event.is_set())
        # Verify SHUTDOWN_COMPLETE was written to the log
        output = log_handle.getvalue()
        self.assertIn("SHUTDOWN_COMPLETE", output)

    def test_no_shutdown_complete_event_not_set(self):
        """Without SHUTDOWN_COMPLETE, shutdown_event stays clear."""
        pipe_data = [b"normal output\n", b"still running\n", b"bye\n"]
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = pipe_data

        log_handle = io.StringIO()
        shutdown_event = threading.Event()

        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event)

        self.assertFalse(shutdown_event.is_set())

    def test_shutdown_complete_with_prefix_does_not_set_event(self):
        """When prefix is non-empty, SHUTDOWN_COMPLETE is logged but doesn't set event."""
        pipe_data = [b"SHUTDOWN_COMPLETE\n"]
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = pipe_data

        log_handle = io.StringIO()
        shutdown_event = threading.Event()

        self.pw._drain_pipe(mock_pipe, log_handle, "[stderr] ", shutdown_event)

        self.assertFalse(shutdown_event.is_set())
        self.assertIn("[stderr] SHUTDOWN_COMPLETE", log_handle.getvalue())

    def test_shutdown_event_is_none_no_error(self):
        """When shutdown_event is None and SHUTDOWN_COMPLETE appears, no error."""
        pipe_data = [b"SHUTDOWN_COMPLETE\n"]
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = pipe_data

        log_handle = io.StringIO()

        # Should not raise even though shutdown_event is None
        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event=None)
        self.assertIn("SHUTDOWN_COMPLETE", log_handle.getvalue())

    def test_empty_pipe(self):
        """No data in pipe results in empty log and event not set."""
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = [b""]  # EOF immediately

        log_handle = io.StringIO()
        shutdown_event = threading.Event()

        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event)

        self.assertEqual(log_handle.getvalue(), "")
        self.assertFalse(shutdown_event.is_set())
        mock_pipe.close.assert_called_once()

    def test_line_without_newline(self):
        """Line without trailing newline is still processed."""
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = [b"no newline here", b""]

        log_handle = io.StringIO()
        shutdown_event = threading.Event()

        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event)

        self.assertIn("no newline here", log_handle.getvalue())


class TestDrainPipeUtf8Errors(unittest.TestCase):
    """10. _drain_pipe handles UTF-8 decode errors."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_invalid_utf8_bytes_do_not_crash(self):
        """Invalid UTF-8 bytes are handled with errors='replace'."""
        pipe_data = [b"\xff\xfe\x00\x01\n", b"valid text\n"]
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = pipe_data

        log_handle = io.StringIO()
        shutdown_event = threading.Event()

        # Must not raise
        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event)

        output = log_handle.getvalue()
        # The invalid bytes should have been replaced with replacement chars
        self.assertIn("valid text", output)
        # The pipe must have been closed
        mock_pipe.close.assert_called_once()

    def test_mixed_encoding_data(self):
        """Mix of valid UTF-8 and invalid bytes is handled gracefully."""
        pipe_data = [
            b"hello \xf0\x28\x8c\x28 world\n",  # invalid UTF-8 sequence
            b"goodbye\n",
        ]
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = pipe_data

        log_handle = io.StringIO()

        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event=None)

        output = log_handle.getvalue()
        self.assertIn("goodbye", output)
        # Invalid data should be replaced (we see replacement characters)
        self.assertNotIn("\xf0\x28\x8c\x28", output)

    def test_null_bytes_in_stream(self):
        """Null bytes in pipe data don't cause issues."""
        pipe_data = [b"line\x00with\x00nulls\n", b"end\n"]
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = pipe_data

        log_handle = io.StringIO()

        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event=None)

        output = log_handle.getvalue()
        self.assertIn("line", output)
        self.assertIn("end", output)

    def test_very_long_line_with_invalid_bytes(self):
        """Long line with mixed valid/invalid bytes is handled."""
        long_invalid = b"valid_start_" + b"\xff\xfe" * 50 + b"_valid_end\n"
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = [long_invalid, b""]

        log_handle = io.StringIO()

        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event=None)

        output = log_handle.getvalue()
        self.assertIn("valid_start", output)
        self.assertIn("valid_end", output)
        self.assertNotIn("\xff\xfe", output)  # replaced with \ufffd

    def test_decode_error_on_pipe_does_not_raise(self):
        """If pipe.readline raises a ValueError, it's caught silently."""
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = ValueError("pipe error")

        log_handle = io.StringIO()

        # Should not raise — the except (ValueError, IOError) catches it
        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event=None)

        mock_pipe.close.assert_called_once()

    def test_io_error_on_readline_is_caught(self):
        """IOError from readline is caught silently."""
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = IOError("I/O error")

        log_handle = io.StringIO()

        self.pw._drain_pipe(mock_pipe, log_handle, "", shutdown_event=None)

        mock_pipe.close.assert_called_once()


class TestRestartLogThreadSafety(unittest.TestCase):
    """9. Thread safety of _RESTART_LOG under concurrent access."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()
        self.pw._BACKOFF_CAP_NORMAL = 30.0
        self.pw._BACKOFF_CAP_SERVER = 60.0
        self.errors = []

    def _thread_worker(self, sid: str, server_type: str, iterations: int,
                       start_barrier: threading.Barrier):
        """Concurrently call _restart_backoff on the same SID."""
        try:
            start_barrier.wait(timeout=5)
            for _ in range(iterations):
                self.pw._restart_backoff(sid, server_type)
        except Exception as e:
            self.errors.append(e)

    def test_concurrent_access_same_sid(self):
        """Multiple threads accessing the same SID doesn't cause errors."""
        num_threads = 8
        iterations = 20
        barrier = threading.Barrier(num_threads)

        threads = []
        for _ in range(num_threads):
            t = threading.Thread(
                target=self._thread_worker,
                args=("shared_sid", "normal", iterations, barrier),
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(self.errors, [], f"Thread safety errors: {self.errors}")

    def test_concurrent_access_different_sids(self):
        """Multiple threads on different SIDs all succeed."""
        num_threads = 16
        barrier = threading.Barrier(num_threads)

        threads = []
        for i in range(num_threads):
            t = threading.Thread(
                target=self._thread_worker,
                args=(f"sid_{i}", "normal", 15, barrier),
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(self.errors, [], f"Thread safety errors: {self.errors}")

    def test_mixed_server_types_concurrent(self):
        """Mixed normal and server-type backoff calls are thread-safe."""
        barrier = threading.Barrier(8)
        threads = []
        for i in range(4):
            t = threading.Thread(
                target=self._thread_worker,
                args=("srv_sid", "long_running", 15, barrier),
            )
            threads.append(t)
        for i in range(4):
            t = threading.Thread(
                target=self._thread_worker,
                args=("norm_sid", "normal", 15, barrier),
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(self.errors, [], f"Thread safety errors: {self.errors}")

    def test_rlock_protects_log_during_read_modify_write(self):
        """_RESTART_LOCK ensures read-modify-write on _RESTART_LOG is atomic."""
        # Verify the lock is a threading.Lock (not a non-lock)
        self.assertIsInstance(self.pw._RESTART_LOCK, threading.Lock)

    def test_no_data_corruption_after_concurrent_writes(self):
        """After concurrent access, _RESTART_LOG structure is valid."""
        barrier = threading.Barrier(6)
        threads = []
        for i in range(6):
            t = threading.Thread(
                target=self._thread_worker,
                args=("corrupt_test", "normal", 10, barrier),
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(self.errors, [])
        with self.pw._RESTART_LOCK:
            entry = self.pw._RESTART_LOG.get("corrupt_test")
        self.assertIsNotNone(entry)
        # All required keys must be present
        for key in ("count", "last_start", "last_restart", "last_run_duration"):
            self.assertIn(key, entry,
                          f"Missing key '{key}' in _RESTART_LOG entry")
        # count should be at least 1 (after 6x10 calls)
        self.assertGreaterEqual(entry["count"], 1)
        # last_start and last_restart should be non-zero
        self.assertGreater(entry["last_start"], 0)
        self.assertGreater(entry["last_restart"], 0)


class TestApplyRestartPolicy(unittest.TestCase):
    """Additional tests for apply_restart_policy integration."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()
        self.pw._BACKOFF_CAP_NORMAL = 30.0
        self.pw._BACKOFF_CAP_SERVER = 60.0

    def test_clean_shutdown_removes_backoff(self):
        """A cleanly stopped script has its backoff entry removed."""
        inst = _make_script_instance()
        with self.pw._RESTART_LOCK:
            self.pw._RESTART_LOG["test_script"] = {
                "count": 5, "last_start": 100.0,
                "last_restart": 100.0, "last_run_duration": 0.0,
            }

        proc_info = {
            "status": self.pw.STATUS_STOPPED,
            "exit_code": 0,
        }

        self.pw.apply_restart_policy(inst, proc_info)

        with self.pw._RESTART_LOCK:
            self.assertNotIn("test_script", self.pw._RESTART_LOG)

    def test_policy_never_stops_crashed_script(self):
        """Policy 'never' transitions CRASHED to STOPPED."""
        inst = _make_script_instance(restart_policy="never")
        proc_info = {"status": self.pw.STATUS_CRASHED}
        _, result = self.pw.apply_restart_policy(inst, proc_info)
        self.assertEqual(result["status"], self.pw.STATUS_STOPPED)

    def test_policy_on_failure_calls_start_script(self):
        """Policy 'on-failure' restarts a crashed script."""
        inst = _make_script_instance(restart_policy="on-failure")
        proc_info = {"status": self.pw.STATUS_CRASHED}

        with self.pw._RESTART_LOCK:
            self.pw._RESTART_LOG["test_script"] = {
                "count": 0, "last_start": 0.0,
                "last_restart": 0.0, "last_run_duration": 0.0,
            }

        orig_start = self.pw.start_script

        def fake_start(*a, **kw):
            return {"status": self.pw.STATUS_RUNNING, "pid": 999}

        with patch.object(self.pw, "start_script", side_effect=fake_start):
            with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
                _, result = self.pw.apply_restart_policy(inst, proc_info)

        self.assertEqual(result["status"], self.pw.STATUS_RUNNING)

    def test_policy_always_calls_start_script(self):
        """Policy 'always' restarts a crashed script."""
        inst = _make_script_instance(restart_policy="always")
        proc_info = {"status": self.pw.STATUS_CRASHED}

        with self.pw._RESTART_LOCK:
            self.pw._RESTART_LOG["test_script"] = {
                "count": 0, "last_start": 0.0,
                "last_restart": 0.0, "last_run_duration": 0.0,
            }

        def fake_start(*a, **kw):
            return {"status": self.pw.STATUS_RUNNING, "pid": 999}

        with patch.object(self.pw, "start_script", side_effect=fake_start):
            with patch("engine.process_wrapper.time.monotonic", return_value=1000.0):
                _, result = self.pw.apply_restart_policy(inst, proc_info)

        self.assertEqual(result["status"], self.pw.STATUS_RUNNING)


class TestStopScriptWithTimeout(unittest.TestCase):
    """Verify stop_script uses per-script timeout correctly."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_stop_script_uses_proc_info_timeout(self):
        """stop_script reads shutdown_timeout from proc_info."""
        proc_info = {
            "script_id": "test",
            "server_type": "normal",
            "status": self.pw.STATUS_RUNNING,
            "shutdown_timeout": 12.0,
            "process": MagicMock(),
            "pid": 123,
            "stdout_thread": None,
            "stderr_thread": None,
            "log_handle": None,
            "shutdown_detected": threading.Event(),
        }
        mock_proc = proc_info["process"]
        mock_proc.pid = 123
        mock_proc.returncode = 0

        result = self.pw.stop_script(proc_info)

        # Ensure proc.wait was called with timeout=12.0
        wait_calls = mock_proc.wait.call_args_list
        self.assertIn(
            call(timeout=12.0),
            wait_calls,
            msg="stop_script should pass shutdown_timeout to proc.wait()",
        )
        self.assertEqual(result["status"], self.pw.STATUS_STOPPED)

    def test_stop_script_override_timeout(self):
        """stop_script accepts an explicit timeout override."""
        proc_info = {
            "script_id": "test",
            "server_type": "normal",
            "status": self.pw.STATUS_RUNNING,
            "shutdown_timeout": 5.0,
            "process": MagicMock(),
            "pid": 123,
            "stdout_thread": None,
            "stderr_thread": None,
            "log_handle": None,
            "shutdown_detected": threading.Event(),
        }
        mock_proc = proc_info["process"]
        mock_proc.pid = 123
        mock_proc.returncode = 0

        _ = self.pw.stop_script(proc_info, timeout=99.0)

        # Should use the override value 99.0
        wait_calls = mock_proc.wait.call_args_list
        self.assertIn(
            call(timeout=99.0),
            wait_calls,
            msg="stop_script should use the timeout override",
        )


class TestCheckHealthWithShutdownDetection(unittest.TestCase):
    """check_health handles SHUTDOWN_COMPLETE detection."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()

    def test_shutdown_detected_triggers_stop(self):
        """When shutdown_detected event is set, check_health returns STOPPED."""
        shutdown_event = threading.Event()
        shutdown_event.set()

        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = None

        proc_info = {
            "script_id": "test",
            "status": self.pw.STATUS_RUNNING,
            "process": mock_proc,
            "shutdown_detected": shutdown_event,
            "exit_code": None,
            "pid": 42,
        }

        result = self.pw.check_health(proc_info)
        self.assertEqual(result["status"], self.pw.STATUS_STOPPED)
        self.assertEqual(result["exit_code"], 0)


class TestStartScriptBasic(unittest.TestCase):
    """Basic start_script tests with mocked subprocess."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()
        self.pw._ADOPTION_CLEANUP_DONE = False

    @patch("engine.process_wrapper.subprocess.Popen")
    def test_start_script_returns_running_info(self, mock_popen):
        """start_script returns a proc_info with RUNNING status."""
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline.side_effect = [b""]
        mock_popen.return_value = mock_proc

        inst = _make_script_instance(sid="test_start")
        with patch.object(Path, "exists", return_value=True):
            with tempfile.TemporaryDirectory() as tmpdir:
                result = self.pw.start_script(inst, log_base_dir=Path(tmpdir))

        self.assertEqual(result["status"], self.pw.STATUS_RUNNING)
        self.assertEqual(result["pid"], 9999)
        self.assertIsNotNone(result["log_path"])

    @patch("engine.process_wrapper.subprocess.Popen")
    def test_start_script_failure_returns_crashed(self, mock_popen):
        """When subprocess.Popen raises, start_script returns CRASHED."""
        mock_popen.side_effect = FileNotFoundError("python not found")

        inst = _make_script_instance(sid="fail_script")
        with patch.object(Path, "exists", return_value=True):
            with tempfile.TemporaryDirectory() as tmpdir:
                result = self.pw.start_script(inst, log_base_dir=Path(tmpdir))

        self.assertEqual(result["status"], self.pw.STATUS_CRASHED)


class TestStartAllEnabled(unittest.TestCase):
    """start_all_enabled basic behavior."""

    def setUp(self):
        self.pw = _pw
        self.pw._RESTART_LOG.clear()
        self.pw._ADOPTION_CLEANUP_DONE = False

    def test_stale_pid_cleanup_runs_once(self):
        """Stale PID cleanup runs on first start_all_enabled call."""
        with patch.object(self.pw, "cleanup_stale_pids", wraps=self.pw.cleanup_stale_pids) as mock_cleanup:
            registry = {}
            self.pw.start_all_enabled(registry)
            mock_cleanup.assert_called_once()


# ===========================================================================
# Main
# ===========================================================================
if __name__ == "__main__":
    unittest.main(verbosity=2)
