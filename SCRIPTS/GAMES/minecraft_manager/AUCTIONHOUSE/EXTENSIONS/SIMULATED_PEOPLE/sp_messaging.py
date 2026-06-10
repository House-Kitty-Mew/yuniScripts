"""
sp_messaging.py — AI-generated persona messages for player purchases.

When a simulated persona buys an item from a player's listing, the AI
(DeepSeek API) generates a personalized message from the persona to the
player.  The message reflects:
  - The persona's archetype/personality
  - The item purchased
  - The price paid
  - Current life events or mood

If the DeepSeek API is unavailable, fall back to archetype-specific
message templates that still feel personalized.

Messages are sent to the player via /tellraw RCON command with the
format::
    §7[AH] §f{persona} bought 1x {item} for {price}em
    §o§7"{message}"
"""

import json, random
from typing import Optional, Callable

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
    get_db, add_player_message, get_active_life_events
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area

log = get_logger()


# ── Archetype-specific message templates (fallback) ─────────────────

_MESSAGE_TEMPLATES = {
    "adventurer": [
        "This {item} will serve me well on my next expedition! Thanks, traveler.",
        "Lost my old {item} in a creeper explosion. This one looks sturdier. Appreciate it.",
        "Headed into the deep caves tomorrow — this {item} might be what keeps me alive.",
    ],
    "merchant": [
        "A fair price for quality {item}. If you come across more, I'm always buying.",
        "The market's been unpredictable lately. This {item} is a solid investment.",
        "Good doing business with you. The {item} arrived in excellent condition.",
    ],
    "builder": [
        "Perfect! This {item} is exactly what I needed for the town hall expansion.",
        "Quality materials are getting harder to find. Glad I caught this listing.",
        "My crew was running low on {item}. You've saved the project timeline!",
    ],
    "miner": [
        "Been looking for {item} for weeks. This saves me a trip to the deep caves.",
        "My last {item} broke at Y=-45. Nearly lost all my findings. Thank you.",
        "Good price on {item}. The mines have been stingy lately.",
    ],
    "farmer": [
        "The harvest is coming in strong this season. Needed this {item} for repairs.",
        "My chickens will be happy about this. Wait, is that weird to say?",
        "Good {item} is hard to come by in these parts. Fair trade.",
    ],
    "warrior": [
        "{item}. Finally. My old one had more holes than edge. Payment sent.",
        "The arena doesn't wait for anyone. This {item} will do nicely.",
        "Smart move listing this. I've been saving up for just this kind of deal.",
    ],
    "mage": [
        "The arcane properties of this {item} are surprisingly potent. A worthy purchase.",
        "I've been researching {item} for my latest thesis. This sample is perfect.",
        "Fascinating craftsmanship. The enchantments on this {item} will serve my studies.",
    ],
    "vagabond": [
        "Hey! Thanks for the {item}. Lost mine in a card game. Don't ask.",
        "Found some coins in an old chest. Spent them on this. Worth it.",
        "Needed {item} for the road. Appreciate the quick sale!",
    ],
}

_MOOD_MODIFIERS = {
    "happy": [" 😊", " Couldn't be happier!", " Made my day!", ""],
    "stressed": [" Been a rough week.", " Barely scraped the coins together.", " ...worth it though."],
    "motivated": [" Big plans ahead!", " This is just the beginning.", " Onward!"],
    "cautious": [" Let's see how this holds up.", " Hopefully this lasts.", " Price was right."],
}

_ITEM_FRIENDLY_NAMES = {}


def _friendly_name(item_id: str) -> str:
    """Convert minecraft:item_id to a friendly name."""
    if item_id in _ITEM_FRIENDLY_NAMES:
        return _ITEM_FRIENDLY_NAMES[item_id]
    if ":" in item_id:
        short = item_id.split(":")[1]
    else:
        short = item_id
    name = short.replace("_", " ").title()
    _ITEM_FRIENDLY_NAMES[item_id] = name
    return name


def generate_template_message(persona: dict, item_id: str, price: float) -> str:
    """Generate a persona message using archetype templates.

    Used as fallback when DeepSeek API is unavailable.

    Args:
        persona: Persona dict with name, archetype, etc.
        item_id: The item purchased
        price: The price paid

    Returns:
        Message string (without quotes)
    """
    archetype = persona.get("archetype", "adventurer")
    templates = _MESSAGE_TEMPLATES.get(archetype, _MESSAGE_TEMPLATES["adventurer"])
    template = random.choice(templates)
    item_name = _friendly_name(item_id)
    msg = template.format(item=item_name)

    # Add mood modifier from life events
    life_events = get_active_life_events(persona["persona_uuid"])
    if life_events:
        mood = life_events[0].get("mood_impact", "neutral")
        modifiers = _MOOD_MODIFIERS.get(mood, [""])
        msg += random.choice(modifiers)

    return msg


def generate_ai_message(persona: dict, item_id: str, price: float) -> str:
    """Generate a persona message using DeepSeek API.

    This is the primary method.  Templates are fallback.

    Args:
        persona: Persona dict
        item_id: The item purchased
        price: The price paid

    Returns:
        Message text
    """
    # Try DeepSeek API first
    archetype = persona.get("archetype", "adventurer")
    name = persona.get("name", "Someone")
    item_name = _friendly_name(item_id)

    # Build a compact prompt
    prompt = (
        f"Write a 1-2 sentence message from a Minecraft {archetype} "
        f"named {name} who just bought {item_name} for {price} emeralds "
        f"from another player's Auction House listing. "
        f"They are {persona.get('wealth_tier', 'working')} in wealth. "
        f"Make it sound like something their personality type would say "
        f"— {'formal and shrewd' if archetype == 'merchant' else 'casual and poetic' if archetype == 'vagabond' else 'warm and fitting their role'}. "
        f"Use first person. Keep it short. No quotes."
    )

    try:
        from AUCTIONHOUSE.ah_ai_engine import _call_deepseek
        system = "You are a Minecraft villager writing a short thank-you message after buying from the Auction House. Be brief, in-character, and creative."
        response = _call_deepseek(prompt, system_prompt=system)
        if response and "market_assessment" in response:
            # The AI returned a full analysis instead of just a message
            # Fall through to template
            pass
        elif response and response.get("choices"):
            # Parse standard OpenAI-style response
            content = response["choices"][0]["message"]["content"]
            if content and len(content) < 300:
                return content.strip()
    except Exception:
        log.debug("sp_messaging", "DeepSeek API unavailable, using template fallback")

    # Fallback
    return generate_template_message(persona, item_id, price)


def send_purchase_message(persona: dict, item_id: str, price: float,
                           player_name: str, listing_uuid: str,
                           transaction_uuid: str,
                           rcon_func: Optional[Callable] = None):
    """Generate and send a persona's purchase message to the player.

    Args:
        persona: Persona dict
        item_id: Minecraft item ID
        price: Price paid
        player_name: Player who listed the item
        listing_uuid: The listing UUID
        transaction_uuid: UUID for this transaction
        rcon_func: Optional RCON function for tellraw
    """
    cfg = get_config()
    if not cfg.get("enabled", True):
        return

    message = generate_ai_message(persona, item_id, price)
    item_name = _friendly_name(item_id)
    persona_name = persona.get("name", "Someone")

    # Store in database
    add_player_message(
        persona["persona_uuid"],
        player_name,
        message,
        transaction_uuid or listing_uuid,
    )

    # Send to player via RCON tellraw
    if rcon_func:
        try:
            tellraw = json.dumps([
                {"text": f"§7[AH] §f{persona_name} bought 1x {item_name} for {price:.2f}em"},
                {"text": f"\n§7§o\"{message}\"", "italic": True, "color": "gray"},
            ])
            rcon_func(f"tellraw {player_name} {tellraw}")
            log.info("sp_messaging",
                     f"Message sent: {persona_name} → {player_name}: \"{message[:60]}...\"")
        except Exception as e:
            log.error("sp_messaging", f"Failed to send tellraw: {e}")


# ══════════════════════════════════════════════════════════════════════
# Board Thank-You Messages
# ══════════════════════════════════════════════════════════════════════

_THANK_YOU_TEMPLATES = {
    "adventurer": [
        "Thank you! I was about to head into the deep caves empty-handed.",
        "This is perfect. I owe you one!",
        "The caves are dangerous without proper gear. Really appreciate this.",
    ],
    "merchant": [
        "Fair trade. If you're ever looking to sell more, I'm always buying.",
        "Good doing business with you. The quality is excellent.",
        "You saved me a trip to the market. Well worth it.",
    ],
    "builder": [
        "This is exactly what I needed for the town hall!",
        "My project was stalled without this. Thank you!",
        "Quality materials are hard to find. You're a godsend.",
    ],
    "miner": [
        "My pick broke at Y=-45. I thought I was done for. Thank you!",
        "The mines have been brutal lately. This will last me a good week.",
        "Found diamonds but couldn't reach them. Now I can! Thanks.",
    ],
    "farmer": [
        "The harvest is coming in and I couldn't do it without this. Thanks!",
        "My tools were falling apart. You've saved the season's crops.",
        "Fair price for good equipment. The farm thanks you too!",
    ],
    "warrior": [
        "The raid is tonight. You arrived just in time.",
        "I'll put this to good use. If you ever need protection, I owe you.",
        "Solid gear. The mobs won't know what hit them.",
    ],
    "mage": [
        "The arcane energies in this are perfect for my research. Thank you!",
        "I've been seeking this for weeks. Excellent craftsmanship.",
        "This will channel enchantments beautifully. Thanks!",
    ],
    "vagabond": [
        "Hey, thanks! Lost my last one in a card game. This is better anyway.",
        "Found a few coins, saw your listing, had to have it. Appreciate it!",
        "You're a good sort. If you ever pass through my camp, I'll share my stew.",
    ],
}

_DEFAULT_THANK_YOUS = [
    "Thank you! This was exactly what I needed.",
    "You're a lifesaver! I was starting to worry.",
    "Much appreciated. I won't forget this favor.",
    "Perfect timing! Thank you for the quick sale.",
]

def generate_board_thank_you(entry: dict) -> str:
    """Generate a thank-you message for a fulfilled board entry."""
    archetype = entry.get("archetype", entry.get("persona_archetype", "adventurer"))
    templates = _THANK_YOU_TEMPLATES.get(archetype, _DEFAULT_THANK_YOUS)
    return random.choice(templates)
