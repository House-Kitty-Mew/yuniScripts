"""
eco_protocol.py — Shared protocol constants for the Economy Bridge.

Defines Phooks events, error codes, and operation limits shared between
the Auction House system and the economy bridge.
"""

# ── Phooks Event Names ──────────────────────────────────────────────

# Requests (AH → Bridge)
EVENT_BALANCE = "economy_balance"
EVENT_DEDUCT = "economy_deduct"
EVENT_CREDIT = "economy_credit"
EVENT_TRANSFER = "economy_transfer"
EVENT_SET = "economy_set"
EVENT_LEDGER = "economy_ledger"
EVENT_STATS = "economy_stats"

# Responses (Bridge → AH)
EVENT_BALANCE_RESPONSE = "economy_balance_response"
EVENT_DEDUCT_RESPONSE = "economy_deduct_response"
EVENT_CREDIT_RESPONSE = "economy_credit_response"
EVENT_TRANSFER_RESPONSE = "economy_transfer_response"
EVENT_SET_RESPONSE = "economy_set_response"
EVENT_LEDGER_RESPONSE = "economy_ledger_response"
EVENT_STATS_RESPONSE = "economy_stats_response"

# All listen events
LISTEN_EVENTS = [
    EVENT_BALANCE, EVENT_DEDUCT, EVENT_CREDIT,
    EVENT_TRANSFER, EVENT_SET, EVENT_LEDGER, EVENT_STATS,
]

# All emit events
EMIT_EVENTS = [
    EVENT_BALANCE_RESPONSE, EVENT_DEDUCT_RESPONSE, EVENT_CREDIT_RESPONSE,
    EVENT_TRANSFER_RESPONSE, EVENT_SET_RESPONSE, EVENT_LEDGER_RESPONSE,
    EVENT_STATS_RESPONSE,
]

# ── Reason constants (for wallet_ledger reason field) ──────────────

REASON_LISTING_FEE = "AH_LISTING_FEE"
REASON_BID = "AH_BID_HOLD"
REASON_BIN_PURCHASE = "AH_BIN_PURCHASE"
REASON_SALE_CREDIT = "AH_SALE_CREDIT"
REASON_CANCEL_FEE = "AH_CANCEL_FEE"
REASON_EXPIRY_WIN = "AH_EXPIRY_WIN"
REASON_ADMIN_ADJUST = "AH_ADMIN_ADJUST"
REASON_ROLLBACK = "AH_ROLLBACK"

# ── Error codes ────────────────────────────────────────────────────

ECODE_PLAYER_NOT_FOUND = "ECO_001"
ECODE_INSUFFICIENT_FUNDS = "ECO_002"
ECODE_EXCEEDS_MAX = "ECO_003"
ECODE_DB_ERROR = "ECO_004"
ECODE_RCON_FAILED = "ECO_005"

ERROR_MESSAGES = {
    ECODE_PLAYER_NOT_FOUND: "Player not found in economy database",
    ECODE_INSUFFICIENT_FUNDS: "Insufficient funds",
    ECODE_EXCEEDS_MAX: "Amount exceeds maximum per-transaction limit",
    ECODE_DB_ERROR: "Database error",
    ECODE_RCON_FAILED: "RCON command failed (bridge will fallback to DB)",
}

# ── Safety limits ──────────────────────────────────────────────────

MAX_BALANCE = 1_000_000_000  # 1 billion (sanity cap)
MIN_BALANCE = 0
