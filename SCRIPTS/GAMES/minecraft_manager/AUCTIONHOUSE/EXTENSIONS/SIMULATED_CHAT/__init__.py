"""
SIMULATED_CHAT — Player-to-persona chat system.

Enables real-time chat between players and simulated personas via
the \ah msg and \ah qmsg command system.

Features:
  - Immersive AI responses based on persona stats and memories
  - Three-tier memory integration (player conversations stored)
  - Interest level tracking (0-100) per player-persona pair
  - Proactive message queuing from personas to players
  - Stat changes based on player interactions (gifts, insults)
  - Connection tracking for long-term relationships
"""

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()
EXTENSION_NAME = "SIMULATED_CHAT"


def on_load(registry):
    """Register hooks with the AH plugin registry."""
    from .pl_chat_database import ensure_schema
    ensure_schema()
    log.info("pl_chat",
             f"Extension '{EXTENSION_NAME}' loaded — "
             f"player-to-persona chat active")


def handle_command(parts: list[str]) -> dict:
    """Handle \ah msg and \ah qmsg commands.

    This is called from the minecraft_manager's stdio handler
    when it receives an 'ah' command with a msg or qmsg sub-command.

    Args:
        parts: The remaining command parts after 'ah'

    Returns:
        Dict with response data
    """
    from .pl_chat_handler import handle_command as handler
    return handler(parts)
