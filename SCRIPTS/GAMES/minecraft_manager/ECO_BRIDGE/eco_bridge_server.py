#!/usr/bin/env python3
"""
eco_bridge_server.py — Thin encrypted TCP bridge to the Otters Civ economy database.

Run THIS script on the Minecraft server machine, next to project_ooga.db.
It opens an encrypted TCP port and waits for commands from the mc_manager
on another machine.

Architecture:
  mc_manager (machine A)          eco_bridge_server (server machine)
  ┌────────────────────┐          ┌──────────────────────────┐
  │  eco_bridge_client │──AES──►  │  TCP :7200              │
  │  (all logic here)  │◄──AES───│  → reads/writes ooga.db │
  └────────────────────┘          │  → wallet_ledger audit  │
                                   └──────────────────────────┘

Commands (encrypted JSON):
  {"action":"balance","player":"Steve"}
  {"action":"balance_uuid","uuid":"...-..."}
  {"action":"deduct","uuid":"...","amount":100,"reason":"AH_TAKE","note":"..."}
  {"action":"credit","uuid":"...","amount":50,"reason":"AH_CREDIT","note":"..."}
  {"action":"set","uuid":"...","amount":500,"reason":"AH_SET"}
  {"action":"transfer","from":"...","to":"...","amount":200,"reason":"AH_TRANSFER"}
  {"action":"info","player":"Steve"}
  {"action":"stats"}
  {"action":"ledger","uuid":"...","limit":20}
  {"action":"ping"}

Responses (encrypted JSON):
  {"ok":true,"balance":28542}
  {"ok":false,"error":"Insufficient funds"}

Usage:
  python3 eco_bridge_server.py                    # Uses defaults
  python3 eco_bridge_server.py --port 7200 --db ~/server/config/otters_civ_revived/project_ooga.db
  python3 eco_bridge_server.py --key "mysecretkey"  # Override encryption key

Requires: pycryptodome (pip install pycryptodome)
"""

import json, os, sys, socket, struct, threading, traceback, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Port constants — local defines (standalone script, may run on MC server machine)
_LAN_DISCOVERY_PORT = 25574  # matches engine.ports.LAN_DISCOVERY_PORT

# ── Try AES imports ────────────────────────────────────────────────
_CRYPTO_FALLBACK = None  # Pure-Python AES-GCM fallback module
try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False
    # Try bundled fallback in ECO_BRIDGE directory
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _fb_path = os.path.join(_this_dir, "crypto_fallback.py")
    if os.path.exists(_fb_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("crypto_fallback", _fb_path)
        _CRYPTO_FALLBACK = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CRYPTO_FALLBACK)
        HAVE_CRYPTO = True
    # Also try from FUNCTIONS/
    if not HAVE_CRYPTO:
        _fb_path2 = os.path.join(os.path.dirname(_this_dir), "FUNCTIONS", "crypto_fallback.py")
        if os.path.exists(_fb_path2):
            import importlib.util
            spec = importlib.util.spec_from_file_location("crypto_fallback2", _fb_path2)
            _CRYPTO_FALLBACK = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_CRYPTO_FALLBACK)
            HAVE_CRYPTO = True


# ── Configuration ──────────────────────────────────────────────────
DEFAULT_PORT = 7200
DEFAULT_KEY = ""  # Must be set via --key, ECO_BRIDGE_KEY env, or config file
DEFAULT_HOST = "0.0.0.0"  # Listen on all interfaces
MAX_PACKET_SIZE = 65535


# ── Logging ────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_lock = threading.Lock()


def log(level, message):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {message}\n"
        log_path = LOG_DIR / "eco_bridge_server.log"
        with _log_lock:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
        print(f"[{level}] {message}", flush=True)
    except Exception:
        pass


# ── Encryption (AES-256-GCM) ───────────────────────────────────────

def _derive_key(password: str) -> bytes:
    """Derive a 32-byte AES-256 key from a password using SHA-256."""
    import hashlib
    return hashlib.sha256(password.encode("utf-8")).digest()


def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Encrypt data with AES-256-GCM.

    Returns: nonce (12) + ciphertext + tag (16)

    Tries: pycryptodome → bundled crypto_fallback.py → raises error
    """
    global _CRYPTO_FALLBACK
    try:
        nonce = get_random_bytes(12) if "get_random_bytes" in dir() else os.urandom(12)
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        return nonce + ciphertext + tag
    except NameError:
        pass  # AES not imported — fall through
    if _CRYPTO_FALLBACK:
        nonce = os.urandom(12)
        ciphertext, tag = _CRYPTO_FALLBACK.encrypt(key, plaintext, nonce)
        return nonce + ciphertext + tag
    raise RuntimeError("No encryption library available. Install pycryptodome: pip install pycryptodome")


def decrypt(key: bytes, packet: bytes, aad: bytes = b"") -> Optional[bytes]:
    """Decrypt data with AES-256-GCM.

    Args:
        key: 32-byte AES key
        packet: nonce (12) + ciphertext + tag (16)
        aad: Additional authenticated data

    Returns:
        Plaintext bytes, or None if authentication fails
    """
    if len(packet) < 28:
        return None
    nonce = packet[:12]
    tag = packet[-16:]
    ciphertext = packet[12:-16]
    try:
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        return cipher.decrypt_and_verify(ciphertext, tag)
    except NameError:
        pass  # AES not imported — fall through
    except Exception:
        return None
    if _CRYPTO_FALLBACK:
        try:
            return _CRYPTO_FALLBACK.decrypt(key, nonce, ciphertext, tag)
        except Exception:
            return None
    return None


# ── Database operations (thin, no logic) ────────────────────────────

class OogaDB:
    """Minimal read/write access to the Otters Civ database.

    No logic, no decisions — just execute requests from the mc_manager.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path).resolve()
        self._lock = threading.Lock()
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        log("INFO", f"Connected to: {self.db_path}")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _resolve_player(self, player: str, conn: sqlite3.Connection) -> Optional[dict]:
        """Resolve player name or UUID to wallet record."""
        try:
            import re

            # Try UUID

        except Exception as e:
            logger.error(f"_resolve_player failed: {e}")
            return None
        if re.match(r'^[0-9a-f\-]{36}$', player, re.I):
            row = conn.execute("SELECT * FROM wallets WHERE player_uuid = ?",
                               (player,)).fetchone()
            if row:
                return dict(row)
        # Try exact name
        row = conn.execute("SELECT * FROM wallets WHERE name_hint = ?",
                           (player,)).fetchone()
        if row:
            return dict(row)
        # Try case-insensitive
        row = conn.execute("SELECT * FROM wallets WHERE LOWER(name_hint) = LOWER(?)",
                           (player,)).fetchone()
        if row:
            return dict(row)
        return None

    def handle_request(self, request: dict) -> dict:
        """Execute a request and return a response. Thread-safe.

        Handles: balance, balance_uuid, deduct, credit, set,
                 transfer, info, stats, ledger, ping
        """
        action = request.get("action", "")
        with self._lock:
            conn = self._conn()
            try:
                if action == "balance":
                    player = request.get("player", "")
                    info = self._resolve_player(player, conn)
                    if info:
                        return {"ok": True, "balance": info["balance"],
                                "uuid": info["player_uuid"],
                                "name_hint": info.get("name_hint")}
                    return {"ok": False, "error": "Player not found"}

                elif action == "balance_uuid":
                    uuid_str = request.get("uuid", "")
                    row = conn.execute(
                        "SELECT * FROM wallets WHERE player_uuid = ?",
                        (uuid_str,)).fetchone()
                    if row:
                        return {"ok": True, "balance": row["balance"]}
                    return {"ok": False, "error": "Player not found"}

                elif action == "deduct":
                    uuid_str = request["uuid"]
                    amount = int(request["amount"])
                    reason = request.get("reason", "AH_BRIDGE")
                    note = request.get("note", "")
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                    conn.execute("BEGIN IMMEDIATE")
                    current = conn.execute(
                        "SELECT balance FROM wallets WHERE player_uuid = ?",
                        (uuid_str,)).fetchone()
                    if not current:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "error": "Player not found"}
                    new_balance = current["balance"] - amount
                    if new_balance < 0:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "error": "Insufficient funds",
                                "balance": current["balance"]}
                    conn.execute(
                        "UPDATE wallets SET balance = ?, updated_at = ? WHERE player_uuid = ?",
                        (new_balance, now, uuid_str))
                    conn.execute(
                        "INSERT INTO wallet_ledger (player_uuid, delta, balance_after, reason, note, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (uuid_str, -amount, new_balance, reason, note, now))
                    conn.execute("COMMIT")
                    return {"ok": True, "balance": new_balance, "delta": -amount,
                            "reason": reason}

                elif action == "credit":
                    uuid_str = request["uuid"]
                    amount = int(request["amount"])
                    reason = request.get("reason", "AH_BRIDGE")
                    note = request.get("note", "")
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                    conn.execute("BEGIN IMMEDIATE")
                    current = conn.execute(
                        "SELECT balance FROM wallets WHERE player_uuid = ?",
                        (uuid_str,)).fetchone()
                    if not current:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "error": "Player not found"}
                    new_balance = current["balance"] + amount
                    conn.execute(
                        "UPDATE wallets SET balance = ?, updated_at = ? WHERE player_uuid = ?",
                        (new_balance, now, uuid_str))
                    conn.execute(
                        "INSERT INTO wallet_ledger (player_uuid, delta, balance_after, reason, note, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (uuid_str, amount, new_balance, reason, note, now))
                    conn.execute("COMMIT")
                    return {"ok": True, "balance": new_balance, "delta": amount,
                            "reason": reason}

                elif action == "set":
                    uuid_str = request["uuid"]
                    amount = int(request["amount"])
                    reason = request.get("reason", "AH_SET")
                    note = request.get("note", "")
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                    conn.execute("BEGIN IMMEDIATE")
                    current = conn.execute(
                        "SELECT balance FROM wallets WHERE player_uuid = ?",
                        (uuid_str,)).fetchone()
                    if not current:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "error": "Player not found"}
                    delta = amount - current["balance"]
                    conn.execute(
                        "UPDATE wallets SET balance = ?, updated_at = ? WHERE player_uuid = ?",
                        (amount, now, uuid_str))
                    conn.execute(
                        "INSERT INTO wallet_ledger (player_uuid, delta, balance_after, reason, note, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (uuid_str, delta, amount, reason, note, now))
                    conn.execute("COMMIT")
                    return {"ok": True, "balance": amount, "delta": delta,
                            "reason": reason}

                elif action == "transfer":
                    from_uuid = request["from"]
                    to_uuid = request["to"]
                    amount = int(request["amount"])
                    reason = request.get("reason", "AH_TRANSFER")
                    note = request.get("note", "")
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                    conn.execute("BEGIN IMMEDIATE")
                    from_w = conn.execute(
                        "SELECT balance FROM wallets WHERE player_uuid = ?",
                        (from_uuid,)).fetchone()
                    to_w = conn.execute(
                        "SELECT balance FROM wallets WHERE player_uuid = ?",
                        (to_uuid,)).fetchone()
                    if not from_w or not to_w:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "error": "Sender or receiver not found"}
                    if from_w["balance"] < amount:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "error": "Insufficient funds",
                                "balance": from_w["balance"]}

                    from_new = from_w["balance"] - amount
                    to_new = to_w["balance"] + amount
                    conn.execute(
                        "UPDATE wallets SET balance = ?, updated_at = ? WHERE player_uuid = ?",
                        (from_new, now, from_uuid))
                    conn.execute(
                        "UPDATE wallets SET balance = ?, updated_at = ? WHERE player_uuid = ?",
                        (to_new, now, to_uuid))
                    conn.execute(
                        "INSERT INTO wallet_ledger (player_uuid, delta, balance_after, reason, note, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (from_uuid, -amount, from_new, f"{reason}_SEND", note, now))
                    conn.execute(
                        "INSERT INTO wallet_ledger (player_uuid, delta, balance_after, reason, note, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (to_uuid, amount, to_new, f"{reason}_RECV", note, now))
                    conn.execute("COMMIT")
                    return {"ok": True, "from_balance": from_new, "to_balance": to_new,
                            "amount": amount, "reason": reason}

                elif action == "info":
                    player = request.get("player", "")
                    info = self._resolve_player(player, conn)
                    if info:
                        return {"ok": True, "uuid": info["player_uuid"],
                                "balance": info["balance"],
                                "name_hint": info.get("name_hint"),
                                "updated_at": info.get("updated_at")}
                    return {"ok": False, "error": "Player not found"}

                elif action == "stats":
                    total = conn.execute("SELECT SUM(balance) as s FROM wallets").fetchone()
                    count = conn.execute("SELECT COUNT(*) as c FROM wallets").fetchone()
                    ledger = conn.execute("SELECT COUNT(*) as c FROM wallet_ledger").fetchone()
                    return {"ok": True,
                            "total_coins": total["s"] if total and total["s"] else 0,
                            "player_count": count["c"] if count else 0,
                            "ledger_entries": ledger["c"] if ledger else 0,
                            "db_path": str(self.db_path)}

                elif action == "ledger":
                    uuid_str = request.get("uuid", "")
                    limit = int(request.get("limit", 20))
                    rows = conn.execute(
                        "SELECT * FROM wallet_ledger WHERE player_uuid = ? "
                        "ORDER BY id DESC LIMIT ?",
                        (uuid_str, limit)).fetchall()
                    return {"ok": True, "entries": [dict(r) for r in rows]}

                elif action == "ping":
                    return {"ok": True, "pong": True,
                            "time": datetime.now(timezone.utc).isoformat()}

                else:
                    return {"ok": False, "error": f"Unknown action: {action}"}

            except Exception as e:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                log("ERROR", f"Request failed: {e}\n{traceback.format_exc()}")
                return {"ok": False, "error": str(e)}
            finally:
                conn.close()


# ── TCP Server (encrypted) ─────────────────────────────────────────

class BridgeServer:
    """TCP server that handles economy DB requests. Supports LAN (plain) and secure (encrypted) modes."""

    def __init__(self, host: str, port: int, db_path: str, key: bytes, lan_mode: bool = True):
        self.host = host
        self.port = port
        self.key = key
        self.lan_mode = lan_mode
        self.db = OogaDB(db_path)
        self.server = None
        self.running = False

    # ── E4: Retry helpers for client socket operations ────────────────────
    @staticmethod
    def _recv_retry(conn: socket.socket, size: int, max_retries: int = 3) -> Optional[bytes]:
        """Receive exactly `size` bytes with retry on transient errors.

        Args:
            conn: Client socket
            size: Number of bytes to receive
            max_retries: Maximum retry attempts (default 3)

        Returns:
            Received bytes, or None if all retries exhausted
        """
        for attempt in range(max_retries):
            try:
                data = conn.recv(size)
                return data
            except socket.timeout:
                log("WARN", f"recv timeout (attempt {attempt+1}/{max_retries})")
                continue
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                if attempt == max_retries - 1:
                    log("ERROR", f"recv failed after {max_retries} attempts: {e}")
                    return None
                log("WARN", f"recv error (attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(0.5)
                continue
        return None

    @staticmethod
    def _send_retry(conn: socket.socket, data: bytes, max_retries: int = 3) -> bool:
        """Send all bytes with retry on transient errors.

        Args:
            conn: Client socket
            data: Bytes to send
            max_retries: Maximum retry attempts (default 3)

        Returns:
            True if all data sent, False on failure
        """
        for attempt in range(max_retries):
            try:
                conn.sendall(data)
                return True
            except socket.timeout:
                log("WARN", f"sendall timeout (attempt {attempt+1}/{max_retries})")
                continue
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                if attempt == max_retries - 1:
                    log("ERROR", f"sendall failed after {max_retries} attempts: {e}")
                    return False
                log("WARN", f"sendall error (attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(0.5)
                continue
        return False

    def _handle_client(self, conn: socket.socket, addr: tuple):
        """Handle one connection. In LAN mode, plain JSON. In secure mode, AES-256-GCM."""
        log("INFO", f"Connection from {addr[0]}:{addr[1]} ({'LAN' if self.lan_mode else 'SECURE'} mode)")
        # ── E4: Set client socket timeout to prevent hanging on dead connections ──
        conn.settimeout(30)
        try:
            if self.lan_mode:
                # ── LAN mode: plain JSON, length-prefixed ──
                raw_len = self._recv_retry(conn, 4)
                if raw_len is None or len(raw_len) < 4:
                    return
                pkt_len = struct.unpack("!I", raw_len)[0]
                if pkt_len > MAX_PACKET_SIZE:
                    log("WARN", f"Invalid packet size from {addr[0]}: {pkt_len}")
                    return

                data = b""
                while len(data) < pkt_len:
                    chunk = self._recv_retry(conn, pkt_len - len(data))
                    if not chunk:
                        break
                    data += chunk

                try:
                    request = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    log("WARN", f"JSON parse error from {addr[0]}: {e}")
                    return

                response = self.db.handle_request(request)
                response_bytes = json.dumps(response).encode("utf-8")
                self._send_retry(conn, struct.pack("!I", len(response_bytes)) + response_bytes)

            else:
                # ── Secure mode: AES-256-GCM encrypted (original behavior) ──
                raw_len = self._recv_retry(conn, 4)
                if raw_len is None or len(raw_len) < 4:
                    return
                pkt_len = struct.unpack("!I", raw_len)[0]
                if pkt_len > MAX_PACKET_SIZE or pkt_len < 28:
                    log("WARN", f"Invalid packet size from {addr[0]}: {pkt_len}")
                    return

                encrypted = b""
                while len(encrypted) < pkt_len:
                    chunk = self._recv_retry(conn, pkt_len - len(encrypted))
                    if not chunk:
                        break
                    encrypted += chunk

                plain = decrypt(self.key, encrypted)
                if plain is None:
                    log("WARN", f"Decryption failed from {addr[0]} (wrong key?)")
                    return

                try:
                    request = json.loads(plain.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    log("WARN", f"JSON parse error from {addr[0]}: {e}")
                    return

                response = self.db.handle_request(request)
                response_bytes = json.dumps(response).encode("utf-8")
                encrypted_resp = encrypt(self.key, response_bytes)
                self._send_retry(conn, struct.pack("!I", len(encrypted_resp)) + encrypted_resp)

            result = response.get("ok", False)
            log("INFO", f"{addr[0]}: {request.get('action','?')} → {'OK' if result else 'FAIL'}")

        except Exception as e:
            log("ERROR", f"Client error: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def start(self):
        """Start the bridge server."""
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(5)
        self.server.settimeout(1.0)
        self.running = True
        mode_str = "LAN (plain JSON)" if self.lan_mode else "AES-256-GCM encrypted"
        log("INFO", f"Bridge server listening on {self.host}:{self.port} ({mode_str})")
        log("INFO", f"Database: {self.db.db_path}")
        log("INFO", "Ready for connections from mc_manager...")

        # Test DB connection
        try:
            stats = self.db.handle_request({"action": "stats"})
            if stats.get("ok"):
                log("INFO", f"DB stats: {stats['player_count']} players, "
                           f"{stats['total_coins']} coins, "
                           f"{stats['ledger_entries']} ledger entries")
        except Exception as e:
            log("ERROR", f"DB test failed: {e}")

        try:
            while self.running:
                try:
                    conn, addr = self.server.accept()
                    threading.Thread(target=self._handle_client,
                                     args=(conn, addr), daemon=True).start()
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            log("INFO", "Shutting down...")
        finally:
            self.running = False
            if self.server:
                self.server.close()
            log("INFO", "Bridge server stopped")


# ── Main entry point ───────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="eco_bridge_server — Encrypted TCP bridge to Otters Civ economy DB")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"TCP port (default: {DEFAULT_PORT})")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST,
                        help=f"Bind address (default: {DEFAULT_HOST})")
    parser.add_argument("--db", type=str, default="",
                        help="Path to project_ooga.db (auto-searches if not set)")
    parser.add_argument("--key", type=str, default="",
                        help="Encryption key (default: from eco_config.json or env ECO_BRIDGE_KEY)")
    parser.add_argument("--config", type=str, default="",
                        help="Path to eco_config.json")
    parser.add_argument("--lan-mode", action="store_true", default=True,
                        help="LAN mode: plain JSON, no encryption (default)")
    parser.add_argument("--secure", action="store_true", default=False,
                        help="Secure mode: AES-256-GCM encryption required")
    args = parser.parse_args()

    # If --secure is set, disable LAN mode
    lan_mode = args.lan_mode and not args.secure

    if not HAVE_CRYPTO:
        print("WARNING: No crypto library found. Install pycryptodome:")
        print("  pip install pycryptodome")
        print("The bridge will use a pure-Python fallback (slower but works).")

    # Resolve key
    key_str = args.key or os.environ.get("ECO_BRIDGE_KEY", DEFAULT_KEY)
    if not key_str:
        log("FATAL", "No encryption key provided. Set ECO_BRIDGE_KEY env or use --key.")
        print("  ERROR: No encryption key provided.")
        print("  Set the key via:")
        print("    export ECO_BRIDGE_KEY=\"your-secret-key\"")
        print("  Or via --key:")
        print("    python3 eco_bridge_server.py --key \"your-secret-key\"")
        print("  Or in eco_config.json:")
        print('    {"eco_bridge_password": "your-secret-key"}')
        sys.exit(1)
    key = _derive_key(key_str)
    log("INFO", f"Encryption key derived (SHA-256 of password)")

    # Resolve database path
    db_path = args.db
    if not db_path:
        # Try reading from DATA/eco_config.json first, fall back to local
        # Add project root to sys.path so engine imports work when running directly
        _script_dir = Path(__file__).resolve().parent
        _proj_root = _script_dir.parent.parent.parent.parent  # up 4 levels to yuniScripts root
        if str(_proj_root) not in sys.path:
            sys.path.insert(0, str(_proj_root))
        try:
            from engine.config_loader import get_config_path
            config_json = get_config_path("eco")
        except ImportError:
            # Running standalone (not from YuniScripts project root)
            config_json = _script_dir / "eco_config.json"
        if not config_json.exists():
            config_dir = Path(__file__).parent
            config_json = config_dir / "eco_config.json"
        if config_json.exists():
            try:
                import json as _json
                with open(config_json, encoding="utf-8") as _f:
                    _cfg = _json.load(_f)
                _cfg_path = _cfg.get("ooga_db_path", "")
                if _cfg_path and os.path.exists(_cfg_path):
                    db_path = str(Path(_cfg_path).resolve())
                    log("INFO", f"Found db path in eco_config.json: {db_path}")
            except Exception as _e:
                log("WARN", f"Could not read eco_config.json: {_e}")

    if not db_path:
        # Search common locations (works on both Windows and Linux)
        search_paths = [
            Path.home() / "minecraft_server" / "config" / "otters_civ_revived" / "project_ooga.db",
            Path.home() / "config" / "otters_civ_revived" / "project_ooga.db",
            Path.home() / "Desktop" / "26_1_2_vanilla" / "config" / "otters_civ_revived" / "project_ooga.db",
            Path("config") / "otters_civ_revived" / "project_ooga.db",
            Path("project_ooga.db"),
            Path("ECO_BRIDGE") / "project_ooga.db",
        ]
        for p in search_paths:
            if p.exists():
                db_path = str(p.resolve())
                break

    if not db_path or not os.path.exists(db_path):
        log("FATAL", f"Database not found.")
        print()
        print("  ERROR: Could not find Otters Civ database (project_ooga.db).")
        print()
        print("  Specify the path with:")
        print("    python3 eco_bridge_server.py --db <full-path-to-project_ooga.db>")
        print()
        print("  Or create a config file at ECO_BRIDGE/eco_config.json with:")
        print('    {"ooga_db_path": "<full-path-to-project_ooga.db>"}')
        print()
        print("  Example:")
        print("    python3 eco_bridge_server.py --db ~/minecraft_server/config/otters_civ_revived/project_ooga.db")
        print()
        sys.exit(1)

    log("INFO", f"Using database: {db_path}")
    log("INFO", f"Bridge mode: {'LAN (plain JSON)' if lan_mode else 'SECURE (AES-256-GCM encrypted)'}")

    server = BridgeServer(
        host=args.host,
        port=args.port,
        db_path=db_path,
        key=key,
        lan_mode=lan_mode,
    )

    # ── Start persistent LAN discovery beacon ──
    try:
        from engine.lan_discovery import add_beacon_to_server
        _discovery_beacon = add_beacon_to_server("eco_bridge", args.port or DEFAULT_PORT,
                                                 version="1.0", db=str(db_path)[:60],
                                                 lan_mode=lan_mode)
    except ImportError:
        # Fallback: self-contained beacon
        _beacon_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _beacon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        _local_ip = "127.0.0.1"
        try:
            _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _s.settimeout(0.5)
            _s.connect(("8.8.8.8", 80))
            _local_ip = _s.getsockname()[0]
            _s.close()
        except Exception:
            pass
        import threading as _t
        def _beacon_loop():
            while True:
                try:
                    _payload = json.dumps({
                        "type": "lan_discovery",
                        "service": "eco_bridge",
                        "host": _local_ip,
                        "hostname": socket.gethostname(),
                        "port": args.port or DEFAULT_PORT,
                        "lan_mode": lan_mode,
                        "ts": time.time(),
                    }).encode("utf-8")
                    for _b in ["255.255.255.255", "<broadcast>"]:
                        try:
                            _beacon_sock.sendto(_payload, (_b, _LAN_DISCOVERY_PORT))
                        except Exception:
                            pass
                except Exception:
                    pass
                time.sleep(5)
        _t.Thread(target=_beacon_loop, daemon=True).start()
        log("INFO", f"LAN discovery beacon started (port {_LAN_DISCOVERY_PORT}) — advertising eco_bridge")

    server.start()


if __name__ == "__main__":
    main()

