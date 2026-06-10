# sign_item.py – encrypted item signing with client-side deduplication
import os, sys, traceback, json, time, uuid, socket, random, re, sqlite3, queue, shutil
from pathlib import Path
from datetime import datetime

# Import minescript EARLY so fallback blocks can use minescript.echo() for errors
import minescript
from minescript import EventQueue

SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = _PROJECT_ROOT / "DATA"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = SCRIPT_DIR / "sign_item_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _early_log(level, message):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = LOG_DIR / f"sign_item_{today}.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {message}\n"
        with open(log_path, "a") as f:
            f.write(line)
    except Exception:
        pass

# Load config from centralized DATA/ (with legacy migration)
CONFIG_PATH = DATA_DIR / "sign_item_config.json"
_LEGACY_PATH = SCRIPT_DIR / "sign_item_config.json"
if _LEGACY_PATH.exists() and not CONFIG_PATH.exists():
    shutil.copy2(_LEGACY_PATH, CONFIG_PATH)
    _early_log("INFO", f"Migrated sign_item_config.json -> {CONFIG_PATH}")
config = {}
try:
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
except FileNotFoundError:
    config = {"logging_enabled": True, "debug_echo": False, "cooldown_seconds": 10.0, "udp_timeout": 3.0}

LOGGING_ENABLED = config.get("logging_enabled", True)
DEBUG_ECHO = config.get("debug_echo", False)
COOLDOWN_SECONDS = config.get("cooldown_seconds", 10.0)
UDP_TIMEOUT = config.get("udp_timeout", 3.0)

SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 25571  # engine.ports.ITEM_SIGNING_PORT (local define — minescript can't import engine)
ENCRYPTION_KEYS = {}

# Helpers path
HELPERS_DIR = SCRIPT_DIR / "HELPERS"
sys.path.insert(0, str(HELPERS_DIR))

# ---------- base64 fallback ----------
try:
    import base64
    b64decode = base64.b64decode
    b64encode = base64.b64encode
    _early_log("INFO", "Using standard library base64.")
except ImportError:
    try:
        import base64_fallback
        b64decode = base64_fallback.b64decode
        b64encode = base64_fallback.b64encode
        _early_log("INFO", "Using pure‑Python base64 fallback.")
    except ImportError:
        msg = "No base64 module found."
        _early_log("FATAL", msg)
        minescript.echo(f"§c{msg}")
        sys.exit(1)

# ---------- Encryption fallback ----------
try:
    from Crypto.Cipher import AES as _AES
    _has_pycrypto = True
    _early_log("INFO", "Using pycryptodome for encryption.")
except ImportError:
    try:
        import crypto_fallback
        _AES = None
        _has_pycrypto = False
        _early_log("INFO", "Using pure‑Python AES‑GCM fallback.")
    except ImportError:
        msg = "No encryption module found."
        _early_log("FATAL", msg)
        minescript.echo(f"§c{msg}")
        sys.exit(1)

# ---------- Logging helpers ----------
def log_message(level, message, force=False):
    if not LOGGING_ENABLED and not force:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOG_DIR / f"sign_item_{today}.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {message}"
    with open(log_path, "a") as f:
        f.write(line + "\n")

def debug(msg):
    log_message("DEBUG", msg)

def info(msg):
    log_message("INFO", msg)

def warn(msg):
    log_message("WARN", msg, force=True)
    minescript.echo(f"§6[WARN] {msg}")

def error(msg):
    log_message("ERROR", msg, force=True)
    minescript.echo(f"§c[ERROR] {msg}")

# ---------- Key loader (centralized DATA/ with legacy migration) ----------
KEYS_FILE = DATA_DIR / "sign_item_keys.ini"
_LEGACY_KEYS = SCRIPT_DIR / "sign_item_keys.ini"
if _LEGACY_KEYS.exists() and not KEYS_FILE.exists():
    shutil.copy2(_LEGACY_KEYS, KEYS_FILE)
    _early_log("INFO", f"Migrated sign_item_keys.ini -> {KEYS_FILE}")

def load_keys():
    keys = {}
    if not KEYS_FILE.exists():
        warn(f"Key file not found: {KEYS_FILE}")
        return keys
    with open(KEYS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                player, key_str = line.split('=', 1)
                player = player.strip()
                key_str = key_str.strip()
                try:
                    keys[player] = b64decode(key_str)
                except Exception as e:
                    warn(f"Invalid base64 key for player '{player}': {e}")
    info(f"Loaded {len(keys)} player keys.")
    return keys

ENCRYPTION_KEYS = load_keys()

# ---------- Encryption wrappers ----------
def encrypt_data(key: bytes, plain_data: bytes, seq_num: int) -> bytes:
    nonce = os.urandom(12)
    if _has_pycrypto:
        cipher = _AES.new(key, _AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(plain_data)
    else:
        ciphertext, tag = crypto_fallback.encrypt(key, plain_data, nonce)
    seq_bytes = seq_num.to_bytes(4, 'big')
    return seq_bytes + nonce + ciphertext + tag

def decrypt_data(key: bytes, packet: bytes) -> (int, dict):
    if len(packet) < 4 + 12 + 16:
        raise ValueError("Packet too short")
    seq_num = int.from_bytes(packet[:4], 'big')
    nonce = packet[4:16]
    ciphertext_tag = packet[16:]
    tag = ciphertext_tag[-16:]
    ciphertext = ciphertext_tag[:-16]
    if _has_pycrypto:
        cipher = _AES.new(key, _AES.MODE_GCM, nonce=nonce)
        try:
            plain = cipher.decrypt_and_verify(ciphertext, tag)
        except Exception as e:
            raise ValueError("Decryption/authentication failed") from e
    else:
        try:
            plain = crypto_fallback.decrypt(key, nonce, ciphertext, tag)
        except ValueError as e:
            raise ValueError("Decryption/authentication failed") from e
    return seq_num, json.loads(plain.decode('utf-8'))

# ---------- Database ----------
DB_FILE = SCRIPT_DIR / "signatures.db3"
conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
conn.execute('''CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    player TEXT,
    item TEXT,
    rarity TEXT,
    count INTEGER,
    cert TEXT)''')
conn.commit()

def log_transaction(player, item, rarity, count, cert):
    ts = datetime.now().isoformat()
    conn.execute("INSERT INTO transactions(timestamp, player, item, rarity, count, cert) VALUES (?,?,?,?,?,?)",
                 (ts, player, item, rarity, count, cert))
    conn.commit()
    info(f"DB_INSERT: {player} signed {item} ({rarity}) cert={cert}")

# --- Rarity, color, etc. ---
def adler32(data: str) -> int:
    try:
        import zlib
        return zlib.adler32(data.encode('utf-8')) & 0xffffffff
    except ImportError:
        MOD_ADLER = 65521
        a, b = 1, 0
        for byte in data.encode('utf-8'):
            a = (a + byte) % MOD_ADLER
            b = (b + a) % MOD_ADLER
        return (b << 16) | a

class _Tier:
    def __init__(self, name, prob, color):
        self.name = name; self.prob = prob; self.color = color

_TIERS = [
    _Tier("Garbage", 45, "§7"), _Tier("Common", 25, "§6"),
    _Tier("Uncommon", 15, "§a"), _Tier("Rare", 9, "§9"),
    _Tier("Epic", 4, "§d"), _Tier("Legendary", 1.5, "§c"),
    _Tier("Mythic", 0.499, "§5"), _Tier("Cosmic Perfection", 0.001, None)
]
_THRESHOLDS = []
_cum = 0.0
for tier in _TIERS:
    _cum += tier.prob
    _THRESHOLDS.append(_cum)
_TOTAL_QUALITY_WEIGHT = sum(range(1, 101))
_RANDOM_COLORS = ["§c", "§6", "§e", "§b", "§a", "§d"]

def random_colorize(text):
    return "".join(random.choice(_RANDOM_COLORS) + ch for ch in text)

def generate_rarity(player_name, item_name):
    try:
        r = random.random() * 100.0

    except Exception as e:
        logger.error(f"generate_rarity failed: {e}")
        return None
    chosen = _TIERS[-1]
    for i, t in enumerate(_THRESHOLDS):
        if r <= t:
            chosen = _TIERS[i]
            break
    r2 = random.random() * _TOTAL_QUALITY_WEIGHT
    cum, q = 0, 100
    for n in range(1, 101):
        cum += n
        if r2 <= cum:
            q = n
            break
    if chosen.name == "Cosmic Perfection":
        tier_disp = random_colorize(chosen.name)
    else:
        tier_disp = chosen.color + chosen.name
    tier_line = f"{tier_disp} {q}"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    h = adler32(item_name + ts)
    cert_line = f"§7Cert: {h:08X}"
    return tier_line, cert_line

COLOR_CODE_MAP = {
    '§0': 'black', '§1': 'dark_blue', '§2': 'dark_green', '§3': 'dark_aqua',
    '§4': 'dark_red', '§5': 'dark_purple', '§6': 'gold', '§7': 'gray',
    '§8': 'dark_gray', '§9': 'blue', '§a': 'green', '§b': 'aqua',
    '§c': 'red', '§d': 'light_purple', '§e': 'yellow', '§f': 'white'
}

def strip_mc_color(t):
    return re.sub(r'§.', '', t)

def extract_color_code(t):
    if t.startswith('§') and len(t) >= 2:
        return t[:2], t[2:]
    return None, t

def human_name(item_id):
    if ':' in item_id:
        item_id = item_id.split(':')[1]
    return " ".join(w.capitalize() for w in item_id.split('_'))

# ---------- Client-side duplicate cache (player+item) ----------
_recent_signs = {}          # key: "player:item_id" -> expiry timestamp
DEDUP_WINDOW = 2.0          # seconds

# ---------- Noconfirm mode (persistent toggle via config file) ----------
_NC_MTIME = 0.0           # last-seen config file mtime
_NC_ITEM_KEY = None       # (player, item_id, nbt_len) to detect item changes
_NC_LAST_SIGN = 0.0       # timestamp of last noconfirm auto-sign

def _reload_config():
    """Reload config dict from disk (used for noconfirm toggle detection)."""
    global _NC_MTIME
    try:
        mtime = CONFIG_PATH.stat().st_mtime
        if mtime == _NC_MTIME:
            return _reload_config._cached
        _NC_MTIME = mtime
        with open(CONFIG_PATH) as f:
            _reload_config._cached = json.load(f)
    except Exception:
        _reload_config._cached = {}
    return _reload_config._cached

_reload_config._cached = {}

def _toggle_noconfirm():
    """Toggle the noconfirm flag in config (called from a separate `\\sign_item noconfirm` invocation)."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    current = cfg.get("noconfirm", False)
    cfg["noconfirm"] = not current
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    state = "§aON" if cfg["noconfirm"] else "§cOFF"
    minescript.echo(f"§e[Noconfirm signing mode: {state}]")
    info(f"NOCONFIRM TOGGLED: {'ON' if cfg['noconfirm'] else 'OFF'}")


def _is_duplicate_request(player, item_id):
    """Return True if the same player tried to sign the same item very recently."""
    key = f"{player}:{item_id}"
    now = time.time()
    if key in _recent_signs and now - _recent_signs[key] < DEDUP_WINDOW:
        debug(f"Duplicate request suppressed (player={player}, item={item_id})")
        return True
    _recent_signs[key] = now
    # Clean up old entries occasionally (simple)
    for k in list(_recent_signs.keys()):
        if now - _recent_signs[k] > DEDUP_WINDOW * 2:
            del _recent_signs[k]
    return False

# ---------- Main ----------
def _do_sign(player, item_id, cnt, main_nbt, key, sock, seq_num):
    """Core signing routine: generate rarity, build payload, send via UDP, handle response.

    Returns (success: bool, signed_name: str, tier: str, new_seq_num: int).
    """
    tier, cert = generate_rarity(player, item_id)

    hn = human_name(item_id)
    signed = hn + " (Signed)"
    rcol_code, rtext = extract_color_code(tier)
    rcol = COLOR_CODE_MAP.get(rcol_code, 'white')
    rclean = strip_mc_color(rtext)
    cclean = strip_mc_color(cert)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lore = [
        {"text": rclean, "color": rcol, "italic": False},
        {"text": cclean, "color": "gray", "italic": False},
        {"text": ts, "color": "dark_gray", "italic": False},
        {"text": player, "color": "dark_gray", "italic": False}
    ]
    payload = {
        "request_uuid": str(uuid.uuid4()),
        "player_name": player,
        "item_id": item_id,
        "count": cnt,
        "item_nbt": main_nbt,
        "signed_name": signed,
        "lore_entries": lore
    }
    plain = json.dumps(payload).encode()
    pkt = encrypt_data(key, plain, seq_num)

    ok = False
    try:
        sock.sendto(pkt, (SERVICE_HOST, SERVICE_PORT))
        resp, _ = sock.recvfrom(4096)
        rseq, data = decrypt_data(key, resp)
        if rseq == seq_num:
            if data.get("status") == "ok":
                minescript.echo(f"§aSigned §r{signed} §a({tier}) §afor {player}")
                info(f"SUCCESS: {signed}")
                log_transaction(player, item_id, tier, cnt, cert)
                ok = True
            else:
                msg = data.get("message", "")
                if any(phrase in msg.lower() for phrase in ["wait", "duplicate", "please wait"]):
                    debug(f"Transient server error (silent): {msg}")
                else:
                    error(f"Server: {msg}")
                info(f"FAIL_RESP: {data}")
    except socket.timeout:
        debug("Request timed out (silent)")
    except Exception as e:
        error(f"UDP error: {e}")

    if not ok:
        debug("Signing failed, request discarded.")

    return ok, signed, tier, seq_num + 1


def main():
    global _NC_LAST_SIGN, _NC_ITEM_KEY
    # ---------- Handle one-off toggle commands from chat ----------
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "noconfirm":
            _toggle_noconfirm()
            return
        # Unknown arg – just warn and exit
        minescript.echo(f"§eUnknown argument: {sys.argv[1]}")
        return

    # ---------- Main signing loop ----------
    try:
        info("SCRIPT_STARTED")
        minescript.echo("§aItem Signer enabled.  Type §e\\sign_item noconfirm§a to toggle no‑click mode.")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(UDP_TIMEOUT)
        last_use = 0.0
        seq_num = 0
        busy = False
        MIN_CLICK_INTERVAL = 0.5

        def flush_mouse_events(q):
            flushed = 0
            while True:
                try:
                    ev = q.get(block=False)
                    if ev.type == "mouse" and ev.button == 1:
                        flushed += 1
                except Exception:
                    break
            if flushed:
                debug(f"Flushed {flushed} duplicate mouse event(s)")

        with EventQueue() as q:
            q.register_mouse_listener()
            while True:
                ev = None
                try:
                    ev = q.get(timeout=0.5)
                except queue.Empty:
                    pass  # Timeout – will check noconfirm mode below

                now = time.time()

                # ---- NOCONFIRM PATH (time‑based, no right‑click needed, emerald required as payment) ----
                if ev is None:
                    cfg = _reload_config()
                    if cfg.get("noconfirm", False) and not busy and now - _NC_LAST_SIGN >= COOLDOWN_SECONDS:
                        _NC_LAST_SIGN = now
                        hands = minescript.player_hand_items()
                        off = hands.off_hand
                        main = hands.main_hand
                        if main and main['item'] != "minecraft:air":
                            # Require an emerald in the off-hand as payment
                            if not off or off['item'] != "minecraft:emerald":
                                continue
                            player = minescript.player_name()
                            key = ENCRYPTION_KEYS.get(player)
                            if not key:
                                continue
                            item_id, cnt = main['item'], main['count']
                            main_nbt = main.get('nbt', '') if isinstance(main, dict) else getattr(main, 'nbt', '') or ''
                            # Avoid re‑signing the same item (detect change by item_id + nbt length)
                            item_key = (player, item_id, len(main_nbt) if main_nbt else 0)
                            if item_key == _NC_ITEM_KEY:
                                continue
                            if _is_duplicate_request(player, item_id):
                                continue
                            _NC_ITEM_KEY = item_key
                            busy = True
                            ok, signed, tier, seq_num = _do_sign(player, item_id, cnt, main_nbt, key, sock, seq_num)
                            if ok:
                                debug(f"Noconfirm sign OK: {signed}")
                            busy = False
                    continue  # Back to top of event loop

                # ---- NORMAL PATH (right‑click + emerald in off‑hand) ----
                if ev.type == "mouse" and ev.button == 1 and ev.action == 1:
                    if now - last_use < COOLDOWN_SECONDS:
                        continue
                    if busy:
                        continue

                    hands = minescript.player_hand_items()
                    off = hands.off_hand
                    main = hands.main_hand
                    if not off or off['item'] != "minecraft:emerald":
                        continue
                    if not main or main['item'] == "minecraft:air":
                        continue

                    player = minescript.player_name()
                    key = ENCRYPTION_KEYS.get(player)
                    if not key:
                        error(f"No encryption key for '{player}'.")
                        continue

                    item_id, cnt = main['item'], main['count']

                    # Capture item NBT/components data to preserve all item properties
                    main_nbt = main.get('nbt', '') if isinstance(main, dict) else getattr(main, 'nbt', '') or ''

                    # ---- CLIENT-SIDE DEDUPLICATION ----
                    if _is_duplicate_request(player, item_id):
                        debug("Discarding duplicate request before generating payload.")
                        continue

                    busy = True
                    last_use = now
                    flush_mouse_events(q)

                    ok, signed, tier, seq_num = _do_sign(player, item_id, cnt, main_nbt, key, sock, seq_num)

                    busy = False
                    if ok:
                        debug(f"Normal sign OK: {signed}")

    except Exception as e:
        error(f"FATAL: {e}\n{traceback.format_exc()}")
        raise

if __name__ == "__main__":
    main()

