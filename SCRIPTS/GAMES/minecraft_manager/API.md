# Minecraft Manager API

## Phooks Events

### Listens For
- `sign_request` – Receives an item signing request forwarded by the
  item-signing-bridge. Payload includes:
  - `request_id` – unique ID for matching response
  - `data` – the decrypted sign request (player_name, item_id, count, etc.)
- `ah_list` – Player wants to list an item on the Auction House.
- `ah_bid` – Player places a bid on an auction listing.
- `ah_buy` – Player wants to Buy-It-Now an item.
- `ah_remove` – Player wants to cancel their listing.
- `ah_query` – Player queries listings (search, my listings, report).
- `ah_sub` – Player subscribes to persona announcements (from SIMULATED_ANNOUNCE extension).
- `ah_unsub` – Player unsubscribes from persona announcements.
- `ah_subs` – Player requests their subscription list.
- `ah_announces` – Player checks pending persona announcements.

### Emits
- `sign_response` – Sends the signing result back to the bridge. Payload:
  - `request_id` – matches the original request
  - `result` – dict with `status` ("ok" or "error") and `message`
- `ah_list_response` – Confirmation/rejection of a listing request.
- `ah_bid_response` – Result of a bid placement.
- `ah_buy_response` – Result of a Buy-It-Now purchase.
- `ah_remove_response` – Result of a listing cancellation.
- `ah_query_response` – Listing data or market report.
- `ah_announce` – Market event broadcast to all players.
- `ah_report` – AI-generated market report broadcast.
- `ah_sub_response` – Response to a subscription request.
- `ah_unsub_response` – Response to an unsubscription request.
- `ah_subs_response` – Subscription list data.
- `ah_announces_response` – Pending announcement data.

## Extensions

The Auction House supports dynamically loaded extensions via the `ah_plugin_registry` system.
Extensions register hooks and are automatically discovered on startup.

### SIMULATED_PEOPLE
Simulates up to 200 personas with unique personalities, jobs, wealth, and needs.
Each persona independently makes market decisions (buy, sell, save) on every tick.
Runs on the `on_simulation_cycle_start` hook before the AI economy simulation.
Config: `DATA/simulated_people_config.json`

### SIMULATED_CHAT
Enables players to send messages to simulated personas via `\ah msg <persona_id> <message>`.
Personas respond with context-aware replies using their stats, memories, and player relationship history.
Queued messages can be read with `\ah qmsg list`.
Config: none (uses shared AH database)

### SIMULATED_ANNOUNCE
Notifies players when "interesting" events happen to personas they're subscribed to.
Players subscribe with `\ah sub <persona_id>` and check announcements with `\ah announces`.
Uses AI thinking-mode evaluation to filter out boring events (berry picking, walking).
Only events scoring ≥ 5 on a 1–10 interestingness scale are delivered.
Config: none (auto-discovers event tables from SIMULATED_PEOPLE extension)

## Stdio Commands (stdin JSON protocol)
- `start`  : Start the Minecraft server.
- `stop`   : Stop the server.
- `status` : Returns JSON {online, players, tps, ...}
- `command <mc command>` : Execute arbitrary RCON command.
- `ah list`  : Show active Auction House listing count.
- `ah players <name>` : Show a player's listings.
- `ah simulate` : Force-run an AI simulation cycle.
- `ah status` : Show AH market health (listings, tx, events, scheduler).
- `ah export` : Export all AH data as JSON.
- `ah note <text>` : Add an admin note to the AI helper database.
- `exit` / `quit` : Shut down this manager script.

## Hooks
This script does not expose custom engine hooks.
