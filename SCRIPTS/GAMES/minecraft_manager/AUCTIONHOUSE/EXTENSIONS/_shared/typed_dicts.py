"""
typed_dicts.py — Shared TypedDict definitions for common AH return types.

Provides structured type hints for the dicts returned by extension
functions, making the codebase more maintainable and enabling mypy
validation.

Usage:
    from EXTENSIONS._shared.typed_dicts import (
        PersonaProfile, PersonaHealth, CombatStats,
        WoundRecord, PriceMemory, LifeEvent,
    )

    def get_persona(uuid: str) -> PersonaProfile: ...
"""

from typing import TypedDict, Optional, List, Dict, Any


# ═══════════════════════════════════════════════════════════════════
# Persona types
# ═══════════════════════════════════════════════════════════════════

class PersonaProfile(TypedDict, total=False):
    """A simulated persona's static profile."""
    persona_uuid: str
    name: str
    archetype: str
    job: str
    region: str
    wealth_tier: str
    personality_traits: str  # JSON string
    active: int              # 0 or 1
    created_at: str          # ISO timestamp
    last_active_at: str      # ISO timestamp


class PersonaHealth(TypedDict, total=False):
    """A persona's current health state."""
    persona_uuid: str
    food: float
    hydration: float
    energy: float
    immune: float
    temperature: float
    alive: int               # 0 or 1


class PersonaFinances(TypedDict, total=False):
    """A persona's financial state."""
    persona_uuid: str
    balance: float
    lifetime_income: float
    lifetime_spending: float
    income_per_tick: float
    savings_goal: float
    debt: float


class PersonaNeed(TypedDict, total=False):
    """An item need for a persona."""
    persona_uuid: str
    item_id: str
    urgency: int             # 1-10
    max_price: float
    desired_quantity: int
    quantity_obtained: int
    reason: str


# ═══════════════════════════════════════════════════════════════════
# Combat & wound types
# ═══════════════════════════════════════════════════════════════════

class CombatStats(TypedDict, total=False):
    """Calculated combat attributes for a persona."""
    strength: float
    agility: float
    endurance: float
    perception: float
    pain_threshold: float
    armor_rating: float
    weapon_skill: float
    pain_penalty: float
    terrain: str
    weapon: float


class WoundRecord(TypedDict, total=False):
    """A wound on a persona."""
    wound_uuid: str
    owner_uuid: str
    body_part: str
    wound_type: str
    severity: int            # 1-4
    bleed_rate: float
    pain_level: float
    infection_chance: float
    infection_progress: float
    is_infected: int
    is_bandaged: int
    is_healed: int
    created_by: str
    created_at: str


class CombatResult(TypedDict, total=False):
    """Result of a combat action."""
    hit: bool
    critical: bool
    body_part: str
    wound_type: str
    severity: str
    bleed_rate: float
    pain: float
    message: str
    roll: int
    hit_chance: int


# ═══════════════════════════════════════════════════════════════════
# Memory types
# ═══════════════════════════════════════════════════════════════════

class PriceMemory(TypedDict, total=False):
    """A persona's memory of a market price."""
    memory_uuid: str
    persona_uuid: str
    content: str
    memory_type: str
    importance: float
    confidence: float
    created_at: str
    activation_score: float
    price: float
    item_id: str


class LifeEvent(TypedDict, total=False):
    """A persona's personal life event."""
    event_type: str
    description: str
    financial_impact: float
    mood_impact: str
    duration_hours: int


# ═══════════════════════════════════════════════════════════════════
# State registry types
# ═══════════════════════════════════════════════════════════════════

class SimulationCycleState(TypedDict, total=False):
    """State snapshot for a simulation cycle phase."""
    active_personas: List[Dict[str, Any]]
    persona_needs: Dict[str, List[Dict[str, Any]]]
    interactions: List[Dict[str, Any]]
    relationship_changes: List[Dict[str, Any]]
    chat_messages: List[Dict[str, Any]]
    announcements: List[Dict[str, Any]]
