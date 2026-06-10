"""
tr_banditry.py — Banditry system for SIMULATED_TRADE extension.

Personas can attack trade routes to steal goods:
  - Surprise/stealth check
  - Combat resolution (abstract)
  - Loot calculation with destruction chance
  - Reputation consequences
  - Guard response
"""

import json, math, random
from typing import Any, Optional

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_plugin_registry import fire_hook
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import get_config
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    record_banditry, get_active_caravans_on_route,
    update_notoriety, update_bounty, update_reputation_score,
    get_reputation, ensure_reputation, set_jail_ticks,
    set_cooldown, check_cooldown, is_persona_jailed,
    get_routes_for_persona, get_db,
)

log = get_logger()


def attempt_banditry(attacker_uuid: str, route_uuid: str,
                     group_uuids: Optional[list[str]] = None) -> dict:
    """Attempt a banditry attack on a trade route.

    Args:
        attacker_uuid: The persona leading the attack
        route_uuid: The target route
        group_uuids: Additional personas in the attack group

    Returns:
        {"ok": True, "incident_uuid": str, "outcome": str, ...}
        or {"ok": False, "error": str}
    """
    # ── Validation ──────────────────────────────────────────────
    if is_persona_jailed(attacker_uuid):
        return {"ok": False, "error": "Attacker is in jail"}

    min_combat = get_config("banditry", "min_combat_skill", 20)
    combat_skill = _get_combat_skill(attacker_uuid)
    if combat_skill < min_combat:
        return {"ok": False, "error": f"Combat skill too low ({combat_skill} < {min_combat})"}

    route = _get_route_by_uuid(route_uuid)
    if not route:
        return {"ok": False, "error": "Route not found"}
    if not route.get("is_active"):
        return {"ok": False, "error": "Route is inactive"}

    # Check cooldown
    if check_cooldown(attacker_uuid, "banditry"):
        return {"ok": False, "error": "On banditry cooldown"}

    # ── Gather group combat data ────────────────────────────────
    all_attackers = [attacker_uuid] + (group_uuids or [])
    max_group = get_config("banditry", "max_group_size", 10)
    if len(all_attackers) > max_group:
        return {"ok": False, "error": f"Group too large (max {max_group})"}

    total_combat = combat_skill
    for g_uuid in (group_uuids or []):
        if is_persona_jailed(g_uuid):
            return {"ok": False, "error": f"Group member {g_uuid[:8]} is in jail"}
        total_combat += _get_combat_skill(g_uuid)

    # ── Surprise check ──────────────────────────────────────────
    stealth = _get_stealth_skill(attacker_uuid)
    bandit_activity = route.get("bandit_activity", 0.0)
    guard_perception = 40 + (1 - bandit_activity) * 20
    surprise_roll = random.randint(1, 100)
    surprise_bonus = 1.0

    if (stealth + surprise_roll) > guard_perception:
        surprise_bonus = get_config("banditry", "surprise_multiplier", 1.3)
        log.info("tr_banditry", f"Surprise achieved! stealth={stealth}+roll={surprise_roll} vs perception={guard_perception}")

    # ── Combat resolution ───────────────────────────────────────
    attacker_power = total_combat * surprise_bonus
    defender_power = route.get("guard_combat_power", 5.0) * (1.0 + bandit_activity * 2.0)

    power_ratio = attacker_power / max(defender_power, 0.1)

    if power_ratio > 1.5:
        outcome = "victory"
        loot_pct = 1.0
        casualties = {"attackers": [], "defenders": ["guards_killed"]}
    elif power_ratio >= 0.8:
        outcome = "costly_victory"
        loot_pct = 0.6
        casualties = _roll_costly_casualties(all_attackers)
    else:
        outcome = "defeat"
        loot_pct = 0.0
        casualties = {"attackers": all_attackers, "defenders": []}

    # ── Loot calculation ────────────────────────────────────────
    caravans = get_active_caravans_on_route(route_uuid)
    total_loot = {}
    total_loot_value = 0.0
    destruction_chance = get_config("banditry", "loot_destruction_chance", 0.1)

    for caravan in caravans:
        cargo = json.loads(caravan.get("cargo_json", "{}"))
        for item_id, qty in cargo.items():
            # Loot based on outcome
            lootable = int(qty * loot_pct)
            # Destruction roll
            destroyed = 0
            for _ in range(lootable):
                if random.random() < destruction_chance:
                    destroyed += 1
            actual_loot = lootable - destroyed
            if actual_loot > 0:
                total_loot[item_id] = total_loot.get(item_id, 0) + actual_loot
                total_loot_value += _get_item_value(item_id) * actual_loot

        # Also take gold
        gold_carried = caravan.get("gold_carried", 0.0)
        total_loot_value += gold_carried * loot_pct

    # ── Consequences ────────────────────────────────────────────
    notoriety_ratio = get_config("banditry", "notoriety_per_value_ratio", 0.1)
    notoriety_gained = int(max(50, total_loot_value * notoriety_ratio))
    bounty_ratio = get_config("banditry", "bounty_ratio", 0.5)
    bounty_placed = total_loot_value * bounty_ratio / max(1, len(all_attackers))

    # Apply consequences to all attackers
    for a_uuid in all_attackers:
        ensure_reputation(a_uuid)
        update_notoriety(a_uuid, notoriety_gained)
        update_bounty(a_uuid, bounty_placed)
        update_reputation_score(a_uuid, -notoriety_gained // 2)

    # Capture/arrest if defeated
    if outcome == "defeat":
        escape_chance = get_config("banditry", "defeat_escape_chance", 0.3)
        for a_uuid in all_attackers:
            if random.random() > escape_chance:
                jail_ticks = get_config("banditry", "capture_jail_base_ticks", 50)
                jail_ticks += notoriety_gained // 2
                set_jail_ticks(a_uuid, jail_ticks)
                log.info("tr_banditry", f"{a_uuid[:8]} captured, jailed for {jail_ticks} ticks")

    # ── Update route bandit activity ────────────────────────────
    _increase_bandit_activity(route_uuid, total_loot_value)

    # ── Set cooldowns ───────────────────────────────────────────
    cooldown = 20  # banditry has longer cooldown
    set_cooldown(attacker_uuid, "banditry", cooldown)
    for g_uuid in (group_uuids or []):
        set_cooldown(g_uuid, "banditry", cooldown)

    # ── Record incident ─────────────────────────────────────────
    incident_uuid = record_banditry(
        attacker_uuid=attacker_uuid,
        target_route_uuid=route_uuid,
        combat_power=attacker_power,
        defense_power=defender_power,
        outcome=outcome,
        loot_value=total_loot_value,
        items_stolen=total_loot,
        casualties=casualties,
        notoriety_gained=notoriety_gained,
        bounty_placed=bounty_placed,
    )

    log.info("tr_banditry",
             f"Banditry {incident_uuid}: {attacker_uuid[:8]} on route {route_uuid[:8]} "
             f"-> {outcome} (ratio={power_ratio:.2f}, loot={total_loot_value:.0f}g, "
             f"notoriety={notoriety_gained}, bounty={bounty_placed:.0f})")

    # ── Fire hooks ──────────────────────────────────────────────
    fire_hook("on_banditry_completed",
              incident_uuid=incident_uuid,
              attacker_uuid=attacker_uuid,
              route_uuid=route_uuid,
              outcome=outcome,
              loot_value=total_loot_value,
              notoriety_gained=notoriety_gained)

    return {
        "ok": True,
        "incident_uuid": incident_uuid,
        "outcome": outcome,
        "loot_value": total_loot_value,
        "items_stolen": total_loot,
        "power_ratio": round(power_ratio, 2),
        "notoriety_gained": notoriety_gained,
        "bounty_placed": round(bounty_placed, 2),
        "surprise": surprise_bonus > 1.0,
        "casualties": casualties,
    }


def get_banditry_risk(route_uuid: str, persona_uuid: str) -> dict:
    """Evaluate the risk of banditry on a route from a persona's perspective.

    Returns:
        Dict with risk assessment scores.
    """
    route = _get_route_by_uuid(route_uuid)
    if not route:
        return {"error": "Route not found"}

    combat = _get_combat_skill(persona_uuid)
    stealth = _get_stealth_skill(persona_uuid)
    defense = route.get("guard_combat_power", 5.0) * (1.0 + route.get("bandit_activity", 0.0) * 2.0)

    surprise_chance = min(1.0, max(0.0, (stealth - 30) / 100.0))
    victory_chance = min(1.0, max(0.0, (combat * 1.3 - defense) / (combat * 1.3 + defense + 1)))

    rep = get_reputation(persona_uuid)
    risk_score = 1.0 - victory_chance
    if rep and rep.get("bounty", 0) > 0:
        risk_score += 0.2  # Already wanted

    return {
        "victory_chance": round(victory_chance, 2),
        "surprise_chance": round(surprise_chance, 2),
        "risk_score": round(min(1.0, risk_score), 2),
        "defender_power": round(defense, 1),
        "your_power": round(combat, 1),
        "current_bounty": rep.get("bounty", 0.0) if rep else 0.0,
    }


def process_guard_response() -> dict:
    """Process guard patrols and criminal investigations.

    Returns:
        Stats on arrests made.
    """
    stats = {"arrests": 0, "fines_collected": 0.0}

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
        get_all_wanted_personas
    )
    wanted = get_all_wanted_personas()

    for criminal in wanted:
        # Guards find criminals with probability based on notoriety
        notoriety = criminal.get("notoriety", 0)
        find_chance = min(0.8, notoriety / 500.0)

        if random.random() < find_chance:
            # Attempt arrest
            guard_power = 30.0  # Base guard power
            criminal_power = _get_combat_skill(criminal["persona_uuid"])

            if guard_power > criminal_power or random.random() < 0.6:
                # Arrested
                bounty = criminal.get("bounty", 0.0)
                fine = bounty * get_config("reputation", "bounty_clear_multiplier", 1.5)
                jail_ticks = int(notoriety * get_config("reputation", "jail_ticks_per_notoriety", 0.5))

                set_jail_ticks(criminal["persona_uuid"], jail_ticks)
                # Clear bounty on arrest
                update_bounty(criminal["persona_uuid"], -bounty)

                stats["arrests"] += 1
                stats["fines_collected"] += fine

                log.info("tr_banditry",
                         f"Guard arrest: {criminal['persona_uuid'][:8]} "
                         f"(bounty={bounty:.0f}, jail={jail_ticks} ticks)")

    return stats


# ── Internal helpers ─────────────────────────────────────────────────

def _get_combat_skill(persona_uuid: str) -> float:
    """Get combat skill from state."""
    state = get_state()
    skills = state.get("persona_skills", {})
    return skills.get(persona_uuid, {}).get("combat", 10.0)


def _get_stealth_skill(persona_uuid: str) -> float:
    """Get stealth skill from state."""
    state = get_state()
    skills = state.get("persona_skills", {})
    return skills.get(persona_uuid, {}).get("stealth", 10.0)


def _get_item_value(item_id: str) -> float:
    """Get base value of an item."""
    VALUES = {
        "minecraft:diamond": 50.0,
        "minecraft:iron_ingot": 10.0,
        "minecraft:gold_ingot": 15.0,
        "minecraft:emerald": 20.0,
        "minecraft:netherite_ingot": 200.0,
        "minecraft:diamond_pickaxe": 100.0,
        "minecraft:diamond_sword": 120.0,
    }
    return VALUES.get(item_id, 5.0)


def _roll_costly_casualties(attackers: list[str]) -> dict:
    """Roll for casualties in a costly victory."""
    casualties = {"attackers": [], "defenders": ["some_guards"]}
    for a in attackers:
        if random.random() < 0.3:  # 30% chance of injury
            casualties["attackers"].append(a)
    return casualties


def _increase_bandit_activity(route_uuid: str, loot_value: float) -> None:
    """Increase bandit activity on a route after successful attack."""
    db = get_db()
    increase = min(0.3, loot_value / 1000.0)
    db.execute("""
        UPDATE ext_tr_routes
        SET bandit_activity = MIN(1.0, bandit_activity + ?)
        WHERE route_uuid = ?
    """, (increase, route_uuid))
    db.conn.commit()

    # Update state
    state = get_state()
    state.set("last_bandit_route", route_uuid, "SIMULATED_TRADE")


def _get_route_by_uuid(route_uuid: str) -> Optional[dict]:
    """Get route by UUID from DB."""
    db = get_db()
    cursor = db.execute(
        "SELECT * FROM ext_tr_routes WHERE route_uuid = ?",
        (route_uuid,)
    )
    row = cursor.fetchone()
    return dict(row) if row else None

