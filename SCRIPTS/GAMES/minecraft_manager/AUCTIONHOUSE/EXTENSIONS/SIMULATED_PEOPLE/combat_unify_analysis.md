# Combat System Unification Analysis

## sp_combat (SIMULATED_PEOPLE) ↔ SHM (SIMULATED_HEALTH_MECHANICS)

> **Date:** 2026-06-09  
> **Scope:** Complete data-model and behavioral overlap analysis  
> **Goal:** Identify a bridge layer that connects the two without breaking either

---

## 1. What Each System Tracks

### 1.1 sp_combat.py — Core Combat Engine

**Database Tables Used:**

| Table | Columns | Purpose |
|-------|---------|---------|
| `ext_sp_wounds` | id, wound_uuid, owner_uuid, body_part, wound_type, severity, bleed_rate, pain_level, infection_chance, infection_progress, is_infected, is_bandaged, is_healed, created_by, created_at | Central wound registry for all combat-inflicted injuries |
| `ext_sp_health` | food, hydration, energy, temperature, waste, hygiene, immune, alive, cause_of_death, decay_timer | Seven vital stats consumed/modified by wounds and combat |

**Key Data Models:**

- **Body Parts (6 abstract):** head, torso, left_arm, right_arm, left_leg, right_leg
- **Wound Types (6):** cut, puncture, crush, laceration, fracture, burn
- **Severity (4 levels):** Minor (1) → Moderate (2) → Severe (3) → Critical (4)
- **Terrain Modifiers (11 biomes):** plains, forest, desert, swamp, mountains, ocean, tundra, nether_wastes, crimson_forest, end_islands, deep_dark
- **Weather Modifiers:** rain, snow, fog, wind + temperature/humidity effects
- **Combat Skills:** Single `"combat"` skill read from `sp_skills.get_skills()` — generic level 1-100
- **Combat Stats:** strength, agility, endurance, perception, pain_threshold, armor_rating, weapon_skill

**What sp_combat DOES uniquely:**
- Resolves melee attacks with hit/miss/dodge/parry
- Applies terrain & weather modifiers to every combat action
- Generates wounds with type/severity/bleed/pain
- Self-harm from terrain movement and crafting accidents
- Processes wound bleeding, infection, and healing per tick
- Bandage and herb first-aid system

---

### 1.2 SHM — Simulated Health Mechanics

**Database Tables (15 tables, `ext_shm_*` prefix):**

| Table | Columns | Purpose |
|-------|---------|---------|
| `ext_shm_blood` | blood_volume_ml, max_blood_volume, blood_toxicity, blood_type, oxygen_saturation, red_blood_cells, white_blood_cells, platelets, glucose_mg_dl, hemoglobin_g_dl, circulation_efficiency | Full blood simulation |
| `ext_shm_blood_regeneration` | iron_stores, b12_stores, hydration_for_blood, regeneration_rate | Blood production resources |
| `ext_shm_anatomy_bones` | bone_name, bone_group, location, fractured, healing_progress, pain_contribution | 206 individual bones |
| `ext_shm_anatomy_organs` | organ_name, health, is_damaged, damage_type, healing_progress | 13 organs |
| `ext_shm_muscles` | muscle_group, strength, max_strength, protein_level, fatigue, is_injured, injury_penalty, atrophy_days | 12 muscle groups |
| `ext_shm_genetics` | 10 genetic traits (metabolic_rate, immune_potency, pain_tolerance, healing_factor, disease_resistance, muscle_growth_rate, toxin_resistance, blood_efficiency, organ_vitality, nerve_density) | Inherited from archetype + personality |
| `ext_shm_diseases` | disease_name, disease_type, severity, stage, pathogen_count, virulence, incubation_ticks, duration_ticks, is_contagious, transmission_mode | Active disease progression |
| `ext_shm_disease_spread` | source_persona, target_persona, disease_name, transmission_type, exposure_time, proximity_score, infection_succeeded | Persona-to-persona spread |
| `ext_shm_hygiene` | personal_cleanliness, oral_hygiene, clothing_cleanliness, wound_care, environment_cleanliness | 5 hygiene metrics |
| `ext_shm_immune_response` | immune_strength, inflammation, fever, wbc_production, battle_tick, is_active, is_autoimmune | Tick-by-tick immune battles |
| `ext_shm_pain` | pain_source, source_type, pain_level, max_pain, duration_ticks, is_active | Per-source pain tracking |
| `ext_shm_combat_skills` | skill_name, level, proficiency_title, xp_current, xp_to_next, times_used, last_used_tick, decay_counter | 10 combat skills with XP |
| `ext_shm_negative_traits` | trait_name, severity, cause, is_active, acquired_tick | 10 negative combat traits |
| `ext_shm_healing_log` | healing_target, target_type, progress_before, progress_after, tick | Healing audit trail |
| `ext_shm_ai_decisions` | decision_type, context, reasoning, outcome | AI thinking outcomes |

**Key Data Models:**

- **Bones:** 206 bones across axial (80) + appendicular (126) skeleton
- **Organs:** 13 (brain, heart, lungs L/R, liver, kidneys L/R, stomach, intestines, spleen, pancreas, bladder, gallbladder)
- **Muscles:** 12 groups (chest, back, shoulders, biceps, triceps, forearms, quadriceps, hamstrings, glutes, calves, abdominals, neck)
- **Combat Skills (10):** blades, blunt, archery, polearms, unarmed, blocking, dodging, critical_strike, combat_awareness, first_aid
- **Diseases (10):** food_poisoning, wound_infection, pneumonia, skin_infection, UTI, dental_infection, dysentery, gangrene, sepsis, fungal_infection
- **Genetic Traits (10):** metabolic_rate, immune_potency, pain_tolerance, healing_factor, disease_resistance, muscle_growth_rate, toxin_resistance, blood_efficiency, organ_vitality, nerve_density

---

## 2. Where They Overlap

### 2.1 Wound Tracking

| Aspect | sp_combat (ext_sp_wounds) | SHM (ext_shm_*) |
|--------|---------------------------|-----------------|
| Wound storage | `ext_sp_wounds` table | Reads `ext_sp_wounds` in `shm_blood.py::process_bleeding_tick()` |
| Bleeding | Abstract `bleed_rate` (per tick) | Converts bleed_rate → ml blood loss via `cause_bleeding()` |
| Body part | 6 abstract parts | Maps to specific bones via `_LOCATION_MAP` |
| Infection | `infection_chance` + `infection_progress` on wounds | `wound_infection` disease + `shm_disease.py` progression |
| Healing | Natural healing in `process_wounds_tick()` | `shm_pain.py::process_healing_tick()` with bone/organ/muscle healing |

**The systems share the same `ext_sp_wounds` table.** SHM reads wounds and applies real blood volume loss. This is the primary integration point — it already works at the bleeding level.

### 2.2 Pain

| Aspect | sp_combat | SHM |
|--------|-----------|-----|
| Pain source | Wound pain_level only | Multiple sources: wounds, fractures, infections, organs, muscles |
| Pain function | `_get_pain_level()` — sums wound pain (diminishing returns at 100+) | `get_total_pain()` — sums all pain sources with tolerance/genetics |
| Pain effects | Applied as penalty to combat stats in `get_combat_stats()` | `get_pain_effects()` — 5-tier debuff system |

**Both track pain and apply stat penalties.** SHM's version is more detailed (genetics-aware, per-source, with healing spikes).

### 2.3 Health Stats

| Aspect | sp_combat | SHM |
|--------|-----------|-----|
| Health consumed | `modify_health()` for food/hydration/energy/immune | `modify_health()` in bleeding, disease, etc. |
| Same function | `sp_health.modify_health()` — both systems use it | `sp_health.modify_health()` — same function |
| Death check | Sepsis deaths via `process_wounds_tick()` | Exsanguination via `check_exsanguination()`, organ failure |

**Both call `sp_health.modify_health()` to drain the same vital stats.** This is the second major integration point.

### 2.4 Combat Skills

| Aspect | sp_combat | SHM |
|--------|-----------|-----|
| Skill system | Single `"combat"` skill from `sp_skills.get_skills()` | 10 discrete skills in `ext_shm_combat_skills` |
| Weapon skill | `combat_skill + (weapon_bonus)` | Per-weapon-type skills (blades, blunt, archery, polearms) |
| Hit modifier | `weapon_skill` → hit chance | `get_combat_skill_modifiers()` → hit/damage/dodge/block/perception bonuses |
| Dodge/Parry | Separate `attempt_dodge()`, `attempt_parry()` | `dodging` and `blocking` skills with proficiency |

**Both modify combat resolution.** sp_combat's `get_combat_stats()` reads a single "combat" skill. SHM provides per-weapon skill levels that could replace it.

### 2.5 Infection / Disease

| Aspect | sp_combat | SHM |
|--------|-----------|-----|
| Wound infection | `infection_progress` → `is_infected` on wound | `wound_infection` disease with full incubation/acute/resolution |
| Sepsis | Manual check in `process_wounds_tick()` | `sepsis` disease with organ damage, AI thinking |
| Treatment | Herbs reduce infection_progress | Immune system battles pathogen count |

**sp_combat has a simplified infection model.** SHM's `wound_infection` disease could replace or augment the wound-level infection tracking. Currently they run in parallel — a wound could be `is_infected=1` in ext_sp_wounds AND have an active `wound_infection` disease in ext_shm_diseases.

### 2.6 First Aid / Healing

| Aspect | sp_combat | SHM |
|--------|-----------|-----|
| Bandage | `apply_bandage()` → 80% bleed reduction | Reads bandaged state from ext_sp_wounds |
| Herbs | `apply_herbs()` → infection reduction | No direct herb system (medicine via purchases) |
| Natural healing | `process_wounds_tick()` → bleed/pain reduction | `process_healing_tick()` → bone/organ/muscle healing |

**sp_combat handles the immediate wound treatment. SHM handles the deeper physiological recovery.** They're complementary.

---

## 3. Where They Diverge

### 3.1 Body Part Model

| sp_combat | SHM |
|-----------|-----|
| 6 abstract body parts | 206 individual bones + 13 organs + 12 muscle groups |
| `_roll_body_part()` uses weighted random pick | Fracture logic uses `fracture_from_impact()` with per-bone hit tables |
| Armor protects "head/torso/arms" generically | No armor → bone fracture mapping yet |

**Fundamental granularity difference.** sp_combat's 6-part model feeds into SHM's 206-bone model via `_LOCATION_MAP` in `shm_anatomy.py` — but the mapping is incomplete: "left_arm" maps every bone in that arm, which is lossy.

### 3.2 Terrain & Environment

| sp_combat | SHM |
|-----------|-----|
| 11 biome types with agility/move/perception/slip modifiers | No terrain awareness whatsoever |
| Weather (rain, snow, fog, wind, temperature) modifies combat | No weather effects on health/combat |
| Light level (day/night cycle) affects perception | No light/visibility system |
| Self-harm from terrain movement (`check_terrain_self_harm()`) | No terrain self-harm |

**SHM has zero environmental awareness.** This is sp_combat's exclusive domain and should remain there.

### 3.3 Genetics

| sp_combat | SHM |
|-----------|-----|
| No genetic traits | 10 genetic traits from archetype + personality |
| Static persona stats | `muscle_growth_rate`, `pain_tolerance`, `healing_factor`, etc. affect everything |
| No variability between personas of same archetype | Each persona has unique genetic profile |

**SHM's genetics system is entirely separate** and currently not read by sp_combat. Pain tolerance genetics, for example, should affect combat pain penalties.

### 3.4 Muscle System

| sp_combat | SHM |
|-----------|-----|
| Strength derived from food/energy (simple) | 12 muscle groups with strength, protein, fatigue, atrophy, injury |
| No limb-specific strength | `get_location_strength_penalty()` per limb |
| No protein metabolism | Protein burn during activity, synthesis during rest |

**sp_combat calculates `strength = 10 * food_factor * energy_factor`. SHM tracks per-muscle strength** with hypertrophy, atrophy, and protein costs.

### 3.5 Skill Detail & Evolution

| sp_combat | SHM |
|-----------|-----|
| Single "combat" skill, no sub-types | 10 skills (blades, blunt, archery, polearms, unarmed, blocking, dodging, critical_strike, combat_awareness, first_aid) |
| No XP or level-up | XP system with level-ups, synergy bonuses, soft cap at 50 |
| No decay | Decay over ticks of non-use |
| No negative traits | 10 negative traits (sluggish_reflexes, tunnel_vision, etc.) |
| No proficiency titles | 8 proficiency tiers (Unskilled → Legendary) |

**SHM's combat skills are vastly more detailed** and designed to replace sp_combat's generic skill lookup entirely.

### 3.6 Disease Propagation

| sp_combat | SHM |
|-----------|-----|
| Wound infection only (individual) | 10 disease types with person-to-person spread |
| No contagion mechanics | Airborne/contact/water transmission |
| No incubation stage | 4-stage disease progression (incubation → acute → battle → resolution) |
| No immunity | Temporary immunity after recovery |
| No hygiene link | Hygiene directly affects infection susceptibility (50-200% modifier) |

**SHM's disease system is far more comprehensive.** sp_combat's wound infection is a subset.

### 3.7 Death & Critical Resolution

| sp_combat | SHM |
|-----------|-----|
| Deterministic death (stat thresholds) | AI thinking (`sequentialthinking`) for critical outcomes |
| Sepsis deaths via stat check | Organ failure, exsanguination, sepsis with narrative resolution |
| No organ tracking | 13 organs with failure mechanics |

**SHM uses AI for narrative resolution at critical health junctions.** sp_combat uses purely mechanical death triggers.

### 3.8 Combat Stat Derivation

| sp_combat | SHM |
|-----------|-----|
| `get_combat_stats()` directly | `get_combat_skill_modifiers()` + `get_blood_loss_effects()` + `get_pain_effects()` + `get_muscle_combat_modifier()` |
| Strength from food/energy only | Strength from per-muscle groups × fatigue × injury penalty |
| Agility from energy/hydration only | Agility from pain × blood loss × fracture penalties |
| Perception from hydration/energy | Perception from combat_awareness skill × pain |

**sp_combat calculates combat stats from 3 health variables. SHM would derive them from 100+ tracked values** (blood, bones, muscles, skills, pain, genetics).

---

## 4. Proposed Bridge Layer

> **Design Principle:** The bridge should NOT modify sp_combat's core combat loop. Instead, it should intercept key entry points and enrich them with SHM data. SHM reads from `ext_sp_wounds` already — this extends that bidirectional data flow.

### 4.1 Architecture Overview

```
                    ┌─────────────────────────────┐
                    │     sp_combat.py (Core)      │
                    │   • Combat resolution        │
                    │   • Terrain/weather mods     │
                    │   • Wound generation         │
                    │   • First aid / bandages     │
                    └──────────┬──────────────────┘
                               │ reads/writes
                               ▼
                    ┌─────────────────────────────┐
                    │     ext_sp_wounds table      │
                    │     ext_sp_health table      │
                    └──────────┬──────────────────┘
                               │ SHM also reads/writes
                               ▼
                    ┌─────────────────────────────┐
                    │  SHM Bridge Layer (NEW)      │
                    │  shm_combat_bridge.py         │
                    │                               │
                    │  Functions:                   │
                    │  • bridge_get_combat_stats()  │
                    │  • bridge_on_wound_created()  │
                    │  • bridge_on_wound_processed()│
                    │  • bridge_get_skill_mods()    │
                    │  • bridge_get_pain_effects()  │
                    └──────────┬──────────────────┘
                               │ reads
                               ▼
                    ┌─────────────────────────────┐
                    │  SHM Tables (ext_shm_*)      │
                    │  blood, bones, organs,       │
                    │  muscles, genetics, pain,    │
                    │  combat_skills, diseases     │
                    └─────────────────────────────┘
```

### 4.2 Bridge Functions

#### 4.2.1 `bridge_get_combat_stats(persona_uuid) → dict`

**Replaces** `sp_combat.get_combat_stats()` as the stat provider when SHM is loaded.

**Logic:**
1. Start with sp_combat's existing terrain/weather modifiers (keep these intact)
2. Replace strength derivation with `shm_muscle.get_total_strength()` × injury penalty
3. Replace agility with `(1 - get_pain_effects().agility_penalty)` × blood loss penalty
4. Replace weapon_skill with `shm_combat_skills.get_skill_level(persona, weapon_type)`
5. Add dodge parry bonuses from `shm_combat_skills.get_combat_skill_modifiers()`
6. Apply fracture penalties via `shm_anatomy.get_location_strength_penalty()`

**Implementation Sketch:**
```python
def bridge_get_combat_stats(persona_uuid, target=None):
    """Enriched combat stats using SHM data."""
    # 1. Get terrain/weather from sp_combat (keep existing)
    terrain_stats = sp_combat._get_terrain_combat_baseline(persona_uuid)

    # 2. Get SHM-derived stats
    muscle_strength = shm_muscle.get_total_strength(persona_uuid)
    blood_effects = shm_blood.get_blood_loss_effects(
        shm_blood.get_blood_stats(persona_uuid))
    pain_effects = shm_pain.get_pain_effects(persona_uuid)
    fracture_penalty = shm_anatomy.get_location_strength_penalty(
        persona_uuid, "torso")  # General weakness from fractures
    skill_mods = shm_combat_skills.get_combat_skill_modifiers(persona_uuid)

    # 3. Merge: SHM overrides base health-derived stats, keeps terrain
    return {
        "strength": round(muscle_strength * (1 - pain_effects["strength_penalty"])
                          * (1 - blood_effects.get("penalty", 0)), 1),
        "agility": round(max(1, terrain_stats["agility"]
                          * (1 - pain_effects["agility_penalty"])
                          * (1 - blood_effects.get("penalty", 0))), 1),
        "perception": round(max(1, terrain_stats["perception"]
                           * (1 - pain_effects["perception_penalty"])), 1),
        "weapon_skill": round(skill_mods.get("hit_chance_bonus", 0)
                              + terrain_stats["weapon_skill_mod"], 1),
        "dodge_bonus": skill_mods.get("dodge_bonus", 0),
        "block_bonus": skill_mods.get("block_bonus", 0),
        "critical_bonus": skill_mods.get("critical_chance_bonus", 0),
        "armor_rating": terrain_stats["armor_rating"],  # Keep from sp_combat
    }
```

#### 4.2.2 `bridge_on_wound_created(wound: dict) → None`

**Hook called after** `_create_wound()` in sp_combat inserts a new wound.

**Logic:**
1. Map wound body_part to specific bones via `shm_anatomy._LOCATION_MAP`
2. If severity ≥ 3 and impact_force permits: call `shm_anatomy.fracture_from_impact()`
3. Register pain source via `shm_pain.add_pain_source()`
4. Trigger bleeding via `shm_blood.cause_bleeding()`

**Implementation Sketch:**
```python
def bridge_on_wound_created(wound, impact_force=1.0):
    """Bridge newly created wounds into SHM systems."""
    puuid = wound["owner_uuid"]

    # 1. Map to bones and potentially fracture
    if wound["severity"] >= 3:
        fractures = shm_anatomy.fracture_from_impact(
            puuid, wound["body_part"], impact_force
        )

    # 2. Register SHM pain source
    shm_pain.add_pain_source(
        puuid,
        f"wound_{wound['wound_uuid'][:8]}",
        "wound",
        pain_level=wound["pain_level"] * 2,  # Convert to SHM scale
        duration_ticks=48
    )

    # 3. Convert abstract bleed to blood loss
    bleed_ml = wound["bleed_rate"] * 5.0  # Scale factor
    shm_blood.cause_bleeding(puuid, bleed_ml, wound["body_part"])
```

#### 4.2.3 `bridge_get_pain_effects(persona_uuid) → dict`

**Replaces** sp_combat's `_get_pain_level()` with SHM's full pain system.

**Logic:**
1. Call `shm_pain.get_total_pain()` (includes wound pain, fracture pain, infection pain, organ pain)
2. Apply genetics-based pain tolerance multiplier
3. Return structured debuffs

```python
def bridge_get_pain_effects(persona_uuid):
    """Get pain debuffs using SHM's multi-source pain system."""
    pain = shm_pain.get_total_pain(persona_uuid)
    fracture_pain = shm_anatomy.get_fracture_pain(persona_uuid)
    total = pain + fracture_pain  # SHM's pain already includes wounds

    # Apply genetics
    genetics = shm_database.get_db().fetch_one(
        "SELECT pain_tolerance FROM ext_shm_genetics WHERE persona_uuid = ?",
        (persona_uuid,))
    if genetics:
        total /= max(0.1, genetics["pain_tolerance"])

    return shm_pain.get_pain_effects_for_combat(persona_uuid, total)
```

#### 4.2.4 `bridge_get_skill_level(persona_uuid, weapon_type) → float`

**Replaces** the generic combat skill lookup in `get_combat_stats()`.

**Logic:**
Map weapon_type to SHM skill name, return level with proficiency bonus.

```python
WEAPON_TO_SKILL = {
    "sword": "blades", "axe": "blades", "knife": "blades",
    "mace": "blunt", "hammer": "blunt", "fist": "unarmed",
    "spear": "polearms", "trident": "polearms",
    "bow": "archery", "crossbow": "archery",
}

def bridge_get_skill_level(persona_uuid, weapon_type):
    skill_name = WEAPON_TO_SKILL.get(weapon_type, "unarmed")
    level = shm_combat_skills.get_skill_level(persona_uuid, skill_name)
    # Apply synergy bonuses
    mods = shm_combat_skills.get_combat_skill_modifiers(persona_uuid)
    return level + mods.get("hit_chance_bonus", 0)
```

#### 4.2.5 `bridge_on_healing_tick() → None`

**Called from** `shm_pain.process_healing_tick()` (already in SHM tick loop).

**Current state:** SHM already calls `sp_combat.process_wounds_tick()` isn't directly called from SHM — but SHM's `_on_simulation_cycle_start` runs after sp_health's cycle. The bridge would ensure sp_combat's `process_wounds_tick()` and SHM's healing tick are properly ordered.

```python
def bridge_on_healing_tick(persona_uuid):
    """Coordinate sp_combat wound healing with SHM healing."""
    # SHM reads ext_sp_wounds bleed_rate and converts to blood loss
    # This already happens in shm_blood.process_bleeding_tick()
    # The bridge ensures pain from wounds matches SHM pain tracking
    wounds = sp_combat.get_active_wounds(persona_uuid)
    for w in wounds:
        # Sync wound pain to SHM pain system
        if w["pain_level"] > 0:
            shm_pain.add_pain_source(
                persona_uuid,
                f"wound_{w['wound_uuid'][:8]}",
                "wound",
                w["pain_level"],
                duration_ticks=12
            )
```

### 4.3 Hook Integration Plan

```
sp_combat resolution flow:

  1. get_combat_stats()
     ├── bridge_get_combat_stats() [REPLACE when SHM loaded]
     └── else: keep original

  2. resolve_melee_attack()
     ├── bridge_get_skill_level() [REPLACE weapon skill lookup]
     ├── _create_wound() → calls bridge_on_wound_created() [NEW HOOK]
     └── modify_health() [keep — both systems use same]

  3. process_wounds_tick()
     ├── bridge_on_healing_tick() [NEW HOOK for coordination]
     └── SHM's _on_simulation_cycle_start runs after

  4. _get_pain_level()
     └── bridge_get_pain_effects() [REPLACE when SHM loaded]

SHM tick flow (__init__.py):

  1. Blood regeneration/detox  ← reads ext_sp_wounds bleed_rate
  2. Muscle recovery           ← bridge updates fatigue from combat
  3. Disease progression       ← reads ext_sp_wounds infection
  4. Hygiene decay             ← combat affects clothing_cleanliness
  5. Immune response           ← wound infection triggers immune battle
  6. Pain & healing            ← reads ext_shm_pain + ext_sp_wounds
  7. Combat skill decay        ← bridge: skill use from combat log
  8. Negative traits           ← bridge: check combat patterns
```

### 4.4 Configuration / Feature Flags

Add to SHM config or bridge module:

```python
SHM_BRIDGE_CONFIG = {
    "enabled": True,                  # Master switch
    "replace_combat_stats": True,     # Use SHM muscles/blood/pain for stats
    "replace_weapon_skill": True,     # Use SHM per-weapon skills
    "replace_pain": True,             # Use SHM multi-source pain system
    "enable_fractures": True,         # Fracture bones on severe wounds
    "enable_blood_loss": True,        # Track actual blood volume from wounds
    "enable_skill_decay": True,       # Skill decay during inactivity
    "enable_negative_traits": True,   # Track negative combat traits
    "enable_combat_muscle_use": True, # Combat uses SHM muscles (fatigue + protein)
}
```

### 4.5 Migration Path

**Phase 1 — Read-only SHM augmentation (safe, no sp_combat changes):**
- SHM already reads `ext_sp_wounds` for bleeding → verify correctness
- SHM already reads `ext_sp_health` for health stats → verify correctness
- Add `bridge_on_wound_created()` to register SHM pain/fractures
- No changes to sp_combat.py

**Phase 2 — Optional stat replacement (config-flagged):**
- Add `bridge_get_combat_stats()` but call it from SHM's hook (not sp_combat)
- sp_combat's `get_combat_stats()` remains unchanged; SHM override reads the results and enriches them
- This keeps sp_combat deterministic when SHM isn't loaded

**Phase 3 — Full bidirectional sync:**
- Combat skill usage in sp_combat updates `ext_shm_combat_skills` via `use_skill()`
- Wound infection in sp_combat triggers `shm_disease.expose_to_disease()`
- Bandage application in sp_combat updates `ext_shm_hygiene.wound_care`
- Muscle fatigue from combat updates `ext_shm_muscles` via `use_muscles()`
- Bone fractures from combat modify sp_combat's combat stats

### 4.6 Files to Create / Modify

**NEW:** `SIMULATED_HEALTH_MECHANICS/shm_combat_bridge.py`
- All bridge functions listed above
- `bridge_get_combat_stats()`, `bridge_on_wound_created()`, `bridge_get_pain_effects()`, etc.
- Configuration and feature flags

**MODIFY:** `SIMULATED_HEALTH_MECHANICS/__init__.py`
- Import bridge module
- Call `bridge_on_wound_created()` from `_on_simulation_cycle_start` (or add new hook)
- Call `bridge_on_healing_tick()` during healing phase

**MODIFY (optional):** `SIMULATED_PEOPLE/sp_combat.py`
- Add config-aware import: `from ...SIMULATED_HEALTH_MECHANICS.shm_combat_bridge import ...`
- Guard with try/except for graceful fallback when SHM not installed

---

## 5. Summary

| Dimension | sp_combat | SHM | Bridge Approach |
|-----------|-----------|-----|-----------------|
| Body parts | 6 abstract | 206 bones + 13 organs + 12 muscles | Map 6→bones on severe hits |
| Combat stats | Simple (3 vars) | Complex (100+ vars) | SHM enriches sp_combat derived stats |
| Skills | 1 generic | 10 with XP/decay | Replace weapon_skill with per-weapon lookup |
| Pain | Wound-level only | Multi-source + genetics | Replace pain function with SHM version |
| Bleeding | Abstract rate | Real ml/tick | Already connected |
| Infection | Wound boolean | Full disease system | Trigger diseases from wound infection |
| Terrain | 11 biomes + weather | None | Keep in sp_combat (SHM stays unaware) |
| Genetics | None | 10 traits | Pain tolerance, healing factor → combat effects |
| Death | Deterministic | AI-thinking | SHM evaluator overrides at critical points |

**Key Insight:** The systems are already partially connected — SHM reads `ext_sp_wounds` and `ext_sp_health`. The bridge needs to complete the loop by feeding SHM data (muscle strength, blood loss, pain tolerance, fracture penalties) back into sp_combat's stat derivation, while keeping sp_combat's terrain/weather/combat-resolution logic untouched.
