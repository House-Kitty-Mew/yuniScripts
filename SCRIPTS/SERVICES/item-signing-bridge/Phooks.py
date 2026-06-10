# Phooks.py – Declares events this script listens to and emits.

# ACTUAL EVENTS (from main.py):
#   PHOOKS_EVENTS_LISTEN = ["sign_response"]  — receives signing results from manager
#   PHOOKS_EVENTS_EMIT   = ["sign_request"]   — sends signing requests to manager

PHOOKS_EVENTS_LISTEN = [
    "sign_response",
]

PHOOKS_EVENTS_EMIT = [
    "sign_request",
]
