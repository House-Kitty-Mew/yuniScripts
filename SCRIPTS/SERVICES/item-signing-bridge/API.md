# Item Signing Bridge API

## Overview
Receives encrypted AES-256-GCM signing requests from Minecraft clients
(via UDP port 25571), decrypts them, and forwards them over Phooks
to the Minecraft Manager. Receives Phooks sign_responses from the
manager and sends encrypted acknowledgements back to the client.

## Phooks Events

### Listens For
- `sign_response` – Receives signing result from `mc_manager`. Payload:
  - `request_id` – matches the original request
  - `result` – dict with status ("ok" or "error") and message

### Emits
- `sign_request` – Forwards a decrypted client request to `mc_manager`. Payload:
  - `request_id` – unique ID assigned by bridge
  - `data` – the decrypted sign payload (player_name, item_id, count, signed_name, lore_entries)

## UDP Wire Format (port 25571)
Packets are: `<4-byte-seq><12-byte-nonce><ciphertext><16-byte-tag>`
- Key per player (loaded from `DATA/item_signing_players.keys`)
- Seq numbers for replay protection

## Commands (internal)
None.
