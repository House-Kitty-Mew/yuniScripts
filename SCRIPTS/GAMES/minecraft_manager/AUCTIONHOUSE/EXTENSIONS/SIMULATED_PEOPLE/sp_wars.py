"""
sp_wars.py — War system, sieges, raiding, and resolution.

Guilds can declare war over territory and resources.  Wars involve
raids on claimed areas, siege pressure on settlements, and morale
collapse that forces surrender.
"""

import json, random, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db, add_memory, add_life_event
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_factions import (
    get_guild, set_diplomacy, get_diplomacy, RANK_PERMISSIONS,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_claims import detect_settlements, BUILDING_PRESETS
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area, get_all_areas
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()


# ── War declaration ──────────────────────────────────────────────────

def declare_war(actor_uuid: str, target_guild_uuid: str) -> dict:
    """Declare war on another guild."""
    try:
        guild = get_guild(actor_uuid)

    except Exception as e:
        log.error(f"declare_war failed: {e}")
        return {}
    if not guild:
        return {"error": "not in a guild"}
    perms = RANK_PERMISSIONS.get(guild["my_rank"], [])
    if "war" not in perms:
        return {"error": "insufficient rank"}

    if target_guild_uuid == guild["guild_uuid"]:
        return {"error": "cannot declare war on yourself"}

    # Set diplomacy both ways
    set_diplomacy(actor_uuid, target_guild_uuid, "war")
    # Also set the target's stance toward us (if we can mutate it directly)
    db = get_db()
    target_guild = db.fetch_one("SELECT * FROM ext_sp_guilds WHERE guild_uuid = ?",
                                (target_guild_uuid,))
    if target_guild:
        t_dip = json.loads(target_guild.get("diplomatic_stance", "{}"))
        t_dip[guild["guild_uuid"]] = "war"
        db.execute("UPDATE ext_sp_guilds SET diplomatic_stance = ? WHERE guild_uuid = ?",
                   (json.dumps(t_dip), target_guild_uuid))

    add_life_event(actor_uuid, "war_declared",
                   f"{guild['name']} declared war!",
                   financial_impact=0, mood_impact="stressed", duration_hours=168)

    log.info("sp_wars", f"War declared: {guild['name'][:16]} → guild {target_guild_uuid[:8]}")
    return {"war_declared": True, "target": target_guild_uuid}


def arrange_truce(actor_uuid: str, target_guild_uuid: str) -> dict:
    """End a war by arranging a truce."""
    try:
        guild = get_guild(actor_uuid)

    except Exception as e:
        log.error(f"arrange_truce failed: {e}")
        return {}
    if not guild:
        return {"error": "not in a guild"}
    perms = RANK_PERMISSIONS.get(guild["my_rank"], [])
    if "war" not in perms:
        return {"error": "insufficient rank"}

    set_diplomacy(actor_uuid, target_guild_uuid, "truce")
    db = get_db()
    target_guild = db.fetch_one("SELECT * FROM ext_sp_guilds WHERE guild_uuid = ?",
                                (target_guild_uuid,))
    if target_guild:
        t_dip = json.loads(target_guild.get("diplomatic_stance", "{}"))
        t_dip[guild["guild_uuid"]] = "truce"
        db.execute("UPDATE ext_sp_guilds SET diplomatic_stance = ? WHERE guild_uuid = ?",
                   (json.dumps(t_dip), target_guild_uuid))

    log.info("sp_wars", f"Truce arranged: {guild['name'][:16]} ↔ guild {target_guild_uuid[:8]}")
    return {"truce": True, "target": target_guild_uuid}


# ── Raiding ──────────────────────────────────────────────────────────

def raid_claim(attacker_uuid: str, target_area_uuid: str) -> dict:
    """Raid a claimed area to damage buildings and steal resources.

    Combat resolution simplified: attacker strength vs defender defense.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"raid_claim failed: {e}")
        return {}
    guild = get_guild(attacker_uuid)
    if not guild:
        return {"error": "not in a guild"}
    perms = RANK_PERMISSIONS.get(guild["my_rank"], [])
    if "war" not in perms:
        return {"error": "insufficient rank"}

    diplomacy = get_diplomacy(guild["guild_uuid"])
    claim = db.fetch_one("SELECT * FROM ext_sp_claims WHERE area_uuid = ?",
                         (target_area_uuid,))
    if not claim:
        return {"error": "area not claimed"}
    if claim["guild_uuid"] not in diplomacy or diplomacy[claim["guild_uuid"]] != "war":
        return {"error": "not at war with this claim's guild"}

    # Get attacker stats
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_combat import get_combat_stats
    atk = get_combat_stats(attacker_uuid)
    atk_power = atk["strength"] + atk["weapon_skill"] * 0.5

    # Get defender buildings for defense rating
    buildings = db.fetch_all(
        "SELECT * FROM ext_sp_buildings WHERE area_uuid = ? AND is_complete = 1",
        (target_area_uuid,))
    def_power = sum(b["defense_rating"] for b in buildings) * 2 + 5

    # Raid resolution
    damage = max(0, atk_power * random.uniform(0.5, 1.5) - def_power * 0.5)
    damage = round(damage, 1)

    # Apply damage to buildings
    destroyed = 0
    for b in buildings:
        new_health = b["health"] - damage * 0.3
        if new_health <= 0:
            db.execute("DELETE FROM ext_sp_buildings WHERE id = ?", (b["id"],))
            destroyed += 1
        else:
            db.execute("UPDATE ext_sp_buildings SET health = ? WHERE id = ?",
                       (round(new_health, 1), b["id"]))

    # Weaken claim
    new_strength = claim["claim_strength"] - damage * 0.5
    db.execute("UPDATE ext_sp_claims SET claim_strength = ? WHERE area_uuid = ?",
               (round(max(0, new_strength), 1), target_area_uuid))

    # Record event
    add_life_event(attacker_uuid, "raid",
                   f"Raided area {target_area_uuid[:8]} for {damage:.0f} damage",
                   financial_impact=damage, mood_impact="motivated",
                   duration_hours=24)
    add_memory(attacker_uuid, "raid", detail={
        "target": target_area_uuid[:8], "damage": damage,
        "buildings_destroyed": destroyed,
    }, emotional_weight=8)

    return {
        "damage_dealt": damage,
        "buildings_destroyed": destroyed,
        "claim_strength": round(max(0, new_strength), 1),
    }


# ── Siege mechanics ──────────────────────────────────────────────────

def process_siege_tick() -> dict:
    """Process ongoing sieges — war guilds inflict siege damage on enemy claims.

    Each tick, guilds at war damage enemy claim strength.
    """
    db = get_db()
    guilds = db.fetch_all("SELECT * FROM ext_sp_guilds")
    results = {"sieges_active": 0, "claims_taken": 0}

    for guild in guilds:
        diplomacy = json.loads(guild.get("diplomatic_stance", "{}"))
        war_targets = [k for k, v in diplomacy.items() if v == "war"]
        if not war_targets:
            continue

        # Gather siege power based on guild size
        member_count = db.fetch_one(
            "SELECT COUNT(*) as c FROM ext_sp_guild_members WHERE guild_uuid = ?",
            (guild["guild_uuid"],))
        size = member_count["c"] if member_count else 0
        siege_power = size * random.uniform(1, 3)

        for target_guild in war_targets:
            # Damage all claims of target guild
            claims = db.fetch_all(
                "SELECT * FROM ext_sp_claims WHERE guild_uuid = ?",
                (target_guild,))
            for claim in claims:
                new_strength = claim["claim_strength"] - siege_power * 0.1
                if new_strength <= 0:
                    # Claim taken!
                    db.execute("DELETE FROM ext_sp_claims WHERE id = ?", (claim["id"],))
                    results["claims_taken"] += 1
                else:
                    db.execute("UPDATE ext_sp_claims SET claim_strength = ? WHERE id = ?",
                               (round(new_strength, 1), claim["id"]))
                results["sieges_active"] += 1

    return results


# ── Surrender & war resolution ───────────────────────────────────────

def check_surrender() -> dict:
    """Check if any warring guild should surrender due to low morale.

    Surrender = leader removed, all claims lost, territory annexed.
    """
    db = get_db()
    guilds = db.fetch_all(
        "SELECT * FROM ext_sp_guilds WHERE morale < 15")
    surrendered = 0

    for guild in guilds:
        diplomacy = json.loads(guild.get("diplomatic_stance", "{}"))
        at_war = "war" in diplomacy.values()
        if not at_war:
            continue
        if guild["morale"] > 10:
            continue

        # Surrender: delete guild memberships and claims
        members = db.fetch_all(
            "SELECT persona_uuid FROM ext_sp_guild_members WHERE guild_uuid = ?",
            (guild["guild_uuid"],))
        for m in members:
            add_life_event(m["persona_uuid"], "guild_defeated",
                           f"Your guild {guild['name']} was defeated!",
                           financial_impact=0, mood_impact="stressed",
                           duration_hours=168)

        db.execute("DELETE FROM ext_sp_claims WHERE guild_uuid = ?", (guild["guild_uuid"],))
        db.execute("DELETE FROM ext_sp_guild_members WHERE guild_uuid = ?", (guild["guild_uuid"],))
        db.execute("DELETE FROM ext_sp_guilds WHERE guild_uuid = ?", (guild["guild_uuid"],))
        surrendered += 1
        log.info("sp_wars", f"Guild '{guild['name']}' surrendered and dissolved")

    return {"surrendered": surrendered}


# ── Wars tick ────────────────────────────────────────────────────────

def process_wars_tick() -> dict:
    """Run one war processing tick: siege + morale effects."""
    db = get_db()
    results = {}

    try:
        siege_result = process_siege_tick()
        results["sieges"] = siege_result
    except Exception as e:
        log.warn("sp_wars", f"Siege tick error: {e}")
        results["sieges"] = {"error": str(e)}

    try:
        surrender_result = check_surrender()
        results["surrender"] = surrender_result
    except Exception as e:
        log.warn("sp_wars", f"Surrender check error: {e}")
        results["surrender"] = {"error": str(e)}

    return results

