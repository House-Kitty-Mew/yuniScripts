"""
pl_chat_interest.py — Interest level tracking and message queuing.

Manages per-persona interest in players and handles proactive
message sending from personas to players.
"""

import random
from datetime import datetime, timezone
from typing import Optional
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
    get_or_create_interest, update_interest, get_queued_messages,
    queue_message, get_known_personas
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_persona_by_uuid
)

log = get_logger()

_INTEREST_DECAY_PER_TICK = -0.5
_QUEUE_NOTIFY_INTERVAL_SECONDS = 300  # 5 minutes


def process_interest_decay(persona_uuid: str, player_uuid: str) -> float:
    """Decay interest level over time when no interaction occurs.

    Args:
        persona_uuid: The persona whose interest is decaying
        player_uuid: The player the persona is interested in

    Returns:
        New interest level after decay
    """
    try:
        return update_interest(persona_uuid, player_uuid, _INTEREST_DECAY_PER_TICK)
    except Exception as e:
        log.error("pl_chat", f"process_interest_decay failed: {e}")
        return 0.0


def can_send_proactive_message(persona_uuid: str, player_uuid: str) -> bool:
    """Check if a persona should send a proactive message to a player.

    Personas with interest > 50 may send messages.
    Higher interest = higher chance (0% to 30% per tick at max interest).

    Args:
        persona_uuid: The persona
        player_uuid: The player

    Returns:
        True if the persona should send a proactive message
    """
    try:
        interest = get_or_create_interest(persona_uuid, player_uuid)
        level = interest.get("interest_level", 25.0)
        if level < 50:
            return False
        chance = (level - 50) / 50.0 * 0.3  # 0% to 30% per tick
        return random.random() < chance
    except Exception as e:
        log.error("pl_chat", f"can_send_proactive_message failed: {e}")
        return False


def generate_proactive_message(persona_uuid: str, player_uuid: str) -> Optional[str]:
    """Generate a proactive message from a persona to a player.

    Uses persona's state to create contextually appropriate messages.

    Args:
        persona_uuid: The persona sending the message
        player_uuid: The player to message

    Returns:
        Formatted message string, or None if persona not found
    """
    try:
        persona = get_persona_by_uuid(persona_uuid)
        if not persona:
            return None
        name = persona.get("name", "A Persona")[:8]
        arch = persona.get("archetype", "adventurer")

        messages = {
            "adventurer": f"{name}: \"I was just thinking about our last chat! "
                           "Found something interesting I wanted to share.\"",
            "merchant": f"{name}: \"Good news! I've got some new stock in. "
                         "Thought you might want first pick.\"",
            "farmer": f"{name}: \"The crops are looking beautiful today. "
                       "Reminded me of you — hope you're doing well!\"",
            "warrior": f"{name}: \"Been training hard. "
                        "If you ever need protection, you know where to find me.\"",
            "mage": f"{name}: \"I had a vision last night... "
                     "You were in it. We should talk.\"",
            "vagabond": f"{name}: \"Hey! Was just passing through and thought of you! "
                          "Let's catch up soon!\"",
            "default": f"{name}: \"Just saying hello! Hope everything's going well.\"",
        }
        return messages.get(arch, messages["default"])
    except Exception as e:
        log.error("pl_chat", f"generate_proactive_message failed: {e}")
        return None


def check_and_queue_messages(persona_uuid: str, player_uuid: str,
                              force: bool = False) -> Optional[dict]:
    """Check if a persona should send a message and queue it if so.

    Args:
        persona_uuid: The persona
        player_uuid: The player
        force: Force sending regardless of interest level (for testing)

    Returns:
        Queued message dict, or None
    """
    try:
        if not persona_uuid or not player_uuid:
            return None

        if force or can_send_proactive_message(persona_uuid, player_uuid):
            msg = generate_proactive_message(persona_uuid, player_uuid)
            if msg:
                return queue_message(player_uuid, persona_uuid, msg,
                                      context_type="proactive")

        # Decay interest
        process_interest_decay(persona_uuid, player_uuid)
        return None
    except Exception as e:
        log.error("pl_chat", f"check_and_queue_messages failed: {e}")
        return None
