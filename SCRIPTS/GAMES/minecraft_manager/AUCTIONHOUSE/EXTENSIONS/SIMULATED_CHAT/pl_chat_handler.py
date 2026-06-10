"""
pl_chat_handler.py — Command handler for player-to-persona chat.

Processes \ah msg and \ah qmsg commands from the game's command system.
"""

from typing import Optional
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
    get_or_create_connection, update_connection, create_conversation,
    get_active_conversation, log_message, get_known_personas,
    get_queued_messages, mark_message_read, reply_to_queued,
    queue_message, update_interest, get_or_create_interest
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response

log = get_logger()


def handle_command(parts: list[str], player_uuid: str = "player_terminal") -> dict:
    """Main entry point for \ah msg and \ah qmsg commands.

    Args:
        parts: Split command parts from the stdio handler
        player_uuid: UUID of the player sending the command (defaults to
                     'player_terminal' as fallback when called from terminal)

    Returns:
        Dict with response data for the command result
    """
    try:
        if len(parts) < 1:
            return {"error": "missing sub-command", "usage": "ah msg|qmsg ..."}

        sub = parts[0].lower()
        if sub == "msg":
            return _handle_msg(parts[1:], player_uuid)
        elif sub == "qmsg":
            return _handle_qmsg(parts[1:], player_uuid)
        else:
            return {"error": f"unknown chat sub: {sub}",
                    "usage": "ah msg|qmsg"}
    except Exception as e:
        log.error("pl_chat", f"handle_command failed: {e}")
        return {"error": f"command processing failed: {e}"}


def _handle_msg(args: list[str], player_uuid: str = "player_terminal") -> dict:
    """Handle \ah msg <persona> [message...]

    Args:
        args: Command arguments (persona_id and optional message)
        player_uuid: UUID of the player sending the message

    Returns:
        Dict with status, response, and context_type
    """
    try:
        if not args:
            return {"error": "missing arguments",
                    "usage": "ah msg [persona_id|list] [message...]"}

        if args[0] == "list":
            personas = get_known_personas()
            items = [{"uuid": p["persona_uuid"], "name": p["name"],
                       "archetype": p["archetype"], "job": p["job"]}
                     for p in personas]
            return {"status": "ok", "personas": items, "count": len(items)}

        persona_id = args[0]
        message = " ".join(args[1:]) if len(args) > 1 else ""

        if not message:
            return {"status": "ok",
                    "prompt": f"Messaging {persona_id}... await response."}

        # Get or create connection
        conn = get_or_create_connection(player_uuid, persona_id)
        update_connection(player_uuid, persona_id)

        # Get or create conversation
        conv = get_active_conversation(player_uuid, persona_id)
        is_first = not conv
        if not conv:
            conv_id = create_conversation(player_uuid, persona_id)
        else:
            conv_id = conv["id"]

        # Log player message
        log_message(conv_id, "player", player_uuid, message)

        # Generate response
        context = "first" if is_first else None
        result = generate_response(persona_id, player_uuid, message,
                                    conversation_context=context)

        # Log persona response
        log_message(conv_id, "persona", persona_id,
                    result.get("response", "..."),
                    result.get("stat_changes"))

        # Update interest
        if result.get("stat_changes"):
            sc = result["stat_changes"]
            if "interest_delta" in sc:
                update_interest(persona_id, player_uuid, sc["interest_delta"])

        return {
            "status": "ok",
            "response": result.get("response", "..."),
            "context_type": result.get("context_type"),
        }
    except Exception as e:
        log.error("pl_chat", f"_handle_msg failed: {e}")
        return {"error": f"message handling failed: {e}"}


def _handle_qmsg(args: list[str], player_uuid: str = "player_terminal") -> dict:
    """Handle \ah qmsg list|read|reply

    Args:
        args: Command arguments
        player_uuid: UUID of the player

    Returns:
        Dict with result data
    """
    try:
        if not args:
            return {"error": "missing arguments",
                    "usage": "ah qmsg list|read <id>|reply <id> <message>"}
        cmd = args[0].lower()

        if cmd == "list":
            messages = get_queued_messages(player_uuid)
            items = [{"id": m["id"],
                       "from": m.get("persona_name", m["persona_uuid"][:8]),
                       "content": m["content"][:100],
                       "unread": not m["is_read"],
                       "time": m["created_at"]}
                     for m in messages]
            return {"status": "ok", "messages": items, "count": len(items)}

        elif cmd == "read":
            if len(args) < 2:
                return {"error": "missing message id",
                        "usage": "ah qmsg read <id>"}
            try:
                msg_id = int(args[1])
            except ValueError:
                return {"error": f"invalid message id: {args[1]}"}
            mark_message_read(msg_id)
            messages = get_queued_messages(player_uuid, unread_only=False)
            for m in messages:
                if m["id"] == msg_id:
                    return {"status": "ok", "message": m["content"],
                            "from": m.get("persona_name", m["persona_uuid"][:8]),
                            "time": m["created_at"]}
            return {"error": f"message {msg_id} not found"}

        elif cmd == "reply":
            if len(args) < 3:
                return {"error": "missing arguments",
                        "usage": "ah qmsg reply <id> <message>"}
            try:
                msg_id = int(args[1])
            except ValueError:
                return {"error": f"invalid message id: {args[1]}"}
            reply = " ".join(args[2:])
            replied = reply_to_queued(msg_id, reply)
            if not replied:
                return {"error": f"message {msg_id} not found"}
            return {"status": "ok",
                    "reply": f"Reply sent to message {msg_id}."}

        else:
            return {"error": f"unknown qmsg sub: {cmd}",
                    "usage": "ah qmsg list|read|reply"}
    except Exception as e:
        log.error("pl_chat", f"_handle_qmsg failed: {e}")
        return {"error": f"queued message handling failed: {e}"}
