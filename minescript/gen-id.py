#!/usr/bin/env python3
"""
Minescript command: \gen-id <username>
Generates or retrieves a base64-encoded random 32-byte AES key for a given username.
Keys are stored persistently in generated_keys.ini.
Output is visible only to the player who runs the command.
"""

import sys
import os
import base64

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(SCRIPT_DIR, "generated_keys.ini")

def load_existing_keys():
    """Load existing keys from the INI file. Returns dict {username: key}."""
    keys = {}
    if not os.path.exists(KEYS_FILE):
        return keys
    with open(KEYS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                user, key = line.split('=', 1)
                keys[user.strip()] = key.strip()
    return keys

def save_new_key(username, key_b64):
    """Append a new key entry to the INI file."""
    with open(KEYS_FILE, "a") as f:
        f.write(f"{username}={key_b64}\n")

def main():
    if len(sys.argv) < 2:
        minescript.echo("§cUsage: \\gen-id <username>")
        return

    username = sys.argv[1].strip()
    if not username:
        minescript.echo("§cPlease provide a valid username.")
        return

    existing = load_existing_keys()
    if username in existing:
        # Key already exists – show it
        line = f"{username}={existing[username]}"
        minescript.echo(f"§eExisting key for §6{username}§e:\n§r{line}")
        minescript.echo("§7(Use this same line in sign_item_keys.ini on your server.)")
        return

    # Generate a new cryptographically strong random 32-byte key (AES-256)
    key_bytes = os.urandom(32)
    key_b64 = base64.b64encode(key_bytes).decode('ascii')
    save_new_key(username, key_b64)

    output_line = f"{username}={key_b64}"
    minescript.echo(f"§aNew key generated for §e{username}§a:\n§r{output_line}")
    minescript.echo("§7Add this line to your sign_item_keys.ini file on the server.")
    minescript.echo("§7(Keep this key secret – it's used to encrypt signing requests.)")

if __name__ == "__main__":
    import minescript
    main()
