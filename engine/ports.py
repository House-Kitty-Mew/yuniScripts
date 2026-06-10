"""
ports.py — CANONICAL port definitions for all YuniScripts services.
Import these instead of hardcoding magic port numbers.

Adding a new port? Add it here first, then use the import.
"""
import os

# ── Engine / Admin ───────────────────────────────────────────────
UDP_ADMIN_PORT = 25567          # main.py UDP admin command server
TCP_ADMIN_PORT = 25568          # engine/socket_admin.py TCP admin (Windows)

# ── Phooks event bus ────────────────────────────────────────────
PHOOKS_HUB_PORT = int(os.environ.get("PHOOKS_HUB_PORT", 25573))

# ── Service ports ───────────────────────────────────────────────
MINESCRIPT_SENDER_PORT = 25566   # minescript mc_status_sender.py -> mc-status-relay
ITEM_SIGNING_PORT = 25571        # minescript sign_item.py -> item-signing-bridge
QUERY_PORT = 25572               # mc-status-relay "status" query listener
SERVER_STATS_PORT = 5559         # server_stats_daemon -> server-stats-collector

# ── LAN discovery ───────────────────────────────────────────────
LAN_DISCOVERY_PORT = 25574       # engine/lan_discovery.py broadcasts

# ── Debug ───────────────────────────────────────────────────────
DEBUG_BASE_PORT = 5678           # debugpy remote debugging base port
