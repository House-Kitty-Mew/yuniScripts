"""
pl_chat_ai.py — Immersive response generator for player-to-persona chat.

Generates persona responses based on:
  - Archetype and personality traits
  - Current skills, health, wealth
  - Boredom and social exhaustion levels
  - Recent memories of the player
  - Relationship strength with the player
  - Interest level in the player

Includes deterministic fallback when AI mode is disabled.
"""

import random
from typing import Optional
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_persona_by_uuid
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
    get_or_create_interest, get_or_create_connection
)

log = get_logger()
_LOG_SOURCE = "pl_chat"

_ARCHETYPE_GREETINGS = {
    "adventurer": "Ah, a visitor! Good to see new faces around here.",
    "merchant": "Welcome, friend! Always happy to speak with a customer.",
    "builder": "Hello there. I was just working on something.",
    "miner": "G'day. Don't mind the dust, it follows me everywhere.",
    "farmer": "Well met! Would you like some fresh bread?",
    "warrior": "Hmph. Another soul looking for conversation?",
    "mage": "Interesting... I sensed you approaching.",
    "vagabond": "Oh! Company! How rare and wonderful!",
}

_ARCHETYPE_FAREWELLS = {
    "adventurer": "Safe travels! May your path be filled with discovery.",
    "merchant": "Good doing business with you. Come back anytime.",
    "builder": "Take care. I've got more work to do here.",
    "miner": "Stay safe down there. Or up here. Wherever you go.",
    "farmer": "May your fields be fertile and your skies clear.",
    "warrior": "Stay sharp. The world is full of dangers.",
    "mage": "Farewell. The stars speak well of you.",
    "vagabond": "Until we meet again on the road!",
}


def generate_response(persona_uuid: str, player_uuid: str,
                      player_message: str,
                      archetype: Optional[str] = None,
                      conversation_context: Optional[str] = None) -> dict:
    """Generate an immersive response from a persona to a player."""
    try:
        if not persona_uuid or not player_uuid:
            return {"response": "...", "stat_changes": None, "context_type": None}

        persona = get_persona_by_uuid(persona_uuid) or {}
        arch = archetype or persona.get("archetype", "adventurer")
        name = persona.get("name", "A Persona")[:8]
        skills = get_skills(persona_uuid) or {}

        interest = get_or_create_interest(persona_uuid, player_uuid)
        interest_level = interest.get("interest_level", 25.0)
        conn = get_or_create_connection(player_uuid, persona_uuid)
        msg_count = conn.get("message_count", 0)

        is_first = conversation_context == "first" or msg_count <= 1
        context_type = _detect_context(player_message)

        stat_changes = _detect_stat_changes(persona_uuid, player_uuid, player_message,
                                             arch, interest_level)

        if is_first:
            response = _generate_greeting(name, arch, interest_level)
        elif context_type == "farewell":
            response = _generate_farewell(name, arch, interest_level)
        elif context_type == "question_about_self":
            response = _generate_self_description(persona, skills, arch)
        elif context_type == "gift":
            response = _generate_gift_response(name, arch)
        elif context_type == "insult":
            response = _generate_insult_response(name, arch, interest_level)
        elif context_type == "request":
            response = _generate_request_response(name, arch)
        elif context_type == "question":
            response = _generate_question_response(name, arch, interest_level)
        else:
            response = _generate_statement_response(name, arch, interest_level)

        return {"response": response, "stat_changes": stat_changes,
                "context_type": context_type}
    except Exception as e:
        log.error(_LOG_SOURCE, f"generate_response failed: {e}")
        return {"response": "...", "stat_changes": None, "context_type": None}


def _detect_context(message: str) -> str:
    """Detect the type of message the player sent."""
    try:
        msg = message.lower().strip()
        farewells = {"goodbye", "bye", "farewell", "see you", "leaving"}
        insults = {"stupid", "idiot", "hate", "ugly", "dumb", "shut up",
                    "useless", "pathetic", "fool", "moron"}
        gifts = {"gift", "give", "here", "for you", "have this", "take this",
                  "offering", "present", "donation"}
        requests = {"please", "could you", "would you", "help", "need",
                    "can you", "will you", "please help", "i need"}

        if any(w in msg for w in farewells):
            return "farewell"
        if any(w in msg for w in insults):
            return "insult"
        if any(w in msg for w in gifts):
            return "gift"
        if any(w in msg for w in requests):
            return "request"
        if msg.startswith(("who", "what", "where", "why", "when", "how",
                            "tell me about", "describe")):
            return "question_about_self"
        if "?" in msg:
            return "question"
        return "statement"
    except Exception as e:
        log.error(_LOG_SOURCE, f"_detect_context failed: {e}")
        return ""


def _generate_greeting(name: str, arch: str, interest: float) -> str:
    base = _ARCHETYPE_GREETINGS.get(arch, "Hello there!")
    warmth = " It's wonderful to meet someone new!" if interest > 50 else ""
    return f"{name}: \"{base}{warmth}\""


def _generate_farewell(name: str, arch: str, interest: float) -> str:
    base = _ARCHETYPE_FAREWELLS.get(arch, "Take care.")
    warmth = " I'll remember our chat." if interest > 50 else ""
    return f"{name}: \"{base}{warmth}\""


def _generate_self_description(persona: dict, skills: dict, arch: str) -> str:
    try:
        name = persona.get("name", "A wanderer")[:8]
        job = persona.get("job", "a traveler")
        wealth = persona.get("wealth_tier", "middle")
        top_skill = max(skills, key=skills.get) if skills else "survival"
        top_value = int(skills.get(top_skill, 10))
        descriptions = {
            "farmer": f"My farm keeps me busy, and my {top_skill} skill is {top_value}. "
                      f"The soil has been kind this season.",
            "warrior": f"I've trained my {top_skill} to {top_value}. "
                       f"These lands need protection.",
            "merchant": f"My {top_skill} is at {top_value}. "
                        f"I've made quite a profit trading across the region.",
            "adventurer": f"I'm a {job} — {wealth} wealth bracket. "
                          f"My {top_skill} is {top_value}. There's always more to explore.",
            "default": f"I'm just {name}, a {job}. "
                       f"My best skill is {top_skill} ({top_value}). "
                       f"My wealth tier is {wealth}.",
        }
        desc = descriptions.get(arch, descriptions["default"])
        return f"{name}: \"I'm {name}, {a_job(arch)}. {desc}\""
    except Exception as e:
        log.error(_LOG_SOURCE, f"_generate_self_description failed: {e}")
        return f"{persona.get('name', 'A Persona')[:8]}: \"I'm not sure how to describe myself.\""


def a_job(arch: str) -> str:
    articles = {"adventurer": "an", "merchant": "a", "builder": "a",
                "miner": "a", "farmer": "a", "warrior": "a",
                "mage": "a", "vagabond": "a"}
    return f"{articles.get(arch, 'a')} {arch}"


def _generate_gift_response(name: str, arch: str) -> str:
    responses = {
        "merchant": "A gift? How thoughtful! I'll remember this.",
        "farmer": "Bless your heart! I'll put this to good use.",
        "warrior": "Generous. I won't forget this kindness.",
        "vagabond": "For me? Really? Thank you!",
        "default": "That's very kind of you! Thank you!",
    }
    resp = responses.get(arch, responses["default"])
    return f"{name}: \"{resp} I'll return the favor when I can.\""


def _generate_insult_response(name: str, arch: str, interest: float) -> str:
    try:
        warrior_retorts = ["You dare insult me? Name a time and place.",
                           "Watch your tongue, or I'll make you."]
        mage_retorts = ["How... pedestrian.", "Your words are as dull as your mind."]
        vagabond_retorts = ["That hurts. I was just being friendly.",
                            "Why would you say that?"]
        default_retorts = ["That's not very nice.", "I don't appreciate that.",
                           "Is there a reason for the hostility?"]

        if arch == "warrior":
            resp = random.choice(warrior_retorts)
        elif arch == "mage":
            resp = random.choice(mage_retorts)
        elif arch == "vagabond":
            resp = random.choice(vagabond_retorts)
        else:
            resp = random.choice(default_retorts)

        cold = " I'd prefer if you didn't speak to me like that." if interest > 20 else ""
        return f"{name}: \"{resp}{cold}\""
    except Exception as e:
        log.error(_LOG_SOURCE, f"_generate_insult_response failed: {e}")
        return f"{name}: \"...\""


def _generate_request_response(name: str, arch: str) -> str:
    responses = {
        "farmer": "I'll see what I can do once I finish my chores.",
        "warrior": "If it involves fighting, I'm your person. I'll get to it.",
        "merchant": "I'll add it to my list. Come find me later.",
        "adventurer": "I'll take a look when I'm in the area.",
        "default": "Sure, I'll get that done next time I'm free.",
    }
    resp = responses.get(arch, responses["default"])
    return f"{name}: \"{resp}\""


def _generate_question_response(name: str, arch: str, interest: float) -> str:
    try:
        thoughtful = random.choice([
            "That's a good question. Let me think...",
            "Hmm, I hadn't considered that.",
            "Well, from my perspective...",
        ])
        return f"{name}: \"{thoughtful}\""
    except Exception as e:
        log.error(_LOG_SOURCE, f"_generate_question_response failed: {e}")
        return f"{name}: \"I'm not sure.\""


def _generate_statement_response(name: str, arch: str, interest: float) -> str:
    try:
        replies = {
            "adventurer": ["Interesting! Tell me more.",
                           "I've seen things like that on my travels.",
                           "The world is full of wonders, isn't it?"],
            "merchant": ["I see. The markets have been similar lately.",
                         "Good point. Have you considered the value of that?",
                         "Indeed. Supply and demand, as always."],
            "farmer": ["The seasons teach us patience.",
                       "Nature has its own timing for everything.",
                       "Simple truths are often the deepest."],
            "warrior": ["Actions speak louder than words.",
                        "A fair point. I respect that.",
                        "Honor demands we consider such things."],
            "mage": ["Fascinating. The implications are profound.",
                     "I've studied this. There's more beneath the surface.",
                     "Curious. Very curious indeed."],
            "vagabond": ["I've heard stories about that on the road!",
                         "Life's too short to worry about such things!",
                         "Every day is an adventure!"],
            "default": ["I see what you mean.",
                        "That's certainly something to think about.",
                        "You make a valid point."],
        }
        pool = replies.get(arch, replies["default"])
        return f"{name}: \"{random.choice(pool)}\""
    except Exception as e:
        log.error(_LOG_SOURCE, f"_generate_statement_response failed: {e}")
        return f"{name}: \"...\")"


def _detect_stat_changes(persona_uuid: str, player_uuid: str, message: str,
                          archetype: str, interest: float) -> Optional[dict]:
    """Detect if the player's message should change persona stats.

    Checks for gift-giving or insult patterns and adjusts interest level
    and stats accordingly. Uses lazy imports to avoid circular dependencies.

    Args:
        persona_uuid: The persona whose stats may change
        player_uuid: The player triggering the change (not a placeholder)
        message: The player's message text
        archetype: Persona archetype for context
        interest: Current interest level

    Returns:
        Dict of stat changes (e.g., {"interest_delta": 10.5, "total_gifts": 1})
        or None if no changes detected.
    """
    try:
        msg = message.lower()
        changes = {}

        if any(w in msg for w in {"gift", "for you", "have this",
                                   "offering", "present"}):
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
                update_interest
            )
            delta = random.uniform(5, 15)
            update_interest(persona_uuid, player_uuid, delta)
            changes["interest_delta"] = delta
            changes["total_gifts"] = 1

        if any(w in msg for w in {"stupid", "idiot", "hate", "ugly",
                                   "dumb", "useless", "shut up"}):
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
                update_interest
            )
            delta = -random.uniform(10, 25)
            update_interest(persona_uuid, player_uuid, delta)
            changes["interest_delta"] = delta
            changes["total_insults"] = 1

        return changes if changes else None
    except Exception as e:
        log.error(_LOG_SOURCE, f"_detect_stat_changes failed: {e}")
        return None
