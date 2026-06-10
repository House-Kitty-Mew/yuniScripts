"""
sp_claims.py — Territorial claims, settlements, building construction.

Personas claim areas for themselves or their guild.  Clusters of
buildings form settlements with synergy bonuses.  Buildings provide
shelter, defense, storage, and crafting stations.
"""

import json, random, uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db, add_memory
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, remove_item, count_item
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import get_persona_area, get_all_areas, get_area
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_factions import get_guild
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config

log = get_logger()

# ── Building presets (construction projects) ─────────────────────────

BUILDING_PRESETS = {
    "wall": {
        "name": "Wall",
        "health": 200, "defense": 3.0, "insulation": 0.0,
        "materials": {"wood_log": 10, "stone": 5, "nail": 4},
        "build_ticks": 5,
        "provides": {"defense_cover": 0.6},
        "description": "A defensive wall providing cover from attacks.",
    },
    "gate": {
        "name": "Gate",
        "health": 150, "defense": 2.0, "insulation": 0.1,
        "materials": {"wood_log": 8, "iron_ingot": 2, "nail": 6},
        "build_ticks": 4,
        "provides": {"defense_cover": 0.4},
        "description": "A reinforced entry point for the settlement.",
    },
    "tower": {
        "name": "Watchtower",
        "health": 180, "defense": 4.0, "insulation": 0.2,
        "materials": {"wood_log": 12, "stone": 8, "nail": 6, "rope": 2},
        "build_ticks": 6,
        "provides": {"defense_cover": 0.7, "lookout": True},
        "description": "A tall tower giving defenders height advantage.",
    },
    "storehouse": {
        "name": "Storehouse",
        "health": 150, "defense": 0.5, "insulation": 0.4,
        "materials": {"wood_log": 8, "nail": 4, "rope": 1},
        "build_ticks": 4,
        "provides": {"storage_capacity": 500},
        "description": "A dry building for bulk resource storage.",
    },
    "well": {
        "name": "Well",
        "health": 80, "defense": 0.0, "insulation": 0.0,
        "materials": {"stone": 10, "rope": 2, "clay": 3},
        "build_ticks": 3,
        "provides": {"water_source": True},
        "description": "A fresh water source within the settlement.",
    },
}


# ── Claiming ─────────────────────────────────────────────────────────

def claim_area(persona_uuid: str, area_uuid: str) -> dict:
    """Claim an area for a persona (and their guild if applicable)."""
    try:
        db = get_db()

    except Exception as e:
        log.error(f"claim_area failed: {e}")
        return {}
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_skills import get_skills

    # Check if already claimed
    existing = db.fetch_one(
        "SELECT * FROM ext_sp_claims WHERE area_uuid = ?", (area_uuid,))
    if existing:
        return {"error": "area already claimed"}

    # Check claim limit
    skills = get_skills(persona_uuid) or {}
    leadership = skills.get("leadership", 10)
    max_claims = 1 + int(leadership / 10)
    user_claims = db.fetch_one(
        "SELECT COUNT(*) as c FROM ext_sp_claims WHERE owner_uuid = ?",
        (persona_uuid,))
    if user_claims and user_claims["c"] >= max_claims:
        return {"error": f"max claims ({max_claims}) reached"}

    # Cost
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import update_finance
    cost = 50
    new_bal = update_finance(persona_uuid, -cost, reason="claim_area")
    if new_bal is None or new_bal < 0:
        return {"error": f"cannot afford {cost} claim fee"}

    # Check guild
    guild = get_guild(persona_uuid)
    guild_uuid = guild["guild_uuid"] if guild else None

    claim_uuid = str(uuid_mod.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""INSERT INTO ext_sp_claims (claim_uuid, area_uuid, owner_uuid, guild_uuid, claimed_at)
                  VALUES (?, ?, ?, ?, ?)""",
               (claim_uuid, area_uuid, persona_uuid, guild_uuid, now))
    add_memory(persona_uuid, "claimed_area",
               detail={"area_uuid": area_uuid, "guild": bool(guild)},
               emotional_weight=5)
    return {"claim_uuid": claim_uuid, "area_uuid": area_uuid, "guild_uuid": guild_uuid}


def unclaim_area(persona_uuid: str, area_uuid: str) -> dict:
    """Release a claimed area."""
    db = get_db()
    claim = db.fetch_one("SELECT * FROM ext_sp_claims WHERE area_uuid = ?",
                         (area_uuid,))
    if not claim:
        return {"error": "area not claimed"}
    if claim["owner_uuid"] != persona_uuid:
        return {"error": "you don't own this claim"}
    db.execute("DELETE FROM ext_sp_claims WHERE area_uuid = ?", (area_uuid,))
    return {"unclaimed": True}


# ── Building construction ────────────────────────────────────────────

def start_construction(persona_uuid: str, building_type: str,
                       area_uuid: Optional[str] = None) -> dict:
    """Start constructing a building on a claimed area.

    Consumes materials from the persona's inventory.
    """
    try:
        preset = BUILDING_PRESETS.get(building_type)

    except Exception as e:
        log.error(f"start_construction failed: {e}")
        return {}
    if not preset:
        return {"error": f"unknown building: {building_type}"}

    db = get_db()
    if not area_uuid:
        area = get_persona_area(persona_uuid)
        if not area:
            return {"error": "not in any area"}
        area_uuid = area["area_uuid"]

    # Check claim
    claim = db.fetch_one("SELECT * FROM ext_sp_claims WHERE area_uuid = ?",
                         (area_uuid,))
    if not claim:
        return {"error": "area not claimed"}

    # Consume materials
    for mat_id, qty in preset["materials"].items():
        removed = remove_item(persona_uuid, mat_id, qty)
        if removed < qty:
            # Refund
            for mid, mqty in preset["materials"].items():
                if mid == mat_id:
                    break
                give_item(persona_uuid, mid, mqty)
            give_item(persona_uuid, mat_id, removed)
            return {"error": f"not enough {mat_id}"}

    # Start construction (as a building with build_progress)
    building_uuid = str(uuid_mod.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO ext_sp_buildings
        (building_uuid, area_uuid, owner_uuid, building_type, name,
         health, max_health, material, room_count, max_rooms,
         is_complete, build_progress, defense_rating, insulation_score, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?)
    """, (building_uuid, area_uuid, persona_uuid, building_type,
          preset["name"],
          preset["health"], preset["health"] * 1.5, "mixed",
          0, 0,
          0.0,
          preset.get("defense", 1.0), preset.get("insulation", 0.3),
          now))

    add_memory(persona_uuid, "started_building",
               detail={"type": building_type, "area": area_uuid},
               emotional_weight=6)
    return {"building_uuid": building_uuid, "type": building_type,
            "name": preset["name"], "health": preset["health"]}


def advance_construction(building_uuid: str, ticks: float = 1.0) -> dict:
    """Progress building construction (called per tick).

    Returns dict with completion status.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"advance_construction failed: {e}")
        return {}
    building = db.fetch_one(
        "SELECT * FROM ext_sp_buildings WHERE building_uuid = ?",
        (building_uuid,))
    if not building:
        return {"error": "building not found"}
    if building["is_complete"]:
        return {"error": "already complete"}

    bt = BUILDING_PRESETS.get(building["building_type"])
    total_required = bt["build_ticks"] if bt else 10

    new_progress = building["build_progress"] + ticks
    if new_progress >= total_required:
        db.execute("""UPDATE ext_sp_buildings SET build_progress = ?, is_complete = 1
                      WHERE building_uuid = ?""",
                   (total_required, building_uuid))
        return {"complete": True, "message": f"{building.get('name', 'Building')} complete!"}
    else:
        db.execute("""UPDATE ext_sp_buildings SET build_progress = ? WHERE building_uuid = ?""",
                   (new_progress, building_uuid))
        return {"complete": False, "progress": round(new_progress / total_required * 100, 1)}


# ── Settlement analysis ──────────────────────────────────────────────

def detect_settlements() -> list[dict]:
    """Find settlement clusters by flood-fill over claimed areas with buildings.

    Returns a list of settlement dicts with synergy bonuses computed.
    """
    try:
        db = get_db()

    except Exception as e:
        log.error(f"detect_settlements failed: {e}")
        return []
    areas = get_all_areas()
    claimed_areas = db.fetch_all("""
        SELECT c.*, COUNT(b.id) as building_count
        FROM ext_sp_claims c
        LEFT JOIN ext_sp_buildings b ON c.area_uuid = b.area_uuid AND b.is_complete = 1
        GROUP BY c.id
    """)
    settlements = []

    for claim in claimed_areas:
        if claim["building_count"] < 1:
            continue
        area = get_area(claim["area_uuid"])
        guild_uuid = claim["guild_uuid"]
        settlements.append({
            "area_uuid": claim["area_uuid"],
            "guild_uuid": guild_uuid,
            "owner_uuid": claim["owner_uuid"],
            "buildings": claim["building_count"],
        })

    # Merge by guild
    guild_sets = {}
    for s in settlements:
        key = s["guild_uuid"] or s["owner_uuid"]
        if key not in guild_sets:
            guild_sets[key] = []
        guild_sets[key].append(s)

    results = []
    for key, areas_list in guild_sets.items():
        total_buildings = sum(a["buildings"] for a in areas_list)
        if len(areas_list) >= 3 and total_buildings >= 3:
            # Compute synergy bonuses
            bonuses = _calc_synergy_bonuses(key, areas_list)
            results.append({
                "guild_identifier": key,
                "area_count": len(areas_list),
                "total_buildings": total_buildings,
                "bonuses": bonuses,
            })
    return results


def _calc_synergy_bonuses(identifier: str, area_list: list) -> list:
    """Calculate synergy bonuses from building combinations in a settlement."""
    try:
        db = get_db()

    except Exception as e:
        log.error(f"_calc_synergy_bonuse failed: {e}")
        return []
    area_uuids = [a["area_uuid"] for a in area_list]
    placeholders = ",".join("?" * len(area_uuids))
    types = db.fetch_all(
        f"SELECT building_type FROM ext_sp_buildings WHERE area_uuid IN ({placeholders}) AND is_complete = 1",
        area_uuids)
    btypes = [t["building_type"] for t in types]
    bonuses = []
    if "wall" in btypes and "gate" in btypes and "tower" in btypes:
        bonuses.append({"name": "Fortified", "effect": "+50% defense for all defenders",
                        "type": "defense_mult", "value": 1.5})
    if "storehouse" in btypes and "well" in btypes:
        bonuses.append({"name": "Self-Sufficient", "effect": "-30% food/water decay",
                        "type": "decay_reduce", "value": 0.7})
    if len(btypes) >= 4:
        bonuses.append({"name": "Industrial Hub", "effect": "-40% crafting time",
                        "type": "craft_speed", "value": 0.6})
    return bonuses


# ── Claims tick ──────────────────────────────────────────────────────

def process_claims_tick() -> dict:
    """Decay claim strength for unmaintained claims.

    Claims with no buildings decay faster.
    """
    db = get_db()
    claims = db.fetch_all("""
        SELECT c.*, COUNT(b.id) as building_count
        FROM ext_sp_claims c
        LEFT JOIN ext_sp_buildings b ON c.area_uuid = b.area_uuid AND b.is_complete = 1
        GROUP BY c.id
    """)
    lost = 0
    for c in claims:
        decay = 2.0 if c["building_count"] == 0 else 0.5
        new_strength = c["claim_strength"] - decay
        if new_strength <= 0:
            db.execute("DELETE FROM ext_sp_claims WHERE id = ?", (c["id"],))
            lost += 1
        else:
            db.execute("UPDATE ext_sp_claims SET claim_strength = ? WHERE id = ?",
                       (round(new_strength, 1), c["id"]))
    return {"claims_decayed": len(claims), "claims_lost": lost}

