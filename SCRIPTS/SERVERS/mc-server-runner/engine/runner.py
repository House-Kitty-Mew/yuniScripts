"""
runner.py — MC Server Runner Process Management

Manages the lifecycle of Minecraft server processes within the VFS sandbox:
  - Launch: Extract server files from VFS → physical dir → start process
  - Monitor: Watch server process health, log output
  - Stop: Graceful shutdown via RCON or SIGTERM  
  - Restart: Stop + start cycle
  - Console: Send commands to running server via stdin/RCON

All server files live in the VFS. When a server starts, the runner:
  1. Extracts all files from VFS to a temporary working directory
  2. Sets up the network sandbox (port allocation, firewall)
  3. Launches the Java process
  4. Monitors for crashes and log output
  5. On stop, saves back any changed files to the VFS
"""

import os
import sys
import json
import time
import signal
import struct
import logging
import subprocess
import threading
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from engine.database import Database
from engine.vfs import VFS
from engine.networking import NetworkManager, PortManager
from engine.mod_manager import ModManager
from engine.dynamic_deps import JavaDetector

logger = logging.getLogger('mc-server-runner.runner')

# Default Java arguments for Minecraft servers
DEFAULT_JAVA_ARGS = [
    '-XX:+UseG1GC',
    '-XX:+ParallelRefProcEnabled',
    '-XX:MaxGCPauseMillis=200',
    '-XX:+UnlockExperimentalVMOptions',
    '-XX:+DisableExplicitGC',
    '-XX:G1NewSizePercent=30',
    '-XX:G1MaxNewSizePercent=40',
    '-XX:G1HeapRegionSize=8M',
    '-XX:G1ReservePercent=20',
    '-XX:G1HeapWastePercent=5',
    '-XX:G1MixedGCCountTarget=4',
    '-XX:InitiatingHeapOccupancyPercent=15',
    '-XX:G1MixedGCLiveThresholdPercent=90',
    '-XX:G1RSetUpdatingPauseTimePercent=5',
    '-XX:SurvivorRatio=32',
    '-XX:+PerfDisableSharedMem',
    '-XX:MaxTenuringThreshold=1',
    '-Dusing.aikars.flags=https://mcflags.emc.gs',
    '-Daikars.new.flags=true',
    '-Dlog4j2.formatMsgNoLookups=true',
    '-DIReallyKnowWhatIAmDoingISwear',
]


class ServerRunnerError(Exception):
    """Raised when a server runner operation fails."""
    pass


class ServerRunner:
    """
    Manages the lifecycle of a Minecraft server process within the VFS sandbox.

    Each server instance has:
      - A VFS directory: /servers/<name>/
      - A working directory: vfs/<name>/ (physical, extracted from VFS at runtime)
      - A Java subprocess managed by this runner
    """

    # Class-level registry of running servers
    _running_servers: Dict[int, 'ServerRunner'] = {}
    _lock = threading.Lock()

    def __init__(self, db: Database, vfs: VFS, server_id: int):
        """
        Initialize runner for a server instance.

        Args:
            db: Database instance
            vfs: VFS instance
            server_id: Server instance ID from database
        """
        self.db = db
        self.vfs = vfs
        self.server_id = server_id
        self.server = db.get_server(server_id)
        if not self.server:
            raise ServerRunnerError(f"Server not found: ID={server_id}")

        self.name = self.server['name']
        self.work_dir = Path(vfs.vfs_root) / 'servers' / self.name
        self.network = NetworkManager(db, server_id)
        self.mod_manager = ModManager(db, vfs)  # Shared ModManager
        self._process: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._log_file: Optional[Path] = None
        self._started_at: Optional[str] = None

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self, extract_files: bool = True) -> bool:
        """
        Start the Minecraft server.

        Steps:
          1. Extract server files from VFS to working directory
          2. Generate server.properties from DB config
          3. Install mods
          4. Set up network sandbox
          5. Launch Java process
          6. Start health monitor

        Args:
            extract_files: If True, extract all server files from VFS first

        Returns:
            True if server started successfully

        Raises:
            ServerRunnerError: If server already running or start fails
        """
        if self.is_running():
            raise ServerRunnerError(f"Server '{self.name}' is already running")

        with ServerRunner._lock:
            if self.server_id in ServerRunner._running_servers:
                raise ServerRunnerError(f"Server '{self.name}' is already registered as running")

            # Step 1: Extract server files
            if extract_files:
                self._prepare_workdir()

            # Step 2: Generate server.properties
            self._generate_server_properties()

            # Step 3: Install mods to working directory
            self._install_mods_to_workdir()

            # Step 4: Reserve ports and set up network
            self._setup_network()

            # Step 5: Launch the server
            try:
                self._launch_java_process()
            except Exception as e:
                PortManager.release(self.server['server_port'], self.server_id, self.db)
                PortManager.release(self.server['rcon_port'], self.server_id, self.db)
                raise ServerRunnerError(f"Failed to launch server: {e}")

            # Step 6: Register and start monitor
            ServerRunner._running_servers[self.server_id] = self
            self._started_at = datetime.now().isoformat()
            self._start_monitor()

            logger.info(
                f"Server '{self.name}' started on port {self.server['server_port']} "
                f"(PID={self._process.pid})"
            )
            return True

    def stop(self, timeout: int = 30, force: bool = False) -> bool:
        """
        Stop the Minecraft server gracefully (or force-kill).

        Args:
            timeout: Seconds to wait for graceful shutdown
            force: If True, skip graceful shutdown and kill immediately

        Returns:
            True if server stopped
        """
        if not self.is_running():
            logger.warning(f"Server '{self.name}' is not running")
            return False

        self._stop_event.set()

        try:
            if force:
                self._force_kill()
            else:
                # Try RCON stop first
                if not self._rcon_stop(timeout):
                    # Fall back to SIGTERM
                    self._signal_stop(timeout)
        finally:
            self._cleanup()

        logger.info(f"Server '{self.name}' stopped")
        return True

    def restart(self, timeout: int = 30) -> bool:
        """Restart the server (stop + start)."""
        self.stop(timeout=timeout)
        self._stop_event.clear()
        return self.start(extract_files=False)

    def is_running(self) -> bool:
        """Check if the server process is running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def get_status(self) -> Dict[str, Any]:
        """Get current server status."""
        running = self.is_running()
        status = {
            'server_id': self.server_id,
            'name': self.name,
            'running': running,
            'port': self.server['server_port'],
            'started_at': self._started_at,
        }
        if running and self._process:
            status['pid'] = self._process.pid
        return status

    def send_command(self, command: str) -> str:
        """
        Send a command to the server console via stdin.

        Args:
            command: Minecraft console command (e.g. 'say Hello')

        Returns:
            Response (if any)
        """
        if not self.is_running():
            raise ServerRunnerError(f"Server '{self.name}' is not running")
        if self._process and self._process.stdin:
            self._process.stdin.write(command + '\n')
            self._process.stdin.flush()
            logger.info(f"Command sent to '{self.name}': {command}")
            return f"Command sent: {command}"
        return ""

    # ── Internal: Workdir Preparation ──────────────────────────────

    def _prepare_workdir(self):
        """Extract server files from VFS to physical working directory."""
        server_vfs = f"/servers/{self.name}"

        # Create clean work directory
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Extract all server files
        count = self.vfs.extract_all(server_vfs)
        logger.info(f"Extracted {count} files to {self.work_dir}")

        # Ensure eula.txt is accepted
        eula_path = self.work_dir / 'eula.txt'
        if not eula_path.exists():
            eula_path.write_text('eula=true\n')

    def _generate_server_properties(self):
        """Generate server.properties from DB configuration."""
        props_path = self.work_dir / 'server.properties'
        props = {
            'server-port': str(self.server['server_port']),
            'rcon.port': str(self.server['rcon_port']),
            'rcon.password': self.server.get('rcon_password', ''),
            'enable-rcon': 'true' if self.server.get('rcon_password') else 'false',
            'online-mode': 'true',
            'motd': f'MC Server Runner - {self.name}',
            'max-players': '20',
            'difficulty': 'normal',
            'gamemode': 'survival',
            'pvp': 'true',
            'spawn-animals': 'true',
            'spawn-monsters': 'true',
            'spawn-npcs': 'true',
            'allow-flight': 'false',
            'white-list': 'false',
            'enforce-whitelist': 'false',
            'resource-pack-prompt': '',
            'server-ip': '0.0.0.0',
            'max-world-size': '29999984',
            'view-distance': '10',
            'simulation-distance': '10',
            'tick-rate': '3',
            'rate-limit': '0',
            'use-native-transport': 'true',
            'network-compression-threshold': '256',
        }

        # Apply network sandbox overrides
        net_config = self.network.get_sandbox_config()
        if net_config.get('adapter'):
            props['server-ip'] = net_config['adapter'].get('virtual_ip', '0.0.0.0')

        # Write properties file
        with open(props_path, 'w') as f:
            f.write(f"# Minecraft server properties\n")
            f.write(f"# Generated by MC Server Runner v1.0.0\n")
            f.write(f"# {datetime.now().isoformat()}\n\n")
            for key, value in props.items():
                f.write(f"{key}={value}\n")

        logger.debug(f"Generated server.properties for '{self.name}'")

    def _install_mods_to_workdir(self):
        """Install mods from VFS to the server's mods directory."""
        mods_dir = self.work_dir / 'mods'
        mods_dir.mkdir(exist_ok=True)

        server_mods = self.mod_manager.list_server_mods(self.server_id)
        if not server_mods:
            logger.debug(f"No mods to install for '{self.name}'")
            return

        for mod in server_mods:
            mod_vfs = f"/mods/{mod['slug']}.jar"
            mod_data = self.vfs.read(mod_vfs)
            if mod_data:
                target = mods_dir / f"{mod['slug']}.jar"
                target.write_bytes(mod_data)
                logger.info(f"Installed mod: {mod['name']} v{mod.get('version', '?')}")

    def _setup_network(self):
        """Set up network sandboxing (port reservation + firewall rules)."""
        # Reserve server port
        if not PortManager.reserve(self.server['server_port'], self.server_id, self.db):
            raise ServerRunnerError(
                f"Port {self.server['server_port']} is already in use by another server"
            )

        # Reserve RCON port
        if not PortManager.reserve(self.server['rcon_port'], self.server_id, self.db):
            PortManager.release(self.server['server_port'], self.server_id, self.db)
            raise ServerRunnerError(
                f"RCON port {self.server['rcon_port']} is already in use"
            )

        # Ensure default firewall rules exist
        self.network.initialize_default_rules()

        logger.debug(f"Network sandbox ready for '{self.name}'")

    # ── Internal: Process Management ───────────────────────────────

    def _resolve_java_binary(self, java_version: str) -> Optional[str]:
        """
        Resolve a Java version string to an actual Java binary path.

        Resolution order:
          1. If java_version is a path and exists -> use it directly
          2. If java_version is a pure number (e.g., '17', '21') -> try 'java{version}' via shutil, then 'java'
          3. If java_version is 'java' or empty -> use JavaDetector.find_java()
          4. Fallback: JavaDetector.find_java() for generic detection

        Returns:
            Absolute path to Java binary, or None if not found.
        """
        # 1. Full path check
        if os.path.isabs(java_version) or ('/' in java_version or '\\' in java_version):
            if os.path.isfile(java_version) and os.access(java_version, os.X_OK):
                logger.debug(f"Using Java binary from path: {java_version}")
                return os.path.abspath(java_version)
            logger.warning(f"Java binary path not found or not executable: {java_version}")
            return None

        # 2. Pure version number (e.g., '17', '21')
        if java_version.isdigit():
            # Try version-specific binary first
            version_bin = f"java{java_version}"
            found = shutil.which(version_bin)
            if found:
                logger.debug(f"Found Java {java_version} via '{version_bin}': {found}")
                return os.path.abspath(found)

            # Fall back to generic 'java'
            logger.debug(f"Version-specific 'java{java_version}' not found, falling back to 'java'")
            java_version = 'java'

        # 3. Generic 'java' or empty -> use JavaDetector
        if java_version in ('java', '', None):
            found = JavaDetector.find_java()
            if found:
                logger.debug(f"Found Java via JavaDetector: {found}")
                return found

            # Last resort: shutil.which('java')
            found = shutil.which('java')
            if found:
                logger.debug(f"Found Java via shutil.which: {found}")
                return os.path.abspath(found)

            logger.error("No Java runtime found on system")
            return None

        # 4. Unknown version string -> try as command name
        found = shutil.which(java_version)
        if found:
            logger.debug(f"Found Java via command name '{java_version}': {found}")
            return os.path.abspath(found)

        # 5. Final fallback: try JavaDetector
        found = JavaDetector.find_java()
        if found:
            logger.debug(f"Fallback: found Java via JavaDetector: {found}")
            return os.path.abspath(found)

        logger.error(f"Java runtime not found (version='{java_version}')")
        return None

    def _launch_java_process(self):
        """Launch the Java server process."""
        if not self.work_dir.exists():
            raise ServerRunnerError(f"Working directory does not exist: {self.work_dir}")

        # Determine jar file (server.jar, paper.jar, etc.)
        jar_file = self._find_server_jar()
        if not jar_file:
            raise ServerRunnerError(f"No server jar found in {self.work_dir}")

        # Build Java command
        min_ram = self.server.get('min_ram', '2G')
        max_ram = self.server.get('max_ram', '4G')
        # Resolve Java binary from version string
        java_version = self.server.get('java_version', 'java')
        java_bin = self._resolve_java_binary(java_version)
        if not java_bin:
            raise ServerRunnerError(
                f"Java binary not found for version '{java_version}'. "
                "Install Java or set JAVA_HOME environment variable."
            )
        mc_version = self.server.get('mc_version', '1.20.4')

        cmd = [java_bin]
        cmd.extend(DEFAULT_JAVA_ARGS)
        cmd.append(f'-Xms{min_ram}')
        cmd.append(f'-Xmx{max_ram}')
        cmd.append('-jar')
        cmd.append(str(jar_file))
        cmd.append('nogui')

        # Set up log file
        log_dir = Path.cwd() / 'logs' / self.name
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = log_dir / f"server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        # Mark server as running in flags
        self.db.set_config(self.server_id, 'status', 'starting')

        # Launch process
        self._log_fh = open(self._log_file, 'a')
        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self.work_dir),
                stdin=subprocess.PIPE,
                stdout=self._log_fh,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError as e:
            self._log_fh.close()
            raise ServerRunnerError(f"Java not found: {java_bin}. Is it installed?")

        # Update status
        self.db.set_config(self.server_id, 'status', 'running')
        self.db.set_config(self.server_id, 'pid', str(self._process.pid))
        self.db.set_config(self.server_id, 'started_at', datetime.now().isoformat())

        logger.info(f"Java process launched: PID={self._process.pid}")

    def _find_server_jar(self) -> Optional[Path]:
        """Find the server jar file in the working directory."""
        # Look for common names
        for name in ['fabric-server-launch.jar', 'server.jar', 'paper.jar', 'purpur.jar',
                     'spigot.jar', 'craftbukkit.jar', 'fabric-server.jar',
                     'forge-server.jar', 'minecraft_server.jar']:
            jar = self.work_dir / name
            if jar.exists():
                return jar
        return None

    def _rcon_stop(self, timeout: int = 30) -> bool:
        """Attempt graceful stop via RCON."""
        rcon_port = self.server['rcon_port']
        rcon_pass = self.server.get('rcon_password', '')

        if not rcon_pass:
            return False

        # Send stop command via RCON using mcrcon or direct
        try:
            import socket as _s
            # Simple RCON protocol implementation
            sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(('127.0.0.1', rcon_port))

            # RCON packet: https://wiki.vv/Rcon
            def _send_packet(pkt_type, payload):
                pkt_id = int(time.time()) & 0x7FFFFFFF
                body = struct.pack('<3i', pkt_id, pkt_type, 0) + payload.encode('utf8') + b'\x00\x00'
                sock.send(struct.pack('<i', len(body)) + body)
                resp_len = struct.unpack('<i', sock.recv(4))[0]
                return sock.recv(resp_len)

            # Step 1: Authenticate with RCON (type 3 = SERVERDATA_AUTH)
            _send_packet(3, rcon_pass)

            # Step 2: Send the 'stop' command (type 2 = SERVERDATA_EXECCOMMAND)
            _send_packet(2, 'stop')

            sock.close()
            return True
        except Exception:
            return False

    def _signal_stop(self, timeout: int = 30):
        """Send SIGTERM and wait for process to exit."""
        if not self._process:
            return

        logger.info(f"Sending SIGTERM to PID {self._process.pid}...")
        try:
            self._process.terminate()
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(f"Server did not stop after {timeout}s, force killing...")
            self._force_kill()

    def _force_kill(self):
        """Force kill the server process."""
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=5)
            except Exception:
                pass

    def _start_monitor(self):
        """Start background thread to monitor server health."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name=f"mc-runner-monitor-{self.server_id}"
        )
        self._monitor_thread.start()

    def _monitor_loop(self):
        """Background loop: check process health, log output."""
        while not self._stop_event.is_set():
            if not self.is_running():
                exit_code = self._process.poll() if self._process else -1
                logger.warning(
                    f"Server '{self.name}' stopped unexpectedly "
                    f"(exit code: {exit_code})"
                )
                self.db.set_config(self.server_id, 'status', 'crashed')
                self.db.set_config(self.server_id, 'last_exit_code', str(exit_code))
                self._cleanup()
                break
            time.sleep(5)

    def _cleanup(self):
        """Clean up after server stops."""
        # Release ports (pass db so persistent reservations are also cleared)
        PortManager.release(self.server['server_port'], self.server_id, self.db)
        PortManager.release(self.server['rcon_port'], self.server_id, self.db)

        # Remove from running registry
        with ServerRunner._lock:
            ServerRunner._running_servers.pop(self.server_id, None)

        # Update DB status
        self.db.set_config(self.server_id, 'status', 'stopped')
        self.db.set_config(self.server_id, 'pid', '')
        self.db.set_config(self.server_id, 'stopped_at', datetime.now().isoformat())
        self.db.set_config(self.server_id, 'server_port', '')
        self.db.set_config(self.server_id, 'rcon_port', '')

        # Close log file handle
        if hasattr(self, '_log_fh') and self._log_fh:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

        self._process = None
        logger.info(f"Server '{self.name}' cleanup complete")

    # ── Class Methods ──────────────────────────────────────────────

    @classmethod
    def get_running(cls, server_id: int = None) -> List[Dict[str, Any]]:
        """
        Get status of running servers.

        Args:
            server_id: If provided, get status for specific server

        Returns:
            List of status dicts
        """
        with cls._lock:
            if server_id:
                runner = cls._running_servers.get(server_id)
                if runner:
                    return [runner.get_status()]
                return []
            return [r.get_status() for r in cls._running_servers.values()]

    @classmethod
    def stop_all(cls, timeout: int = 30) -> int:
        """Stop all running servers. Returns count stopped."""
        with cls._lock:
            ids = list(cls._running_servers.keys())

        count = 0
        for sid in ids:
            runner = cls._running_servers.get(sid)
            if runner:
                try:
                    runner.stop(timeout=timeout)
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to stop server {sid}: {e}")
        return count

    @classmethod
    def register_and_start(cls, db: Database, vfs: VFS, server_id: int) -> 'ServerRunner':
        """Factory method: create a runner and start the server."""
        runner = cls(db, vfs, server_id)
        runner.start()
        return runner
