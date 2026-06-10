"""
pl_announce_filter.py — AI thinking-mode event interestingness filter.

Evaluates whether a persona event is "interesting" enough to announce.
The AI thinking mode scores events on a 1-10 scale and provides a
human-readable reason.

Threshold: events with interestingness >= 5 are announced.

Scoring guidelines (used internally by the AI):
  10 = Death, suicide, catastrophic event
  9  = Major life event (marriage, childbirth, divorce)
  8  = War declaration, faction dissolution
  7  = Major combat victory, skill mastery, leadership change
  6  = Faction join/leave, major trade deal (>100 emeralds)
  5  = Moderate trade, new personal record, minor event participation
  4  = ⚡ THRESHOLD — below this is too boring to announce
  3  = Routine task, casual conversation
  2  = Minor resource gathering
  1  = Completely mundane (picked berries, walked somewhere)
"""

import json, time, traceback, os, random
from typing import Optional
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()
EXT_NAME = "pl_announce_filter"

# ── Interestingness Threshold ────────────────────────────────────────

INTERESTINGNESS_THRESHOLD = 5  # Events >= this are announced


# ── AI Thinking Mode: Scoring Rules (deterministic logic) ────────────

def _score_by_event_type(event_type: str, event_data: dict) -> tuple[int, str]:
    """Score an event based on its type using deterministic rules.

    Returns (score, reason) tuple.
    """
    try:
        _type = event_type.lower()

    except Exception as e:
        log.error(f"_score_by_event_type failed: {e}")
        return ()
    _event = event_data or {}

    # ── Death / Suicide ──────────────────────────────────────────────
    if _type in ("death", "suicide", "died", "killed"):
        method = _event.get("method", "unknown")
        return 10, f"Persona died ({method})"

    # ── Major life events ───────────────────────────────────────────
    if _type in ("marriage", "married", "wedding"):
        partner = _event.get("target_id", "someone")
        return 9, f"Persona married {partner}"

    if _type in ("divorce", "divorced", "separation"):
        partner = _event.get("target_id", "someone")
        return 9, f"Persona divorced {partner}"

    if _type in ("childbirth", "born", "child", "birth"):
        return 9, "Persona had a child"

    # ── War & Conflict ──────────────────────────────────────────────
    if _type in ("war_declared", "war", "declare_war"):
        target = _event.get("target_id", "an enemy")
        return 8, f"Persona declared war on {target}"

    if _type in ("war_end", "war_ended", "peace", "truce"):
        return 8, "Persona ended a war"

    if _type in ("faction_disbanded", "disbanded", "faction_dissolved"):
        faction = _event.get("faction_name", "unknown")
        return 8, f"Persona's faction disbanded: {faction}"

    # ── Combat & Achievement ────────────────────────────────────────
    if _type in ("combat_victory", "battle_won", "defeated"):
        enemy = _event.get("target_id", "an enemy")
        return 7, f"Persona won a battle against {enemy}"

    if _type in ("skill_mastery", "mastered", "skill_100"):
        skill = _event.get("skill_name", "a skill")
        return 7, f"Persona mastered {skill}"

    if _type in ("leadership", "became_leader", "elected"):
        role = _event.get("role", "leader")
        return 7, f"Persona became {role}"

    if _type in ("injury", "wounded", "hospitalized"):
        return 7, "Persona was seriously wounded"

    # ── Faction & Social ────────────────────────────────────────────
    if _type in ("faction_join", "joined_faction"):
        faction = _event.get("faction_name", "a faction")
        return 6, f"Persona joined {faction}"

    if _type in ("faction_leave", "left_faction"):
        faction = _event.get("faction_name", "a faction")
        return 6, f"Persona left {faction}"

    if _type in ("major_trade", "big_deal", "major_purchase"):
        value = _event.get("value", 0)
        return 6, f"Persona made a major trade worth {value} emeralds"

    # ── Moderate events ─────────────────────────────────────────────
    if _type in ("trade", "purchase", "sold"):
        value = _event.get("value", 0)
        if value >= 50:
            return 5, f"Persona traded for {value} emeralds"
        return 3, f"Persona made a small trade ({value} emeralds)"

    if _type in ("new_record", "personal_best", "achievement"):
        return 5, "Persona set a new personal record"

    if _type in ("event_participation", "event", "participated"):
        event_name = _event.get("event_name", "an event")
        return 5, f"Persona participated in {event_name}"

    if _type in ("travel", "journey", "expedition"):
        destination = _event.get("destination", "somewhere")
        return 5, f"Persona traveled to {destination}"

    if _type in ("boredom_crisis", "unhappy", "mood_plummet"):
        return 5, "Persona is experiencing a boredom crisis"

    # ── Low-interest events ─────────────────────────────────────────
    if _type in ("conversation", "talked", "chat"):
        return 2, "Persona had a conversation"

    if _type in ("gather", "gathered", "collected", "harvested"):
        item = _event.get("item", "resources")
        count = _event.get("count", 0)
        return 2, f"Persona gathered {count} {item}"

    if _type in ("walk", "moved", "travel", "go_to"):
        return 1, "Persona moved to a new location"

    if _type in ("sleep", "rested", "idle"):
        return 1, "Persona rested"

    if _type in ("eat", "ate", "consume"):
        return 1, "Persona ate something"

    # ── Unknown / Generic ───────────────────────────────────────────
    description = _event.get("description", "")
    if description:
        return 3, f"Event: {description[:100]}"
    return 3, f"Unknown event type: {event_type}"


def _score_by_narrative(event_type: str, event_data: dict, persona_profile: Optional[dict]) -> int:
    """Apply narrative-context scoring based on persona profile.

    Returns an integer bonus (0, +1, or +2) for narrative significance.
    """
    try:
        bonus = 0

    except Exception as e:
        log.error(f"_score_by_narrative failed: {e}")
        return 0
    if not persona_profile:
        return 0

    # A persona's own significant milestones get a bonus
    _event = event_data or {}

    # If the persona is already famous/important, their events matter more
    status = (persona_profile.get("status") or "").lower()
    if status in ("famous", "leader", "noble", "legendary", "hero"):
        bonus += 1

    # Wealthy persona making a trade is more notable
    wealth = int(persona_profile.get("wealth", 0) or 0)
    if wealth > 1000 and event_type in ("trade", "major_trade", "purchase"):
        bonus += 1

    # High-level persona achieving something is more interesting
    level = int(persona_profile.get("level", 0) or 0)
    if level > 20 and event_type in ("skill_mastery", "combat_victory", "new_record"):
        bonus += 1

    return min(bonus, 2)  # Cap at +2


def evaluate_event_interestingness(
    event_type: str,
    event_data: Optional[dict] = None,
    persona_profile: Optional[dict] = None,
    ai_mode: bool = False
) -> dict:
    """Evaluate how interesting an event is for announcement purposes.

    This is the main entry point for filtering events.  Uses deterministic
    scoring by default, but supports AI thinking-mode evaluation.

    Args:
        event_type: The type/category of the event
        event_data: Dict with details about the event
        persona_profile: Dict with persona metadata (status, wealth, level)
        ai_mode: If True, use AI thinking-mode evaluation (slower but richer)

    Returns:
        Dict with keys:
            interestingness: int 1-10
            should_announce: bool (True if >= threshold)
            reason: str explanation
    """
    base_score, reason = _score_by_event_type(event_type, event_data or {})
    narrative_bonus = _score_by_narrative(event_type, event_data or {}, persona_profile)
    final_score = min(base_score + narrative_bonus, 10)

    if ai_mode:
        # In AI mode, we add contextual richness using thinking-style evaluation
        # This is a lightweight simulation of "thinking mode" — in production
        # this could call DeepSeek API for truly contextual evaluation.
        ai_boost = _ai_thinking_boost(event_type, event_data or {}, persona_profile)
        final_score = min(final_score + ai_boost, 10)
        if ai_boost > 0:
            reason += f" [AI: +{ai_boost} narrative significance]"

    return {
        "interestingness": final_score,
        "should_announce": final_score >= INTERESTINGNESS_THRESHOLD,
        "reason": reason,
    }


def _ai_thinking_boost(
    event_type: str,
    event_data: dict,
    persona_profile: Optional[dict]
) -> int:
    """AI thinking-mode: additional contextual boost beyond deterministic rules.

    This simulates what DeepSeek thinking mode would evaluate:
      - Is this event surprising or rare for this persona?
      - Does it connect to a larger narrative?
      - Would a player actually care about this?

    Returns 0, 1, or 2.
    """
    try:
        boost = 0

    except Exception as e:
        log.error(f"_ai_thinking_boost failed: {e}")
        return 0
    _type = event_type.lower()

    # Check for rarity/surprise factor
    rarity = (event_data.get("rarity") or "common").lower()
    if rarity in ("rare", "epic", "legendary"):
        boost += 1

    # Events with a target persona are more interesting (interpersonal drama)
    if event_data.get("target_id"):
        boost += 1

    # Events with significant value change
    value = int(event_data.get("value", 0) or 0)
    if value > 200:
        boost += 1

    # Emotional events (any type with emotional_content flag)
    if event_data.get("emotional", False) or event_data.get("dramatic", False):
        boost += 1

    # If persona profile shows this is unusual behavior
    if persona_profile and _type == "trade":
        wealth = int(persona_profile.get("wealth", 0) or 0)
        if wealth < 50 and value > 100:
            boost += 1  # Poor persona making big trade — interesting!

    # If persona has no history of combat but wins a battle
    if persona_profile and _type in ("combat_victory", "defeated"):
        combat_count = int(persona_profile.get("combat_count", 0) or 0)
        if combat_count <= 1:
            boost += 1  # First battle — notable!

    return min(boost, 2)


def batch_filter_events(
    events: list[dict],
    ai_mode: bool = False
) -> list[dict]:
    """Filter a batch of events, returning only those worth announcing.

    Each event dict should have:
        event_type: str
        persona_id: str
        data: dict (optional)
        persona_profile: dict (optional)

    Returns:
        List of event dicts enriched with:
            interestingness: int
            should_announce: bool
            reason: str
            title: str (generated summary)
            description: str (detailed summary)
    """
    results = []
    for event in events:
        event_type = event.get("event_type", "unknown")
        event_data = event.get("data", {})
        persona_profile = event.get("persona_profile")
        persona_id = event.get("persona_id", "unknown")

        eval_result = evaluate_event_interestingness(
            event_type=event_type,
            event_data=event_data,
            persona_profile=persona_profile,
            ai_mode=ai_mode,
        )

        if eval_result["should_announce"]:
            title = _generate_title(persona_id, event_type, event_data)
            description = _generate_description(persona_id, event_type, event_data, eval_result)

            enriched = dict(event)
            enriched["interestingness"] = eval_result["interestingness"]
            enriched["should_announce"] = True
            enriched["reason"] = eval_result["reason"]
            enriched["title"] = title
            enriched["description"] = description
            results.append(enriched)

    # Sort by interestingness (highest first)
    results.sort(key=lambda e: e["interestingness"], reverse=True)
    return results


def _generate_title(persona_id: str, event_type: str, event_data: dict) -> str:
    """Generate a concise title for an announcement."""
    try:
        _type = event_type.lower()

    except Exception as e:
        log.error(f"_generate_title failed: {e}")
        return ""
    name = persona_id

    # Death
    if _type in ("death", "suicide", "died", "killed"):
        method = event_data.get("method", "")
        if method:
            return f"☠ {name} died ({method})"
        return f"☠ {name} has died"

    # Life events
    if _type in ("marriage", "married", "wedding"):
        partner = event_data.get("target_id", "someone")
        return f"💍 {name} married {partner}"
    if _type in ("divorce", "divorced"):
        partner = event_data.get("target_id", "someone")
        return f"💔 {name} divorced {partner}"
    if _type in ("childbirth", "born", "birth"):
        return f"👶 {name} had a child"

    # War
    if _type in ("war_declared", "declare_war"):
        target = event_data.get("target_id", "an enemy")
        return f"⚔ {name} declared war on {target}"
    if _type in ("war_end", "peace", "truce"):
        return f"☮ {name} made peace"

    # Combat
    if _type in ("combat_victory", "battle_won", "defeated"):
        enemy = event_data.get("target_id", "an enemy")
        return f"🏆 {name} defeated {enemy} in battle"
    if _type in ("injury", "wounded"):
        return f"🩸 {name} was seriously wounded"

    # Faction
    if _type in ("faction_join", "joined_faction"):
        faction = event_data.get("faction_name", "a faction")
        return f"🤝 {name} joined {faction}"
    if _type in ("faction_leave", "left_faction"):
        faction = event_data.get("faction_name", "a faction")
        return f"🚪 {name} left {faction}"
    if _type in ("faction_disbanded", "disbanded"):
        faction = event_data.get("faction_name", "unknown")
        return f"💥 {faction} has disbanded"

    # Skill
    if _type in ("skill_mastery", "mastered"):
        skill = event_data.get("skill_name", "a skill")
        return f"⭐ {name} mastered {skill}"
    if _type in ("leadership", "became_leader", "elected"):
        role = event_data.get("role", "leader")
        return f"👑 {name} became {role}"

    # Trade
    value = int(event_data.get("value", 0) or 0)
    if _type in ("major_trade", "big_deal"):
        return f"💰 {name} made a major deal ({value} emeralds)"
    if value >= 50 and _type in ("trade", "purchase", "sold"):
        return f"🛒 {name} traded for {value} emeralds"

    # Travel
    if _type in ("travel", "journey", "expedition"):
        dest = event_data.get("destination", "somewhere")
        return f"🗺 {name} traveled to {dest}"

    # Boredom crisis
    if _type in ("boredom_crisis", "unhappy"):
        return f"😞 {name} is unhappy"

    # Generic
    if _type in ("new_record", "personal_best", "achievement"):
        return f"🎯 {name} achieved a personal best"
    if _type in ("event_participation", "participated"):
        ename = event_data.get("event_name", "an event")
        return f"🎪 {name} participated in {ename}"

    return f"📢 {name}: {event_type}"


def _generate_description(
    persona_id: str,
    event_type: str,
    event_data: dict,
    eval_result: dict
) -> str:
    """Generate a detailed description for an announcement."""
    try:
        _type = event_type.lower()

    except Exception as e:
        log.error(f"_generate_descriptio failed: {e}")
        return ""
    name = persona_id
    reason = eval_result.get("reason", "")

    if _type in ("death", "suicide", "died", "killed"):
        method = event_data.get("method", "unknown circumstances")
        location = event_data.get("location", "somewhere")
        return f"{name} has died in {location} due to {method}. This is a significant loss to the community."

    if _type in ("marriage", "married", "wedding"):
        partner = event_data.get("target_id", "someone")
        location = event_data.get("location", "their home village")
        return f"{name} married {partner} in a ceremony at {location}. The community celebrates this union."

    if _type in ("war_declared", "declare_war"):
        target = event_data.get("target_id", "an enemy")
        reason_why = event_data.get("reason", "unknown reasons")
        return f"{name} has declared war on {target} over {reason_why}. Tensions are rising."

    if _type in ("combat_victory", "battle_won"):
        enemy = event_data.get("target_id", "an enemy")
        location = event_data.get("location", "the battlefield")
        return f"{name} emerged victorious against {enemy} at {location}. A notable military achievement."

    if _type in ("faction_join", "joined_faction"):
        faction = event_data.get("faction_name", "a faction")
        return f"{name} has joined forces with {faction}, strengthening their position."

    if _type in ("skill_mastery", "mastered"):
        skill = event_data.get("skill_name", "a skill")
        return f"After years of practice, {name} has achieved mastery in {skill}. Truly impressive."

    if _type in ("major_trade", "big_deal"):
        value = event_data.get("value", 0)
        item = event_data.get("item", "goods")
        return f"{name} negotiated a major deal worth {value} emeralds for {item}. A savvy business move."

    # Generic description falls back to reason
    detail = event_data.get("description", "")
    if detail:
        return f"{name}: {detail}"
    return f"{name}: {reason}"


def format_announcement_for_chat(announcement: dict) -> str:
    """Format an announcement dict into a chat-friendly string.

    Returns a Minecraft color-code formatted string.
    """
    try:
        title = announcement.get("title", "Announcement")

    except Exception as e:
        log.error(f"format_announcement_ failed: {e}")
        return ""
    desc = announcement.get("description", "")
    interestingness = announcement.get("interestingness", 5)

    # Color code based on interestingness
    if interestingness >= 9:
        color = "§4"  # dark red — death/major
        prefix = "§l⚠ MAJOR EVENT ⚠§r"
    elif interestingness >= 7:
        color = "§c"  # red — significant
        prefix = "§l✦ NOTABLE ✦§r"
    elif interestingness >= 5:
        color = "§6"  # gold
        prefix = "§l● EVENT ●§r"
    else:
        color = "§7"  # gray (shouldn't reach here, but just in case)
        prefix = "§l○ ○ ○§r"

    return f"{prefix} {color}{title}§r\n§7{desc}§r"

