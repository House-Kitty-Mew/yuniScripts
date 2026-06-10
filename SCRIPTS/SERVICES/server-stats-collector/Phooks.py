# Phooks.py – Declares events this script listens to and emits.
#
# This script uses GuiApiClient (via engine.gui_api_client) which internally
# creates a PhooksClient that emits gui_tab_register and gui_widget_update
# and listens for gui_data_request.
#
# The main loop does NOT directly use Phooks — it communicates with the
# server_stats daemon via raw UDP with token-based auth.
# Command listener is also raw UDP (CMD_PORT).

PHOOKS_EVENTS_LISTEN = [
    "gui_data_request",            # GuiApiClient internal – dashboard requests data refresh
]

PHOOKS_EVENTS_EMIT = [
    "gui_tab_register",            # GuiApiClient internal – register dashboard tab/widgets
    "gui_widget_update",           # GuiApiClient internal – push widget data to dashboard
]