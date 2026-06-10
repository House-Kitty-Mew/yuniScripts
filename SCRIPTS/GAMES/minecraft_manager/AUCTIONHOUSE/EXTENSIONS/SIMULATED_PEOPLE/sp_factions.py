"""
sp_factions.py — Guilds, membership, diplomacy, shared economy.

Guilds are groups of personas who cooperate on territory, building,
and defense.  Members share resources, build settlements together,
and can wage war on rival guilds.
"""

import json, random, uuid as uuid_mod
from datetime import datetime, timezone, timedelta
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db, add_memory, add_life_event
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, remove_item, count_item, get_inventory
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

GUILD_RANKS = ["leader", "elder", "member", "recruit"]

RANK_PERMISSIONS = {
    "leader": ["claim", "build", "invite", "kick", "promote", "war", "treasury"],
    "elder":  ["claim", "build", "invite", "war"],
    "member": ["build", "gather"],
    "recruit":["gather"],
}


# ── Guild CRUD ───────────────────────────────────────────────────────

def create_guild(name: str, leader_uuid: str) -> dict:
    """Form a new guild with a persona as leader.

    Requires leadership skill >= 20.
    """
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills
    skills = get_skills(leader_uuid) or {}
    leadership = skills.get("leadership", 0)
    if leadership < 20:
        return {"error": f"leadership skill {leadership} too low (need 20)"}

    db = get_db()
    existing = db.fetch_one(
        "SELECT id FROM ext_sp_guild_members WHERE persona_uuid = ?",
        (leader_uuid,))
    if existing:
        return {"error": "already in a guild"}

    guild_uuid = str(uuid_mod.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""INSERT INTO ext_sp_guilds (guild_uuid, name, leader_uuid, created_at)
                  VALUES (?, ?, ?, ?)""",
               (guild_uuid, name, leader_uuid, now))
    db.execute("""INSERT INTO ext_sp_guild_members (guild_uuid, persona_uuid, rank, joined_at)
                  VALUES (?, ?, 'leader', ?)""",
               (guild_uuid, leader_uuid, now))
    add_memory(leader_uuid, "guild_formed", detail={"guild": name}, emotional_weight=9)
    log.info("sp_factions", f"Guild '{name}' formed by {leader_uuid[:8]}")
    return {"guild_uuid": guild_uuid, "name": name}


def join_guild(persona_uuid: str, guild_uuid: str) -> dict:
    """A persona joins an existing guild as a 'member' (or 'recruit' if renown high)."""
    db = get_db()
    existing = db.fetch_one(
        "SELECT id FROM ext_sp_guild_members WHERE persona_uuid = ?",
        (persona_uuid,))
    if existing:
        return {"error": "already in a guild"}

    guild = db.fetch_one("SELECT * FROM ext_sp_guilds WHERE guild_uuid = ?",
                         (guild_uuid,))
    if not guild:
        return {"error": "guild not found"}

    rank = "member" if guild["renown"] < 100 else "recruit"
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""INSERT INTO ext_sp_guild_members (guild_uuid, persona_uuid, rank, joined_at)
                  VALUES (?, ?, ?, ?)""",
               (guild_uuid, persona_uuid, rank, now))
    add_memory(persona_uuid, "joined_guild", detail={"guild": guild["name"]}, emotional_weight=7)
    return {"guild_uuid": guild_uuid, "name": guild["name"], "rank": rank}


def leave_guild(persona_uuid: str) -> dict:
    """Remove a persona from their guild."""
    db = get_db()
    member = db.fetch_one(
        "SELECT * FROM ext_sp_guild_members WHERE persona_uuid = ?",
        (persona_uuid,))
    if not member:
        return {"error": "not in a guild"}
    if member["rank"] == "leader":
        return {"error": "leader cannot leave — disband or transfer first"}

    guild = db.fetch_one("SELECT * FROM ext_sp_guilds WHERE guild_uuid = ?",
                         (member["guild_uuid"],))
    db.execute("DELETE FROM ext_sp_guild_members WHERE persona_uuid = ?",
               (persona_uuid,))
    add_memory(persona_uuid, "left_guild",
               detail={"guild": guild["name"] if guild else "?"},
               emotional_weight=6)
    return {"left": True}


def set_rank(actor_uuid: str, target_uuid: str, new_rank: str) -> dict:
    """Promote or demote a guild member (leader only)."""
    try:
        if new_rank not in GUILD_RANKS:

            return {"error": f"invalid rank: {new_rank}"}

    except Exception as e:
        log.error(f"set_rank failed: {e}")
        return {}

    db = get_db()
    actor = db.fetch_one(
        "SELECT * FROM ext_sp_guild_members WHERE persona_uuid = ?",
        (actor_uuid,))
    if not actor or actor["rank"] != "leader":
        return {"error": "only guild leaders can change ranks"}

    target = db.fetch_one(
        "SELECT * FROM ext_sp_guild_members WHERE persona_uuid = ?",
        (target_uuid,))
    if not target or target["guild_uuid"] != actor["guild_uuid"]:
        return {"error": "target not in your guild"}

    db.execute("""UPDATE ext_sp_guild_members SET rank = ? WHERE persona_uuid = ?""",
               (new_rank, target_uuid))
    add_memory(target_uuid, "rank_changed",
               detail={"new_rank": new_rank, "by": actor_uuid[:8]},
               emotional_weight=5)
    return {"rank": new_rank}


def get_guild(persona_uuid: str) -> Optional[dict]:
    """Get the guild info and member list for a persona."""
    db = get_db()
    member = db.fetch_one(
        "SELECT * FROM ext_sp_guild_members WHERE persona_uuid = ?",
        (persona_uuid,))
    if not member:
        return None

    guild = db.fetch_one("SELECT * FROM ext_sp_guilds WHERE guild_uuid = ?",
                         (member["guild_uuid"],))
    if not guild:
        return None

    members = db.fetch_all(
        "SELECT p.name, p.persona_uuid, gm.rank FROM ext_sp_guild_members gm "
        "JOIN ext_sp_profiles p ON gm.persona_uuid = p.persona_uuid "
        "WHERE gm.guild_uuid = ?", (member["guild_uuid"],))

    return {
        **guild,
        "my_rank": member["rank"],
        "members": members,
    }


def deposit_to_treasury(persona_uuid: str, amount: float) -> dict:
    """Deposit coins into guild treasury."""
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import update_finance
    guild = get_guild(persona_uuid)
    if not guild:
        return {"error": "not in a guild"}
    new_bal = update_finance(persona_uuid, -amount, reason="guild_deposit")
    if new_bal is None:
        return {"error": "insufficient funds"}
    db = get_db()
    db.execute("UPDATE ext_sp_guilds SET treasury = treasury + ? WHERE guild_uuid = ?",
               (amount, guild["guild_uuid"]))
    return {"deposited": amount, "new_treasury": guild["treasury"] + amount}


# ── Diplomacy ────────────────────────────────────────────────────────

def set_diplomacy(actor_uuid: str, target_guild_uuid: str,
                  stance: str) -> dict:
    """Set diplomatic stance toward another guild.

    Stances: ally, neutral, war, truce, vassal
    """
    try:
        if stance not in ("ally", "neutral", "war", "truce", "vassal"):

            return {"error": f"invalid stance: {stance}"}

    except Exception as e:
        log.error(f"set_diplomacy failed: {e}")
        return {}

    guild = get_guild(actor_uuid)
    if not guild:
        return {"error": "not in a guild"}
    perms = RANK_PERMISSIONS.get(guild["my_rank"], [])
    if "war" not in perms and stance in ("war", "ally", "truce"):
        return {"error": "insufficient rank for diplomacy"}

    db = get_db()
    current = json.loads(guild.get("diplomatic_stance", "{}"))
    current[target_guild_uuid] = stance
    db.execute("UPDATE ext_sp_guilds SET diplomatic_stance = ? WHERE guild_uuid = ?",
               (json.dumps(current), guild["guild_uuid"]))
    return {"stance": stance}


def get_diplomacy(guild_uuid: str) -> dict:
    """Get full diplomatic stance map for a guild."""
    db = get_db()
    guild = db.fetch_one("SELECT diplomatic_stance FROM ext_sp_guilds WHERE guild_uuid = ?",
                         (guild_uuid,))
    if not guild:
        return {}
    return json.loads(guild["diplomatic_stance"]) if guild.get("diplomatic_stance") else {}


# ── Guild tick ───────────────────────────────────────────────────────

def process_guild_tick() -> dict:
    """Update guild morale, renown, and drift diplomacy.

    Call daily (every 6 ticks).
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"process_guild_tick failed: {e}")
        return {}
    guilds = db.fetch_all("SELECT * FROM ext_sp_guilds")
    results = {"processed": len(guilds), "morale_shifts": 0}

    for guild in guilds:
        # Count members
        member_count = db.fetch_one(
            "SELECT COUNT(*) as c FROM ext_sp_guild_members WHERE guild_uuid = ?",
            (guild["guild_uuid"],))
        count = member_count["c"] if member_count else 0

        # Count claims
        claim_count = db.fetch_one(
            "SELECT COUNT(*) as c FROM ext_sp_claims WHERE guild_uuid = ?",
            (guild["guild_uuid"],))
        claims = claim_count["c"] if claim_count else 0

        # Count buildings on claimed areas
        buildings = db.fetch_one("""
            SELECT COUNT(*) as c FROM ext_sp_buildings b
            JOIN ext_sp_claims c ON b.area_uuid = c.area_uuid
            WHERE c.guild_uuid = ?
        """, (guild["guild_uuid"],))
        b_count = buildings["c"] if buildings else 0

        # Morale factors
        morale = guild["morale"]
        if count >= 3:
            morale += 1       # Strength in numbers
        if claims >= 3:
            morale += 1       # Territory pride
        if b_count >= 3:
            morale += 1       # Developed settlement
        if guild["treasury"] >= 1000:
            morale += 1       # Financial security

        # Check if at war
        diplomacy = json.loads(guild.get("diplomatic_stance", "{}"))
        at_war = "war" in diplomacy.values()
        if at_war:
            morale -= 2       # War strain

        morale = max(10, min(100, morale))

        # Renown drifts toward member/claim count baseline
        target_renown = count * 10 + claims * 5 + b_count * 3
        renown = guild["renown"] + (target_renown - guild["renown"]) * 0.1

        db.execute("""UPDATE ext_sp_guilds SET morale = ?, renown = ? WHERE guild_uuid = ?""",
                   (round(morale, 1), round(renown, 1), guild["guild_uuid"]))

        # Diplomacy drift: war→neutral over time, ally→neutral slowly
        for other, stance in list(diplomacy.items()):
            if stance == "war" and random.random() < 0.02:
                diplomacy[other] = "neutral"
                log.info("sp_factions", f"War between {guild['name'][:16]} and guild {other[:8]} ended naturally")
            elif stance == "ally" and random.random() < 0.01:
                diplomacy[other] = "neutral"
        db.execute("UPDATE ext_sp_guilds SET diplomatic_stance = ? WHERE guild_uuid = ?",
                   (json.dumps(diplomacy), guild["guild_uuid"]))

        results["morale_shifts"] += 1

    return results

