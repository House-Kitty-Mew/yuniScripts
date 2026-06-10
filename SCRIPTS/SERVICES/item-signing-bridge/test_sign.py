#!/usr/bin/env python3
"""Test client – sends encrypted sign request to bridge via UDP.

Usage:
    python3 test_sign.py                    # Uses example key & payload below
    python3 test_sign.py <key_b64> <player> # Custom key and player

Example:
    python3 test_sign.py abc123... PlayerName
"""

import json, socket, os, sys
from pathlib import Path

# Add the bridge's FUNCTIONS directory to path
BRIDGE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BRIDGE_DIR))

from FUNCTIONS.base64_fallback import b64decode
from FUNCTIONS.crypto_fallback import encrypt

# Port constant — local define (test script, may not have engine in path)
BRIDGE_PORT = 25571  # matches engine.ports.ITEM_SIGNING_PORT

# ---- Configuration ----
DEFAULT_KEY_B64 = "REPLACE_WITH_A_VALID_BASE64_KEY"
DEFAULT_PLAYER = "TestPlayer"
BRIDGE_HOST = "127.0.0.1"

def main():
    if len(sys.argv) >= 3:
        key_b64 = sys.argv[1]
        player = sys.argv[2]
    else:
        key_b64 = DEFAULT_KEY_B64
        player = DEFAULT_PLAYER

    key = b64decode(key_b64.encode())
    if len(key) != 32:
        print(f"Error: Key must be 32 bytes (got {len(key)})")
        sys.exit(1)

    payload = {
        "player_name": player,
        "item_id": "minecraft:diamond_sword",
        "count": 1,
        "signed_name": "Diamond Sword (Signed)",
        "lore_entries": [
            {"text": "Test Item", "color": "white"},
            {"text": "Cert: TEST1234", "color": "gray"}
        ]
    }
    plain = json.dumps(payload).encode()

    seq = 1
    nonce = os.urandom(12)
    ct, tag = encrypt(key, plain, nonce)
    packet = seq.to_bytes(4, 'big') + nonce + ct + tag

    print(f"Sending sign request for {player}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    sock.sendto(packet, (BRIDGE_HOST, BRIDGE_PORT))

    try:
        resp, _ = sock.recvfrom(4096)
        sock.close()

        resp_seq = int.from_bytes(resp[:4], 'big')
        resp_nonce = resp[4:16]
        resp_ct = resp[16:-16]
        resp_tag = resp[-16:]

        from FUNCTIONS.crypto_fallback import decrypt
        plain_resp = decrypt(key, resp_nonce, resp_ct, resp_tag)
        result = json.loads(plain_resp)
        print("Response:", json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
