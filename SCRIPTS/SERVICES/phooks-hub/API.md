# Phooks Hub
Central event bus for YuniScripts. Listens on UDP port 25573.

## Wire Protocol (UDP JSON)
All packets are newline-delimited JSON objects sent to UDP port 25573.

### REGISTER
`{"command": "REGISTER", "script_id": "...", "listen_events": ["e1", "e2"]}`
Registers a script to receive forwarded events.

### UNREGISTER
`{"command": "UNREGISTER", "script_id": "..."}`
Removes a script from the hub.

### EMIT
`{"command": "EMIT", "event": "event_name", "data": {...}, "sender": "script_id"}`
Forwards `event` to all registered listeners (except the sender).

### PING → PONG
`{"command": "PING"}` → responds with `{"response": "PONG"}`

## Phooks Events
The hub itself does not define PHOOKS_EVENTS_LISTEN / PHOOKS_EVENTS_EMIT.
It acts as the relay for all other scripts' events.

## Hooks Provided
None.

## Hooks Consumed
None.
