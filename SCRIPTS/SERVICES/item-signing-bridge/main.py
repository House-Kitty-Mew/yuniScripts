#!/usr/bin/env python3
"""Item Signing Bridge – Phooks for all inter‑script communication."""
import json, socket, os, sys, queue, threading, signal, time
from pathlib import Path
from engine.phooks_client import PhooksClient
from datetime import datetime
from collections import deque

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    log_path = LOG_DIR / "signing_bridge.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    _log_buffer.append(line)

sys.path.insert(0, str(Path(__file__).parent))
from FUNCTIONS.base64_fallback import b64decode
from FUNCTIONS.crypto_fallback import encrypt as aes_encrypt, decrypt as aes_decrypt

# ── Project root & GUI API ────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from engine.gui_api_client import GuiApiClient
from engine.config_loader import assert_valid_config, register_config_schema
# ── Validate config at startup ──
register_config_schema("item_signing_bridge",
    required=["listen_host", "listen_port"],
    types={"listen_port": int})



# ── GUI stats tracking ────────────────────────────────────────────
_gui_stats = {"requests_served": 0, "active_connections": 0}
_gui_instance = None
_start_time = time.time()
_log_buffer = deque(maxlen=50)

def _format_uptime(seconds):
    try:
        d, rem = divmod(int(seconds), 86400)

    except Exception as e:
        logger.error(f"_format_uptime failed: {e}")
        return None
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)

def _get_gui_data():
    uptime = time.time() - _start_time
    return {
        "status": "Running" if not _shutdown_flag else "Stopped",
        "requests_served": _gui_stats["requests_served"],
        "uptime": _format_uptime(uptime),
        "active_connections": _gui_stats.get("active_connections", 0),
        "log": "\n".join(_log_buffer),
    }



from engine.config_loader import get_config_path
CONFIG_PATH = get_config_path("item_signing_bridge")
KEYS_FILE = get_config_path("item_signing_bridge").parent / "item_signing_players.keys"

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)
LISTEN_HOST = config["listen_host"]
LISTEN_PORT = int(config["listen_port"])
config = assert_valid_config("item_signing_bridge", config)

def load_player_keys(path):
    keys = {}
    if not path.exists():
        log(f"No player keys file at {path}")
        return keys
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                name, key_str = line.split('=', 1)
                name = name.strip()
                key_str = key_str.strip()
                try:
                    key = b64decode(key_str.encode())
                    if len(key) != 32:
                        log(f"Invalid key length for {name}: {len(key)}")
                        continue
                    keys[name] = key
                except Exception as e:
                    log(f"Invalid key for {name}: {e}")
    return keys

PLAYER_KEYS = load_player_keys(KEYS_FILE)
log(f"Loaded {len(PLAYER_KEYS)} player keys.")

def encrypt_response(key, plain_data, seq_num):
    nonce = os.urandom(12)
    ct, tag = aes_encrypt(key, plain_data, nonce)
    return seq_num.to_bytes(4, 'big') + nonce + ct + tag

def try_decrypt_with_keys(packet, keys):
    if len(packet) < 4 + 12 + 16:
        raise ValueError("Packet too short")
    seq_num = int.from_bytes(packet[:4], 'big')
    nonce = packet[4:16]
    ct_tag = packet[16:]
    tag = ct_tag[-16:]
    ct = ct_tag[:-16]

    log(f"RECV_PKT ({len(packet)} bytes): {packet.hex()}")
    for player, key in keys.items():
        try:
            plain = aes_decrypt(key, nonce, ct, tag)
            data = json.loads(plain.decode('utf-8'))
            log(f"Decrypted for {player}: {data}")
            return seq_num, data, key
        except Exception:
            continue
    raise ValueError("Decryption failed with all keys")

# ── Shutdown handler ─────────────────────────────────────────────
_shutdown_flag = False
_phooks_instance = None
_listener_sock = None

def _shutdown(signum=None, frame=None):
    global _shutdown_flag
    if _shutdown_flag:
        return
    _shutdown_flag = True
    log("Shutting down...")
    # Unregister from Phooks hub
    if _phooks_instance:
        try:
            _phooks_instance.unregister()
        except Exception:
            pass
    # Close GUI API client
    if _gui_instance:
        try:
            _gui_instance.close()
        except Exception:
            pass
    # Close listener socket
    if _listener_sock:
        try:
            _listener_sock.close()
        except Exception:
            pass
    print("SHUTDOWN_COMPLETE", flush=True)

def main():
    global _phooks_instance, _listener_sock

    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except OSError:
        pass  # SIGTERM not available on Windows
    signal.signal(signal.SIGINT, _shutdown)

    phooks = PhooksClient(
        script_id="signing_bridge",
        listen_events=["sign_response"],
        emit_events=["sign_request"]
    )
    _phooks_instance = phooks
    phooks.register()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except (AttributeError, OSError):
        pass
    sock.bind((LISTEN_HOST, LISTEN_PORT))
    sock.settimeout(0.1)  # Short timeout to ensure frequent phooks event checks
    _listener_sock = sock
    log(f"Listening for encrypted clients on {LISTEN_HOST}:{LISTEN_PORT}")

    # ── GUI Dashboard setup ───────────────────────────────────────────
    gui = GuiApiClient("SERVICES/item-signing-bridge", "Signing Bridge")
    global _gui_instance
    _gui_instance = gui
    gui.register_tab([
        {"type": "label", "id": "status", "title": "Bridge Status", "default": "Starting..."},
        {"type": "label", "id": "requests_served", "title": "Requests Served", "default": 0},
        {"type": "label", "id": "uptime", "title": "Uptime", "default": "0s"},
        {"type": "label", "id": "active_connections", "title": "Active Connections", "default": 0},
        {"type": "message", "id": "log", "title": "Activity Log", "default": ""},
    ])
    gui.on_data_request(_get_gui_data)

    pending = {}
    next_request_id = 0

    try:
        while not _shutdown_flag:
            try:
                packet, addr = sock.recvfrom(4096)
                try:
                    seq_num, data, used_key = try_decrypt_with_keys(packet, PLAYER_KEYS)
                except ValueError:
                    log(f"Decryption failed from {addr}")
                    continue

                request_id = next_request_id
                next_request_id += 1
                pending[request_id] = (addr, used_key, seq_num)

                log(f"Emitting sign_request id={request_id}")
                phooks.emit("sign_request", {
                    "request_id": request_id,
                    "data": data
                })
            except socket.timeout:
                pass

            if _shutdown_flag:
                break

            # Drain ALL available phooks events to prevent starvation
            # when many sign_responses arrive at once.
            while True:
                event = phooks.receive(timeout=0.01)
                if event is None:
                    break
                if event.get("event") == "sign_response":
                    resp_data = event["data"]
                    req_id = resp_data["request_id"]
                    result = resp_data["result"]
                    log(f"Received sign_response for id={req_id}: {result}")

                    if req_id in pending:
                        client_addr, key, seq_num = pending.pop(req_id)
                        _gui_stats["requests_served"] += 1
                        resp_plain = json.dumps(result).encode('utf-8')
                        resp_packet = encrypt_response(key, resp_plain, seq_num)
                        sock.sendto(resp_packet, client_addr)
                    else:
                        log(f"Unknown request id {req_id}")
                # Check shutdown between drain iterations
                if _shutdown_flag:
                    break

    except KeyboardInterrupt:
        _shutdown()
    except Exception as e:
        log(f"Fatal error: {e}")
    finally:
        _shutdown()

if __name__ == "__main__":
    main()
