"""
ah_protocol.py — Shared constants & message schemas for the Auction House.

Used by both the server-side (mc_manager) and client-side (minescript)
to ensure consistent communication.  Defines the message structures for
all Phooks AH events.
"""

# ──────────────────────────────────────────────────────────────────────
# Event names
# ──────────────────────────────────────────────────────────────────────

# Client → Manager
EVENT_AH_LIST = "ah_list"
EVENT_AH_BID = "ah_bid"
EVENT_AH_BUY = "ah_buy"
EVENT_AH_REMOVE = "ah_remove"
EVENT_AH_QUERY = "ah_query"

# Manager → Client
EVENT_AH_LIST_RESPONSE = "ah_list_response"
EVENT_AH_BID_RESPONSE = "ah_bid_response"
EVENT_AH_BUY_RESPONSE = "ah_buy_response"
EVENT_AH_REMOVE_RESPONSE = "ah_remove_response"
EVENT_AH_QUERY_RESPONSE = "ah_query_response"

# Manager → All
EVENT_AH_ANNOUNCE = "ah_announce"
EVENT_AH_REPORT = "ah_report"

# ──────────────────────────────────────────────────────────────────────
# Message schemas (as dicts showing expected structure)
# ──────────────────────────────────────────────────────────────────────

SCHEMA_AH_LIST = {
    "request_uuid": "str (uuid4)",
    "player_name": "str",
    "item_id": "str (e.g. minecraft:diamond_sword)",
    "item_count": "int (default 1)",
    "item_nbt": "str (optional, full NBT/component data)",
    "signed_name": "str (optional, display name if signed)",
    "rarity": "str (optional, tier string from sign_item.py)",
    "cert_hash": "str (optional, certification hash)",
    "start_price": "float",
    "buy_now_price": "float (optional, 0 = auction only)",
    "duration_hours": "int (optional, default 48)",
}

SCHEMA_AH_LIST_RESPONSE_OK = {
    "status": "'ok'",
    "message": "str",
    "listing_uuid": "str (uuid4)",
    # Optional extras:
    "listed_at": "str (ISO 8601)",
    "expires_at": "str (ISO 8601)",
}

SCHEMA_AH_LIST_RESPONSE_ERROR = {
    "status": "'error'",
    "message": "str (error description)",
    "code": "str (optional, error code like 'AH_002')",
}

SCHEMA_AH_BID = {
    "request_uuid": "str (uuid4)",
    "player_name": "str",
    "listing_uuid": "str (uuid4)",
    "bid_amount": "float",
}

SCHEMA_AH_BID_RESPONSE_OK = {
    "status": "'ok'",
    "message": "str",
    "listing_uuid": "str",
    "new_current_bid": "float",
    "previous_bidder": "str or null",
}

SCHEMA_AH_BUY = {
    "request_uuid": "str (uuid4)",
    "player_name": "str",
    "listing_uuid": "str (uuid4)",
    "quantity": "int (default 1)",
}

SCHEMA_AH_BUY_RESPONSE_OK = {
    "status": "'ok'",
    "message": "str",
    "listing_uuid": "str",
    "item_id": "str",
    "item_count": "int",
    "price": "float",
    "seller": "str",
    "buyer": "str",
    "seller_payout": "float",
    "fee": "float",
}

SCHEMA_AH_REMOVE = {
    "request_uuid": "str (uuid4)",
    "player_name": "str",
    "listing_uuid": "str (uuid4)",
}

SCHEMA_AH_REMOVE_RESPONSE_OK = {
    "status": "'ok'",
    "message": "str",
    "listing_uuid": "str",
    "item_id": "str",
    "item_count": "int",
    "had_bids": "bool",
    "cancel_fee": "float",
}

SCHEMA_AH_QUERY = {
    "request_uuid": "str (uuid4)",
    "player_name": "str",
    "filter_type": "str: 'all'|'my'|'item:<id>'|'player:<name>'|'category:<cat>'|'report'",
    "filter_value": "str (optional)",
    "page": "int (default 1)",
    "per_page": "int (default 10)",
}

SCHEMA_AH_QUERY_RESPONSE_OK = {
    "status": "'ok'",
    "listings": "list[dict]",
    "total": "int",
    "page": "int",
    "per_page": "int",
    "total_pages": "int",
}

SCHEMA_AH_SIM_STATUS = SCHEMA_AH_SIM_STATUS = {
    "scheduler": {
        "running": "bool",
        "interval_minutes": "int",
        "enabled": "bool",
    },
    "last_run": {
        "time": "str (ISO 8601) or null",
        "result": "dict or null",
    },
    "market": {
        "active_listings": "int",
        "player_listings": "int",
        "sim_listings": "int",
        "tx_24h": "int",
        "volume_24h": "float",
    },
    "events": "list[dict]",
    "notes": "list[dict]",
    "personas": {
        "active_count": "int",
        "tier": "str",
    },
    "board": {
        "open_needs": "int",
    },
}


SCHEMA_AH_ANNOUNCE = {
    "type": "str: 'market_event'|'report'|'outbid'|'sold'",
    "title": "str",
    "message": "str",
    "affected_items": "list[str] (optional)",
    "multiplier": "float (optional)",
    "duration": "str (optional, human-readable)",
}

# ──────────────────────────────────────────────────────────────────────
# Error codes
# ──────────────────────────────────────────────────────────────────────

ERROR_CODES = {
    "AH_001": "Item already has an active listing",
    "AH_002": "Max listings reached (see config)",
    "AH_003": "Insufficient emeralds",
    "AH_004": "Item not found in inventory",
    "AH_005": "Bid too low (must exceed by 10%)",
    "AH_006": "Auction expired — item returned to seller",
    "AH_007": "Not your listing to cancel",
    "AH_008": "AI simulation failed — check logs",
    "AH_009": "DeepSeek API key not configured",
    "AH_010": "Cannot bid on your own listing",
    "AH_011": "BIN price not set (auction only)",
    "AH_012": "Market event has no effect on this item",
    "AH_013": "Item data too large for Phooks packet",
    "AH_014": "Listing not found",
}

# ──────────────────────────────────────────────────────────────────────
# Filter values for query
# ──────────────────────────────────────────────────────────────────────

FILTER_TYPES = frozenset({
    "all", "my", "item", "player", "category", "simulated", "report"
})

# ──────────────────────────────────────────────────────────────────────
# Listing statuses
# ──────────────────────────────────────────────────────────────────────

LISTING_STATUSES = frozenset({
    "active", "expired", "sold", "cancelled", "pending"
})

# ── Chat events (SIMULATED_CHAT extension) ────────────────────────────
# Client → Manager
EVENT_AH_MSG = "ah_msg"
EVENT_AH_QMSG = "ah_qmsg"
# Manager → Client
EVENT_AH_MSG_RESPONSE = "ah_msg_response"
# Payloads:
#   ah_msg:       {"player_name": str, "args": [<persona_id>, <message>]}
#   ah_msg_resp:  {"player_name": str, "response": str, "status": "ok"|"error"}
#   ah_qmsg:      {"player_name": str, "args": ["list"|"read"|"reply", ...]}
