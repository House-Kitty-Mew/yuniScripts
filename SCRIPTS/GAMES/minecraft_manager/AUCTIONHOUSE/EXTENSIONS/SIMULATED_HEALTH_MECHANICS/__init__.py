"""
SIMULATED_HEALTH_MECHANICS — AH Extension: Detailed health & combat mechanics.

Replaces basic health tracking with a full physiological simulation:
  - Blood system (volume, toxicity, type, oxygen, cells, clotting)
  - Complete human skeleton (206 bones tracked individually)
  - 13 organs with health tracking and failure mechanics
  - 12 muscle groups with strength, protein, fatigue, atrophy
  - Genetic system derived from persona archetype & traits
  - Disease & bacteria infection (no viruses — too strong)
  - Hygiene system with bacteria spread between personas
  - Immune response & autoimmune battles
  - Pain tracking with healing mechanics and weakness
  - Combat skills evolution (10 skills, proficiency, decay, negative traits)
  - AI thinking for critical health outcome resolution

Safety:
  - All hooks wrapped in try/except by registry
  - Uses own tables (ext_shm_*) — never modifies core AH data
  - Graceful degradation if dependencies missing
"""

from datetime import datetime, timezone

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import (
    ensure_schema, initialize_persona
)

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Globals
# ══════════════════════════════════════════════════════════════════════

_shm_tick = 0


# ══════════════════════════════════════════════════════════════════════
# Hook: on_simulation_cycle_start
# ══════════════════════════════════════════════════════════════════════

def _on_simulation_cycle_start(**kwargs) -> dict:
    """Run the complete SHM health tick.

    Order of operations:
    1. Blood regeneration & detoxification
    2. Bleeding from wounds
    3. Muscle recovery & atrophy check
    4. Disease progression & spread
    5. Hygiene decay
    6. Immune response processing
    7. Pain decay & healing
    8. Combat skill decay
    9. Negative traits check
    10. Critical condition evaluation (AI thinking)
    """
    global _shm_tick
    _shm_tick += 1

    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_active_personas

    personas = get_active_personas()
    results = {
        "tick": _shm_tick,
        "processed": len(personas),
        "blood_regenerated": 0,
        "toxicity_detoxed": 0,
        "bleeding_events": 0,
        "disease_ticks": 0,
        "disease_spreads": 0,
        "muscle_recovery": 0,
        "hygiene_decay": 0,
        "immune_battles": 0,
        "pain_decay": 0,
        "skill_decay": 0,
        "negative_traits": 0,
        "critical_decisions": 0,
    }

    for persona in personas:
        puuid = persona["persona_uuid"]
        try:
            _process_persona_tick(puuid, persona, results)
        except Exception as e:
            log.debug("shm_extension", f"SHM tick error for {puuid[:8]}: {e}")

    log.info("shm_extension",
             f"Tick #{_shm_tick}: {results['processed']} personas, "
             f"{results['disease_ticks']} disease ticks, "
             f"{results['critical_decisions']} critical decisions")

    return results


def _process_persona_tick(persona_uuid: str, persona: dict, results: dict):
    """Process all SHM systems for a single persona."""
    db_lazy = lambda: None
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db

    # ── 1. Blood regeneration ──────────────────────────────────────
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_blood import (
            process_blood_regeneration_tick, process_detoxification_tick,
            process_bleeding_tick, check_exsanguination
        )
        regen = process_blood_regeneration_tick(persona_uuid)
        if regen and regen.get("regenerating"):
            results["blood_regenerated"] += 1

        detox = process_detoxification_tick(persona_uuid)
        if detox and detox.get("detox"):
            results["toxicity_detoxed"] += 1

        bleed = process_bleeding_tick(persona_uuid)
        if bleed and bleed.get("bleeding"):
            results["bleeding_events"] += 1
    except Exception:
        pass

    # ── 2. Muscle recovery & use ────────────────────────────────────
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_muscle import (
            process_muscle_recovery_tick
        )
        muscle = process_muscle_recovery_tick(persona_uuid)
        if muscle and muscle.get("muscles"):
            results["muscle_recovery"] += 1
    except Exception:
        pass

    # ── 3. Disease progression ──────────────────────────────────────
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_disease import (
            process_disease_tick, try_spread_disease
        )
        diseases = process_disease_tick(persona_uuid)
        if diseases:
            results["disease_ticks"] += len(diseases)

        spreads = try_spread_disease(persona_uuid)
        if spreads:
            results["disease_spreads"] += len(spreads)
    except Exception:
        pass

    # ── 4. Hygiene decay ────────────────────────────────────────────
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_hygiene import (
            process_hygiene_decay_tick
        )
        hygiene = process_hygiene_decay_tick(persona_uuid)
        if hygiene and not hygiene.get("error"):
            results["hygiene_decay"] += 1
    except Exception:
        pass

    # ── 5. Immune response ──────────────────────────────────────────
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_immune import (
            process_immune_ticks
        )
        immune = process_immune_ticks(persona_uuid)
        if immune:
            results["immune_battles"] += len(immune)
    except Exception:
        pass

    # ── 6. Pain & healing ───────────────────────────────────────────
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_pain import (
            process_healing_tick
        )
        healing = process_healing_tick(persona_uuid)
        if healing:
            results["pain_decay"] += 1
    except Exception:
        pass

    # ── 7. Combat skill decay ───────────────────────────────────────
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
            process_skill_decay_tick
        )
        decay = process_skill_decay_tick(persona_uuid)
        if decay:
            results["skill_decay"] += len(decay)
    except Exception:
        pass

    # ── 8. Negative traits ──────────────────────────────────────────
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_skills import (
            process_negative_traits_tick
        )
        traits = process_negative_traits_tick(persona_uuid)
        if traits:
            results["negative_traits"] += len(traits)
    except Exception:
        pass

    # ── 9. Critical condition check (every 6 ticks) ─────────────────
    if _shm_tick % 6 == 0:
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_ai_engine import (
                evaluate_critical_condition, decide_critical_outcome, apply_outcome
            )
            critical = evaluate_critical_condition(persona_uuid)
            if critical:
                decision = decide_critical_outcome(persona_uuid)
                if decision.get("outcome") != "stable":
                    apply_outcome(persona_uuid, decision)
                    results["critical_decisions"] += 1
        except Exception:
            pass


# ── Hook: on_listing_created ─────────────────────────────────────────

def _on_listing_created(**kwargs) -> dict:
    """Called when a listing is created.

    If the listed item is medicine, food, or medical supplies,
    adjust persona purchase urgency upward.
    """
    listing = kwargs.get("listing", {})
    item_id = listing.get("item_id", "")
    if not item_id:
        return {}

    # Medical/health items that personas need
    health_items = {
        "minecraft:golden_apple": "medicine",
        "minecraft:enchanted_golden_apple": "strong_medicine",
        "minecraft:honey_bottle": "medicine",
        "minecraft:suspicious_stew": "medicine",
        "minecraft:cooked_beef": "high_protein_food",
        "minecraft:cooked_porkchop": "high_protein_food",
        "minecraft:cooked_chicken": "protein_food",
        "minecraft:cooked_mutton": "protein_food",
        "minecraft:cooked_cod": "protein_food",
        "minecraft:cooked_salmon": "protein_food",
        "minecraft:bread": "food",
        "minecraft:apple": "food",
        "minecraft:carrot": "food",
        "minecraft:potato": "food",
        "minecraft:beetroot_soup": "food",
        "minecraft:mushroom_stew": "food",
        "minecraft:potion": "medicine",
        "minecraft:water_bucket": "clean_water",
        "minecraft:white_wool": "bandage_material",
    }

    item_type = health_items.get(item_id)
    if not item_type:
        return {}

    # Find personas with low health stats and increase purchase urgency
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        sp_db_instance = sp_db()

        if item_type in ("medicine", "strong_medicine"):
            # Find personas with active diseases or wounds
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
            db = get_db()
            sick_personas = db.fetch_all("""
                SELECT DISTINCT d.persona_uuid FROM ext_shm_diseases d
                WHERE d.is_active = 1
            """)
            for p in sick_personas:
                sp_db_instance.execute("""
                    UPDATE ext_sp_needs SET urgency = MIN(10, urgency + 2)
                    WHERE persona_uuid = ? AND item_id = ?
                """, (p["persona_uuid"], item_id))
            return {"type": "medicine", "matched_sick": len(sick_personas)}

        elif item_type in ("high_protein_food", "protein_food"):
            # Find personas with low protein levels
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
            db = get_db()
            low_protein = db.fetch_all("""
                SELECT DISTINCT persona_uuid FROM ext_shm_muscles
                WHERE protein_level < 30
            """)
            for p in low_protein:
                sp_db_instance.execute("""
                    UPDATE ext_sp_needs SET urgency = MIN(10, urgency + 1)
                    WHERE persona_uuid = ? AND item_id = ?
                """, (p["persona_uuid"], item_id))
            return {"type": "protein_food", "matched_low_protein": len(low_protein)}

        elif item_type == "clean_water":
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import get_persona_health
            sick_p = sp_db_instance.fetch_all("""
                SELECT persona_uuid FROM ext_sp_health WHERE hydration < 35 AND alive = 1
            """)
            for p in sick_p:
                sp_db_instance.execute("""
                    UPDATE ext_sp_needs SET urgency = MIN(10, urgency + 2)
                    WHERE persona_uuid = ? AND item_id = ?
                """, (p["persona_uuid"], item_id))
            return {"type": "water", "matched_dehydrated": len(sick_p)}

    except Exception as e:
        log.debug("shm_extension", f"Listing created health check error: {e}")

    return {}


# ── Hook: on_purchase ────────────────────────────────────────────────

def _on_purchase(**kwargs) -> dict:
    """Called when an item is purchased.

    If a persona buys medicine or food, apply health benefits.
    """
    transaction = kwargs.get("transaction", {})
    buyer = transaction.get("buyer", "")
    item_id = transaction.get("item_id", "")
    quantity = transaction.get("quantity", 1)

    if not item_id or not buyer:
        return {}

    # Check if this is a persona purchase
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        persona = sp_db().fetch_one(
            "SELECT persona_uuid FROM ext_sp_profiles WHERE name = ? AND active = 1",
            (buyer,))
        if not persona:
            return {}
    except Exception:
        return {}

    puuid = persona["persona_uuid"]

    # Apply health benefit based on item
    benefits = {
        "minecraft:golden_apple": {"immune": 30, "food": 20},
        "minecraft:enchanted_golden_apple": {"immune": 50, "food": 40, "healing_factor": 2.0},
        "minecraft:honey_bottle": {"immune": 15},
        "minecraft:suspicious_stew": {"immune": 10, "food": 15},
        "minecraft:cooked_beef": {"food": 30, "protein": 15},
        "minecraft:cooked_porkchop": {"food": 25, "protein": 12},
        "minecraft:cooked_chicken": {"food": 20, "protein": 10},
        "minecraft:cooked_mutton": {"food": 20, "protein": 10},
        "minecraft:cooked_cod": {"food": 15, "protein": 12},
        "minecraft:cooked_salmon": {"food": 20, "protein": 14},
    }

    if item_id not in benefits:
        return {}

    benefit = benefits[item_id]

    # Apply to health
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_health import modify_health
        if "food" in benefit:
            modify_health(puuid, food=benefit["food"])
        if "immune" in benefit:
            modify_health(puuid, immune=benefit["immune"])
    except Exception:
        pass

    # Apply to muscle protein
    try:
        if "protein" in benefit:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db
            db = get_db()
            db.execute("""
                UPDATE ext_shm_muscles SET protein_level = MIN(100, protein_level + ?)
                WHERE persona_uuid = ?
            """, (benefit["protein"] * quantity, puuid))
    except Exception:
        pass

    return {
        "health_benefit_applied": True,
        "buyer": buyer[:8],
        "item": item_id,
        "benefit": benefit,
    }


# ── Hook: on_simulation_cycle_end ────────────────────────────────────

def _on_simulation_cycle_end(**kwargs) -> dict:
    """Called after main simulation cycle completes.

    Here we log any notable health events and ensure data consistency.
    """
    cycle_result = kwargs.get("cycle_result", {})
    return {"status": "complete", "shm_tick": _shm_tick}


# ══════════════════════════════════════════════════════════════════════
# Extension Entry Point
# ══════════════════════════════════════════════════════════════════════

def on_load(registry):
    """Called by ah_plugin_registry when discovering extensions.

    Args:
        registry: The _HookRegistry instance from ah_plugin_registry
    """
    # 1. Ensure database schema exists
    ensure_schema()

    # 2. Initialize SHM data for existing personas (safe to re-run)
    _initialize_existing_personas()

    # 3. Initialize bridge pending actions schema (GAP #1 fix)
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_bridge_pending import (
            ensure_pending_schema
        )
        ensure_pending_schema()
    except Exception:
        pass

    # 4. Migrate existing wounds to SHM pain tracking (safe to re-run)
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_combat_bridge import (
            bridge_migrate_existing_wounds
        )
        bridge_migrate_existing_wounds()
    except Exception:
        pass

    # 5. Register hooks with priority=20 (runs after SIMULATED_PEOPLE at priority=10)
    #    This fixes GAP #3 — explicit tick ordering contract
    registry.register("on_simulation_cycle_start", "SIMULATED_HEALTH_MECHANICS",
                      _on_simulation_cycle_start, priority=20)
    registry.register("on_listing_created", "SIMULATED_HEALTH_MECHANICS",
                      _on_listing_created)
    registry.register("on_purchase", "SIMULATED_HEALTH_MECHANICS",
                      _on_purchase)
    registry.register("on_simulation_cycle_end", "SIMULATED_HEALTH_MECHANICS",
                      _on_simulation_cycle_end)

    log.info("shm_extension",
             "Simulated Health Mechanics extension loaded (blood, bones, muscles, "
             "genetics, disease, hygiene, immune, pain, combat skills, bridge)")


def _initialize_existing_personas():
    """Initialize SHM data for all existing active personas.

    Safe to call repeatedly — uses INSERT OR IGNORE.
    """
    try:
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_active_personas
        personas = get_active_personas()
        initialized = 0
        for p in personas:
            try:
                initialize_persona(p["persona_uuid"], p.get("archetype", "adventurer"))
                initialized += 1
            except Exception:
                pass
        if initialized:
            log.info("shm_extension", f"Initialized SHM data for {initialized} existing personas")
    except Exception:
        pass
