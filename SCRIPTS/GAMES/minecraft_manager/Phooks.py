# Phooks.py – Declares events this script listens to and emits.

# ACTUAL EVENTS (from main.py):
#   PHOOKS_EVENTS_LISTEN = ["sign_request", "multi_command", ...]
#   PHOOKS_EVENTS_EMIT   = ["sign_response", "multi_response", "multi_broadcast", ...]

PHOOKS_EVENTS_LISTEN = [
    "sign_request",
    # Auction House events (from minescript clients)
    "ah_list",
    "ah_bid",
    "ah_buy",
    "ah_remove",
    "ah_query",
    "ah_test",
    # LootPower game events (from minescript)
    "ah_games",
    # Announce system events (sub/unsub/subs from minescript)
    "ah_sub",
    "ah_unsub",
    "ah_subs",
    "ah_announces",
    # Economy bridge events (from AH → bridge)
    "economy_balance",
    "economy_deduct",
    "economy_credit",
    "economy_transfer",
    "economy_set",
    "economy_ledger",
    "economy_stats",
    # ── Multi-Server events ───────────────────────────────────
    # Route an MC command to a specific server
    # data: {"server": "my-server", "command": "list"}
    "multi_command",
    # Run a custom command on a server
    # data: {"server": "my-server", "custom_command": "backup"}
    "multi_custom_command",
    # Broadcast an MC command to all servers
    # data: {"command": "say Hello everyone!"}
    "multi_broadcast",
    # Enable/disable a subsystem on a server
    # data: {"server": "my-server", "subsystem": "auction_house", "enabled": true}
    "multi_subsystem_toggle",
    # Get server detail / list / status
    # data: {"action": "list|detail|status", "server": "..."}
    "multi_query",
]

PHOOKS_EVENTS_EMIT = [
    "sign_response",
    # Auction House responses
    "ah_list_response",
    "ah_bid_response",
    "ah_buy_response",
    "ah_remove_response",
    "ah_query_response",
    "ah_test_response",
    # LootPower game responses
    "ah_games_response",
    # Auction House broadcasts
    "ah_announce",
    "ah_report",
    # Announce system responses
    "ah_sub_response",
    "ah_unsub_response",
    "ah_subs_response",
    "ah_announces_response",
    # Economy bridge responses
    "economy_balance_response",
    "economy_deduct_response",
    "economy_credit_response",
    "economy_transfer_response",
    "economy_set_response",
    "economy_ledger_response",
    "economy_stats_response",
    # ── Multi-Server responses ────────────────────────────────
    # Response to multi_command
    # data: {"server": "my-server", "command": "list", "response": "...", "status": "ok|error"}
    "multi_command_response",
    "multi_custom_command_response",
    "multi_broadcast_response",
    "multi_subsystem_toggle_response",
    # data: {"servers": [...], "status": "ok"}
    "multi_query_response",
]
