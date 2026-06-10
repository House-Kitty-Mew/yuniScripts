"""
ah_announcer.py — In-game announcement system via RCON.

Sends Minecraft chat/title messages to players using the mc_manager's
RCON capabilities.  Provides:
  - broadcast()       — Send a tellraw message to all players
  - tell_player()     — Send a private tellraw to a specific player
  - broadcast_title() — Send a title/subtitle to all players
  - announce_event()  — Announce a market event start/end
  - announce_ai_report() — Announce an AI-generated market report
  - notify_outbid()   — Notify a player they've been outbid

All functions accept an optional ``rcon_func`` argument so the caller
can inject their RCON command function (e.g. from mc_manager's main.py).
If not provided, tries to use an internal RCON client configured via ah_config.
"""

import json, socket, struct
from datetime import datetime, timezone
from typing import Optional, Callable

from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()
cfg = get_config

# ──────────────────────────────────────────────────────────────────────
# Internal RCON command (fallback if no rcon_func provided)
# ──────────────────────────────────────────────────────────────────────

def _rcon_command(cmd: str, timeout: float = 3.0) -> Optional[str]:
    """Execute an RCON command using config credentials.

    This is a fallback for when the caller doesn't provide their own
    rcon_func.  Uses the same protocol as mc_manager's built-in RCON.
    """
    config = cfg()
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((config.rcon_host, config.rcon_port))

        # Auth
        auth_pkt = struct.pack('<ii', 0, 3) + config.rcon_password.encode() + b'\x00\x00'
        sock.sendall(struct.pack('<i', len(auth_pkt)) + auth_pkt)
        _ = sock.recv(4)
        resp_data = sock.recv(4096)
        if len(resp_data) < 10:
            return None

        # Command
        cmd_bytes = cmd.encode()
        cmd_pkt = struct.pack('<ii', 2, 2) + cmd_bytes + b'\x00\x00'
        sock.sendall(struct.pack('<i', len(cmd_pkt)) + cmd_pkt)

        # Response
        len_data = sock.recv(4)
        if len(len_data) < 4:
            return None
        length = struct.unpack('<i', len_data)[0]
        response = b''
        while len(response) < length:
            chunk = sock.recv(length - len(response))
            if not chunk:
                break
            response += chunk
        if response:
            return response[4:-2].decode('utf-8', errors='replace')
        return None
    except Exception as e:
        log.error("announcer", f"RCON command failed: {e}")
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


# ──────────────────────────────────────────────────────────────────────
# Message builders
# ──────────────────────────────────────────────────────────────────────

def _tellraw_json(text: str, color: str = "white", bold: bool = False,
                  italic: bool = False) -> str:
    """Build a tellraw JSON component."""
    comp = {"text": text, "color": color, "bold": bold, "italic": italic}
    return json.dumps([comp])


def _tellraw_components(*components: dict) -> str:
    """Build a tellraw from multiple components."""
    return json.dumps(list(components))


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def broadcast(message: str, color: str = "gold",
              prefix: str = "§6[AH] ",
              rcon_func: Optional[Callable] = None) -> bool:
    """Broadcast a message to all online players via /tellraw @a.

    Args:
        message: The message text
        color: Minecraft color code name
        prefix: Prefix prepended to the message
        rcon_func: Optional RCON command function

    Returns:
        True if the broadcast was sent successfully
    """
    full_text = f"{prefix}{message}"
    cmd = f'tellraw @a {_tellraw_json(full_text, color=color)}'
    return _send(cmd, rcon_func)


def tell_player(player: str, message: str, color: str = "gold",
                rcon_func: Optional[Callable] = None) -> bool:
    """Send a private message to a specific player.

    Args:
        player: Minecraft player name
        message: Message text
        color: Minecraft color code name
        rcon_func: Optional RCON command function

    Returns:
        True if successful
    """
    cmd = f'tellraw {player} {_tellraw_json(f"§6[AH] {message}", color=color)}'
    return _send(cmd, rcon_func)


def broadcast_title(title: str, subtitle: Optional[str] = None,
                    rcon_func: Optional[Callable] = None) -> bool:
    """Send a title to all players.

    Args:
        title: The main title text
        subtitle: Optional subtitle text
        rcon_func: Optional RCON command function

    Returns:
        True if successful
    """
    ok = True
    if title:
        ok &= _send(f'title @a title {json.dumps(title)}', rcon_func)
    if subtitle:
        ok &= _send(f'title @a subtitle {json.dumps(subtitle)}', rcon_func)
    ok &= _send('title @a times 10 70 20', rcon_func)  # fade in 0.5s, stay 3.5s, fade out 1s
    return ok


def announce_event(action: str, event_title: str, event_flavor: str,
                   rarity_tier: str, affected_items: list[str],
                   rcon_func: Optional[Callable] = None) -> bool:
    """Announce a market event start or end.

    Args:
        action: 'start' or 'end'
        event_title: Player-facing event title
        event_flavor: Flavor text
        rarity_tier: 'small', 'medium', 'rare', 'major'
        affected_items: List of affected item IDs
        rcon_func: Optional RCON command function

    Returns:
        True if all messages sent successfully
    """
    tier_colors = {"small": "§7", "medium": "§e", "rare": "§c", "major": "§4"}
    tier_icons = {"small": "•", "medium": "★", "rare": "✦", "major": "◆"}
    color = tier_colors.get(rarity_tier, "§6")
    icon = tier_icons.get(rarity_tier, "•")

    if action == "start":
        # Title for important events
        if rarity_tier in ("rare", "major"):
            broadcast_title(
                title=f"{color}{icon} {event_title} {icon}",
                subtitle=f"§7{event_flavor[:80]}",
                rcon_func=rcon_func
            )
        else:
            broadcast(
                f"{color}{icon} {event_title}",
                color="gold",
                rcon_func=rcon_func
            )
            broadcast(
                f"§7{event_flavor}",
                color="gray",
                rcon_func=rcon_func
            )

        # Show affected items
        item_names = ", ".join(a.split(":")[1] if ":" in a else a for a in affected_items)
        broadcast(
            f"§7Affected items: §f{item_names}",
            color="gray",
            rcon_func=rcon_func
        )

        log.info("announcer", f"Event announcement sent: {event_title} ({action})",
                 {"rarity": rarity_tier})

    elif action == "end":
        broadcast(
            f"§aThe event \"{event_title}\" has concluded. Prices are returning to normal.",
            color="green",
            rcon_func=rcon_func
        )

    return True


def announce_ai_report(summary: str, highlights: list[str],
                       rcon_func: Optional[Callable] = None) -> bool:
    """Announce an AI-generated market report to players.

    Args:
        summary: 1-2 sentence market overview
        highlights: List of bullet-point highlights
        rcon_func: Optional RCON command function

    Returns:
        True if sent successfully
    """
    broadcast("§6═══ §e📊 Market Report §6═══", color="gold", rcon_func=rcon_func)
    broadcast(f"§7{summary}", color="gray", rcon_func=rcon_func)

    for h in highlights[:5]:  # Max 5 highlights
        broadcast(f" §7• §f{h}", color="white", rcon_func=rcon_func)

    broadcast("§6══════════════════════", color="gold", rcon_func=rcon_func)
    return True


def notify_outbid(player: str, item_name: str, new_bid: float,
                  rcon_func: Optional[Callable] = None) -> bool:
    """Notify a player they've been outbid on an item.

    Args:
        player: The player who was outbid
        item_name: Name of the item they were outbid on
        new_bid: The new current bid
        rcon_func: Optional RCON command function

    Returns:
        True if sent successfully
    """
    msg = f"§cYou've been outbid on §f{item_name}§c! New highest bid: §e{new_bid:.2f} emeralds"
    return tell_player(player, msg, color="red", rcon_func=rcon_func)


def notify_sold(player: str, item_name: str, price: float,
                rcon_func: Optional[Callable] = None) -> bool:
    """Notify a seller that their item sold.

    Args:
        player: The seller
        item_name: Name of the item sold
        price: Sale price
        rcon_func: Optional RCON command function

    Returns:
        True if sent successfully
    """
    msg = f"§aYour §f{item_name} §asold for §e{price:.2f} emeralds§a!"
    return tell_player(player, msg, color="green", rcon_func=rcon_func)


def notify_won(player: str, item_name: str, price: float,
               rcon_func: Optional[Callable] = None) -> bool:
    """Notify a player they won an auction.

    Args:
        player: The winning bidder
        item_name: Name of the item
        price: Winning price
        rcon_func: Optional RCON command function

    Returns:
        True if sent successfully
    """
    msg = f"§6You won the auction for §f{item_name}§6! Price: §e{price:.2f} emeralds"
    return tell_player(player, msg, color="gold", rcon_func=rcon_func)


def _send(cmd: str, rcon_func: Optional[Callable] = None) -> bool:
    """Send a command via RCON.

    Uses the provided rcon_func, or falls back to the internal RCON client.
    """
    try:
        if rcon_func is not None:
            rcon_func(cmd)
        else:
            _rcon_command(cmd)
        return True
    except Exception as e:
        log.error("announcer", f"Failed to send RCON command: {e}")
        return False

