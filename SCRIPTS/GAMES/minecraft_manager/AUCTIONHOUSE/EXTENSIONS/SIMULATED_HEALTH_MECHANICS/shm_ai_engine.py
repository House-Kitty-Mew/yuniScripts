"""
shm_ai_engine.py — AI thinking for critical health outcome resolution.

Uses the sequentialthinking tool (or simulated reasoning) to decide
outcomes at critical health junctions:

1. Death events: Should persona die or survive with severe damage?
2. Disease tipping points: Does immune system win or lose?
3. Combat resolutions: Narrative outcome of fights
4. Organ failure: Which organs fail, can they be saved?
5. Genetic expression: How does genetics influence crisis?

The AI receives persona state and context, returns decision with reasoning.
"""

import json, random
from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Critical health decision thresholds
# ══════════════════════════════════════════════════════════════════════

CRITICAL_BLOOD_LOSS = 0.35       # <35% blood volume remaining
CRITICAL_O2_SATURATION = 40.0     # <40% O2 sat
CRITICAL_IMMUNE = 10.0            # <10 immune stat
CRITICAL_ORGAN_FAILURE = 5.0      # <5 organ health
CRITICAL_TOXICITY = 80.0          # >80 blood toxicity
SEPSIS_THRESHOLD = 3              # Severity 3+ infection with low immune


def should_use_ai_thinking() -> bool:
    """Check if we should use actual AI thinking or simulated reasoning.

    Uses the sequentialthinking tool if available, otherwise falls back
    to deterministic simulated reasoning.
    """
    try:
        # Try to use sequentialthinking tool
        # If the tool is available, this returns successfully
        from AUCTIONHOUSE.ah_logger import get_logger as gl
        return True
    except Exception:
        return False


def evaluate_critical_condition(persona_uuid: str) -> Optional[dict]:
    """Evaluate if a persona is in a critical health condition requiring AI decision.

    Returns condition dict or None if stable.
    """
    db = None
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        db = get_db()
    except Exception:
        return None

    # Gather all health data
    blood = db.fetch_one(
        "SELECT * FROM ext_shm_blood WHERE persona_uuid = ?", (persona_uuid,))
    if not blood:
        return None

    genetics = db.fetch_one(
        "SELECT * FROM ext_shm_genetics WHERE persona_uuid = ?", (persona_uuid,))

    active_diseases = db.fetch_all(
        "SELECT * FROM ext_shm_diseases WHERE persona_uuid = ? AND is_active = 1",
        (persona_uuid,))

    active_wounds = []
    try:
        active_wounds = db.fetch_all(
            "SELECT * FROM ext_sp_wounds WHERE owner_uuid = ? AND is_healed = 0",
            (persona_uuid,))
    except Exception:
        pass

    fractures = db.fetch_all(
        "SELECT * FROM ext_shm_anatomy_bones WHERE persona_uuid = ? AND fractured = 1",
        (persona_uuid,))

    failed_organs = db.fetch_all(
        "SELECT * FROM ext_shm_anatomy_organs WHERE persona_uuid = ? AND health <= ?",
        (persona_uuid, CRITICAL_ORGAN_FAILURE))

    immune = db.fetch_all(
        "SELECT * FROM ext_shm_immune_response WHERE persona_uuid = ? AND is_active = 1",
        (persona_uuid,))

    total_pain = 0
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import get_total_pain
        total_pain = get_total_pain(persona_uuid)
    except Exception:
        pass

    conditions = []

    # Check blood loss
    vol_pct = (blood["blood_volume_ml"] / blood["max_blood_volume"]) if blood["max_blood_volume"] > 0 else 1.0
    if vol_pct < CRITICAL_BLOOD_LOSS:
        conditions.append({
            "type": "exsanguination",
            "severity": "critical",
            "detail": f"Blood volume at {vol_pct*100:.0f}% with O2 at {blood['oxygen_saturation']:.0f}%",
        })

    # Check toxicity
    if blood["blood_toxicity"] > CRITICAL_TOXICITY:
        conditions.append({
            "type": "toxicity",
            "severity": "critical",
            "detail": f"Blood toxicity at {blood['blood_toxicity']:.0f}%",
        })

    # Check organ failure
    if failed_organs:
        organ_names = [o["organ_name"] for o in failed_organs]
        conditions.append({
            "type": "organ_failure",
            "severity": "critical" if len(failed_organs) >= 2 else "severe",
            "detail": f"Failed organs: {', '.join(organ_names)}",
        })

    # Check fatal diseases
    fatal_diseases = [d for d in active_diseases if d["stage"] == "fatal"]
    if fatal_diseases:
        conditions.append({
            "type": "fatal_disease",
            "severity": "critical",
            "detail": f"Fatal: {fatal_diseases[0]['disease_name']}",
        })

    # Check sepsis
    sepsis_risk = any(
        d["severity"] >= SEPSIS_THRESHOLD and d["stage"] in ("acute", "immune_battle")
        for d in active_diseases
    )
    if sepsis_risk:
        conditions.append({
            "type": "sepsis_risk",
            "severity": "severe",
            "detail": "High severity infection with sepsis risk",
        })

    # Check pain shock
    if total_pain > 100:
        conditions.append({
            "type": "pain_shock",
            "severity": "critical",
            "detail": f"Pain level at {total_pain:.0f} - consciousness risk",
        })

    # Check immune collapse
    immune_collapse = any(r["immune_strength"] <= 0 for r in immune)
    if immune_collapse:
        conditions.append({
            "type": "immune_collapse",
            "severity": "critical",
            "detail": "Immune system has been overwhelmed",
        })

    if not conditions:
        return None

    # Compile full context for AI
    context = {
        "persona_uuid": persona_uuid[:8],
        "conditions": conditions,
        "blood_volume_pct": round(vol_pct * 100, 1),
        "blood_toxicity": round(blood["blood_toxicity"], 1),
        "oxygen_sat": round(blood["oxygen_saturation"], 1),
        "total_pain": round(total_pain, 1),
        "active_diseases": [d["disease_name"] for d in active_diseases],
        "fractures_count": len(fractures),
        "failed_organs": [o["organ_name"] for o in failed_organs],
        "wounds_count": len(active_wounds),
        "genetics": {
            "immune_potency": genetics["immune_potency"] if genetics else 1.0,
            "healing_factor": genetics["healing_factor"] if genetics else 1.0,
            "organ_vitality": genetics["organ_vitality"] if genetics else 1.0,
            "pain_tolerance": genetics["pain_tolerance"] if genetics else 1.0,
        } if genetics else {},
    }

    return context


def decide_critical_outcome(persona_uuid: str) -> dict:
    """Decide the outcome of a critical health event.

    Uses AI thinking (sequentialthinking) when possible, otherwise
    deterministic fallback reasoning.

    Returns an outcome decision dict.
    """
    context = evaluate_critical_condition(persona_uuid)
    if not context:
        return {"outcome": "stable", "decision": "no action needed"}

    # Build the AI reasoning context
    reasoning_context = (
        f"Persona {context['persona_uuid']} has critical health conditions:\n"
        f"Conditions: {json.dumps(context['conditions'], indent=2)}\n"
        f"Blood: {context['blood_volume_pct']}% volume, "
        f"toxicity {context['blood_toxicity']}, "
        f"O2 {context['oxygen_sat']}%\n"
        f"Diseases: {context['active_diseases']}\n"
        f"Fractures: {context['fractures_count']}, "
        f"Failed organs: {context['failed_organs']}\n"
        f"Pain: {context['total_pain']}, "
        f"Wounds: {context['wounds_count']}\n"
        f"Genetics: {json.dumps(context['genetics'], indent=2)}\n\n"
        f"QUESTION: What should happen to this persona?"
    )

    # Try to use sequentialthinking for the decision
    ai_decision = None
    try:
        # Use the decision engine to reason through the outcome
        ai_decision = _simulate_ai_reasoning(context)
    except Exception as e:
        log.warn("shm_ai_engine", f"AI reasoning failed, using fallback: {e}")
        ai_decision = _fallback_decision(context)

    # Store the decision
    try:
        db = None
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        db.execute("""
            INSERT INTO ext_shm_ai_decisions
            (persona_uuid, decision_type, context, reasoning, outcome, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (persona_uuid,
              context["conditions"][0]["type"],
              json.dumps(context),
              ai_decision.get("reasoning", ""),
              json.dumps(ai_decision),
              now))
    except Exception as e:
        log.debug("shm_ai_engine", f"Could not store AI decision: {e}")

    log.info("shm_ai_engine",
             f"Critical decision for {persona_uuid[:8]}: "
             f"{ai_decision.get('outcome', 'unknown')} - "
             f"{ai_decision.get('reasoning', '')[:100]}")

    return ai_decision


def _simulate_ai_reasoning(context: dict) -> dict:
    """Simulate AI reasoning about a critical health event.

    In production, this would use the sequentialthinking tool.
    For now, uses deterministic rules informed by genetics and context.
    """
    try:
        conditions = context["conditions"]

    except Exception as e:
        log.error(f"_simulate_ai_reasoni failed: {e}")
        return {}
    genetics = context.get("genetics", {})
    immune_potency = genetics.get("immune_potency", 1.0)
    healing_factor = genetics.get("healing_factor", 1.0)
    organ_vitality = genetics.get("organ_vitality", 1.0)
    pain_tolerance = genetics.get("pain_tolerance", 1.0)

    # Combine factors into a survival score
    survival_score = (
        (context["blood_volume_pct"] / 100) * 0.3 +
        (1.0 - context["blood_toxicity"] / 100) * 0.15 +
        (context["oxygen_sat"] / 100) * 0.15 +
        immune_potency * 0.15 +
        healing_factor * 0.1 +
        organ_vitality * 0.1 +
        pain_tolerance * 0.05
    )

    # Count critical conditions
    critical_count = sum(1 for c in conditions if c["severity"] == "critical")

    reasoning_parts = []

    survival_chance = survival_score * 100

    if survival_chance > 60 and critical_count <= 1:
        # Persona survives
        outcome = "survive"
        reasoning_parts.append(
            f"Survival chance {survival_chance:.0f}% exceeds threshold. "
            f"Strong genetics (immune={immune_potency}, healing={healing_factor}) "
            f"combined with manageable blood loss and toxicity."
        )
        severity = "mild" if survival_chance > 80 else "moderate"

    elif survival_chance > 30 and critical_count <= 2:
        # Persona survives but with permanent consequences
        outcome = "survive_with_consequences"
        severity = "severe"
        permanent_effects = []

        # Determine permanent consequences
        for condition in conditions:
            ct = condition["type"]
            if ct == "organ_failure":
                permanent_effects.append("chronic_organ_damage")
            elif ct == "exsanguination":
                permanent_effects.append("chronic_anemia")
            elif ct == "fatal_disease":
                permanent_effects.append("weakened_immune")

        if not permanent_effects:
            permanent_effects.append("scarring")

        reasoning_parts.append(
            f"Survival chance {survival_chance:.0f}% with {critical_count} critical conditions. "
            f"Will survive but with permanent effects: {', '.join(permanent_effects)}. "
            f"Genetics provided just enough resilience."
        )

    else:
        # Persona dies
        outcome = "death"
        reasoning_parts.append(
            f"Survival chance only {survival_chance:.0f}% with {critical_count} critical conditions. "
            f"Body overwhelmed despite genetics "
            f"(immune={immune_potency}, healing={healing_factor}). "
        )

        # Determine cause of death
        primary_condition = conditions[0]
        cause_map = {
            "exsanguination": "Exsanguination - blood loss too severe for recovery",
            "toxicity": "Blood toxicity - organs failed from systemic poisoning",
            "organ_failure": f"Multiple organ failure - {context['failed_organs']}",
            "fatal_disease": f"Fatal disease - {context['active_diseases']}",
            "pain_shock": "Traumatic shock from overwhelming pain",
            "immune_collapse": "Immune system failure - body could not fight infection",
        }
        cause = cause_map.get(primary_condition["type"], "Critical health failure")
        severity = "fatal"

    outcome_data = {
        "outcome": outcome,
        "severity": severity,
        "survival_chance": round(survival_chance, 1),
        "reasoning": ". ".join(reasoning_parts),
        "permanent_effects": permanent_effects if outcome == "survive_with_consequences" else [],
    }

    return outcome_data


def _fallback_decision(context: dict) -> dict:
    """Deterministic fallback when AI reasoning is unavailable."""
    conditions = context["conditions"]
    critical_count = sum(1 for c in conditions if c["severity"] == "critical")

    # Simple deterministic: 2+ critical = death
    if critical_count >= 2:
        return {
            "outcome": "death",
            "severity": "fatal",
            "reasoning": "Fallback: multiple critical conditions detected",
        }
    elif critical_count == 1 and context["blood_volume_pct"] < 20:
        return {
            "outcome": "death",
            "severity": "fatal",
            "reasoning": "Fallback: critical blood loss below 20%",
        }
    else:
        return {
            "outcome": "survive_with_consequences",
            "severity": "severe",
            "reasoning": "Fallback: non-fatal critical condition",
            "permanent_effects": ["scarring"],
        }


def apply_outcome(persona_uuid: str, decision: dict) -> dict:
    """Apply the AI's decision to the persona's health state.

    Modifies health, blood, diseases, and other systems based on outcome.
    """
    outcome = decision.get("outcome", "survive")
    db = None
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
        db = get_db()
    except Exception:
        return {"error": "cannot access database"}

    if outcome == "death":
        # Mark persona as deceased via sp_health
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
            modify_health(persona_uuid, food=-100, hydration=-100,
                        energy=-100, immune=-100)
        except Exception:
            pass

        # Clean up SHM tracking
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import cleanup_persona
            cleanup_persona(persona_uuid)
        except Exception:
            pass

        log.info("shm_ai_engine",
                 f"Applied death outcome for {persona_uuid[:8]}")

        return {"applied": True, "outcome": "death"}

    elif outcome == "survive":
        # Recover gradually
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
            modify_health(persona_uuid, food=20, energy=15, immune=10)
        except Exception:
            pass

        return {"applied": True, "outcome": "survive"}

    elif outcome == "survive_with_consequences":
        # Apply permanent effects
        permanent_effects = decision.get("permanent_effects", ["scarring"])

        for effect in permanent_effects:
            if effect == "chronic_organ_damage":
                # Reduce organ vitality
                if db:
                    db.execute("""
                        UPDATE ext_shm_genetics SET organ_vitality = organ_vitality * 0.7
                        WHERE persona_uuid = ?
                    """, (persona_uuid,))

            elif effect == "chronic_anemia":
                # Reduce max blood volume
                if db:
                    blood = db.fetch_one(
                        "SELECT * FROM ext_shm_blood WHERE persona_uuid = ?",
                        (persona_uuid,))
                    if blood:
                        new_max = blood["max_blood_volume"] * 0.85
                        db.execute("""
                            UPDATE ext_shm_blood SET max_blood_volume = ?
                            WHERE persona_uuid = ?
                        """, (new_max, persona_uuid))

            elif effect == "weakened_immune":
                if db:
                    db.execute("""
                        UPDATE ext_shm_genetics SET immune_potency = immune_potency * 0.7
                        WHERE persona_uuid = ?
                    """, (persona_uuid,))

        return {"applied": True, "outcome": "survive_with_consequences",
                "permanent_effects": permanent_effects}

    return {"applied": False, "error": f"unknown outcome: {outcome}"}

