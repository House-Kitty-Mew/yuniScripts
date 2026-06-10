"""
sp_skills.py — Persona skill system.

Each persona has 7 skills (1-100) that affect their behavior, resource
discovery, combat effectiveness, and purchasing power.

Skills are generated on persona creation and improve through use.
"""

import random, json
from typing import Optional
from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db, ensure_schema

log = get_logger()


# ── Skill definitions ───────────────────────────────────────────────

SKILL_NAMES = ["mining", "combat", "farming", "trading", "crafting", "exploration", "leadership"]

# Archetype → starting skill ranges
_SKILL_RANGES = {
    "miner":       {"mining": (40, 70), "combat": (10, 30), "farming": (5, 15), "trading": (10, 30),
                    "crafting": (15, 35), "exploration": (20, 40), "leadership": (5, 20)},
    "farmer":      {"mining": (5, 20), "combat": (5, 20), "farming": (40, 70), "trading": (15, 35),
                    "crafting": (10, 25), "exploration": (10, 25), "leadership": (10, 25)},
    "warrior":     {"mining": (10, 25), "combat": (50, 80), "farming": (5, 15), "trading": (5, 20),
                    "crafting": (10, 25), "exploration": (20, 40), "leadership": (20, 45)},
    "merchant":    {"mining": (5, 15), "combat": (5, 20), "farming": (5, 15), "trading": (50, 80),
                    "crafting": (15, 30), "exploration": (10, 25), "leadership": (20, 40)},
    "builder":     {"mining": (20, 40), "combat": (10, 25), "farming": (10, 25), "trading": (15, 30),
                    "crafting": (40, 70), "exploration": (10, 25), "leadership": (10, 30)},
    "mage":        {"mining": (10, 25), "combat": (15, 35), "farming": (10, 25), "trading": (20, 40),
                    "crafting": (40, 65), "exploration": (20, 45), "leadership": (10, 30)},
    "adventurer":  {"mining": (20, 40), "combat": (30, 55), "farming": (5, 20), "trading": (10, 25),
                    "crafting": (10, 25), "exploration": (40, 70), "leadership": (15, 35)},
    "vagabond":    {"mining": (15, 30), "combat": (20, 40), "farming": (5, 15), "trading": (5, 20),
                    "crafting": (10, 20), "exploration": (30, 60), "leadership": (5, 15)},
}


def generate_skills(archetype: str) -> dict[str, int]:
    """Generate starting skills for a persona based on archetype.

    Returns dict: {skill_name: level (1-100)}
    """
    ranges = _SKILL_RANGES.get(archetype, _SKILL_RANGES["adventurer"])
    skills = {}
    for skill in SKILL_NAMES:
        lo, hi = ranges.get(skill, (10, 30))
        skills[skill] = random.randint(lo, hi)
    return skills


def save_skills(persona_uuid: str, skills: dict[str, int]):
    """Insert or update skills for a persona."""
    db = get_db()
    db.execute("""
        INSERT OR REPLACE INTO ext_sp_persona_skills
        (persona_uuid, mining, combat, farming, trading, crafting, exploration, leadership)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid,
          skills.get("mining", 10),
          skills.get("combat", 10),
          skills.get("farming", 10),
          skills.get("trading", 10),
          skills.get("crafting", 10),
          skills.get("exploration", 10),
          skills.get("leadership", 10)))


def get_skills(persona_uuid: str) -> Optional[dict[str, int]]:
    """Get skills for a persona, or None."""
    db = get_db()
    row = db.fetch_one(
        "SELECT * FROM ext_sp_persona_skills WHERE persona_uuid = ?",
        (persona_uuid,))
    if not row:
        return None
    return {s: row[s] for s in SKILL_NAMES}


def improve_skill(persona_uuid: str, skill: str, amount: int = 1) -> Optional[int]:
    """Increase a skill by amount (capped at 100).

    Returns new skill level, or None if persona not found.
    """
    if skill not in SKILL_NAMES:
        return None
    skills = get_skills(persona_uuid)
    if not skills:
        return None

    new_val = min(100, skills[skill] + amount)
    skills[skill] = new_val
    save_skills(persona_uuid, skills)
    return new_val


def best_skill(persona_uuid: str) -> tuple[str, int]:
    """Get the persona's highest skill.

    Returns (skill_name, level).
    """
    skills = get_skills(persona_uuid) or {s: 10 for s in SKILL_NAMES}
    best = max(skills.items(), key=lambda x: x[1])
    return best


def skill_to_action(skill: str) -> str:
    """Map a skill name to a persona action description."""
    mapping = {
        "mining": "heads to the mines to extract resources",
        "combat": "patrols the territory, keeping watch for threats",
        "farming": "tends to the fields and harvests crops",
        "trading": "visits the market to check prices and trade",
        "crafting": "retreats to the workshop to craft and repair",
        "exploration": "ventures into uncharted territory to explore",
        "leadership": "holds court, managing affairs and giving orders",
    }
    return mapping.get(skill, "goes about their daily business")
