================================================================================
          MINECRAFT ITEM SIGNING SYSTEM – CLIENT & KEY MANAGEMENT
================================================================================

This document explains how to set up and use the Minescript-based item signing
client (sign_item.py) together with the server-side manager (mc_manager.py under
the YuniScripts engine). It also covers the key generation helper (gen-id.py).

================================================================================
TABLE OF CONTENTS
================================================================================

1. Overview
2. Prerequisites
3. Installation
   3.1 Client Files (Minescript)
   3.2 Server Manager (YuniScripts)
4. Configuration
   4.1 sign_item_config.json
   4.2 sign_item_keys.ini
   4.3 Manager Side (keys)
5. Generating Keys with \gen-id
6. How It Works
7. Port Forwarding for Remote Friends
8. Troubleshooting
9. Files Reference

================================================================================
1. OVERVIEW
================================================================================

This system allows Minecraft players to sign items by holding an emerald in the
off-hand and the target item in the main hand, then right-clicking. The client:

- Generates a random rarity (Garbage -> Cosmic Perfection) and a unique
  certificate hash.
- Encrypts the request using AES-GCM with a player-specific key.
- Sends it via UDP to a central manager.
- Uses Phooks to forward the request to the Minecraft Manager script, which validates, removes the emerald and original item, and gives back
  a signed, custom-named item with lore (rarity, cert, timestamp, signer name).

All communication is encrypted; only players with a valid key can use the system.

================================================================================
2. PREREQUISITES
================================================================================

- Minecraft Java Edition (1.19+ recommended)
- Minescript mod (v5.0 or later) installed in your mods/ folder.
- Python 3.8+ (for the manager and client dependencies – Minescript includes its
  own Python runtime, but the manager runs separately).
- Server manager – the mc_manager.py script running under the YuniScripts engine
  (or standalone with the required bridge).
- UDP Bridge (part of YuniScripts: SERVICES/item-signing-bridge) – listens on UDP port 25571, decrypts
  incoming packets, and forwards them as Phooks events to the manager.
  ()

================================================================================
3. INSTALLATION
================================================================================

3.1 Client Files (Minescript)
-----------------------------

Place the following files in your minecraft/minescript/ folder (where Minecraft
is installed, usually .minecraft/):

  - sign_item.py – the main signing script.
  - gen-id.py – key generation helper.
  - HELPERS/ folder containing:
      - crypto_fallback.py (if pycryptodome is not available)
      - base64_fallback.py (if base64 is missing – usually not needed)
  - sign_item_config.json (optional, see Configuration)
  - sign_item_keys.ini (initially empty or containing existing keys)

WARNING: Do not share your sign_item_keys.ini with others. It contains the
secret keys for each player.

3.2 Server Manager (YuniScripts)
--------------------------------

On the server machine (the one running the Minecraft server and the manager):

  - The YuniScripts engine should be installed.
  - Place mc_manager.py inside SCRIPTS/GAMES/minecraft_manager/ (rename to
    main.py if needed).
  - Ensure requirements.txt.
  - Start the engine: python3 main.py (or if using yuniscripts engine, just start yuniscripts.

The manager will listen for sign_request events via Phooks (UDP on port 25573).
You must also run the UDP bridge that listens on port 25571, decrypts packets,
and emits those events if not yuniscripts!.

================================================================================
4. CONFIGURATION
================================================================================

4.1 sign_item_config.json
-------------------------

Config files now live in the centralized `DATA/` directory at the YuniScripts project root.
If you have files in the minescript folder, they will be auto-migrated to `DATA/` on first load
(copy, not move).  You can edit either location — the latest value will be used.

Example (`DATA/sign_item_config.json`):

{
  "logging_enabled": true,
  "debug_echo": false,
  "cooldown_seconds": 10.0,
  "udp_timeout": 3.0
}

- cooldown_seconds – global cooldown between any sign attempts (client side).
- udp_timeout – how long to wait for a response from the manager.
- debug_echo – if true, prints additional debug info in chat.

4.2 sign_item_keys.ini
----------------------

Also lives in `DATA/sign_item_keys.ini` (legacy: `minescript/sign_item_keys.ini` auto-migrated).
This file maps Minecraft usernames to base64-encoded 32-byte AES keys. Format:

PlayerOne=Kx9mP2qL8vXyZ...
PlayerTwo=7xL3mN9qRf...

Each player must have their own key. The client reads this file to find the key
for the local player.

4.3 Manager Side (keys)
-----------------------

On the manager server, the same keys must be present in sign_item_keys.ini
(usually in the same directory as the manager). The manager uses them to
decrypt incoming requests. If a player's key is missing or mismatched,
decryption fails and the request is rejected.

================================================================================
5. GENERATING KEYS WITH \gen-id
================================================================================

The gen-id.py Minescript command simplifies key management.

Usage
-----

In Minecraft chat, type:

  \gen-id FriendName

- If FriendName already has a key stored in generated_keys.ini, the existing key
  is displayed.
- If not, a new random key is generated, saved, and displayed.

Example output:

  New key generated for FriendName:
  FriendName=3L2x7mP9qRf8vWx4bN6cXyZ1aBcDeFgHiJkLmNoPqRsTu=
  Add this line to your sign_item_keys.ini file on the server.

The administrator then copies that line into the server's sign_item_keys.ini
(and also distributes the same key to the friend for their client's
sign_item_keys.ini). The friend does not need to run \gen-id themselves; they
just receive the key and put it in their own client file.

Security tip: Send the key over a secure channel (Signal, encrypted chat, or a
one-time pastebin). Do not post it in plain public chat.

================================================================================
6. HOW IT WORKS
================================================================================

1. Player action: Right-click while holding emerald in off-hand and an item in
   main hand.

2. Client (sign_item.py):
   - Checks cooldown and client-side deduplication (2-second window for same
     player+item).
   - Generates random rarity, quality, and certificate hash.
   - Builds a JSON payload with the item, signed name, lore, timestamp, and a
     UUID.
   - Encrypts payload with AES-GCM using the player's key from
     sign_item_keys.ini.
   - Sends UDP packet to SERVICE_HOST:SERVICE_PORT (default 127.0.0.1:25571).
   - Waits for encrypted ACK from manager.

3. UDP Bridge (separate script, not detailed here):
   - Listens on UDP port 25571.
   - Decrypts the packet using the same key.
   - Forwards the JSON payload as a sign_request event via Phooks (port 25573).

4. Manager (mc_manager.py):
   - Receives the event.
   - Computes a deterministic hash (player + item + count + signed_name,
     excluding randomised lore).
   - Checks if that hash was seen within the last 10 seconds – if yes, rejects
     as duplicate.
   - Checks per-player cooldown (5 seconds) – if too soon, rejects.
   - Executes RCON commands: clear <player> emerald 1, clear <player> <item> <count>.
   - Builds a /give command with custom name and lore.
   - Executes the give command.
   - Sends a success or error response back through Phooks to the UDP bridge.

5. UDP Bridge encrypts and sends ACK to client.

6. Client displays success message (green "Signed ...") or an error.

All RCON traffic stays local (127.0.0.1). Only the UDP port 25571 (or a
forwarded port) is exposed if you allow remote friends.

================================================================================
7. PORT FORWARDING FOR REMOTE FRIENDS
================================================================================

If you want a friend over the internet to use the signing system:

1. Do NOT forward the RCON port (default 25575). The manager only talks to the
   Minecraft server via localhost.

2. Forward UDP port 25571 (or a custom port) from your router to the machine
   running the UDP bridge and manager.

3. On the friend's client, edit sign_item.py (or create a config override) to
   change:

     SERVICE_HOST = "your.public.ip.address"
     SERVICE_PORT = 25571   (same as forwarded port)

4. Ensure the friend has their own entry in sign_item_keys.ini on both client
   and server, with matching keys.

WARNING: Exposing any service to the internet carries risks. Use a VPN (e.g.,
Tailscale) instead of direct port forwarding for better security. The encryption
protects the payload, but the UDP port itself is open to the world.

================================================================================
8. TROUBLESHOOTING
================================================================================

Symptom: "[ERROR] No encryption key for 'Player'"
  Likely cause: Key missing in sign_item_keys.ini on client or server.
  Solution: Generate a key with \gen-id Player and add to both files.

Symptom: "Duplicate request (same item already signed recently)"
  Likely cause: Player clicked twice rapidly.
  Solution: Normal – only the first request is processed. No item or emerald
  is lost.

Symptom: "Please wait X.X seconds before signing again."
  Likely cause: Global per-player cooldown (5 seconds).
  Solution: Wait the indicated time.

Symptom: Request times out (silent)
  Likely cause: UDP packet lost or bridge not running.
  Solution: Check that the UDP bridge is running and that firewall/port
  forwarding is correct.

Symptom: "[ERROR] Server: ..." from manager
  Likely cause: RCON command failed (e.g., item not found).
  Solution: Check that the player actually has the emerald and item in their
  inventory.

Symptom: "\gen-id" command not found
  Likely cause: gen-id.py not in minescript/ folder.
  Solution: Place it there and restart Minecraft or run /minescript reload.

Symptom: Duplicate requests still appear in manager logs
  Likely cause: Client-side dedup window too short.
  Solution: Increase DEDUP_WINDOW in sign_item.py (default 2.0 seconds).

================================================================================
9. FILES REFERENCE
================================================================================

File                         | Location                                     | Purpose
--------------------------------------------------------------------------------
sign_item.py                 | minecraft/minescript/                        | Main signing client
gen-id.py                    | minecraft/minescript/                        | Key generator helper
sign_item_keys.ini           | DATA/sign_item_keys.ini (legacy: minescript/)  | Maps usernames -> base64 keys
generated_keys.ini           | minescript/ folder (local only)                 | Persistent key store from \gen-id
sign_item_config.json        | DATA/sign_item_config.json (legacy: minescript/) | Optional settings
mc_manager.py                | YuniScripts/SCRIPTS/GAMES/minecraft_manager/ | Server-side request processor
UDP bridge script            | (separate)                                   | Listens on UDP 25571, forwards

Getting Help
------------

- Client logs: minecraft/minescript/sign_item_logs/sign_item_YYYY-MM-DD.log
- Manager logs: engine/logs/GAMES_minecraft_manager.log
- Enable debug_echo in sign_item_config.json for more chat details.
- Run the manager with debug = true in its meta.info to see raw RCON responses.

================================================================================
                              END OF DOCUMENT
================================================================================