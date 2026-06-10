"""
multi_server.py — Multi-Server Discovery, RCON Pool, Subsystem Registry, Custom Commands

Integrates with mc-server-runner's server_data.db to discover all managed
Minecraft server instances, then provides:
  - Server discovery & status monitoring
  - Multi-RCON connection pool (concurrent connections per server)
  - Subsystem registry skeleton (per-server on/off, VFS DB paths)
  - Custom command definitions (user-defined named aliases per server)

Default state: RCON routing ON for all servers, all subsystems OFF.
"""

import os
import re
import sys
import json
import time
import struct
import socket
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple, Callable

logger = logging.getLogger('minecraft_manager.multi_server')

# ── Default paths relative to this script's project root ────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_RUNNER_DB = str(_PROJECT_ROOT / '..' / '..' / 'SERVERS' / 'mc-server-runner' / 'DATA' / 'server_data.db')
_DEFAULT_RUNNER_VFS = str(_PROJECT_ROOT / '..' / '..' / 'SERVERS' / 'mc-server-runner' / 'vfs')
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / 'DATA' / 'multi_server_config.json'
_DEFAULT_CUSTOM_COMMANDS_PATH = _PROJECT_ROOT / 'DATA' / 'custom_commands.json'
_DEFAULT_SUBSYSTEM_CONFIG_PATH = _PROJECT_ROOT / 'DATA' / 'subsystem_config.json'

# ── Known subsystem definitions ─────────────────────────────────
SUBSYSTEM_DEFINITIONS = {
    'auction_house': {
        'label': 'Auction House',
        'description': 'Player auction listing, bidding, and AI-driven economy simulation',
        'vfs_db_path': '/servers/{name}/data/auction_house.db',
        'vfs_config_path': '/servers/{name}/config/auction_house.json',
        'default_enabled': False,
        'requires_rcon': True,
        'compatible_types': ['vanilla', 'paper', 'spigot', 'fabric', 'forge'],
    },
    'economy_bridge': {
        'label': 'Economy Bridge',
        'description': 'Otters Civ economy integration (coins, balances, ledger)',
        'vfs_db_path': '/servers/{name}/data/economy_bridge.db',
        'vfs_config_path': '/servers/{name}/config/economy_bridge.json',
        'default_enabled': False,
        'requires_rcon': True,
        'compatible_types': ['vanilla', 'paper', 'spigot', 'fabric', 'forge'],
    },
    'simulated_people': {
        'label': 'Simulated People',
        'description': 'Up to 200 simulated personas with jobs, wealth, and market needs',
        'vfs_db_path': '/servers/{name}/data/simulated_people.db',
        'vfs_config_path': '/servers/{name}/config/simulated_people.json',
        'default_enabled': False,
        'requires_rcon': True,
        'compatible_types': ['vanilla', 'paper', 'spigot', 'fabric', 'forge'],
    },
    'lootpower_games': {
        'label': 'LootPower Games',
        'description': 'Minescript-based loot gambling minigames',
        'vfs_db_path': '/servers/{name}/data/lootpower.db',
        'vfs_config_path': '/servers/{name}/config/lootpower.json',
        'default_enabled': False,
        'requires_rcon': True,
        'compatible_types': ['vanilla', 'paper', 'spigot', 'fabric', 'forge'],
    },
}


# ═══════════════════════════════════════════════════════════════
# 1. Server Discovery
# ═══════════════════════════════════════════════════════════════

class ServerInfo:
    """Represents a discovered Minecraft server instance from mc-server-runner."""

    def __init__(self, row: dict):
        self.id: int = row['id']
        self.name: str = row['name']
        self.mc_version: str = row.get('mc_version', '1.20.4')
        self.server_type: str = row.get('server_type', 'vanilla')
        self.server_port: int = row.get('server_port', 25565)
        self.rcon_port: int = row.get('rcon_port', 25575)
        self.rcon_password: str = row.get('rcon_password', '')
        self.enabled: bool = bool(row.get('enabled', 1))
        self.auto_start: bool = bool(row.get('auto_start', 0))
        self.java_version: str = row.get('java_version', '17')
        self.min_ram: str = row.get('min_ram', '2G')
        self.max_ram: str = row.get('max_ram', '4G')

    @property
    def vfs_server_path(self) -> str:
        return f'/servers/{self.name}'

    @property
    def has_rcon(self) -> bool:
        return bool(self.rcon_password)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'mc_version': self.mc_version,
            'server_type': self.server_type,
            'server_port': self.server_port,
            'rcon_port': self.rcon_port,
            'has_rcon': self.has_rcon,
            'enabled': self.enabled,
        }

    def __repr__(self) -> str:
        return f"<ServerInfo {self.name} (MC {self.mc_version}, {self.server_type})>"


class ServerDiscovery:
    """Discovers Minecraft servers from mc-server-runner's database."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or _DEFAULT_RUNNER_DB
        self._cache: Optional[List[ServerInfo]] = None
        self._cache_time: float = 0
        self._cache_ttl: float = 30.0  # seconds
        self._lock = threading.Lock()

    def discover(self, force_refresh: bool = False) -> List[ServerInfo]:
        """Query mc-server-runner DB and return all server instances.

        Results are cached for _cache_ttl seconds to avoid repeated DB reads.
        """
        now = time.time()
        with self._lock:
            if not force_refresh and self._cache is not None and (now - self._cache_time) < self._cache_ttl:
                return self._cache

            # Validate DB exists
            if not os.path.exists(self.db_path):
                logger.warning(f"mc-server-runner DB not found: {self.db_path}")
                self._cache = []
                self._cache_time = now
                return self._cache

            try:
                import sqlite3
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM server_instances ORDER BY name"
                ).fetchall()
                conn.close()

                servers = [ServerInfo(dict(r)) for r in rows]
                self._cache = servers
                self._cache_time = now
                logger.info(f"Discovered {len(servers)} servers from {self.db_path}")
                return servers

            except Exception as e:
                logger.error(f"Failed to discover servers: {e}")
                self._cache = []
                self._cache_time = now
                return []

    def get_server(self, name: str) -> Optional[ServerInfo]:
        """Find a server by name (case-insensitive)."""
        servers = self.discover()
        for s in servers:
            if s.name.lower() == name.lower():
                return s
        return None

    def get_server_by_id(self, server_id: int) -> Optional[ServerInfo]:
        """Find a server by its DB ID."""
        servers = self.discover()
        for s in servers:
            if s.id == server_id:
                return s
        return None

    @property
    def server_count(self) -> int:
        return len(self.discover())


# ═══════════════════════════════════════════════════════════════
# 2. RCON Connection Pool
# ═══════════════════════════════════════════════════════════════

class RconConnection:
    """Manages a single RCON connection to a Minecraft server."""

    def __init__(self, host: str, port: int, password: str, server_name: str):
        self.host = host
        self.port = port
        self.password = password
        self.server_name = server_name
        self._sock: Optional[socket.socket] = None
        self._authenticated: bool = False
        self._lock = threading.Lock()
        self._last_used: float = 0
        self._connect_time: float = 0

    def connect(self, timeout: float = 5.0) -> bool:
        """Open socket and authenticate with RCON server."""
        with self._lock:
            if self._sock and self._authenticated:
                return True

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((self.host, self.port))

                # RCON auth packet (type 3 = SERVERDATA_AUTH)
                auth_id = int(time.time()) & 0x7FFFFFFF
                auth_body = struct.pack('<ii', auth_id, 3) + self.password.encode('utf-8') + b'\x00\x00'
                sock.sendall(struct.pack('<i', len(auth_body)) + auth_body)

                # Read auth response
                resp_len_data = sock.recv(4)
                if len(resp_len_data) < 4:
                    raise ConnectionError("No auth response length")
                resp_len = struct.unpack('<i', resp_len_data)[0]
                resp_data = sock.recv(resp_len)
                if len(resp_data) < 8:
                    raise ConnectionError("Auth response too short")

                resp_id = struct.unpack('<i', resp_data[:4])[0]
                if resp_id == -1:
                    raise ConnectionError("RCON authentication rejected (wrong password?)")

                self._sock = sock
                self._authenticated = True
                self._connect_time = time.time()
                self._last_used = time.time()
                logger.info(f"RCON connected to {self.server_name} ({self.host}:{self.port})")
                return True

            except Exception as e:
                logger.warning(f"RCON connect failed for {self.server_name}: {e}")
                self._cleanup()
                return False

    def send(self, command: str, timeout: float = 5.0) -> str:
        """Send an RCON command and return the response."""
        with self._lock:
            if not self._sock or not self._authenticated:
                if not self.connect(timeout):
                    raise ConnectionError(f"RCON not connected to {self.server_name}")

            try:
                self._sock.settimeout(timeout)

                # Command packet (type 2 = SERVERDATA_EXECCOMMAND)
                cmd_id = int(time.time()) & 0x7FFFFFFF
                cmd_body = struct.pack('<ii', cmd_id, 2) + command.encode('utf-8') + b'\x00\x00'
                self._sock.sendall(struct.pack('<i', len(cmd_body)) + cmd_body)

                # Read response
                length_data = self._sock.recv(4)
                if len(length_data) < 4:
                    return ""
                length = struct.unpack('<i', length_data)[0]
                response = b''
                while len(response) < length:
                    chunk = self._sock.recv(length - len(response))
                    if not chunk:
                        break
                    response += chunk

                self._last_used = time.time()
                if response:
                    response = response[4:-2]  # skip request ID and null terminators
                return response.decode('utf-8', errors='replace')

            except (socket.timeout, ConnectionError, BrokenPipeError, OSError) as e:
                logger.warning(f"RCON send failed for {self.server_name}: {e}")
                self._cleanup()
                raise ConnectionError(f"RCON error on {self.server_name}: {e}")

    def disconnect(self):
        """Close the RCON socket."""
        with self._lock:
            self._cleanup()

    def _cleanup(self):
        """Internal cleanup without lock."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._authenticated = False

    @property
    def is_connected(self) -> bool:
        return self._sock is not None and self._authenticated

    @property
    def age(self) -> float:
        if self._connect_time:
            return time.time() - self._connect_time
        return 0

    def __del__(self):
        self._cleanup()


class RconPool:
    """Manages RCON connections to multiple servers concurrently.

    Connections are lazy — created on first command and cached for reuse.
    Supports broadcasting a command to all connected servers.
    """

    def __init__(self, discovery: ServerDiscovery, rcon_host: str = '127.0.0.1'):
        self.discovery = discovery
        self.rcon_host = rcon_host
        self._connections: Dict[str, RconConnection] = {}
        self._lock = threading.Lock()
        self._keepalive_thread: Optional[threading.Thread] = None
        self._keepalive_running = False
        self._keepalive_interval = 30.0  # ping every 30s to keep connections alive

    def _get_or_create(self, server_name: str) -> Optional[RconConnection]:
        """Get existing connection or create a new one for the server."""
        server = self.discovery.get_server(server_name)
        if not server:
            logger.warning(f"Server not found: {server_name}")
            return None
        if not server.has_rcon:
            logger.warning(f"Server '{server_name}' has no RCON password configured")
            return None

        with self._lock:
            if server_name in self._connections:
                conn = self._connections[server_name]
                if conn.is_connected:
                    return conn
                # Stale connection — remove and recreate
                del self._connections[server_name]

            conn = RconConnection(self.rcon_host, server.rcon_port, server.rcon_password, server_name)
            if not conn.connect():
                return None
            self._connections[server_name] = conn
            return conn

    def command(self, server_name: str, cmd: str, timeout: float = 5.0) -> str:
        """Send a command to a specific server via RCON.

        Returns the server's response text.
        Raises ConnectionError if RCON fails.
        """
        conn = self._get_or_create(server_name)
        if conn is None:
            raise ConnectionError(f"Cannot connect RCON to server '{server_name}'")
        return conn.send(cmd, timeout)

    def broadcast(self, cmd: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Send a command to ALL currently connected servers.

        Returns dict mapping server_name -> response (or error message).
        Only sends to servers we have active RCON connections for.
        """
        results = {}
        server_names = list(self._connections.keys())
        if not server_names:
            # If no active connections, try all discovered servers
            all_servers = self.discovery.discover()
            server_names = [s.name for s in all_servers if s.has_rcon]
            if not server_names:
                return {'error': 'No servers with RCON configured'}

        for name in server_names:
            try:
                resp = self.command(name, cmd, timeout)
                results[name] = resp
            except Exception as e:
                results[name] = f"Error: {e}"

        return results

    def disconnect(self, server_name: str):
        """Disconnect RCON from a specific server."""
        with self._lock:
            conn = self._connections.pop(server_name, None)
            if conn:
                conn.disconnect()
                logger.info(f"RCON disconnected from {server_name}")

    def disconnect_all(self):
        """Disconnect all RCON connections."""
        with self._lock:
            for name, conn in self._connections.items():
                conn.disconnect()
            self._connections.clear()
            logger.info("All RCON connections closed")

    def get_connected_servers(self) -> List[str]:
        """Return list of currently connected server names."""
        with self._lock:
            return [name for name, conn in self._connections.items() if conn.is_connected]

    def get_server_status(self, server_name: str) -> Optional[dict]:
        """Get online status and basic info for a server via RCON."""
        try:
            players = self.command(server_name, 'list', timeout=3)
            return {
                'online': True,
                'players_raw': players,
                'player_count': self._parse_player_count(players),
            }
        except (ConnectionError, Exception) as e:
            return {'online': False, 'error': str(e)}

    def _parse_player_count(self, players_raw: str) -> int:
        """Parse 'There are X of max Y players online: ...' -> X."""
        m = re.search(r'There are (\d+) of', players_raw)
        return int(m.group(1)) if m else 0

    def start_keepalive(self):
        """Start background thread that periodically pings all connections."""
        if self._keepalive_running:
            return
        self._keepalive_running = True
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()
        logger.info("RCON keepalive started")

    def stop_keepalive(self):
        """Stop the keepalive background thread."""
        self._keepalive_running = False
        logger.info("RCON keepalive stopped")

    def _keepalive_loop(self):
        """Periodically ping all connections to keep them alive."""
        while self._keepalive_running:
            time.sleep(self._keepalive_interval)
            server_names = list(self._connections.keys())
            for name in server_names:
                try:
                    self.command(name, 'list', timeout=3)
                except Exception:
                    pass  # will be reconnected on next use


# ═══════════════════════════════════════════════════════════════
# 3. Subsystem Registry (Skeleton)
# ═══════════════════════════════════════════════════════════════

class SubsystemRegistry:
    """Skeleton for per-server subsystem management.

    Each subsystem has:
      - enabled / disabled per server
      - VFS database path (for storing per-server data)
      - VFS config path (for per-server configuration)
      - init / shutdown lifecycle methods (stubs for now)

    Default state: ALL subsystems OFF for every server.
    """

    def __init__(self, config_path: str = None, vfs_root: str = None):
        self.config_path = config_path or str(_DEFAULT_SUBSYSTEM_CONFIG_PATH)
        self.vfs_root = vfs_root or _DEFAULT_RUNNER_VFS
        self._config: Dict[str, Dict[str, bool]] = {}  # {server_name: {subsystem_name: enabled}}
        self._lock = threading.Lock()
        self._load_config()

    def _load_config(self):
        """Load subsystem configuration from JSON file."""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path) as f:
                    self._config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load subsystem config: {e}")
            self._config = {}
        logger.debug(f"Loaded subsystem config for {len(self._config)} servers")

    def _save_config(self):
        """Persist subsystem configuration to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self._config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save subsystem config: {e}")

    def is_enabled(self, server_name: str, subsystem: str) -> bool:
        """Check if a subsystem is enabled for a server."""
        return self._config.get(server_name, {}).get(subsystem, False)

    def set_enabled(self, server_name: str, subsystem: str, enabled: bool) -> bool:
        """Enable or disable a subsystem for a server.

        Returns True if the state changed, False if already in that state.
        """
        if subsystem not in SUBSYSTEM_DEFINITIONS:
            logger.warning(f"Unknown subsystem: {subsystem}")
            return False

        with self._lock:
            if server_name not in self._config:
                self._config[server_name] = {}
            old = self._config[server_name].get(subsystem, False)
            if old == enabled:
                return False
            self._config[server_name][subsystem] = enabled
            self._save_config()
            action = "enabled" if enabled else "disabled"
            logger.info(f"Subsystem '{subsystem}' {action} for server '{server_name}'")
            return True

    def get_subsystem_vfs_db_path(self, server_name: str, subsystem: str) -> Optional[str]:
        """Get the VFS path for a subsystem's database."""
        if subsystem not in SUBSYSTEM_DEFINITIONS:
            return None
        return SUBSYSTEM_DEFINITIONS[subsystem]['vfs_db_path'].format(name=server_name)

    def get_subsystem_vfs_config_path(self, server_name: str, subsystem: str) -> Optional[str]:
        """Get the VFS path for a subsystem's configuration."""
        if subsystem not in SUBSYSTEM_DEFINITIONS:
            return None
        return SUBSYSTEM_DEFINITIONS[subsystem]['vfs_config_path'].format(name=server_name)

    def list_subsystems(self, server_name: str) -> List[dict]:
        """List all subsystems and their status for a given server."""
        result = []
        for key, defn in SUBSYSTEM_DEFINITIONS.items():
            result.append({
                'key': key,
                'label': defn['label'],
                'description': defn['description'],
                'enabled': self.is_enabled(server_name, key),
                'default_enabled': defn['default_enabled'],
                'requires_rcon': defn['requires_rcon'],
                'vfs_db_path': self.get_subsystem_vfs_db_path(server_name, key),
            })
        return result

    def get_enabled_subsystems(self, server_name: str) -> List[str]:
        """Get list of enabled subsystem keys for a server."""
        return [
            key for key in SUBSYSTEM_DEFINITIONS
            if self.is_enabled(server_name, key)
        ]

    def init_subsystem(self, server_name: str, subsystem: str) -> bool:
        """Initialize a subsystem for a server.

        Currently a stub — future implementation will:
          1. Extract the subsystem DB from VFS to a temp path
          2. Run initialization SQL
          3. Start the subsystem scheduler/engine

        Returns True if init was successful.
        """
        if not self.is_enabled(server_name, subsystem):
            logger.warning(f"Cannot init '{subsystem}' for '{server_name}' — subsystem not enabled")
            return False

        if subsystem not in SUBSYSTEM_DEFINITIONS:
            logger.warning(f"Unknown subsystem: {subsystem}")
            return False

        vfs_db = self.get_subsystem_vfs_db_path(server_name, subsystem)
        logger.info(f"Subsystem init stub: {subsystem} for {server_name} (VFS DB: {vfs_db})")
        # TODO: Phase 3+ — extract DB from VFS, run init SQL, start scheduler
        return True

    def shutdown_subsystem(self, server_name: str, subsystem: str) -> bool:
        """Shutdown a subsystem for a server.

        Currently a stub — future implementation will:
          1. Stop the subsystem scheduler
          2. Save the DB back to VFS

        Returns True if shutdown was successful.
        """
        vfs_db = self.get_subsystem_vfs_db_path(server_name, subsystem)
        logger.info(f"Subsystem shutdown stub: {subsystem} for {server_name} (VFS DB: {vfs_db})")
        return True

    def get_server_subsystem_summary(self, server_name: str) -> dict:
        """Get a summary of all subsystems for a server."""
        return {
            'server': server_name,
            'subsystems': self.list_subsystems(server_name),
            'enabled_count': len(self.get_enabled_subsystems(server_name)),
        }


# ═══════════════════════════════════════════════════════════════
# 4. Custom Command Manager
# ═══════════════════════════════════════════════════════════════

class CustomCommandManager:
    """Manages user-defined custom commands per server.

    Custom commands are named aliases that execute one or more MC commands
    (separated by semicolons). They are defined by admins via CLI/GUI and
    stored in a JSON file.

    Each custom command has:
      - name: User-defined alias (e.g., 'backup', 'restart-warning')
      - server: Target server name (or '*' for all servers)
      - mc_commands: Semicolon-separated MC commands to execute
      - description: Optional human-readable description
    """

    def __init__(self, config_path: str = None):
        self.config_path = config_path or str(_DEFAULT_CUSTOM_COMMANDS_PATH)
        self._commands: Dict[str, List[dict]] = {}  # {server_name: [{name, mc_commands, description}]}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        """Load custom commands from JSON file."""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path) as f:
                    self._commands = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load custom commands: {e}")
            self._commands = {}
        logger.debug(f"Loaded custom commands for {len(self._commands)} servers")

    def _save(self):
        """Persist custom commands to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self._commands, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save custom commands: {e}")

    def list(self, server_name: str = None) -> Dict[str, List[dict]]:
        """List custom commands, optionally filtered by server name.

        Returns dict mapping server_name -> list of command dicts.
        """
        with self._lock:
            if server_name:
                return {server_name: self._commands.get(server_name, [])}
            return dict(self._commands)

    def add(self, server_name: str, name: str, mc_commands: str,
            description: str = '', overwrite: bool = False) -> bool:
        """Add a custom command for a server.

        Args:
            server_name: Target server ('*' for all servers)
            name: Command alias (lowercase, alphanumeric + hyphens)
            mc_commands: Semicolon-separated MC commands
            description: Optional human-readable description
            overwrite: If True, replace existing command with same name

        Returns True if added/updated.
        """
        if not name or not name.strip():
            logger.warning("Custom command name is required")
            return False
        if not mc_commands or not mc_commands.strip():
            logger.warning("Custom command mc_commands is required")
            return False

        name = name.strip().lower().replace(' ', '-')
        cmd_entry = {
            'name': name,
            'mc_commands': mc_commands.strip(),
            'description': description.strip() or name,
            'created_at': datetime.now().isoformat(),
        }

        with self._lock:
            if server_name not in self._commands:
                self._commands[server_name] = []

            existing = [c for c in self._commands[server_name] if c['name'] == name]
            if existing:
                if not overwrite:
                    logger.warning(f"Custom command '{name}' already exists for '{server_name}'")
                    return False
                self._commands[server_name] = [
                    cmd_entry if c['name'] == name else c
                    for c in self._commands[server_name]
                ]
            else:
                self._commands[server_name].append(cmd_entry)

            self._save()
            logger.info(f"Custom command '{name}' added for '{server_name}'")
            return True

    def remove(self, server_name: str, name: str) -> bool:
        """Remove a custom command for a server."""
        name = name.strip().lower()
        with self._lock:
            if server_name not in self._commands:
                return False
            before = len(self._commands[server_name])
            self._commands[server_name] = [
                c for c in self._commands[server_name] if c['name'] != name
            ]
            if len(self._commands[server_name]) == before:
                return False
            self._save()
            logger.info(f"Custom command '{name}' removed from '{server_name}'")
            return True

    def run(self, server_name: str, command_name: str,
            rcon_func: Callable[[str, str], str]) -> Dict[str, Any]:
        """Execute a custom command and return results.

        Args:
            server_name: Server to run the command on
            command_name: Name of the custom command to execute
            rcon_func: Function that takes (server_name, mc_command) -> response

        Returns dict with results per MC command.
        """
        command_name = command_name.strip().lower()
        with self._lock:
            # Check for server-specific command
            commands = self._commands.get(server_name, [])
            cmd_entry = next((c for c in commands if c['name'] == command_name), None)

            # Check for global command ('*')
            if cmd_entry is None:
                global_commands = self._commands.get('*', [])
                cmd_entry = next((c for c in global_commands if c['name'] == command_name), None)

            if cmd_entry is None:
                return {'error': f"Custom command '{command_name}' not found for '{server_name}'"}

            mc_commands_raw = cmd_entry['mc_commands']

        # Execute each MC command
        results = {}
        for mc_cmd in mc_commands_raw.split(';'):
            mc_cmd = mc_cmd.strip()
            if not mc_cmd:
                continue
            try:
                resp = rcon_func(server_name, mc_cmd)
                results[mc_cmd] = resp
            except Exception as e:
                results[mc_cmd] = f"Error: {e}"

        return {
            'command': command_name,
            'server': server_name,
            'mc_commands': mc_commands_raw,
            'results': results,
        }

    def get_commands_for_server(self, server_name: str) -> List[dict]:
        """Get all custom commands available to a server (global + server-specific)."""
        with self._lock:
            server_cmds = self._commands.get(server_name, [])
            global_cmds = [
                {**c, 'global': True} for c in self._commands.get('*', [])
            ]
            return server_cmds + global_cmds


# ═══════════════════════════════════════════════════════════════
# 5. MultiServerManager Facade
# ═══════════════════════════════════════════════════════════════

class MultiServerManager:
    """Top-level facade that ties discovery, RCON pool, subsystems, and custom commands together.

    This is the primary interface used by main.py and the GUI.
    """

    def __init__(self, config: dict = None):
        if config is None:
            config = {}

        self.runner_db_path = config.get('runner_db_path', _DEFAULT_RUNNER_DB)
        self.runner_vfs_root = config.get('runner_vfs_root', _DEFAULT_RUNNER_VFS)
        self.rcon_host = config.get('default_rcon_host', '127.0.0.1')

        # Initialize subsystems
        self.discovery = ServerDiscovery(self.runner_db_path)
        self.rcon_pool = RconPool(self.discovery, self.rcon_host)
        self.subsystems = SubsystemRegistry(
            vfs_root=self.runner_vfs_root,
        )
        self.custom_commands = CustomCommandManager()

        logger.info(f"MultiServerManager initialized (DB: {self.runner_db_path})")

    # ── Convenience methods ────────────────────────────────────

    def list_servers(self, with_status: bool = False) -> List[dict]:
        """List all discovered servers, optionally with live status."""
        servers = self.discovery.discover()
        result = []
        for s in servers:
            info = s.to_dict()
            if with_status:
                info['status'] = self.rcon_pool.get_server_status(s.name)
            else:
                info['status'] = None
            info['subsystems'] = self.subsystems.list_subsystems(s.name)
            result.append(info)
        return result

    def send_command(self, server_name: str, cmd: str) -> str:
        """Send an RCON command to a specific server."""
        return self.rcon_pool.command(server_name, cmd)

    def broadcast_command(self, cmd: str) -> Dict[str, Any]:
        """Send a command to all connected servers."""
        return self.rcon_pool.broadcast(cmd)

    def get_server_detail(self, server_name: str) -> Optional[dict]:
        """Get full detail about a server including status and subsystem info."""
        server = self.discovery.get_server(server_name)
        if not server:
            return None
        return {
            **server.to_dict(),
            'status': self.rcon_pool.get_server_status(server_name),
            'subsystems': self.subsystems.list_subsystems(server_name),
            'custom_commands': self.custom_commands.get_commands_for_server(server_name),
            'vfs_server_path': server.vfs_server_path,
        }

    def shutdown(self):
        """Clean shutdown — close RCON connections and save state."""
        self.rcon_pool.stop_keepalive()
        self.rcon_pool.disconnect_all()
        logger.info("MultiServerManager shut down")


# ═══════════════════════════════════════════════════════════════
# Quick config file creation helper
# ═══════════════════════════════════════════════════════════════

def ensure_default_config():
    """Write default multi_server_config.json if it doesn't exist."""
    config_path = _DEFAULT_CONFIG_PATH
    if config_path.exists():
        return

    config = {
        'runner_db_path': str(_DEFAULT_RUNNER_DB),
        'runner_vfs_root': str(_DEFAULT_RUNNER_VFS),
        'default_rcon_host': '127.0.0.1',
        'keepalive_interval_seconds': 30,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    logger.info(f"Default config written to {config_path}")


def _find_runner_db() -> str:
    """Auto-detect the mc-server-runner database path relative to this script."""
    # Walk up from engine/ to project root, then to mc-server-runner
    candidates = [
        str(_PROJECT_ROOT / '..' / '..' / 'SERVERS' / 'mc-server-runner' / 'DATA' / 'server_data.db'),
        str(_PROJECT_ROOT / '..' / '..' / 'mc-server-runner' / 'DATA' / 'server_data.db'),
        str(Path.home() / 'Documents' / 'dev-yuniScripts' / 'SCRIPTS' / 'SERVERS' / 'mc-server-runner' / 'DATA' / 'server_data.db'),
        str(Path.home() / 'Documents' / 'yuniScripts' / 'SCRIPTS' / 'SERVERS' / 'mc-server-runner' / 'DATA' / 'server_data.db'),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]  # default to first candidate


if __name__ == '__main__':
    # Quick self-test
    logging.basicConfig(level=logging.INFO)
    print("MultiServerManager self-test...")
    print(f"Default runner DB: {_DEFAULT_RUNNER_DB}")
    print(f"Exists: {os.path.exists(_DEFAULT_RUNNER_DB)}")
    ensure_default_config()

    mgr = MultiServerManager()
    servers = mgr.list_servers()
    print(f"\nDiscovered {len(servers)} servers:")
    for s in servers:
        print(f"  {s['name']:<20} MC {s['mc_version']:<8} {s['server_type']:<10} RCON:{s['rcon_port']}")
        for sub in s['subsystems']:
            status = 'ON' if sub['enabled'] else 'OFF'
            print(f"    {sub['key']:<20} [{status}]")
    mgr.shutdown()
