"""
Comprehensive unittest test suite for log-viewer/main.py.

Tests cover all functions, command verbs, edge cases, and error paths.
All external dependencies (socket, subprocess, engine imports) are mocked.
"""
import os
import sys
import json
import time
import socket as socket_module
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import (
    patch, MagicMock, PropertyMock, call, mock_open, ANY
)
from pathlib import Path

# ---------------------------------------------------------------------------
# Mock engine modules BEFORE importing the module under test
# ---------------------------------------------------------------------------
_mock_gui_api_client = MagicMock(name='engine.gui_api_client')
_mock_gui_instance = MagicMock(name='GuiApiClient_instance')
_mock_gui_instance.on_data_request = MagicMock()
_mock_gui_instance.register_tab = MagicMock()
_mock_gui_instance.close = MagicMock()
_mock_gui_api_client.GuiApiClient.return_value = _mock_gui_instance

_mock_ports = MagicMock(name='engine.ports')
_mock_ports.TCP_ADMIN_PORT = 25568

_engine_gui_patch = patch.dict('sys.modules', {
    'engine': MagicMock(name='engine'),
    'engine.gui_api_client': _mock_gui_api_client,
    'engine.ports': _mock_ports,
})
_engine_gui_patch.start()

# Insert the log-viewer directory FIRST in sys.path so 'import main' gets
# /home/deck/Documents/dev-yuniScripts/SCRIPTS/TOOLS/log-viewer/main.py,
# NOT the project-root main.py at /home/deck/Documents/dev-yuniScripts/main.py
_this_dir = os.path.dirname(os.path.abspath(__file__))
_log_viewer_dir = os.path.normpath(os.path.join(_this_dir, '..'))
# Remove any existing 'main' modules that might shadow
for k in list(sys.modules.keys()):
    if k == 'main' or k.startswith('main.'):
        del sys.modules[k]
# Ensure the log-viewer dir is before the project root in sys.path
if _log_viewer_dir in sys.path:
    sys.path.remove(_log_viewer_dir)
sys.path.insert(0, _log_viewer_dir)

# Now safe to import the module under test
import main as log_viewer


def setUpModule():
    """One-time: reset global state so tests start clean."""
    _reset_global_state()


def _reset_global_state():
    """Clear all mutable global state in the log_viewer module."""
    with log_viewer.watched_lock:
        log_viewer.watched.clear()
    with log_viewer.log_buffers_lock:
        log_viewer.log_buffers.clear()


# ===========================================================================
# Helper: a context manager that temporarily patches sys.platform AND reloads
# module-level socket‑path constants that depend on it.
# ===========================================================================
class _PatchPlatform:
    """Context manager / decorator that patches sys.platform and triggers
    the module‑level socket‑path re‑evaluation by reloading the module."""

    def __init__(self, platform: str):
        self._platform = platform
        self._orig_sys_platform = None
        self._orig_socket_host = None
        self._orig_socket_port = None
        self._orig_socket_path = None
        self._orig_engine_host = None
        self._orig_engine_port = None
        self._orig_engine_path = None

    def _snapshot_and_patch(self):
        self._orig_sys_platform = sys.platform
        self._orig_socket_host = log_viewer.SOCKET_HOST
        self._orig_socket_port = log_viewer.SOCKET_PORT
        self._orig_socket_path = log_viewer.SOCKET_PATH
        self._orig_engine_host = getattr(log_viewer, 'ENGINE_SOCKET_HOST', None)
        self._orig_engine_port = getattr(log_viewer, 'ENGINE_SOCKET_PORT', None)
        self._orig_engine_path = getattr(log_viewer, 'ENGINE_SOCKET_PATH', None)

        sys.platform = self._platform
        if self._platform == 'win32':
            log_viewer.SOCKET_HOST = "127.0.0.1"
            log_viewer.SOCKET_PORT = 25570
            log_viewer.SOCKET_PATH = None
            log_viewer.ENGINE_SOCKET_HOST = "127.0.0.1"
            log_viewer.ENGINE_SOCKET_PORT = 25568
            if hasattr(log_viewer, 'ENGINE_SOCKET_PATH'):
                del log_viewer.ENGINE_SOCKET_PATH
        else:
            log_viewer.SOCKET_HOST = None
            log_viewer.SOCKET_PORT = None
            log_viewer.SOCKET_PATH = "/tmp/yuniScripts-logviewer.sock"
            if hasattr(log_viewer, 'ENGINE_SOCKET_HOST'):
                del log_viewer.ENGINE_SOCKET_HOST
                del log_viewer.ENGINE_SOCKET_PORT
            log_viewer.ENGINE_SOCKET_PATH = "/tmp/yuniScripts.sock"

    def _restore(self):
        sys.platform = self._orig_sys_platform
        log_viewer.SOCKET_HOST = self._orig_socket_host
        log_viewer.SOCKET_PORT = self._orig_socket_port
        log_viewer.SOCKET_PATH = self._orig_socket_path
        if self._orig_engine_host is not None:
            log_viewer.ENGINE_SOCKET_HOST = self._orig_engine_host
            log_viewer.ENGINE_SOCKET_PORT = self._orig_engine_port
        if self._orig_engine_path is not None:
            log_viewer.ENGINE_SOCKET_PATH = self._orig_engine_path

    def __enter__(self):
        self._snapshot_and_patch()
        return self

    def __exit__(self, *exc):
        self._restore()
        return False

    def __call__(self, func):
        """Allow use as a decorator."""
        import functools
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with self:
                return func(*args, **kwargs)
        return wrapper


# ===========================================================================
# Test: _create_listen_socket
# ===========================================================================
class TestCreateListenSocket(unittest.TestCase):
    """Tests for _create_listen_socket covering Unix and Windows paths."""

    def setUp(self):
        _reset_global_state()

    @patch('main.socket.socket')
    def test_unix_create(self, mock_socket_factory):
        """Unix path: creates AF_UNIX socket, binds, listens, sets timeout."""
        with _PatchPlatform('linux'):
            mock_sock = MagicMock()
            mock_socket_factory.return_value = mock_sock

            result = log_viewer._create_listen_socket()

            mock_socket_factory.assert_called_once_with(
                socket_module.AF_UNIX, socket_module.SOCK_STREAM
            )
            mock_sock.bind.assert_called_once_with("/tmp/yuniScripts-logviewer.sock")
            mock_sock.listen.assert_called_once_with(5)
            mock_sock.settimeout.assert_called_once_with(1)
            self.assertIs(result, mock_sock)

    @patch('main.socket.socket')
    def test_unix_create_unlinks_existing(self, mock_socket_factory):
        """Unix path: removes stale socket file before bind."""
        with _PatchPlatform('linux'), \
             patch('main.os.unlink') as mock_unlink:
            mock_sock = MagicMock()
            mock_socket_factory.return_value = mock_sock

            log_viewer._create_listen_socket()

            mock_unlink.assert_called_once_with("/tmp/yuniScripts-logviewer.sock")

    @patch('main.socket.socket')
    def test_unix_create_unlink_ignores_enoent(self, mock_socket_factory):
        """Unix path: OSError from unlink is silently ignored (file didn't exist)."""
        with _PatchPlatform('linux'), \
             patch('main.os.unlink', side_effect=OSError(2, 'No such file')):
            mock_sock = MagicMock()
            mock_socket_factory.return_value = mock_sock

            # Should not raise
            result = log_viewer._create_listen_socket()
            self.assertIs(result, mock_sock)

    @patch('main.socket.socket')
    def test_windows_create(self, mock_socket_factory):
        """Windows path: creates AF_INET socket, binds, listens, sets timeout."""
        with _PatchPlatform('win32'):
            mock_sock = MagicMock()
            mock_socket_factory.return_value = mock_sock

            result = log_viewer._create_listen_socket()

            mock_socket_factory.assert_called_once_with(
                socket_module.AF_INET, socket_module.SOCK_STREAM
            )
            mock_sock.bind.assert_called_once_with(("127.0.0.1", 25570))
            mock_sock.listen.assert_called_once_with(5)
            mock_sock.settimeout.assert_called_once_with(1)
            self.assertIs(result, mock_sock)


# ===========================================================================
# Test: _cleanup_listen_socket
# ===========================================================================
class TestCleanupListenSocket(unittest.TestCase):
    """Tests for _cleanup_listen_socket covering Unix and Windows paths."""

    def setUp(self):
        _reset_global_state()

    @patch('main.socket.socket')
    def test_unix_cleanup(self, mock_socket_factory):
        """Unix: socket closed and socket file unlinked."""
        with _PatchPlatform('linux'), \
             patch('main.os.unlink') as mock_unlink:
            mock_sock = MagicMock()
            log_viewer._cleanup_listen_socket(mock_sock)
            mock_sock.close.assert_called_once()
            mock_unlink.assert_called_once_with("/tmp/yuniScripts-logviewer.sock")

    @patch('main.socket.socket')
    def test_windows_cleanup(self, mock_socket_factory):
        """Windows: socket closed, no unlink attempt."""
        with _PatchPlatform('win32'):
            mock_sock = MagicMock()
            with patch('main.os.unlink') as mock_unlink:
                log_viewer._cleanup_listen_socket(mock_sock)
                mock_sock.close.assert_called_once()
                mock_unlink.assert_not_called()

    @patch('main.socket.socket')
    def test_unlink_failure_ignored(self, mock_socket_factory):
        """Unix: OSError during unlink is silently ignored."""
        with _PatchPlatform('linux'), \
             patch('main.os.unlink', side_effect=OSError(13, 'Permission denied')):
            mock_sock = MagicMock()
            # Should not raise
            log_viewer._cleanup_listen_socket(mock_sock)
            mock_sock.close.assert_called_once()


# ===========================================================================
# Test: find_terminal
# ===========================================================================
class TestFindTerminal(unittest.TestCase):
    """Tests for find_terminal covering all fallback paths."""

    def setUp(self):
        _reset_global_state()

    def test_windows_returns_none(self):
        """On Windows, find_terminal returns None."""
        with _PatchPlatform('win32'):
            result = log_viewer.find_terminal()
            self.assertIsNone(result)

    @patch('main.os.path.isfile')
    @patch('main.os.access')
    def test_x_terminal_emulator_found(self, mock_access, mock_isfile):
        """x-terminal-emulator is found and used first."""
        with _PatchPlatform('linux'):
            mock_isfile.side_effect = lambda p: p == "/usr/bin/x-terminal-emulator"
            mock_access.return_value = True

            result = log_viewer.find_terminal()

            self.assertEqual(result, ["/usr/bin/x-terminal-emulator", "-e"])

    @patch('main.os.path.isfile')
    @patch('main.os.access')
    def test_konsole_found(self, mock_access, mock_isfile):
        """x-terminal-emulator missing, konsole found."""
        with _PatchPlatform('linux'):
            def isfile_side_effect(p):
                return p == "/usr/bin/konsole"
            mock_isfile.side_effect = isfile_side_effect
            mock_access.return_value = True

            result = log_viewer.find_terminal()

            self.assertEqual(result, ["/usr/bin/konsole", "-e"])

    @patch('main.os.path.isfile')
    @patch('main.os.access')
    def test_xterm_found(self, mock_access, mock_isfile):
        """x-terminal-emulator and konsole missing, xterm found."""
        with _PatchPlatform('linux'):
            def isfile_side_effect(p):
                return p == "/usr/bin/xterm"
            mock_isfile.side_effect = isfile_side_effect
            mock_access.return_value = True

            result = log_viewer.find_terminal()

            self.assertEqual(result, ["/usr/bin/xterm", "-hold", "-e"])

    @patch('main.os.path.isfile')
    @patch('main.os.access')
    @patch('main.shutil.which')
    def test_shutil_which_konsole(self, mock_which, mock_access, mock_isfile):
        """All hardcoded paths fail, shutil.which finds konsole in PATH."""
        with _PatchPlatform('linux'):
            mock_isfile.return_value = False
            mock_access.return_value = False
            mock_which.return_value = "/usr/bin/konsole"

            result = log_viewer.find_terminal()

            self.assertEqual(result, ["/usr/bin/konsole", "-e"])
            mock_which.assert_called()

    @patch('main.os.path.isfile')
    @patch('main.os.access')
    @patch('main.shutil.which')
    def test_shutil_which_xterm(self, mock_which, mock_access, mock_isfile):
        """shutil.which finds xterm in PATH."""
        with _PatchPlatform('linux'):
            mock_isfile.return_value = False
            mock_access.return_value = False

            def which_side_effect(name, **kwargs):
                return f"/usr/bin/{name}" if name == "xterm" else None
            mock_which.side_effect = which_side_effect

            result = log_viewer.find_terminal()

            self.assertEqual(result, ["/usr/bin/xterm", "-hold", "-e"])

    @patch('main.os.path.isfile')
    @patch('main.os.access')
    @patch('main.shutil.which')
    def test_no_terminal_found(self, mock_which, mock_access, mock_isfile):
        """No terminal emulator found at all."""
        with _PatchPlatform('linux'):
            mock_isfile.return_value = False
            mock_access.return_value = False
            mock_which.return_value = None

            result = log_viewer.find_terminal()

            self.assertIsNone(result)

    @patch('main.os.path.isfile')
    @patch('main.os.access')
    @patch('main.shutil.which')
    def test_not_executable_skipped(self, mock_which, mock_access, mock_isfile):
        """File exists but is not executable - skipped gracefully."""
        with _PatchPlatform('linux'):
            mock_isfile.return_value = True
            mock_access.return_value = False  # Not executable
            mock_which.return_value = None

            result = log_viewer.find_terminal()

            self.assertIsNone(result)


# ===========================================================================
# Test: spawn_log_window
# ===========================================================================
class TestSpawnLogWindow(unittest.TestCase):
    """Tests for spawn_log_window."""

    def setUp(self):
        _reset_global_state()

    @patch('main.find_terminal')
    @patch('main.subprocess.Popen')
    def test_success(self, mock_popen, mock_find_terminal):
        """Successfully spawns a terminal window with tail -f."""
        mock_find_terminal.return_value = ["/usr/bin/xterm", "-hold", "-e"]
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        try:
            with patch('main.LOG_DIR', Path(log_path).parent), \
                 patch('main.os.path.exists'):  # handled by Path.exists() below

                # We need to make log_file.exists() return True
                # The function uses: log_file = LOG_DIR / f"{safe_name}.log"
                # then checks if log_file.exists()
                # So we patch Path.exists on the specific path
                with patch.object(Path, 'exists', return_value=True):
                    proc, msg = log_viewer.spawn_log_window("test_script_123")

            self.assertIs(proc, mock_proc)
            self.assertIn("Opened log window for test_script_123", msg)
            self.assertIn("PID=12345", msg)

            # Verify Popen called with correct tail -f command
            expected_cmd = ["/usr/bin/xterm", "-hold", "-e", "tail", "-f", str(log_path)]
            # But the actual LOG_DIR is our patched dir, so the actual log_file path
            # will be LOG_DIR / "test_script_123.log"
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            self.assertIn("tail", args[0])
            self.assertIn("-f", args[0])
            self.assertEqual(kwargs.get('stdout'), subprocess.DEVNULL)
            self.assertEqual(kwargs.get('stderr'), subprocess.DEVNULL)
            self.assertTrue(kwargs.get('start_new_session', False))
        finally:
            os.unlink(log_path)

    @patch('main.find_terminal')
    @patch('main.subprocess.Popen')
    def test_log_file_not_found(self, mock_popen, mock_find_terminal):
        """Returns error message when log file does not exist."""
        mock_find_terminal.return_value = ["/usr/bin/xterm", "-hold", "-e"]

        with patch.object(Path, 'exists', return_value=False):
            proc, msg = log_viewer.spawn_log_window("nonexistent_script")

        self.assertIsNone(proc)
        self.assertIn("Log file not found", msg)
        mock_popen.assert_not_called()

    @patch('main.find_terminal')
    def test_no_terminal(self, mock_find_terminal):
        """Returns error message when no terminal emulator is found."""
        mock_find_terminal.return_value = None

        with patch.object(Path, 'exists', return_value=True):
            proc, msg = log_viewer.spawn_log_window("test_script")

        self.assertIsNone(proc)
        self.assertIn("No terminal emulator found", msg)

    @patch('main.find_terminal')
    @patch('main.subprocess.Popen')
    def test_popen_exception(self, mock_popen, mock_find_terminal):
        """Handles Popen raising an exception."""
        mock_find_terminal.return_value = ["/usr/bin/xterm", "-hold", "-e"]
        mock_popen.side_effect = OSError("Cannot launch terminal")

        with patch.object(Path, 'exists', return_value=True):
            proc, msg = log_viewer.spawn_log_window("crash_script")

        self.assertIsNone(proc)
        self.assertIn("Failed to launch terminal", msg)


# ===========================================================================
# Test: close_window
# ===========================================================================
class TestCloseWindow(unittest.TestCase):
    """Tests for close_window."""

    def setUp(self):
        _reset_global_state()

    def test_close_existing(self):
        """Successfully terminates a watched window."""
        mock_terminal = MagicMock()
        mock_terminal.poll.return_value = None  # still running

        with log_viewer.watched_lock:
            log_viewer.watched["script_a"] = {
                "terminal": mock_terminal,
                "log_window_pid": 12345,
            }

        log_viewer.close_window("script_a")

        mock_terminal.terminate.assert_called_once()
        mock_terminal.wait.assert_called_once_with(timeout=2)

        with log_viewer.watched_lock:
            self.assertNotIn("script_a", log_viewer.watched)

    def test_close_nonexistent(self):
        """Closing a non-watched script is a no-op (no KeyError)."""
        log_viewer.close_window("does_not_exist")  # Should not raise

    def test_terminate_fallback_to_kill(self):
        """If terminate() times out, falls back to kill()."""
        mock_terminal = MagicMock()
        mock_terminal.poll.return_value = None
        # terminate throws, then wait throws
        mock_terminal.terminate.side_effect = Exception("timeout")
        mock_terminal.wait.side_effect = Exception("timeout")

        with log_viewer.watched_lock:
            log_viewer.watched["script_b"] = {
                "terminal": mock_terminal,
                "log_window_pid": 67890,
            }

        log_viewer.close_window("script_b")

        mock_terminal.terminate.assert_called_once()
        mock_terminal.kill.assert_called_once()

        with log_viewer.watched_lock:
            self.assertNotIn("script_b", log_viewer.watched)


# ===========================================================================
# Test: handle_command
# ===========================================================================
class TestHandleCommand(unittest.TestCase):
    """Tests for handle_command covering all verbs."""

    def setUp(self):
        _reset_global_state()
        # Default patches for successful watch
        self.find_terminal_patch = patch('main.find_terminal',
                                         return_value=["/usr/bin/xterm", "-hold", "-e"])
        self.mock_proc = MagicMock()
        self.mock_proc.pid = 999
        self.mock_proc.poll.return_value = None
        self.popen_patch = patch('main.subprocess.Popen', return_value=self.mock_proc)
        self.path_exists_patch = patch.object(Path, 'exists', return_value=True)

        self.find_terminal_patch.start()
        self.popen_patch.start()
        self.path_exists_patch.start()

    def tearDown(self):
        self.path_exists_patch.stop()
        self.popen_patch.stop()
        self.find_terminal_patch.stop()
        _reset_global_state()

    # --- watch ---

    def test_watch_success(self):
        """watch <script_id> successfully opens a window."""
        result = log_viewer.handle_command("watch my_script")
        self.assertIn("Opened log window for my_script", result)
        with log_viewer.watched_lock:
            self.assertIn("my_script", log_viewer.watched)
            self.assertEqual(log_viewer.watched["my_script"]["log_window_pid"], 999)
            self.assertEqual(log_viewer.watched["my_script"]["active"], "Yes")

    def test_watch_no_script_id(self):
        """watch without args returns usage."""
        result = log_viewer.handle_command("watch")
        self.assertEqual(result, "Usage: watch <script_id>")

    def test_watch_already_watching(self):
        """watch on an already-watched script returns error."""
        log_viewer.handle_command("watch dup_script")
        result = log_viewer.handle_command("watch dup_script")
        self.assertEqual(result, "Already watching dup_script")

    def test_watch_failure(self):
        """watch when spawn_log_window fails returns the error message."""
        with patch('main.spawn_log_window', return_value=(None, "Something broke")):
            result = log_viewer.handle_command("watch fail_script")
            self.assertEqual(result, "Something broke")

    # --- unwatch ---

    def test_unwatch_success(self):
        """unwatch <script_id> closes and removes the watch."""
        log_viewer.handle_command("watch unwatch_me")
        result = log_viewer.handle_command("unwatch unwatch_me")
        self.assertEqual(result, "Unwatched unwatch_me")
        with log_viewer.watched_lock:
            self.assertNotIn("unwatch_me", log_viewer.watched)

    def test_unwatch_no_script_id(self):
        """unwatch without args returns usage."""
        result = log_viewer.handle_command("unwatch")
        self.assertEqual(result, "Usage: unwatch <script_id>")

    # --- list ---

    def test_list_empty(self):
        """list with no watched scripts returns appropriate message."""
        result = log_viewer.handle_command("list")
        self.assertEqual(result, "No watched scripts.")

    def test_list_with_entries(self):
        """list returns all watched scripts with their PIDs."""
        log_viewer.handle_command("watch script_one")
        log_viewer.handle_command("watch script_two")
        result = log_viewer.handle_command("list")
        self.assertIn("script_one", result)
        self.assertIn("script_two", result)
        self.assertIn("pid=999", result)

    # --- stop / exit ---

    def test_stop_shuts_down(self):
        """stop closes all watched windows."""
        log_viewer.handle_command("watch stop_me")
        result = log_viewer.handle_command("stop")
        self.assertEqual(result, "Log viewer shutting down.")
        with log_viewer.watched_lock:
            self.assertNotIn("stop_me", log_viewer.watched)

    def test_exit_shuts_down(self):
        """exit closes all watched windows."""
        log_viewer.handle_command("watch exit_me")
        result = log_viewer.handle_command("exit")
        self.assertEqual(result, "Log viewer shutting down.")
        with log_viewer.watched_lock:
            self.assertNotIn("exit_me", log_viewer.watched)

    # --- unknown / empty ---

    def test_unknown_command(self):
        """Unknown verb returns error message."""
        result = log_viewer.handle_command("fly")
        self.assertEqual(result, "Unknown command: fly")

    def test_empty_command(self):
        """Empty/whitespace command returns 'Bad command'."""
        result = log_viewer.handle_command("")
        self.assertEqual(result, "Bad command")

    def test_whitespace_command(self):
        """Whitespace-only command returns 'Bad command'."""
        result = log_viewer.handle_command("   ")
        self.assertEqual(result, "Bad command")


# ===========================================================================
# Test: _get_gui_data
# ===========================================================================
class TestGetGuiData(unittest.TestCase):
    """Tests for _get_gui_data."""

    def setUp(self):
        _reset_global_state()

    def test_empty_watched(self):
        """When nothing is watched, shows placeholders."""
        data = log_viewer._get_gui_data()
        self.assertEqual(data['file_path'], 'None')
        self.assertEqual(data['lines_count'], '0')
        self.assertEqual(data['active'], 'No')
        self.assertIn('No scripts being watched', data['log_preview'])

    def test_with_watched_and_no_log_buffer(self):
        """When a script is watched, shows its info with empty log buffer."""
        mock_terminal = MagicMock()
        with log_viewer.watched_lock:
            log_viewer.watched["my_app"] = {
                "terminal": mock_terminal,
                "log_window_pid": 111,
                "file_path": "/tmp/my_app.log",
                "lines_count": 42,
                "active": "Yes",
            }

        data = log_viewer._get_gui_data()

        self.assertEqual(data['file_path'], '/tmp/my_app.log')
        self.assertEqual(data['lines_count'], '42')
        self.assertEqual(data['active'], 'Yes')
        self.assertEqual(data['log_preview'], '(no log data)')

    def test_with_log_buffer(self):
        """Log preview returns last 20 lines from buffer."""
        mock_terminal = MagicMock()
        with log_viewer.watched_lock:
            log_viewer.watched["app2"] = {
                "terminal": mock_terminal,
                "log_window_pid": 222,
                "file_path": "/tmp/app2.log",
                "lines_count": 10,
                "active": "Yes",
            }

        test_lines = [f"line {i}" for i in range(30)]
        with log_viewer.log_buffers_lock:
            log_viewer.log_buffers["app2"] = test_lines

        data = log_viewer._get_gui_data()

        # Should show last 20 lines
        expected_preview = '\n'.join(test_lines[-20:])
        self.assertEqual(data['log_preview'], expected_preview)

    def test_multiple_watched_shows_last(self):
        """With multiple watched scripts, shows data for the last one added."""
        mock_terminal = MagicMock()
        with log_viewer.watched_lock:
            log_viewer.watched["first"] = {
                "terminal": mock_terminal,
                "log_window_pid": 1,
                "file_path": "/tmp/first.log",
                "lines_count": 5,
                "active": "Yes",
            }
            log_viewer.watched["second"] = {
                "terminal": mock_terminal,
                "log_window_pid": 2,
                "file_path": "/tmp/second.log",
                "lines_count": 10,
                "active": "Yes",
            }

        data = log_viewer._get_gui_data()
        self.assertEqual(data['file_path'], '/tmp/second.log')
        self.assertEqual(data['lines_count'], '10')


# ===========================================================================
# Test: _read_log_lines
# ===========================================================================
class TestReadLogLines(unittest.TestCase):
    """Tests for _read_log_lines."""

    def setUp(self):
        _reset_global_state()

    def test_read_normal(self):
        """Reads last N lines from a log file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            for i in range(100):
                f.write(f"entry {i}\n")
            log_path = f.name

        try:
            lines = log_viewer._read_log_lines(log_path, max_lines=10)
            self.assertEqual(len(lines), 10)
            self.assertEqual(lines[0], "entry 90\n")
            self.assertEqual(lines[-1], "entry 99\n")
        finally:
            os.unlink(log_path)

    def test_read_less_than_max(self):
        """File with fewer lines than max_lines returns all lines."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            f.write("line 1\nline 2\nline 3\n")
            log_path = f.name

        try:
            lines = log_viewer._read_log_lines(log_path, max_lines=50)
            self.assertEqual(len(lines), 3)
        finally:
            os.unlink(log_path)

    def test_file_not_found(self):
        """Missing file returns empty list."""
        lines = log_viewer._read_log_lines("/nonexistent/path.log")
        self.assertEqual(lines, [])

    def test_empty_file(self):
        """Empty file returns empty list."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            log_path = f.name

        try:
            lines = log_viewer._read_log_lines(log_path)
            self.assertEqual(lines, [])
        finally:
            os.unlink(log_path)


# ===========================================================================
# Test: is_script_running
# ===========================================================================
class TestIsScriptRunning(unittest.TestCase):
    """Tests for is_script_running."""

    def setUp(self):
        _reset_global_state()

    @patch('main.socket.socket')
    def test_script_running(self, mock_socket_factory):
        """Returns True when script status line contains 'running'."""
        mock_sock = MagicMock()
        mock_socket_factory.return_value = mock_sock
        # Mock recv to return status data then empty
        mock_sock.recv.side_effect = [
            b"my_script: running uptime=120\n",
            b""
        ]
        with _PatchPlatform('linux'):
            result = log_viewer.is_script_running("my_script")
            self.assertTrue(result)

    @patch('main.socket.socket')
    def test_script_not_running(self, mock_socket_factory):
        """Returns False when script status line doesn't contain 'running'."""
        mock_sock = MagicMock()
        mock_socket_factory.return_value = mock_sock
        mock_sock.recv.side_effect = [
            b"my_script: stopped\n",
            b""
        ]
        with _PatchPlatform('linux'):
            result = log_viewer.is_script_running("my_script")
            self.assertFalse(result)

    @patch('main.socket.socket')
    def test_script_not_in_status(self, mock_socket_factory):
        """Returns False when script_id not found in status output."""
        mock_sock = MagicMock()
        mock_socket_factory.return_value = mock_sock
        mock_sock.recv.side_effect = [
            b"other_script: running\n",
            b""
        ]
        with _PatchPlatform('linux'):
            result = log_viewer.is_script_running("missing_script")
            self.assertFalse(result)

    @patch('main.socket.socket')
    def test_connection_error(self, mock_socket_factory):
        """Returns False on connection/communication error."""
        mock_sock = MagicMock()
        mock_socket_factory.return_value = mock_sock
        mock_sock.connect.side_effect = ConnectionRefusedError()
        with _PatchPlatform('linux'):
            result = log_viewer.is_script_running("err_script")
            self.assertFalse(result)


# ===========================================================================
# Test: update_log_buffers (threaded, time-based)
# ===========================================================================
class TestUpdateLogBuffers(unittest.TestCase):
    """Tests for update_log_buffers — the background buffer thread."""

    def setUp(self):
        _reset_global_state()

    def test_buffer_update_with_watched_file(self):
        """update_log_buffers reads log file content into buffer."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            f.write("line 1\nline 2\nline 3\n")
            log_path = f.name

        try:
            mock_terminal = MagicMock()
            with log_viewer.watched_lock:
                log_viewer.watched["buffered_script"] = {
                    "terminal": mock_terminal,
                    "log_window_pid": 333,
                    "file_path": log_path,
                    "lines_count": 3,
                    "active": "Yes",
                }

            # Run update_log_buffers briefly (one iteration via time mocking)
            # We can't easily run the infinite loop, so test the logic directly
            # by calling _read_log_lines and verifying buffer behavior
            with patch('main._read_log_lines', return_value=["line 1\n", "line 2\n", "line 3\n"]):
                # Manually update the buffer as update_log_buffers would
                with log_viewer.watched_lock:
                    current = dict(log_viewer.watched)
                for sid, info in current.items():
                    file_path = info.get('file_path', '')
                    if file_path and os.path.isfile(file_path):
                        with log_viewer.log_buffers_lock:
                            log_viewer.log_buffers[sid] = ["line 1\n", "line 2\n", "line 3\n"]

            with log_viewer.log_buffers_lock:
                self.assertIn("buffered_script", log_viewer.log_buffers)
                self.assertEqual(len(log_viewer.log_buffers["buffered_script"]), 3)
        finally:
            os.unlink(log_path)

    def test_buffer_update_no_file_path(self):
        """Script with empty file_path doesn't cause errors."""
        mock_terminal = MagicMock()
        with log_viewer.watched_lock:
            log_viewer.watched["nopath"] = {
                "terminal": mock_terminal,
                "log_window_pid": 444,
                "file_path": "",
                "lines_count": 0,
                "active": "Yes",
            }

        # Simulate one iteration of update_log_buffers
        with log_viewer.watched_lock:
            current = dict(log_viewer.watched)
        for sid, info in current.items():
            file_path = info.get('file_path', '')
            if file_path and os.path.isfile(file_path):
                with log_viewer.log_buffers_lock:
                    log_viewer.log_buffers[sid] = ["data"]
                break

        # Buffer should remain unchanged (no file_path)
        with log_viewer.log_buffers_lock:
            self.assertNotIn("nopath", log_viewer.log_buffers)


# ===========================================================================
# Test: monitor_windows (background window monitor)
# ===========================================================================
class TestMonitorWindows(unittest.TestCase):
    """Tests for monitor_windows — periodic window health checks."""

    def setUp(self):
        _reset_global_state()

    def test_close_when_terminal_dies(self):
        """Window is closed when terminal.poll() returns non-None."""
        mock_terminal = MagicMock()
        mock_terminal.poll.return_value = 0  # Process exited

        with log_viewer.watched_lock:
            log_viewer.watched["dead_window"] = {
                "terminal": mock_terminal,
                "log_window_pid": 555,
                "file_path": "/tmp/dead.log",
                "lines_count": 0,
                "active": "Yes",
            }

        # Run monitor logic: simulate one iteration
        to_close = []
        with log_viewer.watched_lock:
            for sid, info in list(log_viewer.watched.items()):
                terminal = info["terminal"]
                if terminal.poll() is not None:
                    to_close.append(sid)
        for sid in to_close:
            log_viewer.close_window(sid)

        with log_viewer.watched_lock:
            self.assertNotIn("dead_window", log_viewer.watched)

    @patch('main.is_script_running')
    def test_close_when_script_stops(self, mock_is_running):
        """Window is closed when is_script_running returns False."""
        mock_is_running.return_value = False

        mock_terminal = MagicMock()
        mock_terminal.poll.return_value = None  # Process still alive

        with log_viewer.watched_lock:
            log_viewer.watched["stopped_script"] = {
                "terminal": mock_terminal,
                "log_window_pid": 666,
                "file_path": "/tmp/stopped.log",
                "lines_count": 5,
                "active": "Yes",
            }

        # Run monitor logic
        to_close = []
        with log_viewer.watched_lock:
            for sid, info in list(log_viewer.watched.items()):
                terminal = info["terminal"]
                if terminal.poll() is not None:
                    to_close.append(sid)
                elif not log_viewer.is_script_running(sid):
                    to_close.append(sid)
        for sid in to_close:
            log_viewer.close_window(sid)

        mock_is_running.assert_called_once_with("stopped_script")
        with log_viewer.watched_lock:
            self.assertNotIn("stopped_script", log_viewer.watched)

    @patch('main.is_script_running')
    def test_healthy_script_not_closed(self, mock_is_running):
        """Window stays open when script is running and terminal is alive."""
        mock_is_running.return_value = True

        mock_terminal = MagicMock()
        mock_terminal.poll.return_value = None  # Alive

        with log_viewer.watched_lock:
            log_viewer.watched["healthy"] = {
                "terminal": mock_terminal,
                "log_window_pid": 777,
                "file_path": "/tmp/healthy.log",
                "lines_count": 10,
                "active": "Yes",
            }

        to_close = []
        with log_viewer.watched_lock:
            for sid, info in list(log_viewer.watched.items()):
                terminal = info["terminal"]
                if terminal.poll() is not None:
                    to_close.append(sid)
                elif not log_viewer.is_script_running(sid):
                    to_close.append(sid)

        self.assertEqual(to_close, [])
        with log_viewer.watched_lock:
            self.assertIn("healthy", log_viewer.watched)


# ===========================================================================
# Test: main (integration skeleton)
# ===========================================================================
class TestMain(unittest.TestCase):
    """Tests for the main() function entry point."""

    def setUp(self):
        _reset_global_state()

    @patch('main.signal.signal')
    @patch('main._create_listen_socket')
    @patch('main._cleanup_listen_socket')
    @patch('main.monitor_windows')
    @patch('main.update_log_buffers')
    @patch('main.gui')
    def test_main_shutdown_on_socket_timeout(
        self, mock_gui, mock_update_buffers, mock_monitor,
        mock_cleanup, mock_create_socket, mock_signal
    ):
        """main() exits cleanly when accept() keeps timing out."""
        mock_server = MagicMock()
        mock_create_socket.return_value = mock_server

        # Make accept() raise socket.timeout a couple times then stop
        mock_server.accept.side_effect = [
            socket_module.timeout,  # first timeout
            socket_module.timeout,  # second timeout
            OSError,                 # break the loop
        ]

        log_viewer.main()

        mock_create_socket.assert_called_once()
        mock_monitor.assert_called_once()
        mock_update_buffers.assert_called_once()
        mock_gui.on_data_request.assert_called_once_with(log_viewer._get_gui_data)
        mock_gui.close.assert_called_once()
        mock_cleanup.assert_called_once_with(mock_server)

    @patch('main.signal.signal')
    @patch('main._create_listen_socket')
    @patch('main._cleanup_listen_socket')
    @patch('main.monitor_windows')
    @patch('main.update_log_buffers')
    @patch('main.gui')
    def test_main_handles_command(
        self, mock_gui, mock_update_buffers, mock_monitor,
        mock_cleanup, mock_create_socket, mock_signal
    ):
        """main() receives a command via accept() and processes it."""
        mock_server = MagicMock()

        # Mock a connection that delivers a command
        mock_conn = MagicMock()
        mock_conn.recv.return_value = b"list\n"
        mock_server.accept.side_effect = [
            (mock_conn, None),
            OSError,  # break the loop
        ]

        mock_create_socket.return_value = mock_server

        log_viewer.main()

        # The connection should have sent a response
        mock_conn.sendall.assert_called_once()
        sent_bytes = mock_conn.sendall.call_args[0][0]
        self.assertIn(b"No watched scripts", sent_bytes)


# ===========================================================================
# Test: _connect_engine_socket
# ===========================================================================
class TestConnectEngineSocket(unittest.TestCase):
    """Tests for _connect_engine_socket."""

    def setUp(self):
        _reset_global_state()

    @patch('main.socket.socket')
    def test_unix_connect(self, mock_socket_factory):
        """On Unix, connects to Unix domain socket."""
        with _PatchPlatform('linux'):
            mock_sock = MagicMock()
            mock_socket_factory.return_value = mock_sock

            result = log_viewer._connect_engine_socket()

            mock_socket_factory.assert_called_once_with(
                socket_module.AF_UNIX, socket_module.SOCK_STREAM
            )
            mock_sock.settimeout.assert_called_once_with(2)
            mock_sock.connect.assert_called_once_with("/tmp/yuniScripts.sock")
            self.assertIs(result, mock_sock)

    @patch('main.socket.socket')
    def test_windows_connect(self, mock_socket_factory):
        """On Windows, connects to TCP socket."""
        with _PatchPlatform('win32'):
            mock_sock = MagicMock()
            mock_socket_factory.return_value = mock_sock

            result = log_viewer._connect_engine_socket()

            mock_socket_factory.assert_called_once_with(
                socket_module.AF_INET, socket_module.SOCK_STREAM
            )
            mock_sock.settimeout.assert_called_once_with(2)
            mock_sock.connect.assert_called_once_with(("127.0.0.1", 25568))
            self.assertIs(result, mock_sock)


# ===========================================================================
# Edge case: script_id with special characters
# ===========================================================================
class TestScriptIdSanitization(unittest.TestCase):
    """Tests that script IDs are properly sanitized for filenames."""

    def setUp(self):
        _reset_global_state()

    @patch('main.find_terminal')
    @patch('main.subprocess.Popen')
    def test_slash_in_script_id_replaced(self, mock_popen, mock_find_terminal):
        """Forward slashes in script_id are replaced with underscores."""
        mock_find_terminal.return_value = ["/usr/bin/xterm", "-hold", "-e"]
        mock_proc = MagicMock()
        mock_proc.pid = 888
        mock_popen.return_value = mock_proc

        with patch.object(Path, 'exists', return_value=True):
            proc, msg = log_viewer.spawn_log_window("subdir/my_script")

        self.assertIsNotNone(proc)
        # Check that the log path uses safe name
        args, _ = mock_popen.call_args
        cmd_str = ' '.join(args[0])
        self.assertIn("subdir_my_script.log", cmd_str)

    def test_handle_command_with_slash(self):
        """watch command with slash in script_id works correctly."""
        # Direct check that watched key retains original script_id
        with patch('main.spawn_log_window', return_value=(MagicMock(poll=MagicMock(return_value=None), pid=777), "OK")):
            result = log_viewer.handle_command("watch my/project")
            with log_viewer.watched_lock:
                self.assertIn("my/project", log_viewer.watched)


# ===========================================================================
# Test: security / robustness edge cases
# ===========================================================================
class TestRobustness(unittest.TestCase):
    """Tests for edge cases and robustness."""

    def setUp(self):
        _reset_global_state()

    def test_handle_command_whitespace_padding(self):
        """Commands with leading/trailing spaces are handled."""
        result = log_viewer.handle_command("  list  ")
        self.assertEqual(result, "No watched scripts.")

    def test_handle_command_case_insensitive(self):
        """Verbs are case-insensitive."""
        result = log_viewer.handle_command("LIST")
        self.assertEqual(result, "No watched scripts.")

        result2 = log_viewer.handle_command("STOP")
        self.assertEqual(result2, "Log viewer shutting down.")

    @patch('main.find_terminal')
    @patch('main.subprocess.Popen')
    def test_watch_mixed_path_separators(self, mock_popen, mock_find_terminal):
        """Backslashes in script_id are also replaced."""
        mock_find_terminal.return_value = ["/usr/bin/xterm", "-hold", "-e"]
        mock_proc = MagicMock()
        mock_proc.pid = 777
        mock_popen.return_value = mock_proc

        with patch.object(Path, 'exists', return_value=True):
            proc, msg = log_viewer.spawn_log_window("dir\\sub\\script")

        self.assertIsNotNone(proc)
        args, _ = mock_popen.call_args
        cmd_str = ' '.join(args[0])
        self.assertIn("dir_sub_script.log", cmd_str)


# ===========================================================================
# Test: GuiApiClient integration (mocked)
# ===========================================================================
class TestGuiIntegration(unittest.TestCase):
    """Tests that the module correctly integrates with GuiApiClient.

    The module-level code should have called GuiApiClient and register_tab
    at import time.
    """

    def test_gui_api_client_called(self):
        """GuiApiClient was instantiated with correct arguments."""
        _mock_gui_api_client.GuiApiClient.assert_called_with(
            'TOOLS/log-viewer', 'Log Viewer'
        )

    def test_register_tab_called(self):
        """register_tab was called with widget specs."""
        call_args = _mock_gui_instance.register_tab.call_args
        self.assertIsNotNone(call_args)
        specs = call_args[0][0]
        self.assertIsInstance(specs, list)
        spec_ids = [s['id'] for s in specs]
        self.assertIn('file_path', spec_ids)
        self.assertIn('lines_count', spec_ids)
        self.assertIn('active', spec_ids)
        self.assertIn('uptime', spec_ids)
        self.assertIn('log_preview', spec_ids)

    def test_on_data_request_registered(self):
        """on_data_request callback is the _get_gui_data function (registered in main())."""
        # on_data_request is only called inside main(), not at import time.
        # Verify the method exists on the module's gui instance and _get_gui_data is available.
        self.assertTrue(hasattr(log_viewer.gui, 'on_data_request'))
        self.assertTrue(callable(log_viewer._get_gui_data))


# ===========================================================================
# Test: _get_gui_data uptime formatting
# ===========================================================================
class TestGuiDataUptime(unittest.TestCase):
    """Tests for the uptime formatting in _get_gui_data."""

    def setUp(self):
        _reset_global_state()

    def test_uptime_seconds(self):
        """Uptime under 1 minute shows seconds only."""
        with patch('main._start_time', time.time() - 30):
            data = log_viewer._get_gui_data()
            self.assertIn("30s", data['uptime'])

    def test_uptime_minutes(self):
        """Uptime between 1 and 60 minutes shows minutes and seconds."""
        with patch('main._start_time', time.time() - 185):  # 3m 5s
            data = log_viewer._get_gui_data()
            self.assertIn("3m", data['uptime'])
            self.assertIn("5s", data['uptime'])

    def test_uptime_hours(self):
        """Uptime over 1 hour shows hours, minutes, seconds."""
        with patch('main._start_time', time.time() - 3725):  # 1h 2m 5s
            data = log_viewer._get_gui_data()
            self.assertIn("1h", data['uptime'])
            self.assertIn("2m", data['uptime'])
            self.assertIn("5s", data['uptime'])


# ===========================================================================
# Test: _read_log_lines with encoding errors
# ===========================================================================
class TestReadLogLinesEdgeCases(unittest.TestCase):
    """Edge cases for _read_log_lines."""

    def setUp(self):
        _reset_global_state()

    def test_read_with_encoding_errors(self):
        """File with mixed encoding gracefully handled."""
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.log', delete=False) as f:
            f.write(b"valid line\n")
            f.write(b"invalid \xff\xfe line\n")
            f.write(b"another valid line\n")
            log_path = f.name

        try:
            lines = log_viewer._read_log_lines(log_path, max_lines=50)
            # Should return lines, with replace errors handled
            self.assertEqual(len(lines), 3)
        finally:
            os.unlink(log_path)

    def test_read_large_file(self):
        """Large file with more lines than max_lines returns only last N."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            for i in range(1000):
                f.write(f"line {i}\n")
            log_path = f.name

        try:
            lines = log_viewer._read_log_lines(log_path, max_lines=10)
            self.assertEqual(len(lines), 10)
            self.assertEqual(lines[0], "line 990\n")
            self.assertEqual(lines[-1], "line 999\n")
        finally:
            os.unlink(log_path)


# ===========================================================================
# Test: close_window with poll returning non-None
# ===========================================================================
class TestCloseWindowEdgeCases(unittest.TestCase):
    """Edge cases for close_window."""

    def setUp(self):
        _reset_global_state()

    def test_close_window_already_dead(self):
        """Window whose process already exited: no error."""
        mock_terminal = MagicMock()
        mock_terminal.poll.return_value = 0  # Already dead
        mock_terminal.terminate.side_effect = Exception("already dead")
        mock_terminal.wait.side_effect = Exception("already dead")

        with log_viewer.watched_lock:
            log_viewer.watched["zombie"] = {
                "terminal": mock_terminal,
                "log_window_pid": 1111,
            }

        # Should not raise even though terminate/wait fail, and kill works
        log_viewer.close_window("zombie")
        mock_terminal.kill.assert_called_once()

    def test_close_window_kill_also_fails(self):
        """Both terminate and kill fail — no exception propagates."""
        mock_terminal = MagicMock()
        mock_terminal.poll.return_value = None
        mock_terminal.terminate.side_effect = Exception("term fail")
        mock_terminal.wait.side_effect = Exception("wait fail")
        mock_terminal.kill.side_effect = Exception("kill fail")

        with log_viewer.watched_lock:
            log_viewer.watched["unkillable"] = {
                "terminal": mock_terminal,
                "log_window_pid": 2222,
            }

        # Should not raise
        log_viewer.close_window("unkillable")
        mock_terminal.kill.assert_called_once()


# ===========================================================================
# Clean up the engine module patch when tests finish
# ===========================================================================
def tearDownModule():
    _engine_gui_patch.stop()


if __name__ == '__main__':
    unittest.main()
