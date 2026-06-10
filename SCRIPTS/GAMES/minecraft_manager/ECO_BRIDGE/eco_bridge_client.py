"""
eco_bridge_client.py — Client that connects to the remote economy bridge server.

This runs on the mc_manager side.  It connects to the eco_bridge_server
(running on the Minecraft server machine) over AES-256-GCM encrypted TCP,
and provides the same EconomyBridge API that the rest of the AH system uses.

All logic (RCON fallback, safety limits, retries) happens HERE, on the
mc_manager side.  The remote server is just a thin DB connector.

Usage:
    bridge = RemoteEconomyBridge("192.168.1.100", 7200, "shared-key")
    balance = bridge.get_balance("Steve")
    bridge.deduct("Steve", 100, "AH_TAKE")
"""

import json, os, socket, struct, time, threading, hashlib, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

# ── AES-256-GCM encryption (same as server) ────────────────────────
_CRYPTO_FALLBACK = None
try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _fb_path = os.path.join(_this_dir, "crypto_fallback.py")
    if os.path.exists(_fb_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("crypto_fallback_client", _fb_path)
        _CRYPTO_FALLBACK = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CRYPTO_FALLBACK)
        HAVE_CRYPTO = True


def _derive_key(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).digest()


def _encrypt(key: bytes, plaintext: bytes) -> bytes:
    try:
        nonce = get_random_bytes(12) if "get_random_bytes" in dir() else os.urandom(12)
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        return nonce + ciphertext + tag
    except NameError:
        pass
    if _CRYPTO_FALLBACK:
        nonce = os.urandom(12)
        ciphertext, tag = _CRYPTO_FALLBACK.encrypt(key, plaintext, nonce)
        return nonce + ciphertext + tag
    raise RuntimeError("No encryption library. pip install pycryptodome")


def _decrypt(key: bytes, packet: bytes) -> Optional[bytes]:
    if len(packet) < 28:
        return None
    nonce = packet[:12]
    tag = packet[-16:]
    ciphertext = packet[12:-16]
    try:
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag)
    except NameError:
        pass
    except Exception:
        return None
    if _CRYPTO_FALLBACK:
        try:
            return _CRYPTO_FALLBACK.decrypt(key, nonce, ciphertext, tag)
        except Exception:
            return None
    return None


# ── Configuration ──────────────────────────────────────────────────
class BridgeConfig:
    """Configuration for the remote economy bridge connection."""

    def __init__(self):
        self.host: str = ""                     # Bridge server host (must be set via config)
        self.port: int = 7200                   # Bridge server port (default protocol)
        self.password: str = ""                 # Shared encryption key (must be set via config)
        self.timeout: float = 5.0               # Connection timeout (seconds)
        self.retry_count: int = 3               # Retries on network failure
        self.retry_delay: float = 1.0           # Seconds between retries

    @classmethod
    def from_json(cls, path: str) -> "BridgeConfig":
        """Load from eco_config.json or a custom path."""
        cfg = cls()
        try:
            import json
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                for k in ["eco_bridge_host", "eco_bridge_port",
                           "eco_bridge_password", "eco_bridge_timeout"]:
                    short = k.replace("eco_bridge_", "")
                    if k in data:
                        setattr(cfg, short, data[k])
        except Exception:
            pass
        return cfg


# ── Remote Economy Bridge Client ───────────────────────────────────

class RemoteEconomyBridge:
    """Client that talks to eco_bridge_server over encrypted TCP.

    Provides the same interface as a local EconomyBridge but routes
    all operations through the remote server.
    """

    def __init__(self, host: str = "", port: int = 7200,
                 password: str = "",
                 timeout: float = 5.0,
                 lan_mode: bool = True):
        self.host = host
        self.port = port
        self.lan_mode = lan_mode
        self.key = _derive_key(password) if password else b""
        self.timeout = timeout
        self._connected = False
        self._last_ping = 0.0
        self._lock = threading.Lock()

    @property
    def is_ready(self) -> bool:
        """Check if the bridge is connected and responding."""
        if not self._connected:
            return False
        if time.time() - self._last_ping > 30:
            # Auto-ping to verify connection
            try:
                self._send_command({"action": "ping"})
                self._last_ping = time.time()
                return True
            except Exception:
                self._connected = False
                return False
        return True

    # ── Low-level communication ──────────────────────────────────

    def _send_command(self, request: dict) -> dict:
        """Send an encrypted command to the bridge and return the response.

        Args:
            request: Dict with "action" and any parameters

        Returns:
            Response dict from the server

        Raises:
            ConnectionError if the bridge is unreachable
            TimeoutError if the bridge doesn't respond in time
            ValueError if decryption/JSON parsing fails
        """
        with self._lock:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect((self.host, self.port))

                if self.lan_mode:
                    # ── LAN mode: plain JSON, length-prefixed ──
                    plain = json.dumps(request).encode("utf-8")
                    sock.sendall(struct.pack("!I", len(plain)) + plain)

                    # Receive response
                    raw_len = sock.recv(4)
                    if len(raw_len) < 4:
                        raise ConnectionError("No response length received")
                    pkt_len = struct.unpack("!I", raw_len)[0]
                    if pkt_len > 65535:
                        raise ConnectionError(f"Invalid response size: {pkt_len}")

                    data = b""
                    while len(data) < pkt_len:
                        chunk = sock.recv(pkt_len - len(data))
                        if not chunk:
                            break
                        data += chunk

                    response = json.loads(data.decode("utf-8"))

                else:
                    # ── Secure mode: AES-256-GCM encrypted ──
                    plain = json.dumps(request).encode("utf-8")
                    encrypted = _encrypt(self.key, plain)
                    sock.sendall(struct.pack("!I", len(encrypted)) + encrypted)

                    # Receive response
                    raw_len = sock.recv(4)
                    if len(raw_len) < 4:
                        raise ConnectionError("No response length received")
                    pkt_len = struct.unpack("!I", raw_len)[0]
                    if pkt_len > 65535 or pkt_len < 28:
                        raise ConnectionError(f"Invalid response size: {pkt_len}")

                    encrypted_resp = b""
                    while len(encrypted_resp) < pkt_len:
                        chunk = sock.recv(pkt_len - len(encrypted_resp))
                        if not chunk:
                            break
                        encrypted_resp += chunk

                    # Decrypt
                    plain_resp = _decrypt(self.key, encrypted_resp)
                    if plain_resp is None:
                        raise ConnectionError("Decryption failed (wrong key?)")
                    response = json.loads(plain_resp.decode("utf-8"))

                self._connected = True
                self._last_ping = time.time()
                return response

            except socket.timeout:
                self._connected = False
                raise TimeoutError(f"Bridge at {self.host}:{self.port} timed out")
            except (socket.error, ConnectionRefusedError) as e:
                self._connected = False
                raise ConnectionError(f"Cannot connect to bridge at {self.host}:{self.port}: {e}")
            finally:
                sock.close()

    def _send_with_retry(self, request: dict) -> dict:
        """Send command with automatic retry on network failure."""
        last_error = None
        for attempt in range(1, BridgeConfig().retry_count + 1):
            try:
                return self._send_command(request)
            except (ConnectionError, TimeoutError) as e:
                last_error = e
                if attempt < BridgeConfig().retry_count:
                    time.sleep(BridgeConfig().retry_delay)
        raise last_error or ConnectionError("All retries failed")

    # ── Public API (mirrors EconomyBridge interface) ─────────────

    def get_balance(self, player: str) -> Optional[int]:
        """Get a player's balance from the remote bridge.

        Args:
            player: Player name or UUID

        Returns:
            Balance in coins, or None if not found
        """
        try:
            resp = self._send_with_retry({
                "action": "balance", "player": player
            })
            if resp.get("ok"):
                return resp["balance"]
            return None
        except Exception:
            return None

    def _resolve_or_uuid(self, player: str) -> Optional[str]:
        """Resolve a player name to UUID, or return the string if it's already a UUID."""
        import re
        if re.match(r'^[0-9a-f\-]{36}$', player, re.I):
            return player  # Already a UUID
        info = self.get_player_info(player)
        if info:
            return info.get("player_uuid")
        return None

    def deduct(self, player: str, amount: int, reason: str = "AH_TAKE",
               note: Optional[str] = None) -> bool:
        """Deduct coins from a player via the remote bridge.

        Auto-resolves player names to UUIDs.  Accepts both names and UUIDs.

        Args:
            player: Player name or UUID
            amount: Coins to deduct
            reason: Reason string for ledger
            note: Optional note

        Returns:
            True if successful
        """
        player_uuid = self._resolve_or_uuid(player)
        if not player_uuid:
            return False
        try:
            resp = self._send_with_retry({
                "action": "deduct", "uuid": player_uuid,
                "amount": amount, "reason": reason,
                "note": note or ""
            })
            return bool(resp.get("ok"))
        except Exception:
            return False

    def credit(self, player: str, amount: int, reason: str = "AH_CREDIT",
               note: Optional[str] = None) -> bool:
        """Add coins to a player via the remote bridge.

        Auto-resolves player names to UUIDs.  Accepts both names and UUIDs.
        """
        player_uuid = self._resolve_or_uuid(player)
        if not player_uuid:
            return False
        try:
            resp = self._send_with_retry({
                "action": "credit", "uuid": player_uuid,
                "amount": amount, "reason": reason,
                "note": note or ""
            })
            return bool(resp.get("ok"))
        except Exception:
            return False

    def set_balance(self, player: str, amount: int,
                    reason: str = "AH_SET") -> bool:
        """Set a player's balance via the remote bridge.

        Auto-resolves player names to UUIDs.
        """
        player_uuid = self._resolve_or_uuid(player)
        if not player_uuid:
            return False
        try:
            resp = self._send_with_retry({
                "action": "set", "uuid": player_uuid,
                "amount": amount, "reason": reason
            })
            return bool(resp.get("ok"))
        except Exception:
            return False

    def transfer(self, from_uuid: str, to_uuid: str, amount: int,
                 reason: str = "AH_TRANSFER") -> dict:
        """Transfer coins between players via the remote bridge."""
        try:
            return self._send_with_retry({
                "action": "transfer", "from": from_uuid, "to": to_uuid,
                "amount": amount, "reason": reason
            })
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_balance(self, player_uuid: str, amount: int,
                    reason: str = "AH_SET") -> bool:
        """Set a player's balance via the remote bridge."""
        try:
            resp = self._send_with_retry({
                "action": "set", "uuid": player_uuid,
                "amount": amount, "reason": reason
            })
            if resp.get("ok"):
                return True
            return False
        except Exception:
            return False

    def get_player_info(self, player: str) -> Optional[dict]:
        """Get full player info from the remote bridge."""
        try:
            resp = self._send_with_retry({
                "action": "info", "player": player
            })
            if resp.get("ok"):
                return {
                    "player_uuid": resp["uuid"],
                    "balance": resp["balance"],
                    "name_hint": resp.get("name_hint"),
                    "updated_at": resp.get("updated_at"),
                }
            return None
        except Exception:
            return None

    def get_ledger(self, player_uuid: str, limit: int = 20) -> list[dict]:
        """Get ledger entries from the remote bridge."""
        try:
            resp = self._send_with_retry({
                "action": "ledger", "uuid": player_uuid, "limit": limit
            })
            if resp.get("ok"):
                return resp.get("entries", [])
            return []
        except Exception:
            return []

    def get_economy_stats(self) -> dict:
        """Get economy stats from the remote bridge."""
        try:
            resp = self._send_with_retry({"action": "stats"})
            if resp.get("ok"):
                return resp
            return {"error": resp.get("error", "Unknown error")}
        except Exception as e:
            return {"error": str(e)}

    def ping(self) -> bool:
        """Ping the bridge server."""
        try:
            resp = self._send_with_retry({"action": "ping"})
            return resp.get("ok", False)
        except Exception:
            return False
