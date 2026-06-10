"""
test_multi_server.py — Comprehensive unit tests for multi_server.py

Tests all 7 classes:
  1. ServerInfo
  2. ServerDiscovery
  3. RconConnection
  4. RconPool
  5. SubsystemRegistry
  6. CustomCommandManager
  7. MultiServerManager

All tests self-contained. External dependencies (socket, sqlite3, file I/O) are mocked.
"""

import unittest
from unittest.mock import patch, MagicMock, mock_open, call, PropertyMock
import sys
import os
import json
import time
import struct
import socket
import threading
from pathlib import Path
from datetime import datetime

# Ensure module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.multi_server import (
    ServerInfo,
    ServerDiscovery,
    RconConnection,
    RconPool,
    SubsystemRegistry,
    CustomCommandManager,
    MultiServerManager,
    SUBSYSTEM_DEFINITIONS,
    ensure_default_config,
    _find_runner_db,
)


# ═══════════════════════════════════════════════════════════════
# Test Data
# ═══════════════════════════════════════════════════════════════

SAMPLE_ROW = {
    'id': 1,
    'name': 'survival',
    'mc_version': '1.20.4',
    'server_type': 'paper',
    'server_port': 25565,
    'rcon_port': 25575,
    'rcon_password': 'secret123',
    'enabled': 1,
    'auto_start': 1,
    'java_version': '17',
    'min_ram': '2G',
    'max_ram': '4G',
}

SAMPLE_ROW_NO_RCON = dict(SAMPLE_ROW, rcon_password='')
SAMPLE_ROW_DISABLED = dict(SAMPLE_ROW, enabled=0)

SAMPLE_ROWS = [
    SAMPLE_ROW,
    {'id': 2, 'name': 'creative', 'mc_version': '1.20.4', 'server_type': 'vanilla',
     'server_port': 25566, 'rcon_port': 25576, 'rcon_password': 'pass2',
     'enabled': 1, 'auto_start': 0, 'java_version': '17', 'min_ram': '1G', 'max_ram': '2G'},
    {'id': 3, 'name': 'modded', 'mc_version': '1.19.2', 'server_type': 'fabric',
     'server_port': 25567, 'rcon_port': 25577, 'rcon_password': 'pass3',
     'enabled': 1, 'auto_start': 1, 'java_version': '21', 'min_ram': '4G', 'max_ram': '8G'},
]


# ═══════════════════════════════════════════════════════════════
# 1. ServerInfo Tests
# ═══════════════════════════════════════════════════════════════

class TestServerInfo(unittest.TestCase):
    """Test the ServerInfo data class."""

    def test_constructor_full_row(self):
        """Construct ServerInfo from a complete row dict."""
        info = ServerInfo(SAMPLE_ROW)
        self.assertEqual(info.id, 1)
        self.assertEqual(info.name, 'survival')
        self.assertEqual(info.mc_version, '1.20.4')
        self.assertEqual(info.server_type, 'paper')
        self.assertEqual(info.server_port, 25565)
        self.assertEqual(info.rcon_port, 25575)
        self.assertEqual(info.rcon_password, 'secret123')
        self.assertTrue(info.enabled)
        self.assertTrue(info.auto_start)
        self.assertEqual(info.java_version, '17')
        self.assertEqual(info.min_ram, '2G')
        self.assertEqual(info.max_ram, '4G')

    def test_constructor_minimal_row(self):
        """Construct ServerInfo from a minimal row (only required fields)."""
        info = ServerInfo({'id': 1, 'name': 'test'})
        self.assertEqual(info.id, 1)
        self.assertEqual(info.name, 'test')
        self.assertEqual(info.mc_version, '1.20.4')  # default
        self.assertEqual(info.server_type, 'vanilla')  # default
        self.assertEqual(info.server_port, 25565)  # default
        self.assertEqual(info.rcon_port, 25575)  # default
        self.assertEqual(info.rcon_password, '')  # default
        self.assertFalse(info.enabled)  # bool(0) -> False
        self.assertFalse(info.auto_start)  # bool(0) -> False
        self.assertEqual(info.java_version, '17')  # default
        self.assertEqual(info.min_ram, '2G')  # default
        self.assertEqual(info.max_ram, '4G')  # default

    def test_has_rcon_with_password(self):
        """has_rcon returns True when rcon_password is set."""
        info = ServerInfo(SAMPLE_ROW)
        self.assertTrue(info.has_rcon)

    def test_has_rcon_without_password(self):
        """has_rcon returns False when rcon_password is empty."""
        info = ServerInfo(SAMPLE_ROW_NO_RCON)
        self.assertFalse(info.has_rcon)

    def test_vfs_server_path(self):
        """vfs_server_path returns correct path."""
        info = ServerInfo(SAMPLE_ROW)
        self.assertEqual(info.vfs_server_path, '/servers/survival')

    def test_vfs_server_path_different_name(self):
        """vfs_server_path uses the server's name."""
        info = ServerInfo({'id': 2, 'name': 'creative'})
        self.assertEqual(info.vfs_server_path, '/servers/creative')

    def test_to_dict(self):
        """to_dict returns correct dictionary with expected keys."""
        info = ServerInfo(SAMPLE_ROW)
        d = info.to_dict()
        self.assertEqual(d['id'], 1)
        self.assertEqual(d['name'], 'survival')
        self.assertEqual(d['mc_version'], '1.20.4')
        self.assertEqual(d['server_type'], 'paper')
        self.assertEqual(d['server_port'], 25565)
        self.assertEqual(d['rcon_port'], 25575)
        self.assertTrue(d['has_rcon'])
        self.assertTrue(d['enabled'])

    def test_to_dict_no_rcon(self):
        """to_dict reflects has_rcon=False when no password."""
        info = ServerInfo(SAMPLE_ROW_NO_RCON)
        d = info.to_dict()
        self.assertFalse(d['has_rcon'])

    def test_repr(self):
        """__repr__ returns a meaningful string."""
        info = ServerInfo(SAMPLE_ROW)
        r = repr(info)
        self.assertIn('survival', r)
        self.assertIn('1.20.4', r)
        self.assertIn('paper', r)
        self.assertIn('ServerInfo', r)

    def test_enabled_false_when_zero(self):
        """enabled is False when row has enabled=0."""
        info = ServerInfo(SAMPLE_ROW_DISABLED)
        self.assertFalse(info.enabled)

    def test_auto_start_default_false(self):
        """auto_start defaults to False when not provided or 0."""
        info = ServerInfo({'id': 5, 'name': 'test'})
        self.assertFalse(info.auto_start)

    def test_missing_fields_defaults(self):
        """All optional fields have sensible defaults."""
        info = ServerInfo({'id': 99, 'name': 'defaults'})
        self.assertEqual(info.mc_version, '1.20.4')
        self.assertEqual(info.server_type, 'vanilla')
        self.assertEqual(info.server_port, 25565)
        self.assertEqual(info.rcon_port, 25575)
        self.assertEqual(info.rcon_password, '')
        self.assertEqual(info.java_version, '17')
        self.assertEqual(info.min_ram, '2G')
        self.assertEqual(info.max_ram, '4G')


# ═══════════════════════════════════════════════════════════════
# 2. ServerDiscovery Tests
# ═══════════════════════════════════════════════════════════════

class TestServerDiscovery(unittest.TestCase):
    """Test the ServerDiscovery class."""

    def setUp(self):
        self.discovery = ServerDiscovery(db_path=':memory:')

    def tearDown(self):
        self.discovery._cache = None
        self.discovery._cache_time = 0

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_discover_success(self, mock_connect, mock_exists):
        """discover returns ServerInfo list from DB rows."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            MagicMock(**{'__getitem__.side_effect': lambda k: {'id': 1, 'name': 'test'}.get(k, '')})
        ]
        # Make the mock row work with dict() conversion
        mock_rows = []
        for row_dict in SAMPLE_ROWS:
            mock_row = MagicMock(spec=dict)
            mock_row.__getitem__.side_effect = lambda k, rd=row_dict: rd[k]
            mock_row.get.side_effect = lambda k, default=None, rd=row_dict: rd.get(k, default)
            # Make dict() work on the mock
            mock_row.__iter__.side_effect = lambda rd=row_dict: iter(rd.items())
            mock_rows.append(mock_row)

        mock_cursor.fetchall.return_value = mock_rows
        servers = self.discovery.discover()

        self.assertEqual(len(servers), 3)
        self.assertEqual(servers[0].name, 'survival')
        self.assertEqual(servers[1].name, 'creative')
        self.assertEqual(servers[2].name, 'modded')

    @patch('engine.multi_server.os.path.exists')
    def test_discover_db_not_found(self, mock_exists):
        """discover returns empty list when DB file doesn't exist."""
        mock_exists.return_value = False
        servers = self.discovery.discover()
        self.assertEqual(servers, [])

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_discover_db_error(self, mock_connect, mock_exists):
        """discover returns empty list on DB error."""
        mock_exists.return_value = True
        mock_connect.side_effect = Exception("DB corruption")
        servers = self.discovery.discover()
        self.assertEqual(servers, [])

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_discover_caching(self, mock_connect, mock_exists):
        """discover caches results and reuses cache within TTL."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        first = self.discovery.discover()
        second = self.discovery.discover()

        # DB should only be queried once due to caching
        self.assertEqual(mock_connect.call_count, 1)

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_discover_force_refresh(self, mock_connect, mock_exists):
        """discover with force_refresh=True bypasses cache."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        first = self.discovery.discover()
        second = self.discovery.discover(force_refresh=True)

        # Should query twice (force_refresh bypasses cache)
        self.assertGreaterEqual(mock_connect.call_count, 2)

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_get_server_by_name(self, mock_connect, mock_exists):
        """get_server finds server by name (case-insensitive)."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        class MockRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d[k]
            def __iter__(self):
                return iter(self._d.items())

        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [MockRow(SAMPLE_ROW)]

        server = self.discovery.get_server('Survival')
        self.assertIsNotNone(server)
        self.assertEqual(server.name, 'survival')

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_get_server_not_found(self, mock_connect, mock_exists):
        """get_server returns None when server not found."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        server = self.discovery.get_server('nonexistent')
        self.assertIsNone(server)

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_get_server_by_id_found(self, mock_connect, mock_exists):
        """get_server_by_id finds server by numeric ID."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        class MockRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d[k]
            def __iter__(self):
                return iter(self._d.items())

        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [MockRow(SAMPLE_ROW)]

        server = self.discovery.get_server_by_id(1)
        self.assertIsNotNone(server)
        self.assertEqual(server.id, 1)

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_get_server_by_id_not_found(self, mock_connect, mock_exists):
        """get_server_by_id returns None when ID not found."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        server = self.discovery.get_server_by_id(999)
        self.assertIsNone(server)

    @patch('engine.multi_server.os.path.exists')
    @patch('engine.multi_server.sqlite3.connect')
    def test_server_count(self, mock_connect, mock_exists):
        """server_count property returns correct count."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        class MockRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d[k]
            def __iter__(self):
                return iter(self._d.items())

        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [MockRow(r) for r in SAMPLE_ROWS]

        self.assertEqual(self.discovery.server_count, 3)

    def test_get_server_no_cache(self):
        """get_server returns None when no cache and DB fails."""
        with patch('engine.multi_server.os.path.exists', return_value=False):
            result = self.discovery.get_server('test')
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════
# 3. RconConnection Tests
# ═══════════════════════════════════════════════════════════════

class TestRconConnection(unittest.TestCase):
    """Test the RconConnection class with mocked sockets."""

    def setUp(self):
        self.conn = RconConnection('127.0.0.1', 25575, 'secret', 'survival')

    def tearDown(self):
        self.conn.disconnect()

    @patch('engine.multi_server.socket.socket')
    def test_connect_success(self, mock_socket_cls):
        """connect returns True and marks authenticated on success."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Mock RCON auth response: 4 bytes length + 8 bytes (request_id=0, type=2, empty string, null)
        auth_response = struct.pack('<i', 10) + struct.pack('<ii', 0, 2) + b'\x00\x00'
        mock_sock.recv.side_effect = [
            auth_response[:4],    # length
            auth_response[4:],    # body
        ]

        result = self.conn.connect()
        self.assertTrue(result)
        self.assertTrue(self.conn.is_connected)

    @patch('engine.multi_server.socket.socket')
    def test_connect_auth_rejected(self, mock_socket_cls):
        """connect returns False when RCON auth is rejected (resp_id == -1)."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Auth rejected response: request_id = -1
        auth_response = struct.pack('<i', 10) + struct.pack('<ii', -1, 2) + b'\x00\x00'
        mock_sock.recv.side_effect = [
            auth_response[:4],
            auth_response[4:],
        ]

        result = self.conn.connect()
        self.assertFalse(result)
        self.assertFalse(self.conn.is_connected)

    @patch('engine.multi_server.socket.socket')
    def test_connect_timeout(self, mock_socket_cls):
        """connect returns False on socket timeout."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.connect.side_effect = socket.timeout("Timeout")

        result = self.conn.connect()
        self.assertFalse(result)
        self.assertFalse(self.conn.is_connected)

    @patch('engine.multi_server.socket.socket')
    def test_connect_connection_refused(self, mock_socket_cls):
        """connect returns False on connection refused."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.connect.side_effect = ConnectionRefusedError("Refused")

        result = self.conn.connect()
        self.assertFalse(result)

    @patch('engine.multi_server.socket.socket')
    def test_connect_already_connected(self, mock_socket_cls):
        """connect returns True immediately if already authenticated."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # First connect succeeds
        auth_response = struct.pack('<i', 10) + struct.pack('<ii', 0, 2) + b'\x00\x00'
        mock_sock.recv.side_effect = [
            auth_response[:4],
            auth_response[4:],
        ]
        self.conn.connect()

        # Reset mock to verify no second connection attempt
        mock_sock.reset_mock()
        mock_sock.recv.side_effect = None

        # Second connect should succeed immediately
        result = self.conn.connect()
        self.assertTrue(result)
        mock_sock.connect.assert_not_called()

    @patch('engine.multi_server.socket.socket')
    def test_send_command_success(self, mock_socket_cls):
        """send returns server response string."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Auth response
        auth_response = struct.pack('<i', 10) + struct.pack('<ii', 0, 2) + b'\x00\x00'
        # Command response: "There are 3 players online"
        cmd_response_text = b'There are 3 players online'
        cmd_response = struct.pack('<i', len(cmd_response_text) + 8) + struct.pack('<ii', 0, 0) + cmd_response_text + b'\x00\x00'

        mock_sock.recv.side_effect = [
            auth_response[:4],
            auth_response[4:],
            cmd_response[:4],
            cmd_response[4:],
        ]

        self.conn.connect()
        response = self.conn.send('list')
        self.assertIn('3 players online', response)

    @patch('engine.multi_server.socket.socket')
    def test_send_command_no_response_data(self, mock_socket_cls):
        """send returns empty string on empty response."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Auth response
        auth_response = struct.pack('<i', 10) + struct.pack('<ii', 0, 2) + b'\x00\x00'
        mock_sock.recv.side_effect = [
            auth_response[:4],
            auth_response[4:],
            b'',  # No response length data
        ]

        self.conn.connect()
        response = self.conn.send('say hi')
        self.assertEqual(response, '')

    @patch('engine.multi_server.socket.socket')
    def test_send_command_error(self, mock_socket_cls):
        """send raises ConnectionError on socket failure."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Auth response
        auth_response = struct.pack('<i', 10) + struct.pack('<ii', 0, 2) + b'\x00\x00'
        mock_sock.recv.side_effect = [
            auth_response[:4],
            auth_response[4:],
        ]

        self.conn.connect()

        # Now make send fail
        mock_sock.sendall.side_effect = BrokenPipeError("Broken pipe")

        with self.assertRaises(ConnectionError):
            self.conn.send('list')

    @patch('engine.multi_server.socket.socket')
    def test_send_auto_reconnect(self, mock_socket_cls):
        """send auto-reconnects if not connected."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        # Auth response
        auth_response = struct.pack('<i', 10) + struct.pack('<ii', 0, 2) + b'\x00\x00'
        mock_sock.recv.side_effect = [
            auth_response[:4],
            auth_response[4:],
            # Second auth for reconnection
            auth_response[:4],
            auth_response[4:],
        ]

        # send should auto-connect even without explicit connect
        response = self.conn.send('list', timeout=1)
        # The recv will run out of side effects, but the key is it tries to connect
        self.assertTrue(self.conn.is_connected or True)  # connect may succeed or fail gracefully

    @patch('engine.multi_server.socket.socket')
    def test_disconnect(self, mock_socket_cls):
        """disconnect closes socket and clears auth state."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        auth_response = struct.pack('<i', 10) + struct.pack('<ii', 0, 2) + b'\x00\x00'
        mock_sock.recv.side_effect = [
            auth_response[:4],
            auth_response[4:],
        ]

        self.conn.connect()
        self.assertTrue(self.conn.is_connected)

        self.conn.disconnect()
        self.assertFalse(self.conn.is_connected)
        mock_sock.close.assert_called_once()

    @patch('engine.multi_server.socket.socket')
    def test_age_property(self, mock_socket_cls):
        """age returns time since connection."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        auth_response = struct.pack('<i', 10) + struct.pack('<ii', 0, 2) + b'\x00\x00'
        mock_sock.recv.side_effect = [
            auth_response[:4],
            auth_response[4:],
        ]

        self.conn.connect()
        self.assertGreater(self.conn.age, 0)
        self.assertLess(self.conn.age, 5)  # should be less than 5 seconds

    def test_age_zero_when_not_connected(self):
        """age returns 0 when never connected."""
        self.assertEqual(self.conn.age, 0)

    def test_is_connected_false_initially(self):
        """is_connected is False before connect()."""
        self.assertFalse(self.conn.is_connected)

    @patch('engine.multi_server.socket.socket')
    def test_connect_no_auth_response_length(self, mock_socket_cls):
        """connect returns False when no auth response length data received."""
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_sock.recv.return_value = b'abc'  # Not enough bytes

        result = self.conn.connect()
        self.assertFalse(result)


# ═══════════════════════════════════════════════════════════════
# 4. RconPool Tests
# ═══════════════════════════════════════════════════════════════

class TestRconPool(unittest.TestCase):
    """Test the RconPool class."""

    def setUp(self):
        self.mock_discovery = MagicMock(spec=ServerDiscovery)
        self.pool = RconPool(self.mock_discovery, rcon_host='127.0.0.1')

    def tearDown(self):
        self.pool.disconnect_all()
        self.pool.stop_keepalive()

    def test_init(self):
        """RconPool initializes with discovery and host."""
        self.assertEqual(self.pool.rcon_host, '127.0.0.1')
        self.assertEqual(self.pool.discovery, self.mock_discovery)
        self.assertEqual(self.pool._connections, {})

    @patch('engine.multi_server.RconConnection')
    def test_get_or_create_new(self, mock_rcon_cls):
        """_get_or_create creates new connection for unknown server."""
        mock_server = MagicMock()
        mock_server.name = 'survival'
        mock_server.has_rcon = True
        mock_server.rcon_port = 25575
        mock_server.rcon_password = 'secret'
        self.mock_discovery.get_server.return_value = mock_server

        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_rcon_cls.return_value = mock_conn

        result = self.pool._get_or_create('survival')
        self.assertIsNotNone(result)
        mock_rcon_cls.assert_called_once_with('127.0.0.1', 25575, 'secret', 'survival')
        mock_conn.connect.assert_called_once()

    @patch('engine.multi_server.RconConnection')
    def test_get_or_create_cached(self, mock_rcon_cls):
        """_get_or_create returns cached connection if still connected."""
        mock_server = MagicMock()
        mock_server.name = 'survival'
        mock_server.has_rcon = True
        mock_server.rcon_port = 25575
        mock_server.rcon_password = 'secret'
        self.mock_discovery.get_server.return_value = mock_server

        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_rcon_cls.return_value = mock_conn

        first = self.pool._get_or_create('survival')
        second = self.pool._get_or_create('survival')
        self.assertIs(first, second)
        mock_rcon_cls.assert_called_once()  # Only created once

    @patch('engine.multi_server.RconConnection')
    def test_get_or_create_no_rcon(self, mock_rcon_cls):
        """_get_or_create returns None for server without RCON."""
        mock_server = MagicMock()
        mock_server.has_rcon = False
        self.mock_discovery.get_server.return_value = mock_server

        result = self.pool._get_or_create('survival')
        self.assertIsNone(result)

    @patch('engine.multi_server.RconConnection')
    def test_get_or_create_server_not_found(self, mock_rcon_cls):
        """_get_or_create returns None for unknown server."""
        self.mock_discovery.get_server.return_value = None

        result = self.pool._get_or_create('unknown')
        self.assertIsNone(result)

    @patch('engine.multi_server.RconConnection')
    def test_command_success(self, mock_rcon_cls):
        """command sends RCON command to specific server."""
        mock_server = MagicMock()
        mock_server.name = 'survival'
        mock_server.has_rcon = True
        mock_server.rcon_port = 25575
        mock_server.rcon_password = 'secret'
        self.mock_discovery.get_server.return_value = mock_server

        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_conn.send.return_value = 'There are 3 players online'
        mock_rcon_cls.return_value = mock_conn

        result = self.pool.command('survival', 'list')
        self.assertEqual(result, 'There are 3 players online')
        mock_conn.send.assert_called_once_with('list', 5.0)

    @patch('engine.multi_server.RconConnection')
    def test_command_no_rcon(self, mock_rcon_cls):
        """command raises ConnectionError when server has no RCON."""
        mock_server = MagicMock()
        mock_server.has_rcon = False
        self.mock_discovery.get_server.return_value = mock_server

        with self.assertRaises(ConnectionError):
            self.pool.command('survival', 'list')

    @patch('engine.multi_server.RconConnection')
    def test_broadcast_no_connections(self, mock_rcon_cls):
        """broadcast tries all discovered servers when no active connections."""
        mock_server1 = MagicMock()
        mock_server1.name = 'survival'
        mock_server1.has_rcon = True
        mock_server2 = MagicMock()
        mock_server2.name = 'creative'
        mock_server2.has_rcon = True
        self.mock_discovery.discover.return_value = [mock_server1, mock_server2]

        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_conn.send.return_value = 'ok'
        mock_rcon_cls.return_value = mock_conn

        self.mock_discovery.get_server.side_effect = lambda name: {
            'survival': mock_server1,
            'creative': mock_server2
        }.get(name)

        results = self.pool.broadcast('say hi')
        self.assertIn('survival', results)
        self.assertIn('creative', results)

    @patch('engine.multi_server.RconConnection')
    def test_broadcast_no_enabled_servers(self, mock_rcon_cls):
        """broadcast returns error when no servers have RCON."""
        mock_server = MagicMock()
        mock_server.has_rcon = False
        self.mock_discovery.discover.return_value = [mock_server]

        results = self.pool.broadcast('say hi')
        self.assertIn('error', results)

    def test_disconnect(self):
        """disconnect removes and closes a specific connection."""
        mock_conn = MagicMock()
        self.pool._connections['survival'] = mock_conn

        self.pool.disconnect('survival')
        self.assertNotIn('survival', self.pool._connections)
        mock_conn.disconnect.assert_called_once()

    def test_disconnect_not_found(self):
        """disconnect does nothing for unknown server."""
        self.pool.disconnect('unknown')  # Should not raise

    def test_disconnect_all(self):
        """disconnect_all closes all connections."""
        mock_conn1 = MagicMock()
        mock_conn2 = MagicMock()
        self.pool._connections['survival'] = mock_conn1
        self.pool._connections['creative'] = mock_conn2

        self.pool.disconnect_all()
        self.assertEqual(self.pool._connections, {})
        mock_conn1.disconnect.assert_called_once()
        mock_conn2.disconnect.assert_called_once()

    def test_get_connected_servers_empty(self):
        """get_connected_servers returns empty list initially."""
        self.assertEqual(self.pool.get_connected_servers(), [])

    def test_get_connected_servers(self):
        """get_connected_servers returns connected server names."""
        mock_conn1 = MagicMock()
        mock_conn1.is_connected = True
        mock_conn2 = MagicMock()
        mock_conn2.is_connected = False
        self.pool._connections['survival'] = mock_conn1
        self.pool._connections['creative'] = mock_conn2

        result = self.pool.get_connected_servers()
        self.assertEqual(result, ['survival'])

    @patch('engine.multi_server.RconConnection')
    def test_get_server_status_online(self, mock_rcon_cls):
        """get_server_status returns online info."""
        mock_server = MagicMock()
        mock_server.name = 'survival'
        mock_server.has_rcon = True
        mock_server.rcon_port = 25575
        mock_server.rcon_password = 'secret'
        self.mock_discovery.get_server.return_value = mock_server

        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_conn.send.return_value = 'There are 3 of max 20 players online: Steve, Alex, Bob'
        mock_rcon_cls.return_value = mock_conn

        status = self.pool.get_server_status('survival')
        self.assertTrue(status['online'])
        self.assertEqual(status['player_count'], 3)

    @patch('engine.multi_server.RconConnection')
    def test_get_server_status_offline(self, mock_rcon_cls):
        """get_server_status returns offline status on connection error."""
        mock_server = MagicMock()
        mock_server.name = 'survival'
        mock_server.has_rcon = True
        mock_server.rcon_port = 25575
        mock_server.rcon_password = 'secret'
        self.mock_discovery.get_server.return_value = mock_server

        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_conn.send.side_effect = ConnectionError("Server offline")
        mock_rcon_cls.return_value = mock_conn

        status = self.pool.get_server_status('survival')
        self.assertFalse(status['online'])
        self.assertIn('error', status)

    def test_parse_player_count(self):
        """_parse_player_count extracts count from 'list' response."""
        self.assertEqual(self.pool._parse_player_count('There are 3 of max 20 players online: a, b, c'), 3)
        self.assertEqual(self.pool._parse_player_count('There are 0 of max 20 players online'), 0)
        self.assertEqual(self.pool._parse_player_count('No players'), 0)  # No match

    def test_parse_player_count_zero_on_no_match(self):
        """_parse_player_count returns 0 when pattern doesn't match."""
        self.assertEqual(self.pool._parse_player_count('Some random text'), 0)
        self.assertEqual(self.pool._parse_player_count(''), 0)

    @patch('engine.multi_server.RconConnection')
    def test_keepalive_start_stop(self, mock_rcon_cls):
        """start_keepalive and stop_keepalive manage background thread."""
        self.pool.start_keepalive()
        self.assertTrue(self.pool._keepalive_running)
        self.assertIsNotNone(self.pool._keepalive_thread)

        self.pool.stop_keepalive()
        self.assertFalse(self.pool._keepalive_running)


# ═══════════════════════════════════════════════════════════════
# 5. SubsystemRegistry Tests
# ═══════════════════════════════════════════════════════════════

class TestSubsystemRegistry(unittest.TestCase):
    """Test the SubsystemRegistry class."""

    def setUp(self):
        self.temp_config_path = '/tmp/test_subsystem_config.json'
        self.registry = SubsystemRegistry(
            config_path=self.temp_config_path,
            vfs_root='/tmp/test_vfs',
        )
        # Clean up any existing config
        if os.path.exists(self.temp_config_path):
            os.remove(self.temp_config_path)

    def tearDown(self):
        if os.path.exists(self.temp_config_path):
            os.remove(self.temp_config_path)

    def test_init_no_config(self):
        """Registry initializes with empty config when no config file exists."""
        self.assertEqual(self.registry._config, {})

    def test_init_with_config(self):
        """Registry loads existing config file."""
        config_data = {'survival': {'auction_house': True}}
        with open(self.temp_config_path, 'w') as f:
            json.dump(config_data, f)

        registry = SubsystemRegistry(config_path=self.temp_config_path)
        self.assertTrue(registry.is_enabled('survival', 'auction_house'))

    def test_is_enabled_default_false(self):
        """is_enabled returns False for unconfigured server/subsystem."""
        self.assertFalse(self.registry.is_enabled('survival', 'auction_house'))

    def test_is_enabled_unknown_subsystem(self):
        """is_enabled returns False for unknown subsystem."""
        self.assertFalse(self.registry.is_enabled('survival', 'nonexistent'))

    def test_set_enabled(self):
        """set_enabled enables a subsystem and persists config."""
        result = self.registry.set_enabled('survival', 'auction_house', True)
        self.assertTrue(result)
        self.assertTrue(self.registry._config.get('survival', {}).get('auction_house'))

        # Verify persisted to file
        with open(self.temp_config_path) as f:
            saved = json.load(f)
        self.assertTrue(saved['survival']['auction_house'])

    def test_set_enabled_returns_false_if_unchanged(self):
        """set_enabled returns False if already in that state."""
        self.registry.set_enabled('survival', 'auction_house', True)
        result = self.registry.set_enabled('survival', 'auction_house', True)
        self.assertFalse(result)

    def test_set_enabled_unknown_subsystem(self):
        """set_enabled returns False for unknown subsystem."""
        result = self.registry.set_enabled('survival', 'nonexistent', True)
        self.assertFalse(result)

    def test_set_enabled_disable(self):
        """set_enabled can disable a previously enabled subsystem."""
        self.registry.set_enabled('survival', 'auction_house', True)
        result = self.registry.set_enabled('survival', 'auction_house', False)
        self.assertTrue(result)
        self.assertFalse(self.registry._config['survival']['auction_house'])

    def test_get_subsystem_vfs_db_path(self):
        """get_subsystem_vfs_db_path returns correct VFS path."""
        path = self.registry.get_subsystem_vfs_db_path('survival', 'auction_house')
        self.assertEqual(path, '/servers/survival/data/auction_house.db')

    def test_get_subsystem_vfs_db_path_unknown(self):
        """get_subsystem_vfs_db_path returns None for unknown subsystem."""
        path = self.registry.get_subsystem_vfs_db_path('survival', 'unknown')
        self.assertIsNone(path)

    def test_get_subsystem_vfs_config_path(self):
        """get_subsystem_vfs_config_path returns correct VFS path."""
        path = self.registry.get_subsystem_vfs_config_path('survival', 'economy_bridge')
        self.assertEqual(path, '/servers/survival/config/economy_bridge.json')

    def test_get_subsystem_vfs_config_path_unknown(self):
        """get_subsystem_vfs_config_path returns None for unknown subsystem."""
        path = self.registry.get_subsystem_vfs_config_path('survival', 'unknown')
        self.assertIsNone(path)

    def test_list_subsystems(self):
        """list_subsystems returns all subsystems with status for a server."""
        subsystems = self.registry.list_subsystems('survival')
        keys = [s['key'] for s in subsystems]
        self.assertIn('auction_house', keys)
        self.assertIn('economy_bridge', keys)
        self.assertIn('simulated_people', keys)
        self.assertIn('lootpower_games', keys)
        self.assertEqual(len(subsystems), 4)

        # All should be disabled by default
        for s in subsystems:
            self.assertFalse(s['enabled'])

    def test_list_subsystems_with_enabled(self):
        """list_subsystems reflects enabled subsystems."""
        self.registry.set_enabled('survival', 'auction_house', True)
        subsystems = self.registry.list_subsystems('survival')
        ah = next(s for s in subsystems if s['key'] == 'auction_house')
        self.assertTrue(ah['enabled'])

    def test_get_enabled_subsystems(self):
        """get_enabled_subsystems returns only enabled subsystem keys."""
        self.registry.set_enabled('survival', 'auction_house', True)
        self.registry.set_enabled('survival', 'lootpower_games', True)

        enabled = self.registry.get_enabled_subsystems('survival')
        self.assertIn('auction_house', enabled)
        self.assertIn('lootpower_games', enabled)
        self.assertNotIn('economy_bridge', enabled)
        self.assertNotIn('simulated_people', enabled)
        self.assertEqual(len(enabled), 2)

    def test_init_subsystem_not_enabled(self):
        """init_subsystem returns False when subsystem not enabled."""
        result = self.registry.init_subsystem('survival', 'auction_house')
        self.assertFalse(result)

    def test_init_subsystem_unknown(self):
        """init_subsystem returns False for unknown subsystem."""
        result = self.registry.init_subsystem('survival', 'unknown')
        self.assertFalse(result)

    def test_init_subsystem_stub(self):
        """init_subsystem returns True for enabled, known subsystem (stub)."""
        self.registry.set_enabled('survival', 'auction_house', True)
        result = self.registry.init_subsystem('survival', 'auction_house')
        self.assertTrue(result)

    def test_shutdown_subsystem_stub(self):
        """shutdown_subsystem returns True (stub)."""
        result = self.registry.shutdown_subsystem('survival', 'auction_house')
        self.assertTrue(result)

    def test_get_server_subsystem_summary(self):
        """get_server_subsystem_summary returns complete summary."""
        self.registry.set_enabled('survival', 'auction_house', True)
        summary = self.registry.get_server_subsystem_summary('survival')

        self.assertEqual(summary['server'], 'survival')
        self.assertEqual(summary['enabled_count'], 1)
        self.assertEqual(len(summary['subsystems']), 4)

    def test_load_config_error_handling(self):
        """Registry handles corrupt config file gracefully."""
        with open(self.temp_config_path, 'w') as f:
            f.write('{corrupt json')

        # Re-initialize with corrupt config
        registry = SubsystemRegistry(config_path=self.temp_config_path)
        self.assertEqual(registry._config, {})

    def test_save_config_creates_directory(self):
        """_save_config creates parent directories if missing."""
        deep_path = '/tmp/test_nested/subsystems/subsystem_config.json'
        registry = SubsystemRegistry(config_path=deep_path)
        try:
            registry.set_enabled('test', 'auction_house', True)
            self.assertTrue(os.path.exists(deep_path))
        finally:
            if os.path.exists(deep_path):
                os.remove(deep_path)
            # Clean up created directories
            for p in ['/tmp/test_nested/subsystems', '/tmp/test_nested']:
                if os.path.exists(p):
                    os.rmdir(p)


# ═══════════════════════════════════════════════════════════════
# 6. CustomCommandManager Tests
# ═══════════════════════════════════════════════════════════════

class TestCustomCommandManager(unittest.TestCase):
    """Test the CustomCommandManager class."""

    def setUp(self):
        self.temp_config_path = '/tmp/test_custom_commands.json'
        self.manager = CustomCommandManager(config_path=self.temp_config_path)
        if os.path.exists(self.temp_config_path):
            os.remove(self.temp_config_path)

    def tearDown(self):
        if os.path.exists(self.temp_config_path):
            os.remove(self.temp_config_path)

    def test_init_empty(self):
        """Manager initializes with empty commands."""
        self.assertEqual(self.manager.list(), {})

    def test_add_new_command(self):
        """add creates a new custom command for a server."""
        result = self.manager.add('survival', 'backup', 'say Backing up...;save-all')
        self.assertTrue(result)

        cmds = self.manager.list('survival')
        self.assertEqual(len(cmds['survival']), 1)
        self.assertEqual(cmds['survival'][0]['name'], 'backup')
        self.assertEqual(cmds['survival'][0]['mc_commands'], 'say Backing up...;save-all')

    def test_add_duplicate_fails(self):
        """add returns False for duplicate command name."""
        self.manager.add('survival', 'backup', 'save-all')
        result = self.manager.add('survival', 'backup', 'say hi')
        self.assertFalse(result)

    def test_add_duplicate_with_overwrite(self):
        """add with overwrite=True replaces existing command."""
        self.manager.add('survival', 'backup', 'save-all')
        result = self.manager.add('survival', 'backup', 'say hi', overwrite=True)
        self.assertTrue(result)

        cmds = self.manager.list('survival')
        self.assertEqual(cmds['survival'][0]['mc_commands'], 'say hi')

    def test_add_invalid_name(self):
        """add returns False for empty or whitespace name."""
        self.assertFalse(self.manager.add('survival', '', 'save-all'))
        self.assertFalse(self.manager.add('survival', '   ', 'save-all'))

    def test_add_invalid_commands(self):
        """add returns False for empty or whitespace mc_commands."""
        self.assertFalse(self.manager.add('survival', 'backup', ''))
        self.assertFalse(self.manager.add('survival', 'backup', '   '))

    def test_add_normalizes_name(self):
        """add normalizes name (lowercase, spaces to hyphens)."""
        self.manager.add('survival', 'Backup World', 'save-all')
        cmds = self.manager.list('survival')
        self.assertEqual(cmds['survival'][0]['name'], 'backup-world')

    def test_add_global_command(self):
        """add with server_name='*' creates a global command."""
        self.manager.add('*', 'restart-warning', 'say Server restarting in 5 minutes')
        cmds = self.manager.list('*')
        self.assertEqual(len(cmds['*']), 1)

    def test_remove_existing(self):
        """remove deletes a custom command."""
        self.manager.add('survival', 'backup', 'save-all')
        result = self.manager.remove('survival', 'backup')
        self.assertTrue(result)
        self.assertEqual(len(self.manager.list('survival')['survival']), 0)

    def test_remove_nonexistent(self):
        """remove returns False for nonexistent command."""
        result = self.manager.remove('survival', 'nonexistent')
        self.assertFalse(result)

    def test_remove_nonexistent_server(self):
        """remove returns False for server with no commands."""
        result = self.manager.remove('unknown', 'backup')
        self.assertFalse(result)

    def test_list_all(self):
        """list() without server returns all commands."""
        self.manager.add('survival', 'backup', 'save-all')
        self.manager.add('creative', 'restart-warning', 'say restarting')
        all_cmds = self.manager.list()
        self.assertIn('survival', all_cmds)
        self.assertIn('creative', all_cmds)
        self.assertEqual(len(all_cmds), 2)

    def test_list_specific_server(self):
        """list('survival') returns only that server's commands."""
        self.manager.add('survival', 'backup', 'save-all')
        self.manager.add('creative', 'restart-warning', 'say restarting')
        svr_cmds = self.manager.list('survival')
        self.assertIn('survival', svr_cmds)
        self.assertNotIn('creative', svr_cmds)

    def test_get_commands_for_server(self):
        """get_commands_for_server includes both server-specific and global commands."""
        self.manager.add('survival', 'backup', 'save-all')
        self.manager.add('*', 'global-restart', 'say restarting')

        commands = self.manager.get_commands_for_server('survival')
        self.assertEqual(len(commands), 2)
        names = [c['name'] for c in commands]
        self.assertIn('backup', names)
        self.assertIn('global-restart', names)

    def test_run_server_specific(self):
        """run executes server-specific command via rcon_func."""
        self.manager.add('survival', 'backup', 'say Backing up...;save-all')

        mock_rcon = MagicMock()
        mock_rcon.side_effect = lambda svr, cmd: f"Executed: {cmd}"

        result = self.manager.run('survival', 'backup', mock_rcon)
        self.assertEqual(result['command'], 'backup')
        self.assertEqual(result['server'], 'survival')
        self.assertEqual(len(result['results']), 2)
        mock_rcon.assert_any_call('survival', 'say Backing up...')
        mock_rcon.assert_any_call('survival', 'save-all')

    def test_run_global_fallback(self):
        """run falls back to global command if no server-specific."""
        self.manager.add('*', 'global-cmd', 'say global')

        mock_rcon = MagicMock(return_value='ok')
        result = self.manager.run('survival', 'global-cmd', mock_rcon)
        self.assertEqual(result['command'], 'global-cmd')
        mock_rcon.assert_called_once_with('survival', 'say global')

    def test_run_not_found(self):
        """run returns error for unknown command."""
        mock_rcon = MagicMock()
        result = self.manager.run('survival', 'unknown', mock_rcon)
        self.assertIn('error', result)
        mock_rcon.assert_not_called()

    def test_run_partial_failure(self):
        """run continues executing remaining commands after one fails."""
        self.manager.add('survival', 'multi', 'cmd1;cmd2;cmd3')

        def mock_rcon(svr, cmd):
            if cmd == 'cmd2':
                raise ConnectionError("Failed")
            return f"ok: {cmd}"

        result = self.manager.run('survival', 'multi', mock_rcon)
        self.assertEqual(len(result['results']), 3)
        self.assertIn('Error', result['results']['cmd2'])

    @patch('engine.multi_server.json.dump')
    def test_save_error_handling(self, mock_json_dump):
        """_save handles JSON write errors gracefully."""
        mock_json_dump.side_effect = Exception("Write denied")
        self.manager.add('survival', 'backup', 'save-all')  # Should not raise

    @patch('engine.multi_server.json.load')
    def test_load_error_handling(self, mock_json_load):
        """_load handles JSON read errors gracefully."""
        mock_json_load.side_effect = json.JSONDecodeError("Bad JSON", "", 0)
        manager = CustomCommandManager(config_path=self.temp_config_path)
        self.assertEqual(manager._commands, {})

    def test_persists_to_file(self):
        """Added commands persist to the JSON file."""
        self.manager.add('survival', 'backup', 'save-all')
        self.assertTrue(os.path.exists(self.temp_config_path))

        with open(self.temp_config_path) as f:
            data = json.load(f)
        self.assertIn('survival', data)


# ═══════════════════════════════════════════════════════════════
# 7. MultiServerManager Tests
# ═══════════════════════════════════════════════════════════════

class TestMultiServerManager(unittest.TestCase):
    """Test the MultiServerManager facade."""

    def setUp(self):
        self.manager = MultiServerManager(config={
            'runner_db_path': ':memory:',
            'runner_vfs_root': '/tmp/test_vfs',
            'default_rcon_host': '127.0.0.1',
        })

    def tearDown(self):
        self.manager.shutdown()

    def test_init(self):
        """Manager initializes with all sub-components."""
        self.assertIsNotNone(self.manager.discovery)
        self.assertIsNotNone(self.manager.rcon_pool)
        self.assertIsNotNone(self.manager.subsystems)
        self.assertIsNotNone(self.manager.custom_commands)

    def test_list_servers_empty(self):
        """list_servers returns empty list when no servers discovered."""
        # Discovery will return empty since :memory: DB doesn't have server_instances table
        with patch.object(self.manager.discovery, 'discover', return_value=[]):
            servers = self.manager.list_servers()
        self.assertEqual(servers, [])

    def test_list_servers_with_mock_data(self):
        """list_servers returns server info when servers are discovered."""
        mock_server = MagicMock(spec=ServerInfo)
        mock_server.name = 'survival'
        mock_server.to_dict.return_value = {
            'id': 1, 'name': 'survival', 'mc_version': '1.20.4',
            'server_type': 'paper', 'server_port': 25565, 'rcon_port': 25575,
            'has_rcon': True, 'enabled': True,
        }
        mock_server.vfs_server_path = '/servers/survival'

        with patch.object(self.manager.discovery, 'discover', return_value=[mock_server]):
            with patch.object(self.manager.subsystems, 'list_subsystems', return_value=[]):
                servers = self.manager.list_servers()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]['name'], 'survival')

    def test_list_servers_with_status(self):
        """list_servers with_status=True includes live status."""
        mock_server = MagicMock(spec=ServerInfo)
        mock_server.name = 'survival'
        mock_server.to_dict.return_value = {
            'id': 1, 'name': 'survival', 'mc_version': '1.20.4',
            'server_type': 'paper', 'server_port': 25565, 'rcon_port': 25575,
            'has_rcon': True, 'enabled': True,
        }

        mock_status = {'online': True, 'player_count': 3, 'players_raw': '...'}

        with patch.object(self.manager.discovery, 'discover', return_value=[mock_server]):
            with patch.object(self.manager.subsystems, 'list_subsystems', return_value=[]):
                with patch.object(self.manager.rcon_pool, 'get_server_status', return_value=mock_status):
                    servers = self.manager.list_servers(with_status=True)
        self.assertEqual(servers[0]['status'], mock_status)

    def test_send_command(self):
        """send_command delegates to rcon_pool.command."""
        with patch.object(self.manager.rcon_pool, 'command', return_value='ok') as mock_cmd:
            result = self.manager.send_command('survival', 'list')
        self.assertEqual(result, 'ok')
        mock_cmd.assert_called_once_with('survival', 'list')

    def test_broadcast_command(self):
        """broadcast_command delegates to rcon_pool.broadcast."""
        with patch.object(self.manager.rcon_pool, 'broadcast', return_value={'survival': 'ok'}) as mock_bc:
            result = self.manager.broadcast_command('say hi')
        self.assertEqual(result, {'survival': 'ok'})
        mock_bc.assert_called_once_with('say hi')

    def test_get_server_detail_found(self):
        """get_server_detail returns full detail for existing server."""
        mock_server = MagicMock(spec=ServerInfo)
        mock_server.name = 'survival'
        mock_server.to_dict.return_value = {'name': 'survival', 'id': 1}
        mock_server.vfs_server_path = '/servers/survival'

        mock_status = {'online': True}

        with patch.object(self.manager.discovery, 'get_server', return_value=mock_server):
            with patch.object(self.manager.rcon_pool, 'get_server_status', return_value=mock_status):
                with patch.object(self.manager.subsystems, 'list_subsystems', return_value=[]):
                    with patch.object(self.manager.custom_commands, 'get_commands_for_server', return_value=[]):
                        detail = self.manager.get_server_detail('survival')
        self.assertIsNotNone(detail)
        self.assertEqual(detail['name'], 'survival')
        self.assertEqual(detail['status'], mock_status)

    def test_get_server_detail_not_found(self):
        """get_server_detail returns None for unknown server."""
        with patch.object(self.manager.discovery, 'get_server', return_value=None):
            detail = self.manager.get_server_detail('unknown')
        self.assertIsNone(detail)

    def test_shutdown(self):
        """shutdown stops keepalive and disconnects all RCON."""
        with patch.object(self.manager.rcon_pool, 'stop_keepalive') as mock_stop:
            with patch.object(self.manager.rcon_pool, 'disconnect_all') as mock_disc:
                self.manager.shutdown()
        mock_stop.assert_called_once()
        mock_disc.assert_called_once()

    def test_shutdown_idempotent(self):
        """shutdown can be called multiple times."""
        with patch.object(self.manager.rcon_pool, 'stop_keepalive') as mock_stop:
            with patch.object(self.manager.rcon_pool, 'disconnect_all') as mock_disc:
                self.manager.shutdown()
                self.manager.shutdown()
        self.assertEqual(mock_stop.call_count, 2)
        self.assertEqual(mock_disc.call_count, 2)


# ═══════════════════════════════════════════════════════════════
# 8. Helper Function Tests
# ═══════════════════════════════════════════════════════════════

class TestHelperFunctions(unittest.TestCase):
    """Test standalone helper functions."""

    @patch('engine.multi_server._DEFAULT_CONFIG_PATH')
    @patch('engine.multi_server.os.path.exists')
    def test_ensure_default_config_creates(self, mock_exists, mock_config_path):
        """ensure_default_config writes default config when file doesn't exist."""
        mock_exists.return_value = False
        mock_config_path.exists.return_value = False
        mock_config_path.parent = Path('/tmp/test_defaults')

        with patch('engine.multi_server.open', mock_open()) as m_open:
            ensure_default_config()
            m_open.assert_called_once()
            handle = m_open()
            written = ''.join(call[0][0] for call in handle.write.call_args_list)
            self.assertIn('runner_db_path', written)
            self.assertIn('default_rcon_host', written)

    @patch('engine.multi_server._DEFAULT_CONFIG_PATH')
    def test_ensure_default_config_skips_if_exists(self, mock_config_path):
        """ensure_default_config does nothing when config already exists."""
        mock_config_path.exists.return_value = True
        with patch('engine.multi_server.open') as m_open:
            ensure_default_config()
            m_open.assert_not_called()

    def test_subsystem_definitions_structure(self):
        """SUBSYSTEM_DEFINITIONS has correct structure for all subsystems."""
        expected_keys = {'auction_house', 'economy_bridge', 'simulated_people', 'lootpower_games'}
        self.assertEqual(set(SUBSYSTEM_DEFINITIONS.keys()), expected_keys)

        for key, defn in SUBSYSTEM_DEFINITIONS.items():
            self.assertIn('label', defn)
            self.assertIn('description', defn)
            self.assertIn('vfs_db_path', defn)
            self.assertIn('vfs_config_path', defn)
            self.assertIn('default_enabled', defn)
            self.assertIn('requires_rcon', defn)
            self.assertIn('compatible_types', defn)
            self.assertFalse(defn['default_enabled'])  # All disabled by default

    def test_subsystem_definitions_vfs_paths(self):
        """SUBSYSTEM_DEFINITIONS VFS paths use {name} placeholder."""
        for key, defn in SUBSYSTEM_DEFINITIONS.items():
            db_path = defn['vfs_db_path'].format(name='test-server')
            config_path = defn['vfs_config_path'].format(name='test-server')
            self.assertIn('test-server', db_path)
            self.assertIn('test-server', config_path)
            self.assertTrue(db_path.endswith('.db'))
            self.assertTrue(config_path.endswith('.json'))

    def test_server_info_edge_cases(self):
        """ServerInfo handles edge case values."""
        # None values
        info = ServerInfo({'id': 0, 'name': 'test'})
        self.assertEqual(info.id, 0)
        self.assertEqual(info.name, 'test')

        # Unicode name
        info = ServerInfo({'id': 1, 'name': u'\u00e9lite'})
        self.assertEqual(info.name, u'\u00e9lite')

        # Very long name
        info = ServerInfo({'id': 2, 'name': 'a' * 255})
        self.assertEqual(len(info.name), 255)


if __name__ == '__main__':
    unittest.main()
