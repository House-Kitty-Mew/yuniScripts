"""
pl_announce_handler.py — Command handler for \ah sub, \ah unsub, \ah subs, \ah announces.

Integrates with the chat message queue system so announcements appear
as queued messages when players use \ah qmsg list.
"""

import json, time, traceback, re
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_database import get_db

from .pl_announce_database import (
    subscribe, unsubscribe, get_subscriptions, count_subscriptions,
    get_undelivered_announcements, mark_all_delivered,
    get_announcement_count, log_delivery,
)

log = get_logger()
EXT_NAME = "pl_announce_handler"

# ── Validation patterns ──────────────────────────────────────────────

_PERSONA_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')
_PLAYER_NAME_RE = re.compile(r'^[A-Za-z0-9_]{2,32}$')

# ── Command dispatch ─────────────────────────────────────────────────

def handle_command(parts: list[str], player_name: str = "") -> dict:
    """Handle \ah announce sub-commands.

    Called from the minescript command handler when a player types:
      \ah sub <persona_id>     — subscribe to a persona
      \ah unsub <persona_id>   — unsubscribe from a persona
      \ah subs                 — list subscriptions
      \ah announces            — check pending announcements

    Args:
        parts: Remaining command parts after 'ah announce' (or 'ah sub' etc.)
        player_name: The player issuing the command

    Returns:
        Dict with response data for the minescript client
    """
    if not parts:
        return _help()

    cmd = parts[0].lower()
    args = parts[1:]

    if cmd == "sub":
        return _cmd_sub(args, player_name)
    elif cmd == "unsub":
        return _cmd_unsub(args, player_name)
    elif cmd == "subs":
        return _cmd_subs(player_name)
    elif cmd in ("announces", "pending", "notifications"):
        return _cmd_announces(player_name)
    elif cmd in ("clear", "read"):
        return _cmd_clear(player_name)
    elif cmd == "help":
        return _help()
    else:
        return _help()


def _help() -> dict:
    """Return help text for announce commands."""
    return {
        "type": "response",
        "message": [
            "§6=== Announce System Help ===§r",
            "§e\\ah sub <persona_id>§r  — Subscribe to updates about a persona",
            "§e\\ah unsub <persona_id>§r — Unsubscribe from a persona",
            "§e\\ah subs§r              — List your current subscriptions",
            "§e\\ah announces§r          — Check pending announcements",
            "§e\\ah announces clear§r    — Clear/mark all as read",
        ]
    }


def _cmd_sub(args: list[str], player_name: str) -> dict:
    """Subscribe to a persona."""
    if not args:
        return {"type": "response", "message": ["Â§cUsage: \\ah sub <persona_id>Â§r"]}

    persona_id = args[0].strip()

    # Validate persona_id format
    if not _PERSONA_ID_RE.match(persona_id):
        return {"type": "response", "message": [
            "§cInvalid persona ID. Use letters, numbers, underscores, and hyphens only (max 64 chars).§r"
        ]}

    # Validate player name
    if not _PLAYER_NAME_RE.match(player_name):
        return {"type": "response", "message": [
            "§cInvalid player name.§r"
        ]}

    # Check if persona exists in the database
    if not _persona_exists(persona_id):
        return {"type": "response", "message": [
            f"§cPersona '{persona_id}' not found in the simulated world.§r"
        ]}

    # Check subscription limit (max 20 subscriptions per player)
    current_count = count_subscriptions(player_name)
    if current_count >= 20:
        return {"type": "response", "message": [
            "§cYou can only subscribe to up to 20 personas. "
            "Use §e\\ah unsub <id>§c to remove some first.§r"
        ]}

    # Check if already subscribed
    subs = get_subscriptions(player_name)
    existing = [s for s in subs if s["persona_id"] == persona_id]
    if existing:
        return {"type": "response", "message": [
            f"§eYou are already subscribed to {persona_id}§r"
        ]}

    success = subscribe(player_name, persona_id)
    if success:
        log.info(EXT_NAME, f"Player {player_name} subscribed to {persona_id}")
        return {"type": "response", "message": [
            f"§aSubscribed to {persona_id}! You'll receive updates about their major events.§r",
            "§7Use §e\\ah announces§7 to check for pending updates.§r"
        ]}
    else:
        return {"type": "response", "message": [
            "§cFailed to subscribe. Database error.§r"
        ]}


def _cmd_unsub(args: list[str], player_name: str) -> dict:
    """Unsubscribe from a persona."""
    if not args:
        return {"type": "response", "message": ["Â§cUsage: \\ah unsub <persona_id>Â§r"]}

    persona_id = args[0].strip()

    if not _PERSONA_ID_RE.match(persona_id):
        return {"type": "response", "message": [
            "§cInvalid persona ID.§r"
        ]}

    success = unsubscribe(player_name, persona_id)
    if success:
        log.info(EXT_NAME, f"Player {player_name} unsubscribed from {persona_id}")
        return {"type": "response", "message": [
            f"§aUnsubscribed from {persona_id}.§r"
        ]}
    else:
        return {"type": "response", "message": [
            f"§cYou are not subscribed to '{persona_id}'.§r"
        ]}


def _cmd_subs(player_name: str) -> dict:
    """List current subscriptions."""
    subs = get_subscriptions(player_name)

    if not subs:
        return {"type": "response", "message": [
            "§7You are not subscribed to anyone.",
            "§eUse §6\\ah sub <persona_id>§e to subscribe.§r"
        ]}

    lines = [
        f"§6=== Your Subscriptions ({len(subs)}/20) ===§r"
    ]
    for sub in subs:
        pid = sub["persona_id"]
        ts = sub["created_at"]
        lines.append(f"  §e{pid}§r §7(subscribed: {_format_ts(ts)})§r")

    lines.append(f"§7Use §e\\ah announces§7 to check for pending updates.§r")
    return {"type": "response", "message": lines}


def _cmd_announces(player_name: str) -> dict:
    """Return pending announcements."""
    announcements = get_undelivered_announcements(player_name)

    if not announcements:
        return {"type": "response", "message": [
            "§7No pending announcements. §eUse \\ah sub <persona_id>§7 to subscribe.§r"
        ]}

    count = len(announcements)
    from .pl_announce_filter import format_announcement_for_chat

    # Build formatted message
    lines = [
        f"§6=== Pending Announcements ({count}) ===§r"
    ]
    for i, ann in enumerate(announcements, 1):
        formatted = format_announcement_for_chat(ann)
        lines.append(f"§7--- #{i} ---§r")
        lines.append(formatted)

    lines.append(f"§7Use §e\\ah announces clear§7 to mark all as read.§r")
    return {"type": "response", "message": lines}


def _cmd_clear(player_name: str) -> dict:
    """Mark all announcements as read/delivered."""
    announcements = get_undelivered_announcements(player_name)
    count = len(announcements)

    if count == 0:
        return {"type": "response", "message": [
            "§7No pending announcements to clear.§r"
        ]}

    # Log deliveries
    for ann in announcements:
        log_delivery(
            player_name=player_name,
            persona_id=ann["persona_id"],
            event_type=ann["event_type"],
            title=ann["title"],
            interestingness=ann["interestingness"],
        )

    mark_all_delivered(player_name)
    return {"type": "response", "message": [
        f"§aCleared {count} announcement(s).§r"
    ]}


# ── Helpers ──────────────────────────────────────────────────────────

def _persona_exists(persona_id: str) -> bool:
    """Check if a persona exists in the simulated people database."""
    db = None
    try:
        db = get_db()
        row = db.execute(
            "SELECT 1 FROM ext_sp_profiles WHERE persona_id = ? LIMIT 1",
            (persona_id,)
        ).fetchone()
        return row is not None
    except Exception:
        # If the SIMULATED_PEOPLE table doesn't exist, check ah_database instead
        if db is None:
            return True  # db error, can't check either table
        try:
            row = db.execute(
                "SELECT 1 FROM ext_sp_personas WHERE persona_id = ? LIMIT 1",
                (persona_id,)
            ).fetchone()
            return row is not None
        except Exception:
            # Allow subscription even if table doesn't exist yet
            # (the persona might be created later)
            return True


def _format_ts(timestamp: float) -> str:
    """Format a Unix timestamp as a human-readable string."""
    try:
        from datetime import datetime
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%b %d, %H:%M")
    except (ValueError, OSError):
        return "recently"

