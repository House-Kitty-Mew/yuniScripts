# server_stats_daemon.py
#!/usr/bin/env python3
"""
Server script for collecting system stats and sending to clients reliably over UDP with simple packet ordering and resend.
Refactored for Python 3.9+ and fixed for Windows (no temp.txt required).
"""

import socket
import sqlite3
import logging
import os
import json
import time
import argparse
import threading
from pathlib import Path
from typing import Dict, Any, Optional
import psutil

try:
    import xxhash
except ImportError:
    xxhash = None

# Configuration
LOGGING_ENABLED: bool = True  # Set to False to disable console logging
DB_FILE = 'server_stats.db3'
TOKEN_FILE = 'tokens.coin'
PROCESS_FILE = 'progs.p'
HOST = '0.0.0.0'
PORT = 5559  # engine.ports.SERVER_STATS_PORT
MAX_PACKET_SIZE = 60000
ACK_TIMEOUT = 5.0  # seconds
PACKET_RETRIES = 3
PREPARE_CMD = 'PREPARE'
READY_CMD = 'READY'
RESET_CMD = 'RESET'
ACK_PREFIX = 'ACK'


def setup_logging() -> None:
    if LOGGING_ENABLED:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    else:
        logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')


def ensure_flat_files() -> None:
    # Create tokens.coin and progs.p with comments if missing
    for filename, comment in [(TOKEN_FILE, '# Add one token per line, no whitespace\n'),
                              (PROCESS_FILE, '# Add process_name|alias per line\n')]:
        if not Path(filename).exists():
            with open(filename, 'w') as f:
                f.write(comment)
            logging.info(f'Created {filename} with template comments')


def load_tokens() -> set:
    tokens = set()
    with open(TOKEN_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                tokens.add(line)
    logging.info(f'Loaded {len(tokens)} tokens')
    return tokens


def load_process_mapping() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    with open(PROCESS_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '|' in line:
                name, alias = line.split('|',1)
                mapping[name] = alias
    logging.info(f'Loaded process mapping for {len(mapping)} processes')
    return mapping


class Database:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self) -> None:
        c = self.conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS connections (
                id INTEGER PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                client_address TEXT
            )''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                error TEXT
            )''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                temp_f REAL,
                ram_total_mb REAL,
                ram_used_mb REAL,
                ram_free_mb REAL,
                ram_percent REAL,
                cpu_percents TEXT,
                disk_total_mb REAL,
                disk_free_mb REAL,
                disk_percent REAL,
                net_bytes_sent INTEGER,
                net_bytes_recv INTEGER,
                process_aliases TEXT
            )''')
        self.conn.commit()

    def log_connection(self, address: str) -> None:
        self.conn.execute('INSERT INTO connections(client_address) VALUES(?)', (address,))
        self.conn.commit()

    def log_error(self, message: str) -> None:
        self.conn.execute('INSERT INTO errors(error) VALUES(?)', (message,))
        self.conn.commit()

    def log_stats(self, data: Dict[str, Any]) -> None:
        self.conn.execute('''
            INSERT INTO stats(
                temp_f, ram_total_mb, ram_used_mb, ram_free_mb, ram_percent,
                cpu_percents, disk_total_mb, disk_free_mb, disk_percent,
                net_bytes_sent, net_bytes_recv, process_aliases
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            data['temp_f'], data['ram_total_mb'], data['ram_used_mb'],
            data['ram_free_mb'], data['ram_percent'],
            json.dumps(data['cpu_percents']),
            data['disk_total_mb'], data['disk_free_mb'], data['disk_percent'],
            data['net_bytes_sent'], data['net_bytes_recv'],
            json.dumps(data['process_aliases'])
        ))
        self.conn.commit()


def get_cpu_temperature():
    """
    Retrieve CPU temperature in Celsius.
    Uses psutil sensors_temperatures (works on Windows 10+), falls back to
    a tempfile under the system temp directory, otherwise returns 0.0.
    """
    try:
        # Modern Windows – psutil can read hardware sensors
        temps = psutil.sensors_temperatures()
        if temps:
            for name, entries in temps.items():
                if entries:
                    # Return the first available temperature
                    return entries[0].current
    except Exception:
        pass

    # Fallback: read from platform-appropriate temp file
    # (e.g., written by a hardware monitor)
    try:
        import tempfile
        temp_path = os.path.join(tempfile.gettempdir(), "cpu_temp.txt")
        with open(temp_path, 'r') as f:
            data = f.read().strip()
        if data:
            return int(data) / 1000.0
    except Exception:
        pass

    # No temperature available
    return 0.0


def collect_stats(process_map: Dict[str, str]) -> Dict[str, Any]:
    # Temperature
    temp_c = get_cpu_temperature()
    temp_f = round((temp_c * 1.8) + 32, 3)
    logging.info(f'Temperature: {temp_c}°C / {temp_f}°F')

    # RAM
    vm = psutil.virtual_memory()
    ram_total = round(vm.total / (1024*1024), 2)
    ram_used = round((vm.total - vm.available) / (1024*1024), 2)
    ram_free = round(vm.available / (1024*1024), 2)
    ram_percent = vm.percent
    logging.info(f'RAM total: {ram_total} MB, used: {ram_used} MB, free: {ram_free} MB ({ram_percent}%)')

    # CPU usage per core
    cpu_percents = [round(p,3) for p in psutil.cpu_percent(interval=1, percpu=True)]
    logging.info(f'CPU percents per core: {cpu_percents}')

    # Disk – use C: drive on Windows, / on Linux
    if os.name == 'nt':
        disk_path = 'C:\\'
    else:
        disk_path = '/'
    du = psutil.disk_usage(disk_path)
    disk_total = round(du.total / (1024*1024), 2)
    disk_free = round(du.free / (1024*1024), 2)
    disk_percent = du.percent
    logging.info(f'Disk ({disk_path}) total: {disk_total} MB, free: {disk_free} MB ({disk_percent}%)')

    # Network
    net = psutil.net_io_counters()
    net_sent = net.bytes_sent
    net_recv = net.bytes_recv
    logging.info(f'Network sent: {net_sent} bytes, recv: {net_recv} bytes')

    # Monitored processes
    processes = []
    for proc in psutil.process_iter(attrs=['name']):
        name = proc.info['name']
        if name in process_map:
            processes.append(process_map[name])
    logging.info(f'Process aliases: {processes}')

    return {
        'temp_f': temp_f,
        'ram_total_mb': ram_total,
        'ram_used_mb': ram_used,
        'ram_free_mb': ram_free,
        'ram_percent': ram_percent,
        'cpu_percents': cpu_percents,
        'disk_total_mb': disk_total,
        'disk_free_mb': disk_free,
        'disk_percent': disk_percent,
        'net_bytes_sent': net_sent,
        'net_bytes_recv': net_recv,
        'process_aliases': processes
    }


def send_payload(stats: Dict[str, Any], address: tuple, sock: socket.socket, db: Database) -> None:
    payload = json.dumps(stats).encode('utf-8')
    total_packets = (len(payload) + MAX_PACKET_SIZE -1)//MAX_PACKET_SIZE

    # Handshake
    logging.info(f'Sending {PREPARE_CMD} to {address}')
    sock.sendto(PREPARE_CMD.encode(), address)
    sock.settimeout(ACK_TIMEOUT)
    try:
        data, addr = sock.recvfrom(1024)
        if addr != address or data.decode() != READY_CMD:
            raise RuntimeError(f'Unexpected handshake response: {data.decode()}')
    except Exception as e:
        msg = f'Handshake failed: {e}'
        logging.error(msg)
        db.log_error(msg)
        sock.sendto(RESET_CMD.encode(), address)
        sock.settimeout(None)
        return
    sock.settimeout(None)

    # Send packets
    for packet_id in range(1, total_packets+1):
        start = (packet_id-1)*MAX_PACKET_SIZE
        chunk = payload[start:start+MAX_PACKET_SIZE]
        if xxhash:
            hash_hex = xxhash.xxh64(chunk, seed=packet_id).hexdigest().encode()
        else:
            hash_hex = b''
        header = f'{packet_id}/{total_packets}|'.encode() + hash_hex + b'|'
        packet = header + chunk

        ack_received = False
        for attempt in range(1, PACKET_RETRIES+1):
            logging.info(f'Sending packet {packet_id}/{total_packets} (attempt {attempt})')
            sock.sendto(packet, address)
            sock.settimeout(ACK_TIMEOUT)
            try:
                ack, addr = sock.recvfrom(1024)
                if addr==address and ack.decode() == f'{ACK_PREFIX}|{packet_id}':
                    ack_received = True
                    break
            except socket.timeout:
                logging.warning(f'No ACK for packet {packet_id}, retrying')
            finally:
                sock.settimeout(None)
        if not ack_received:
            msg = f'Client never responded for packet {packet_id}'
            logging.error(msg)
            db.log_error(msg)
            sock.sendto(RESET_CMD.encode(), address)
            return

    # Done
    logging.info('All packets sent')
    sock.sendto(b'DONE', address)


def handle_request(raw_data: bytes, address: tuple, sock: socket.socket, tokens: set, process_map: dict, db: Database, lan_mode: bool = True) -> None:
    db.log_connection(f'{address[0]}:{address[1]}')
    try:
        text = raw_data.decode('utf-8')
    except Exception as e:
        msg = f'Failed to decode data from {address}: {e}'
        logging.error(msg)
        db.log_error(msg)
        return

    if RESET_CMD in text:
        logging.info(f'Received RESET from {address}, sending RESET_ACK')
        sock.sendto(b'RESET_ACK', address)
        return

    if lan_mode:
        # LAN mode: accept plain "check" command (no token required)
        cmd = text.strip()
        if cmd == 'check':
            logging.info(f'LAN-mode check from {address}')
            stats = collect_stats(process_map)
            if LOGGING_ENABLED:
                db.log_stats(stats)
            send_payload(stats, address, sock, db)
        else:
            msg = f'Unknown command from {address}: {cmd}'
            logging.error(msg)
            db.log_error(msg)
    else:
        # Secure mode: require token prefix
        parts = text.split('|',1)
        if len(parts)!=2:
            msg = f'Malformed request from {address}: {text}'
            logging.error(msg)
            db.log_error(msg)
            return
        token, cmd = parts
        if token not in tokens:
            msg = f'Unauthorized token from {address}: {token}'
            logging.warning(msg)
            db.log_error(msg)
            return
        if cmd.strip()=='check':
            stats = collect_stats(process_map)
            if LOGGING_ENABLED:
                db.log_stats(stats)
            send_payload(stats, address, sock, db)
        else:
            msg = f'Unknown command from {address}: {cmd}'
            logging.error(msg)
            db.log_error(msg)


# ── LAN discovery beacon (self-contained, stdlib only) ──────────────
_DISCOVERY_PORT = 25574  # engine.ports.LAN_DISCOVERY_PORT

def _get_lan_ip():
    """Get the machine's LAN IP address (not 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except (OSError, socket.error):
        pass
    return "127.0.0.1"


def _lan_discovery_beacon(service_type, port, stop_event, lan_mode=True):
    """Broadcast a UDP beacon every 5 seconds on the LAN."""
    local_ip = _get_lan_ip()
    hostname = socket.gethostname()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except (AttributeError, OSError):
        pass
    while not stop_event.is_set():
        try:
            beacon_data = json.dumps({
                "type": "lan_discovery",
                "service": service_type,
                "host": local_ip,
                "hostname": hostname,
                "port": port,
                "lan_mode": lan_mode,
                "ts": time.time(),
            }).encode("utf-8")
            for bcast_addr in ["255.255.255.255", "<broadcast>"]:
                try:
                    sock.sendto(beacon_data, (bcast_addr, _DISCOVERY_PORT))
                except OSError:
                    pass
            if local_ip and local_ip != "127.0.0.1":
                parts = local_ip.rsplit(".", 1)
                if len(parts) == 2:
                    try:
                        sock.sendto(beacon_data,
                                    (f"{parts[0]}.255", _DISCOVERY_PORT))
                    except OSError:
                        pass
        except OSError:
            pass
        stop_event.wait(5)
    sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Server Stats Daemon")
    parser.add_argument("--lan-mode", action="store_true", default=True,
                        help="LAN mode: no token auth required (default)")
    parser.add_argument("--secure", action="store_true", default=False,
                        help="Secure mode: require token authentication")
    parser.add_argument("--port", type=int, default=PORT,
                        help=f"UDP listen port (default: {PORT})")
    args, _ = parser.parse_known_args()

    # If --secure is set, disable LAN mode
    lan_mode = args.lan_mode and not args.secure

    setup_logging()
    ensure_flat_files()
    tokens = load_tokens()
    process_map = load_process_mapping()
    db = Database(DB_FILE)

    mode_label = "LAN" if lan_mode else "SECURE"
    logging.info(f'Starting in {mode_label} mode')

    # ── Start LAN discovery beacon ──
    _stop_beacon = threading.Event()
    _beacon_thread = threading.Thread(
        target=_lan_discovery_beacon,
        args=("server_stats", args.port, _stop_beacon),
        kwargs={"lan_mode": lan_mode},
        daemon=True,
    )
    _beacon_thread.start()
    logging.info(f'LAN discovery beacon started (port {_DISCOVERY_PORT}) — '
                 f'advertising "server_stats" on port {args.port}')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, args.port))
    logging.info(f'Server listening on {HOST}:{args.port}')

    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
                handle_request(data, addr, sock, tokens, process_map, db, lan_mode=lan_mode)
            except Exception as e:
                msg = f'Unexpected error in main loop: {e}'
                logging.error(msg)
                db.log_error(msg)
    finally:
        _stop_beacon.set()
        sock.close()

if __name__=='__main__':
    main()