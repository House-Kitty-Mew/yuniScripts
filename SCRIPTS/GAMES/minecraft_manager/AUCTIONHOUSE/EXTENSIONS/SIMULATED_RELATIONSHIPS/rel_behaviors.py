"""
rel_behaviors.py — Complex behavior patterns for simulated relationships.

Defines behavioral archetypes, compatibility matrices, and behavior-driven
decision trees that control how personas interact when they meet.

Each persona's behavior is derived from:
  - Their archetype (from SIMULATED_PEOPLE)
  - Personality traits (aggression, generosity, curiosity, etc.)
  - Current relationship state with the other persona
  - Active situationships
  - Skills (combat, trading, leadership)
  - Health status (hungry, tired personas are more irritable)
  - Wealth tier

Provides:
  - Behavior compatibility checks
  - Behavior-driven outcome probabilities
  - Narrative generation helpers
"""

import random
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()


# ── Behavioral Archetypes (extending the SP archetype system) ───────

BEHAVIOR_ARCHETYPES = {
    "adventurer": {
        "name": "Adventurer",
        "traits": {"aggression": 4, "generosity": 5, "curiosity": 9,
                    "sociability": 6, "cautiousness": 3, "territoriality": 2,
                    "honor": 6, "impulsiveness": 7},
        "conflict_threshold": 40,     # Lower = more likely to escalate conflicts
        "cooperation_bias": 0.6,       # 0-1: likelihood to cooperate
        "gossip_spread": 0.7,          # How likely they spread rumors/news
        "grudge_holding": 0.3,         # How long they hold grudges
        "jealousy": 3,                 # 1-10
        "forgiveness": 6,              # 1-10
    },
    "merchant": {
        "name": "Merchant",
        "traits": {"aggression": 2, "generosity": 3, "curiosity": 6,
                    "sociability": 8, "cautiousness": 7, "territoriality": 3,
                    "honor": 4, "impulsiveness": 2},
        "conflict_threshold": 60,
        "cooperation_bias": 0.8,
        "gossip_spread": 0.8,
        "grudge_holding": 0.6,
        "jealousy": 5,
        "forgiveness": 4,
    },
    "builder": {
        "name": "Builder",
        "traits": {"aggression": 2, "generosity": 6, "curiosity": 5,
                    "sociability": 5, "cautiousness": 6, "territoriality": 7,
                    "honor": 5, "impulsiveness": 3},
        "conflict_threshold": 55,
        "cooperation_bias": 0.7,
        "gossip_spread": 0.4,
        "grudge_holding": 0.5,
        "jealousy": 4,
        "forgiveness": 5,
    },
    "miner": {
        "name": "Miner",
        "traits": {"aggression": 4, "generosity": 4, "curiosity": 5,
                    "sociability": 3, "cautiousness": 5, "territoriality": 8,
                    "honor": 5, "impulsiveness": 4},
        "conflict_threshold": 45,
        "cooperation_bias": 0.5,
        "gossip_spread": 0.3,
        "grudge_holding": 0.7,
        "jealousy": 6,
        "forgiveness": 3,
    },
    "farmer": {
        "name": "Farmer",
        "traits": {"aggression": 2, "generosity": 7, "curiosity": 4,
                    "sociability": 5, "cautiousness": 6, "territoriality": 6,
                    "honor": 6, "impulsiveness": 2},
        "conflict_threshold": 60,
        "cooperation_bias": 0.8,
        "gossip_spread": 0.5,
        "grudge_holding": 0.4,
        "jealousy": 3,
        "forgiveness": 7,
    },
    "warrior": {
        "name": "Warrior",
        "traits": {"aggression": 8, "generosity": 5, "curiosity": 5,
                    "sociability": 5, "cautiousness": 4, "territoriality": 7,
                    "honor": 8, "impulsiveness": 6},
        "conflict_threshold": 30,
        "cooperation_bias": 0.5,
        "gossip_spread": 0.5,
        "grudge_holding": 0.8,
        "jealousy": 5,
        "forgiveness": 4,
    },
    "mage": {
        "name": "Mage",
        "traits": {"aggression": 3, "generosity": 4, "curiosity": 9,
                    "sociability": 3, "cautiousness": 7, "territoriality": 5,
                    "honor": 4, "impulsiveness": 3},
        "conflict_threshold": 50,
        "cooperation_bias": 0.5,
        "gossip_spread": 0.6,
        "grudge_holding": 0.6,
        "jealousy": 4,
        "forgiveness": 5,
    },
    "vagabond": {
        "name": "Vagabond",
        "traits": {"aggression": 4, "generosity": 5, "curiosity": 8,
                    "sociability": 7, "cautiousness": 2, "territoriality": 1,
                    "honor": 3, "impulsiveness": 8},
        "conflict_threshold": 50,
        "cooperation_bias": 0.6,
        "gossip_spread": 0.9,
        "grudge_holding": 0.2,
        "jealousy": 2,
        "forgiveness": 8,
    },
}


# ── Compatibility Matrix ────────────────────────────────────────────

# ── Default traits for missing archetype data ─────────────────
_DEFAULT_TRAITS = {"aggression": 5, "generosity": 5, "curiosity": 5,
                    "sociability": 5, "cautiousness": 5, "territoriality": 5,
                    "honor": 5, "impulsiveness": 5}

_COMPATIBILITY_MATRIX = {
    # (archetype_a, archetype_b) -> base_compatibility 0-100
    ("adventurer", "adventurer"): 70,
    ("adventurer", "merchant"): 40,
    ("adventurer", "warrior"): 60,
    ("adventurer", "mage"): 55,
    ("adventurer", "miner"): 35,
    ("adventurer", "farmer"): 40,
    ("adventurer", "builder"): 45,
    ("adventurer", "vagabond"): 65,
    ("merchant", "merchant"): 50,
    ("merchant", "warrior"): 35,
    ("merchant", "mage"): 50,
    ("merchant", "miner"): 55,
    ("merchant", "farmer"): 50,
    ("merchant", "builder"): 55,
    ("merchant", "vagabond"): 40,
    ("warrior", "warrior"): 40,
    ("warrior", "mage"): 30,
    ("warrior", "miner"): 45,
    ("warrior", "farmer"): 40,
    ("warrior", "builder"): 45,
    ("warrior", "vagabond"): 50,
    ("mage", "mage"): 60,
    ("mage", "miner"): 30,
    ("mage", "farmer"): 35,
    ("mage", "builder"): 50,
    ("mage", "vagabond"): 45,
    ("miner", "miner"): 60,
    ("miner", "farmer"): 40,
    ("miner", "builder"): 55,
    ("miner", "vagabond"): 35,
    ("farmer", "farmer"): 70,
    ("farmer", "builder"): 55,
    ("farmer", "vagabond"): 30,
    ("builder", "builder"): 65,
    ("builder", "vagabond"): 35,
    ("vagabond", "vagabond"): 60,
}


def get_behavior(archetype: Optional[str] = None) -> dict:
    """Get the full behavioral archetype definition.

    Returns adventurer defaults for None or unknown archetypes.
    """
    if not archetype or not isinstance(archetype, str):
        return BEHAVIOR_ARCHETYPES["adventurer"]
    return BEHAVIOR_ARCHETYPES.get(archetype, BEHAVIOR_ARCHETYPES["adventurer"])


def get_compatibility(archetype_a: str, archetype_b: str) -> int:
    """Get base compatibility between two archetypes (0-100)."""
    key = (archetype_a, archetype_b)
    reverse = (archetype_b, archetype_a)
    base = _COMPATIBILITY_MATRIX.get(key) or _COMPATIBILITY_MATRIX.get(reverse, 50)
    return base


def compute_interaction_bias(persona_a: dict, persona_b: dict,
                              relationship_strength: float = 50.0,
                              use_thinking_mode: bool = True) -> float:
    """Compute the probability (0-1) of a positive interaction.

    When use_thinking_mode is True, generates a persona-aware reasoning
    trace and uses it to subtly influence the bias with personality-driven
    modulation. Falls back to pure deterministic calculation when disabled.

    Factors:
      - Archetype compatibility
      - Current relationship strength
      - Aggression trait of both personas
      - Honor trait (higher = more fair)
    """
    if persona_a is None or persona_b is None:
        return 0.5

    name_a = persona_a.get("name", "Unknown")
    name_b = persona_b.get("name", "Unknown")
    arch_a = persona_a.get("archetype", "adventurer")
    arch_b = persona_b.get("archetype", "adventurer")

    compat = get_compatibility(arch_a, arch_b)

    beh_a = get_behavior(arch_a)
    beh_b = get_behavior(arch_b)

    aggression_factor = (beh_a["traits"]["aggression"] + beh_b["traits"]["aggression"]) / 20.0
    honor_factor = (beh_a["traits"]["honor"] + beh_b["traits"]["honor"]) / 20.0

    # Normalize relationship strength to 0-1
    rel_factor = relationship_strength / 100.0

    # Weighted combination (deterministic baseline)
    bias = (0.35 * (compat / 100.0) +
            0.25 * rel_factor +
            0.20 * honor_factor +
            0.20 * (1.0 - aggression_factor))

    if use_thinking_mode:
        # ── Thinking-mode reasoning trace ──────────────────────────────
        reasoning_trace: list[str] = []
        reasoning_trace.append(
            f"[Thinking] Evaluating interaction between {name_a} ({arch_a}) "
            f"and {name_b} ({arch_b})..."
        )
        reasoning_trace.append(
            f"[Thinking] Archetype compatibility: {compat}/100 "
            f"({'favorable' if compat >= 60 else 'neutral' if compat >= 40 else 'strained'})"
        )
        reasoning_trace.append(
            f"[Thinking] Relationship strength: {relationship_strength:.1f}/100 "
            f"({'strong' if relationship_strength > 65 else 'moderate' if relationship_strength > 35 else 'weak'})"
        )
        reasoning_trace.append(
            f"[Thinking] {name_a}: aggression={beh_a['traits']['aggression']}, "
            f"honor={beh_a['traits']['honor']}, "
            f"curiosity={beh_a['traits']['curiosity']}, "
            f"sociability={beh_a['traits']['sociability']}"
        )
        reasoning_trace.append(
            f"[Thinking] {name_b}: aggression={beh_b['traits']['aggression']}, "
            f"honor={beh_b['traits']['honor']}, "
            f"curiosity={beh_b['traits']['curiosity']}, "
            f"sociability={beh_b['traits']['sociability']}"
        )

        # Synthesize a considered perturbation based on personality nuances
        # High curiosity + high sociability = slight positive boost (they enjoy novelty)
        # High aggression + low honor = slight negative shift
        curiosity_factor = (beh_a["traits"]["curiosity"] + beh_b["traits"]["curiosity"]) / 20.0
        sociability_factor = (beh_a["traits"]["sociability"] + beh_b["traits"]["sociability"]) / 20.0
        forgiveness_factor = (beh_a["traits"].get("forgiveness", 5) +
                              beh_b["traits"].get("forgiveness", 5)) / 20.0
        jealousy_factor = (beh_a["traits"].get("jealousy", 5) +
                           beh_b["traits"].get("jealousy", 5)) / 20.0

        # Modulation: positive drives from curiosity, sociability, forgiveness
        # Negative drag from aggression, jealousy
        thinking_modulation = (
            +0.08 * curiosity_factor
            + 0.06 * sociability_factor
            + 0.06 * forgiveness_factor
            - 0.06 * jealousy_factor
            - 0.06 * (1.0 - honor_factor)      # low honor = less fair
        )

        reasoning_trace.append(
            f"[Thinking] Synthesizing: curiosity={curiosity_factor:.2f}, "
            f"sociability={sociability_factor:.2f}, forgiveness={forgiveness_factor:.2f}, "
            f"jealousy={jealousy_factor:.2f} → modulation={thinking_modulation:+.3f}"
        )

        adjusted_bias = bias + thinking_modulation
        final_bias = max(0.05, min(0.95, adjusted_bias))

        reasoning_trace.append(
            f"[Thinking] Final bias: {final_bias:.3f} "
            f"({'cordial' if final_bias > 0.6 else 'cautious' if final_bias > 0.4 else 'hostile'})"
        )

        log.info("rel_behaviors",
                 f"compute_interaction_bias reasoning for {name_a} / {name_b}: {
                     chr(10).join(reasoning_trace)}")

        return final_bias

    # ── Fallback: pure deterministic (AI disabled) ─────────────────
    return max(0.05, min(0.95, bias))


def determine_contention_outcome(personas: list[dict],
                                  area_type: Optional[str] = None,
                                  has_resources: bool = False,
                                  use_thinking_mode: bool = True) -> dict:
    """Determine the likely outcome of a multi-persona contention event.

    Analyzes all personas involved and predicts the outcome type.

    When use_thinking_mode is True, generates a persona-aware reasoning
    trace that examines each participant, the environment, and resource
    pressures, then uses that reasoning to subtly influence the outcome
    via personality-driven modulation. Falls back to pure deterministic
    calculation when disabled.

    Args:
        personas: List of persona dicts with (archetype, skills, health, etc.)
        area_type: The biome/area type
        has_resources: Whether the area has contested resources
        use_thinking_mode: Whether to generate thinking-mode reasoning

    Returns:
        Dict with outcome, confidence, reason, and optionally reasoning_trace
    """
    if not isinstance(personas, list) or len(personas) == 0:
        return {"outcome": "solitary", "confidence": 1.0,
                "reason": "no personas present"}
    if not area_type or not isinstance(area_type, str):
        area_type = "plains"

    if len(personas) < 2:
        return {"outcome": "solitary", "confidence": 1.0, "reason": "single occupant"}

    # ── Gather persona data ────────────────────────────────────────
    persona_data: list[dict] = []
    total_aggression = 0
    total_territoriality = 0
    total_honor = 0
    total_sociability = 0
    total_impulsiveness = 0
    total_cooperation_bias = 0.0

    for p in personas:
        arch = p.get("archetype", "adventurer")
        beh = get_behavior(arch)
        total_aggression += beh["traits"]["aggression"]
        total_territoriality += beh["traits"]["territoriality"]
        total_honor += beh["traits"]["honor"]
        total_sociability += beh["traits"]["sociability"]
        total_impulsiveness += beh["traits"]["impulsiveness"]
        total_cooperation_bias += beh["cooperation_bias"]
        persona_data.append({
            "name": p.get("name", f"Persona_{p.get('persona_uuid', '?')[:4]}"),
            "archetype": arch,
            "traits": {k: v for k, v in beh["traits"].items()},
            "cooperation_bias": beh["cooperation_bias"],
        })

    n = len(personas)
    avg_aggression = total_aggression / n
    avg_territoriality = total_territoriality / n
    avg_honor = total_honor / n
    avg_sociability = total_sociability / n
    avg_impulsiveness = total_impulsiveness / n
    avg_cooperation = total_cooperation_bias / n

    # Territorial areas increase conflict chance
    territorial_modifier = 1.3 if area_type in ("mountains", "deep_dark",
                                                  "nether_wastes") else 1.0

    # Scarce resources increase conflict
    resource_modifier = 1.5 if has_resources else 0.8

    # Deterministic baseline conflict score
    conflict_score = (avg_aggression * 0.35 +
                      avg_territoriality * 0.25 -
                      avg_honor * 0.15 +
                      (10 - avg_sociability) * 0.15) * territorial_modifier * resource_modifier

    if use_thinking_mode:
        # ── Thinking-mode reasoning trace ──────────────────────────
        reasoning_trace: list[str] = []
        reasoning_trace.append(
            f"[Thinking] Contention analysis — {n} persona(s) in "
            f"'{area_type}' {'with' if has_resources else 'without'} contested resources"
        )

        # Step 1: Profile each participant
        for pd in persona_data:
            t = pd["traits"]
            demeanour = (
                "aggressive" if t["aggression"] >= 7 else
                "assertive" if t["aggression"] >= 5 else
                "passive"
            )
            reasoning_trace.append(
                f"[Thinking]   {pd['name']} ({pd['archetype']}): "
                f"{demeanour}, honor={t['honor']}, "
                f"impulsive={t['impulsiveness']}, "
                f"cooperation={pd['cooperation_bias']:.1f}"
            )

        # Step 2: Environmental reasoning
        reasoning_trace.append(
            f"[Thinking] Environment: area_type='{area_type}', "
            f"territorial_modifier={territorial_modifier}, "
            f"resource_modifier={resource_modifier}"
        )
        pressure_assessment = (
            "high pressure — competition is likely"
            if has_resources and territorial_modifier > 1.0
            else "moderate pressure — neutral conditions"
            if not has_resources
            else "low pressure — ample space"
        )
        reasoning_trace.append(f"[Thinking] Situational pressure: {pressure_assessment}")

        # Step 3: Trait synthesis — compute a thinking-informed modulation
        # High impulsiveness + low honor = escalate tension
        # High cooperation_bias = de-escalate
        thinking_modulation = (
            -0.06 * avg_cooperation       # cooperation reduces conflict
            + 0.08 * (avg_impulsiveness / 10.0)  # impulsiveness increases it
            - 0.05 * avg_honor / 10.0     # honor reduces conflict
        )

        adjusted_score = conflict_score + thinking_modulation * 30.0  # scale into score range
        adjusted_score = max(0.0, adjusted_score)

        reasoning_trace.append(
            f"[Thinking] Synthesis: avg_coop={avg_cooperation:.2f}, "
            f"avg_impulse={avg_impulsiveness:.1f}/10, "
            f"avg_honor={avg_honor:.1f}/10 → "
            f"modulation={thinking_modulation:+.3f} "
            f"(score: {conflict_score:.1f} → {adjusted_score:.1f})"
        )

        # Step 4: Determine outcome from adjusted score
        if adjusted_score > 65:
            outcome = "conflict"
            confidence = adjusted_score / 100.0
            reason = "high aggression and territoriality in a contested area"
            reasoning_trace.append(
                f"[Thinking] Verdict: CONFLICT (score {adjusted_score:.1f} > 65) — "
                f"aggressive impulses outweigh cooperative tendencies"
            )
        elif adjusted_score > 45:
            if avg_honor > 6:
                outcome = "negotiation"
                confidence = adjusted_score / 100.0
                reason = "tension exists but honor encourages diplomacy"
                reasoning_trace.append(
                    f"[Thinking] Verdict: NEGOTIATION (score {adjusted_score:.1f}, "
                    f"high honor={avg_honor:.1f}) — honor channels tension into dialogue"
                )
            else:
                outcome = "avoidance"
                confidence = (100 - adjusted_score) / 100.0
                reason = "uneasy tension leads to mutual avoidance"
                reasoning_trace.append(
                    f"[Thinking] Verdict: AVOIDANCE (score {adjusted_score:.1f}, "
                    f"low honor={avg_honor:.1f}) — tension without diplomatic outlet"
                )
        else:
            if avg_sociability > 6:
                outcome = "social_gathering"
                confidence = (100 - adjusted_score) / 100.0
                reason = "compatible personas engage in social interaction"
                reasoning_trace.append(
                    f"[Thinking] Verdict: SOCIAL GATHERING (score {adjusted_score:.1f} ≤ 45, "
                    f"high sociability={avg_sociability:.1f}) — "
                    f"low tension enables positive socializing"
                )
            else:
                outcome = "peaceful_coexistence"
                confidence = (100 - adjusted_score) / 100.0
                reason = "compatible personas share space peacefully"
                reasoning_trace.append(
                    f"[Thinking] Verdict: PEACEFUL COEXISTENCE (score {adjusted_score:.1f} ≤ 45, "
                    f"low sociability={avg_sociability:.1f}) — "
                    f"calm but reserved coexistence"
                )

        log.info("rel_behaviors",
                 f"determine_contention_outcome reasoning: {chr(10).join(reasoning_trace)}")

        return {
            "outcome": outcome,
            "confidence": confidence,
            "reason": reason,
            "reasoning_trace": reasoning_trace,
        }

    # ── Fallback: pure deterministic (AI disabled) ─────────────────
    if conflict_score > 65:
        return {"outcome": "conflict", "confidence": conflict_score / 100.0,
                "reason": "high aggression and territoriality in a contested area"}
    elif conflict_score > 45:
        if avg_honor > 6:
            return {"outcome": "negotiation", "confidence": conflict_score / 100.0,
                    "reason": "tension exists but honor encourages diplomacy"}
        else:
            return {"outcome": "avoidance", "confidence": (100 - conflict_score) / 100.0,
                    "reason": "uneasy tension leads to mutual avoidance"}
    else:
        if avg_sociability > 6:
            return {"outcome": "social_gathering", "confidence": (100 - conflict_score) / 100.0,
                    "reason": "compatible personas engage in social interaction"}
        else:
            return {"outcome": "peaceful_coexistence", "confidence": (100 - conflict_score) / 100.0,
                    "reason": "compatible personas share space peacefully"}


def generate_contention_narrative(personas: list[dict],
                                   outcome: Optional[str] = None,
                                   area_name: Optional[str] = None,
                                   area_region: Optional[str] = None) -> str:
    if not isinstance(personas, list):
        return "A strange occurrence in the area."
    if not outcome or not isinstance(outcome, str):
        outcome = "peaceful_coexistence"
    if not area_name or not isinstance(area_name, str):
        area_name = "an unknown area"
    if not area_region or not isinstance(area_region, str):
        area_region = "the world"
    """Generate a human-readable narrative for a contention event."""
    names = [p.get("name", f"Persona_{p.get('persona_uuid', '?')[:4]}") for p in personas[:5]]
    archetypes = [p.get("archetype", "?") for p in personas[:5]]
    n = len(names)

    if n == 0:
        return f"The area of {area_name} in the {area_region} was quiet — no one was around."
    if n == 1:
        return f"{names[0]} ({archetypes[0]}) spent some time alone in {area_name}."
    if n == 2:
        template = random.choice([
            f"{names[0]} ({archetypes[0]}) and {names[1]} ({archetypes[1]}) both entered {area_name} in the {area_region} at the same time.",
            f"In {area_name}, {names[0]} crossed paths with {names[1]} among the {area_region} landscape.",
        ])
    else:
        template = random.choice([
            f"A group of {n} personas — {', '.join(names[:-1])} and {names[-1]} — converged on {area_name} in the {area_region}.",
            f"{area_name} in the {area_region} became crowded as {', '.join(names[:-1])} and {names[-1]} arrived simultaneously.",
        ])

    if outcome == "conflict":
        template += " " + random.choice([
            f"Tensions erupted into open conflict as {names[0]} refused to back down.",
            f"Swords were drawn and the situation quickly escalated into violence.",
            f"Arguments turned physical as territorial instincts took over.",
        ])
    elif outcome == "negotiation":
        template += " " + random.choice([
            f"After tense negotiations, they reached an understanding about sharing the space.",
            f"{names[0]} proposed a truce, which was cautiously accepted by the others.",
            f"A heated discussion eventually led to a compromise everyone could accept.",
        ])
    elif outcome == "social_gathering":
        template += " " + random.choice([
            f"Recognizing friendly faces, the group decided to share stories and supplies.",
            f"What started as a chance meeting turned into a lively gathering with trade and conversation.",
            f"The personas greeted each other warmly and exchanged news from their travels.",
        ])
    elif outcome == "peaceful_coexistence":
        template += " " + random.choice([
            f"The personas acknowledged each other with a nod and went about their business peacefully.",
            f"A mutual understanding was reached without words — each kept to their own corner.",
            f"Politely ignoring each other, they shared the space without incident.",
        ])
    elif outcome == "avoidance":
        template += " " + random.choice([
            f"Sensing tension, {names[0]} decided to leave and find a less crowded area.",
            f"Uncomfortable with the situation, most of the group dispersed to different parts of the region.",
        ])

    return template

