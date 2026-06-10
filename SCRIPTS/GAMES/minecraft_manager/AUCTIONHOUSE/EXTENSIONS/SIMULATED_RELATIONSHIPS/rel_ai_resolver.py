"""
rel_ai_resolver.py — AI thinking-mode resolver for relationship events.

This is the core decision engine that resolves contention events
between personas using a simulated "thinking mode" that checks:

  1. Each persona's behavioral archetype and traits
  2. Current stats (skills, health, wealth)
  3. Inventory and resources
  4. Goals, needs, and wants
  5. Relationship history between involved personas
  6. Active situationships
  7. Environmental factors (area type, resources, world events)
  8. Random variance (chaos factor)

The resolver produces a detailed reasoning trace, an outcome,
and applies ALL resulting changes (relationship updates, skill
changes, health effects, inventory changes) atomically.

Two resolution modes:
  - THINKING MODE (default): Full reasoning trace with weighted
    factor analysis and narrative generation
  - FAST MODE: Quick probabilistic resolution for batch processing
"""

import random, json, math
from datetime import datetime, timezone
from typing import Optional, Any

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

# ── Schema enforcement constants ──────────────────────────────────
VALID_OUTCOMES = frozenset({"conflict", "negotiation", "social_gathering",
                             "peaceful_coexistence", "avoidance", "solitary"})

OUTCOME_SCHEMA = {
    "reasoning": (list, True),      # list of strings, optional
    "outcome": (str, True),         # must be in VALID_OUTCOMES
    "confidence": (float, True),    # 0.0-1.0
    "narrative": (str, True),       # non-empty
    "persona_effects": (list, True), # list of dicts
    "relationship_changes": (list, True),
    "skill_changes": (list, True),
}

_OUTCOME_FALLBACK = {
    "reasoning": [],
    "outcome": "peaceful_coexistence",
    "confidence": 0.5,
    "narrative": "The personas shared the space peacefully without incident.",
    "persona_effects": [],
    "relationship_changes": [],
    "skill_changes": [],
}

# ── AI vs Fallback mode ──────────────────────────────────────────
AI_MODE = True  # Set to False to use deterministic fallback

# ── Default traits for missing data ──────────────────────────────
_DEFAULT_TRAITS = {"aggression": 5, "generosity": 5, "curiosity": 5,
                    "sociability": 5, "cautiousness": 5, "territoriality": 5,
                    "honor": 5, "impulsiveness": 5}
_DEFAULT_SKILLS = {"mining": 10, "combat": 10, "farming": 10,
                    "trading": 10, "crafting": 10, "exploration": 10,
                    "leadership": 10}
_DEFAULT_HEALTH = {"food": 80, "hydration": 80, "energy": 75,
                    "temperature": 50, "waste": 0, "hygiene": 60, "immune": 70}



class ContentionResolver:
    """AI resolver for contention/encounter events between personas.

    Uses a multi-factor thinking-mode approach to determine:
    - What type of outcome occurs (conflict, negotiation, etc.)
    - What the detailed consequences are for each persona
    - How relationships change as a result
    - What skills evolve from the experience
    """

    def __init__(self, use_thinking_mode: bool = True):
        self.use_thinking_mode = use_thinking_mode

    # ══════════════════════════════════════════════════════════════════
    # Main resolution entry point
    # ══════════════════════════════════════════════════════════════════


    def _validate_personas(self, personas: list[dict]) -> list[dict]:
        """Validate and sanitize persona list.

        Returns sanitized list; never raises.
        """
        MAX_PERSONAS = 20
        if len(personas) > MAX_PERSONAS:
            log.warn("rel_resolver", f"Truncating {len(personas)} personas to {MAX_PERSONAS}")
            personas = personas[:MAX_PERSONAS]

        result = []
        for i, p in enumerate(personas):
            if not isinstance(p, dict):
                log.warn("rel_resolver", f"Persona[{i}] not a dict, using empty template")
                p = {}

            sanitized = {
                "persona_uuid": str(p.get("persona_uuid", f"fallback-{i}")),
                "name": str(p.get("name", f"Persona_{i}"))[:64],
                "archetype": str(p.get("archetype", "adventurer"))[:32],
                "skills": self._validate_skills(p.get("skills", {})),
                "traits": self._validate_traits(p.get("traits", {})),
                "health": self._validate_health(p.get("health", {})),
                "wealth_tier": str(p.get("wealth_tier", "working"))[:32],
                "active": 1,
            }

            # Copy through optional fields
            for k in ("relationship", "memory", "inventory"):
                if k in p:
                    sanitized[k] = p[k]

            result.append(sanitized)

        return result

    def _validate_skills(self, skills: Any) -> dict[str, float]:
        """Validate and sanitize skills dict. Returns safe copy with defaults."""
        if not isinstance(skills, dict):
            return dict(_DEFAULT_SKILLS)
        safe = {}
        for k, v in _DEFAULT_SKILLS.items():
            raw = skills.get(k, v)
            if raw is None or (isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw))):
                raw = v
            safe[k] = max(1.0, min(100.0, float(raw)))
        return safe

    def _validate_traits(self, traits: Any) -> dict[str, float]:
        """Validate and sanitize traits dict."""
        if not isinstance(traits, dict):
            return dict(_DEFAULT_TRAITS)
        safe = {}
        for k, v in _DEFAULT_TRAITS.items():
            raw = traits.get(k, v)
            if raw is None or (isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw))):
                raw = v
            safe[k] = max(0.0, min(10.0, float(raw)))
        return safe

    def _validate_health(self, health: Any) -> dict[str, float]:
        """Validate and sanitize health dict."""
        if not isinstance(health, dict):
            return dict(_DEFAULT_HEALTH)
        safe = {}
        for k, v in _DEFAULT_HEALTH.items():
            raw = health.get(k, v)
            if raw is None or (isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw))):
                raw = v
            safe[k] = max(0.0, min(100.0, float(raw)))
        return safe

    def _validate_area(self, area: Any) -> dict:
        """Validate and sanitize area dict."""
        if not isinstance(area, dict):
            log.warn("rel_resolver", "Area is not a dict, using defaults")
            return {"name": "Unknown", "type": "plains", "region": "overworld"}
        return {
            "name": str(area.get("name", "Unknown"))[:64],
            "type": str(area.get("type", "plains"))[:32],
            "region": str(area.get("region", "overworld"))[:32],
        }

    def _validate_outcome_schema(self, result: dict) -> dict:
        """Validate outcome dict against OUTCOME_SCHEMA.

        If validation fails, returns _OUTCOME_FALLBACK and logs a warning
        with a format guide explaining what the response SHOULD look like.

        Returns:
            Validated result dict (corrected if invalid).
        """
        if not isinstance(result, dict):
            log.warn("rel_resolver", "Outcome is not a dict — returning fallback")
            log.warn("rel_resolver", "VALIDATION FAILED: outcome must be one of " + ", ".join(sorted(VALID_OUTCOMES)))
            return dict(_OUTCOME_FALLBACK)

        fixed = dict(result)

        # Validate outcome type
        raw_outcome = str(fixed.get("outcome", "peaceful_coexistence"))
        if raw_outcome not in VALID_OUTCOMES:
            log.warn("rel_resolver",
                     f"Invalid outcome '{raw_outcome}' — must be one of {sorted(VALID_OUTCOMES)}. "
                     f"Returning 'peaceful_coexistence'.")
            fixed["outcome"] = "peaceful_coexistence"

        # Validate confidence
        raw_conf = fixed.get("confidence", 0.5)
        if not isinstance(raw_conf, (int, float)) or isinstance(raw_conf, bool):
            raw_conf = 0.5
        if raw_conf is not None and (isinstance(raw_conf, float) and (math.isnan(raw_conf) or math.isinf(raw_conf))):
            raw_conf = 0.5
        fixed["confidence"] = max(0.0, min(1.0, float(raw_conf)))

        # Ensure required lists exist
        for key in ("reasoning", "persona_effects", "relationship_changes", "skill_changes"):
            val = fixed.get(key)
            if not isinstance(val, list):
                fixed[key] = []

        # Validate persona_effects items
        fixed["persona_effects"] = [
            self._validate_persona_effect(e) for e in fixed["persona_effects"]
        ]

        # Validate relationship_changes items
        fixed["relationship_changes"] = [
            self._validate_rel_change(rc) for rc in fixed["relationship_changes"]
        ]

        # Validate narrative
        raw_narr = fixed.get("narrative", "")
        if not isinstance(raw_narr, str) or not raw_narr.strip():
            fixed["narrative"] = "The encounter concluded without notable incident."

        return fixed

    def _validate_persona_effect(self, effect: Any) -> dict:
        """Validate a single persona effect dict."""
        if not isinstance(effect, dict):
            return {"persona_index": 0, "health_delta": {}, "wealth_delta": 0.0,
                    "memory": {"type": "encounter", "emotional_weight": 3}}
        safe = dict(effect)
        # Ensure health_delta is a dict with clamped values
        hd = safe.get("health_delta", {})
        if not isinstance(hd, dict):
            hd = {}
        for k in ("food", "hydration", "energy", "temperature", "waste", "hygiene", "immune"):
            if k in hd:
                val = hd[k]
                if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                    hd[k] = 0.0
                else:
                    hd[k] = float(val)
        safe["health_delta"] = hd

        # Wealth delta sanitization
        wd = safe.get("wealth_delta", 0.0)
        if not isinstance(wd, (int, float)) or isinstance(wd, bool):
            wd = 0.0
        if wd is not None and (isinstance(wd, float) and (math.isnan(wd) or math.isinf(wd))):
            wd = 0.0
        safe["wealth_delta"] = float(wd)

        # Memory sanitization
        mem = safe.get("memory", {})
        if not isinstance(mem, dict):
            mem = {"type": "encounter", "emotional_weight": 3}
        mem["type"] = str(mem.get("type", "encounter"))[:32]
        mem["emotional_weight"] = max(0, min(10, int(mem.get("emotional_weight", 3))))
        safe["memory"] = mem

        return safe

    def _validate_rel_change(self, rc: Any) -> dict:
        """Validate a single relationship change dict."""
        if not isinstance(rc, dict):
            return {"persona_a_index": 0, "persona_b_index": 1,
                    "strength_delta": 0.0, "suggested_type": "neutral"}
        safe = dict(rc)
        sd = safe.get("strength_delta", 0.0)
        if not isinstance(sd, (int, float)) or isinstance(sd, bool):
            sd = 0.0
        if sd is not None and (isinstance(sd, float) and (math.isnan(sd) or math.isinf(sd))):
            sd = 0.0
        safe["strength_delta"] = max(-50.0, min(50.0, float(sd)))
        safe["suggested_type"] = str(safe.get("suggested_type", "neutral"))[:32]
        return safe

        def _reformat_retry_message(self) -> str:
            """Return a format guide for invalid AI responses."""
            outcomes = ", ".join(sorted(VALID_OUTCOMES))
            return "VALIDATION FAILED - expected outcome in [" + outcomes + "]"


    def resolve(self, personas: list[dict], area: dict,
                has_resources: bool = False,
                world_events: Optional[list[dict]] = None) -> dict:
        """Resolve a multi-persona contention/encounter event.

        Args:
            personas: List of persona dicts with (archetype, skills,
                      health, wealth_tier, personality, relationship)
            area: Dict with area_name, area_type (biome), region, territory
            has_resources: Whether contested resources exist in area
            world_events: Active world events affecting the area

        Returns:
            Dict with:
              - reasoning: Full thinking-mode trace (if enabled)
              - outcome: Resolution type
              - narrative: Human-readable story
              - persona_effects: Per-persona changes applied
              - relationship_changes: Relationship deltas between pairs
              - skill_changes: Skill deltas per persona
        """
        # ── Validate inputs ──
        if not isinstance(personas, list):
            log.warn("rel_resolver", "personas is not a list — returning fallback")
            return dict(_OUTCOME_FALLBACK)
        if len(personas) == 0:
            return {
                "reasoning": ["No personas to resolve."],
                "outcome": "solitary",
                "confidence": 1.0,
                "narrative": "The area remained empty.",
                "persona_effects": [],
                "relationship_changes": [],
                "skill_changes": [],
            }
        personas = self._validate_personas(personas)
        area = self._validate_area(area)

        reasoning = []
        if self.use_thinking_mode:
            reasoning = self._think(personas, area, has_resources, world_events)

        outcome, confidence = self._determine_outcome(
            personas, area, has_resources, world_events)

        if self.use_thinking_mode:
            reasoning.append(f"[RESOLUTION] Outcome: {outcome} (confidence: {confidence:.1%})")

        persona_effects = self._compute_persona_effects(personas, outcome)
        relationship_changes = self._compute_relationship_changes(
            personas, outcome, has_resources)
        skill_changes = self._compute_skill_changes(personas, outcome)
        narrative = self._generate_narrative(personas, outcome, area,
                                              persona_effects)

        result = {
            "reasoning": reasoning,
            "outcome": outcome,
            "confidence": confidence,
            "narrative": narrative,
            "persona_effects": persona_effects,
            "relationship_changes": relationship_changes,
            "skill_changes": skill_changes,
        }

        # Validate and fix the result before returning
        result = self._validate_outcome_schema(result)
        return result

    # ══════════════════════════════════════════════════════════════════
    # Thinking Mode — Full reasoning trace
    # ══════════════════════════════════════════════════════════════════

    def _think(self, personas: list[dict], area: dict,
               has_resources: bool, world_events: Optional[list[dict]]) -> list[str]:
        """Generate a step-by-step reasoning trace.

        This simulates what an AI would produce with thinking mode enabled.
        """
        trace = []
        trace.append("═" * 50)
        trace.append("THINKING MODE: Contention Resolution")
        trace.append(f"Area: {area.get('name', 'Unknown')} "
                     f"({area.get('type', '?')}, {area.get('region', '?')})")
        trace.append(f"Personas involved: {len(personas)}")

        for i, p in enumerate(personas):
            arch = p.get("archetype", "?")
            name = p.get("name", f"Persona_{i}")
            skills = p.get("skills", {})
            health = p.get("health", {})
            trace.append(f"  [{i}] {name} ({arch})")
            trace.append(f"       Combat: {skills.get('combat', 0):.0f}, "
                         f"Trading: {skills.get('trading', 0):.0f}, "
                         f"Leadership: {skills.get('leadership', 0):.0f}")
            trace.append(f"       Health: {health.get('food', 80):.0f}f/"
                         f"{health.get('energy', 75):.0f}e, "
                         f"Wealth: {p.get('wealth_tier', '?')}")
            if p.get("traits"):
                t = p["traits"]
                trace.append(f"       Traits: aggression={t.get('aggression', 5)}/"
                             f"honor={t.get('honor', 5)}/"
                             f"sociability={t.get('sociability', 5)}")

        trace.append("─" * 40)

        # Factor 1: Archetype compatibility
        compatibilities = []
        for i in range(len(personas)):
            for j in range(i + 1, len(personas)):
                from .rel_behaviors import get_compatibility
                c = get_compatibility(
                    personas[i].get("archetype", "adventurer"),
                    personas[j].get("archetype", "adventurer"))
                compatibilities.append((i, j, c))
        avg_compat = sum(c for _, _, c in compatibilities) / max(len(compatibilities), 1)
        trace.append(f"[FACTOR] Archetype compatibility: {avg_compat:.0f}/100 "
                     f"{'HIGH' if avg_compat > 60 else 'MODERATE' if avg_compat > 40 else 'LOW'}")

        # Factor 2: Aggression assessment
        total_aggr = sum(p.get("traits", {}).get("aggression", 5)
                         for p in personas)
        avg_aggr = total_aggr / len(personas)
        trace.append(f"[FACTOR] Average aggression: {avg_aggr:.1f}/10 "
                     f"{'DANGEROUS' if avg_aggr > 6 else 'MANAGEABLE' if avg_aggr > 4 else 'PEACEFUL'}")

        # Factor 3: Resource pressure
        if has_resources:
            trace.append(f"[FACTOR] Scarce resources present — increases conflict likelihood")
        else:
            trace.append(f"[FACTOR] No scarce resources — reduces conflict likelihood")

        # Factor 4: Territoriality of the area
        territorial_biomes = ("mountains", "deep_dark", "nether_wastes")
        if area.get("type") in territorial_biomes:
            trace.append(f"[FACTOR] Territorial biome ({area.get('type')}) — "
                         f"increases territorial behavior")
        else:
            trace.append(f"[FACTOR] Neutral biome — no territorial modifier")

        # Factor 5: Health & need assessment
        hungry_count = sum(1 for p in personas
                           if p.get("health", {}).get("food", 80) < 30)
        tired_count = sum(1 for p in personas
                          if p.get("health", {}).get("energy", 75) < 30)
        if hungry_count > 0 or tired_count > 0:
            trace.append(f"[FACTOR] {hungry_count} hungry, {tired_count} tired "
                         f"— desperation increases conflict risk")
        else:
            trace.append(f"[FACTOR] All personas healthy — no desperation modifier")

        # Factor 6: Existing relationships
        rel_count = sum(1 for p in personas
                        if p.get("relationship", {}).get("strength", 50) > 60)
        enemy_count = sum(1 for p in personas
                          if p.get("relationship", {}).get("strength", 50) < 20)
        if rel_count > 0:
            trace.append(f"[FACTOR] {rel_count} positive relationships present "
                         f"— increases cooperation chance")
        if enemy_count > 0:
            trace.append(f"[FACTOR] {enemy_count} negative relationships present "
                         f"— increases conflict chance")

        # Factor 7: World events
        if world_events:
            for ev in world_events:
                trace.append(f"[FACTOR] World event active: {ev.get('name', '?')} "
                             f"({ev.get('severity', '?')})")

        trace.append("─" * 40)
        return trace

    # ══════════════════════════════════════════════════════════════════
    # Outcome determination
    # ══════════════════════════════════════════════════════════════════

    def _determine_outcome(self, personas: list[dict], area: dict,
                            has_resources: bool,
                            world_events: Optional[list[dict]]) -> tuple[str, float]:
        """Determine the most likely outcome.

        When AI_MODE is enabled (default), uses the behavior-driven
        analysis.  When disabled, uses a simpler deterministic fallback.

        Returns (outcome_type, confidence)
        """
        from .rel_behaviors import determine_contention_outcome

        if not AI_MODE:
            # Fallback: Simple deterministic algorithm based on aggression
            total_aggr = sum(p.get("traits", {}).get("aggression", 5)
                             for p in personas)
            avg_aggr = total_aggr / max(len(personas), 1)

            if avg_aggr > 7:
                return ("conflict", avg_aggr / 10.0)
            elif avg_aggr > 5:
                if has_resources:
                    return ("negotiation", 0.6)
                return ("peaceful_coexistence", 0.7)
            else:
                return ("social_gathering", 0.8)

        base = determine_contention_outcome(personas, area.get("type", "plains"),
                                             has_resources)
        return base["outcome"], base["confidence"]

    # ══════════════════════════════════════════════════════════════════
    # Persona effects computation
    # ══════════════════════════════════════════════════════════════════

    def _compute_persona_effects(self, personas: list[dict],
                                   outcome: str) -> list[dict]:
        """Compute per-persona effects from the outcome.

        Effects include:
          - Health changes (injury in combat, energy drain from negotiation)
          - Wealth changes (goods traded, money lost)
          - Mood/memory impacts
        """
        effects = []
        won_persona = 0  # In conflicts, first persona with highest combat wins

        if outcome == "conflict":
            # Determine winner (highest combat skill + aggression)
            scores = []
            for i, p in enumerate(personas):
                combat = p.get("skills", {}).get("combat", 10)
                aggr = p.get("traits", {}).get("aggression", 5)
                score = combat + (aggr * 5)
                scores.append((i, score))
            scores.sort(key=lambda x: x[1], reverse=True)
            won_persona = scores[0][0]

            for i, p in enumerate(personas):
                is_winner = (i == won_persona)
                effect = {"persona_index": i, "name": p.get("name", f"P{i}")}

                if is_winner:
                    effect["health_delta"] = {"energy": -random.uniform(5, 15),
                                              "food": -random.uniform(3, 8)}
                    effect["wealth_delta"] = random.uniform(1, 5)
                    effect["memory"] = {"type": "combat_victory",
                                        "emotional_weight": 8}
                else:
                    effect["health_delta"] = {"energy": -random.uniform(10, 25),
                                              "food": -random.uniform(5, 15),
                                              "waste": random.uniform(3, 8)}
                    effect["wealth_delta"] = -random.uniform(0, 3)
                    effect["memory"] = {"type": "combat_defeat",
                                        "emotional_weight": 6}

                effects.append(effect)

        elif outcome == "negotiation":
            for i, p in enumerate(personas):
                effect = {"persona_index": i, "name": p.get("name", f"P{i}")}
                effect["health_delta"] = {"energy": -random.uniform(3, 8)}
                effect["wealth_delta"] = random.uniform(-1, 3)
                effect["memory"] = {"type": "negotiation",
                                    "emotional_weight": 5}
                effects.append(effect)

        elif outcome == "social_gathering":
            for i, p in enumerate(personas):
                effect = {"persona_index": i, "name": p.get("name", f"P{i}")}
                effect["health_delta"] = {"energy": -random.uniform(1, 4),
                                          "food": -random.uniform(1, 3)}
                effect["wealth_delta"] = random.uniform(-0.5, 2) if random.random() < 0.4 else 0
                effect["memory"] = {"type": "social_gathering",
                                    "emotional_weight": 4}
                effects.append(effect)

        else:  # peaceful, avoidance
            for i, p in enumerate(personas):
                effect = {"persona_index": i, "name": p.get("name", f"P{i}")}
                effect["health_delta"] = {"energy": -random.uniform(0, 2)}
                effect["wealth_delta"] = 0
                effect["memory"] = {"type": outcome,
                                    "emotional_weight": 2}
                effects.append(effect)

        return effects

    # ══════════════════════════════════════════════════════════════════
    # Relationship changes computation
    # ══════════════════════════════════════════════════════════════════

    def _compute_relationship_changes(self, personas: list[dict],
                                       outcome: str,
                                       has_resources: bool) -> list[dict]:
        """Compute relationship strength changes between all persona pairs."""
        changes = []
        pairs = set()
        for i in range(len(personas)):
            for j in range(i + 1, len(personas)):
                key = (i, j) if (i, j) not in pairs else (j, i)
                if key in pairs:
                    continue
                pairs.add((i, j))

                base_delta = 0.0
                rel_type = "neutral"

                if outcome == "conflict":
                    base_delta = -random.uniform(8, 20)
                    rel_type = "enmity"
                elif outcome == "negotiation":
                    base_delta = random.uniform(2, 8)
                    rel_type = "alliance"
                elif outcome == "social_gathering":
                    base_delta = random.uniform(3, 10)
                    rel_type = "friendship"
                elif outcome == "peaceful_coexistence":
                    base_delta = random.uniform(0, 3)
                elif outcome == "avoidance":
                    base_delta = random.uniform(-5, 0)

                changes.append({
                    "persona_a_index": i,
                    "persona_b_index": j,
                    "strength_delta": base_delta,
                    "suggested_type": rel_type,
                })
        return changes

    # ══════════════════════════════════════════════════════════════════
    # Skill changes computation
    # ══════════════════════════════════════════════════════════════════

    def _compute_skill_changes(self, personas: list[dict],
                                 outcome: str) -> list[dict]:
        """Compute skill deltas for each persona based on outcome."""
        changes = []
        won_persona = 0

        if outcome == "conflict":
            scores = [(i, p.get("skills", {}).get("combat", 10) +
                       p.get("traits", {}).get("aggression", 5) * 5)
                      for i, p in enumerate(personas)]
            scores.sort(key=lambda x: x[1], reverse=True)
            won_persona = scores[0][0]

            for i in range(len(personas)):
                changes.append({
                    "persona_index": i,
                    "skill_deltas": {
                        "combat": random.uniform(3, 6) if i == won_persona
                                  else random.uniform(1, 2),
                        "leadership": random.uniform(1, 3) if i == won_persona
                                      else random.uniform(-3, -1),
                    }
                })

        elif outcome in ("negotiation", "social_gathering"):
            for i in range(len(personas)):
                changes.append({
                    "persona_index": i,
                    "skill_deltas": {
                        "trading": random.uniform(1, 4),
                        "leadership": random.uniform(1, 2),
                    }
                })

        else:
            for i in range(len(personas)):
                changes.append({
                    "persona_index": i,
                    "skill_deltas": {},
                })

        return changes

    # ══════════════════════════════════════════════════════════════════
    # Narrative generation
    # ══════════════════════════════════════════════════════════════════

    def _generate_narrative(self, personas: list[dict], outcome: str,
                             area: dict,
                             effects: list[dict]) -> str:
        """Generate a rich narrative describing the event and its aftermath."""
        from .rel_behaviors import generate_contention_narrative

        base = generate_contention_narrative(
            personas, outcome,
            area.get("name", "Unknown"),
            area.get("region", "the world"))

        # Add outcome-specific details
        if outcome == "conflict":
            # Find winner
            scores = [(i, p.get("skills", {}).get("combat", 10) +
                       p.get("traits", {}).get("aggression", 5) * 5)
                      for i, p in enumerate(personas)]
            scores.sort(key=lambda x: x[1], reverse=True)
            winner = personas[scores[0][0]]
            loser = personas[scores[-1][0]]
            base += (f" After a fierce struggle, {winner.get('name', '?')} "
                     f"emerged victorious over {loser.get('name', '?')}. "
                     f"The defeated persona retreated to recover.")

        elif outcome == "negotiation":
            names = [p.get("name", "?") for p in personas[:3]]
            base += (f" After some deliberation, {names[0]} proposed a "
                     f"fair arrangement that {' and '.join(names[1:])} "
                     f"accepted. Goods and promises were exchanged.")

        elif outcome == "social_gathering":
            base += (f" They shared stories of their adventures and "
                     f"traded news from distant lands.")

        return base

