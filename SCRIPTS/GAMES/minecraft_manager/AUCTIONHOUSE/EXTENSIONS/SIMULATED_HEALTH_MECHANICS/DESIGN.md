# Simulated Health Mechanics Extension — Design Specification

## 1. Overview

The Simulated Health Mechanics (SHM) extension replaces and vastly extends the 
basic health system in SIMULATED_PEOPLE. It models the full human body at a 
detailed level — blood, bones, muscles, organs, immune system — and ties it 
to the existing persona system with genetics, disease, hygiene, and combat skills.

**Core Principle:** Every system has inputs (food, water, rest, hygiene, medical 
care), processes (metabolism, immune response, healing, skill growth), and 
outputs (health state, combat effectiveness, death, skill level). These form 
a complex web of interdependent feedback loops.

## 2. Architecture

### 2.1 Extension Structure

```
SIMULATED_HEALTH_MECHANICS/
├── DESIGN.md                 ← This document
├── __init__.py               ← Extension entry point & hook registration
├── shm_database.py           ← Schema (14 tables, ext_shm_* prefix)
├── shm_blood.py              ← Blood volume, toxicity, type, circulation
├── shm_anatomy.py            ← Full skeleton (206 bones), organs, body structure
├── shm_muscle.py             ← Muscle groups, protein, strength, fatigue
├── shm_genetics.py           ← Genetic traits derived from persona archetype
├── shm_disease.py            ← Disease progression & bacteria infection
├── shm_hygiene.py            ← Hygiene tracking & bacteria spread simulation
├── shm_immune.py             ← Immune response & autoimmune battles
├── shm_pain.py               ← Pain signals & healing mechanics
├── shm_combat_skills.py      ← Skill evolution, proficiency, negative traits
├── shm_ai_engine.py          ← AI thinking for final outcome resolution
└── tests/
    ├── test_shm_unit.py      ← Unit tests for all subsystems
    ├── test_shm_dataflow.py  ← Data flow & integration tests
    └── test_shm_integration.py ← 3-round full integration tests
```

### 2.2 Hook Integration

| Hook | What SHM does |
|------|---------------|
| `on_simulation_cycle_start` | Runs full health tick (blood, muscles, disease, immune, healing, hygiene spread) |
| `on_listing_created` | Checks if food/medicine is listed → adjusts persona purchase urgency |
| `on_purchase` | If persona bought medicine/food → applies health benefits |
| `on_simulation_cycle_end` | Runs AI thinking for critical health outcomes |

### 2.3 Dependencies

- **SIMULATED_PEOPLE** — Reads persona profiles, finances, health base stats
- **sp_behavior.py** — Extends purchase decisions with health-driven needs
- **sp_ecosystem.py** — Uses food/water foraging for autonomous health maintenance
- **sp_combat.py** — Extends with detailed wound resolution using anatomy

## 3. Blood System (ext_shm_blood)

### 3.1 Blood Components

| Component | Range | Description |
|-----------|-------|-------------|
| blood_volume_ml | 4000-6000 | Total blood volume (avg 5000ml adult) |
| blood_toxicity | 0-100 | Toxin level (0=clean, 100=lethal) |
| blood_type | A/B/AB/O ± | Genetic blood type, affects transfusion |
| oxygen_saturation | 0-100 | O2 level (healthy 95-100) |
| red_blood_cells | 0-100 | RBC count relative to norm |
| white_blood_cells | 0-100 | WBC count (immune response indicator) |
| platelets | 0-100 | Clotting ability |
| glucose_mg_dl | 0-500 | Blood sugar level |
| hemoglobin_g_dl | 0-20 | Oxygen-carrying capacity |

### 3.2 Blood Dynamics

- **Bleeding:** Wounds cause blood loss (ml/tick based on wound severity)
- **Clotting:** Platelets + wound bandaging reduce bleed rate
- **Toxicity:** Food poisoning, infection, organ failure → +toxicity
- **Detox:** Kidney/liver function → -toxicity (tied to organ health)
- **Oxygen:** Lung health + RBC count → oxygen saturation
- **Circulation:** Heart health + blood volume → circulation efficiency

### 3.3 Blood Loss Consequences

| Blood Loss % | Effect |
|--------------|--------|
| 0-15% | Minor weakness, slight stat penalty |
| 15-30% | Significant weakness, dizziness, confusion |
| 30-40% | Severe debilitation, consciousness risk |
| >40% | Life-threatening organ failure → death |

## 4. Anatomy System (ext_shm_anatomy)

### 4.1 Complete Skeleton (206 Bones)

The human skeleton has 206 bones organized by region:

**Axial Skeleton (80 bones):**
- Skull: cranial (8) + facial (14) = 22 bones
- Hyoid bone: 1
- Auditory ossicles (ears): 6 (3 per ear)
- Vertebral column: 26 (7 cervical + 12 thoracic + 5 lumbar + sacrum + coccyx)
- Rib cage: 25 (sternum + 24 ribs)

**Appendicular Skeleton (126 bones):**
- Upper limbs: 64 (shoulder girdle 4 + arms 6 + hands 54)
- Lower limbs: 62 (pelvic girdle 2 + legs 8 + feet 52)

### 4.2 Organ System

| Organ | Health (0-100) | Function |
|-------|---------------|----------|
| brain | 0-100 | Cognition, consciousness |
| heart | 0-100 | Circulation, blood pumping |
| lungs (2) | 0-100 | Oxygenation |
| liver | 0-100 | Detoxification, metabolism |
| kidneys (2) | 0-100 | Filtration, waste removal |
| stomach | 0-100 | Digestion |
| intestines | 0-100 | Nutrient absorption |
| spleen | 0-100 | Immune filter |
| pancreas | 0-100 | Insulin, digestion |
| bladder | 0-100 | Waste storage |

### 4.3 Bone Fracture System

Each bone tracks: integrity (broken/whole), healing_progress, and pain_contribution.
Fractures severely impair strength/mobility on affected limb(s).

## 5. Muscle System (ext_shm_muscles)

### 5.1 Major Muscle Groups

| Muscle Group | Strength (0-100) | Protein (0-100) | Fatigue (0-100) |
|--------------|-----------------|-----------------|-----------------|
| chest | 0-100 | 0-100 | 0-100 |
| back | 0-100 | 0-100 | 0-100 |
| shoulders | 0-100 | 0-100 | 0-100 |
| biceps | 0-100 | 0-100 | 0-100 |
| triceps | 0-100 | 0-100 | 0-100 |
| forearms | 0-100 | 0-100 | 0-100 |
| quadriceps | 0-100 | 0-100 | 0-100 |
| hamstrings | 0-100 | 0-100 | 0-100 |
| glutes | 0-100 | 0-100 | 0-100 |
| calves | 0-100 | 0-100 | 0-100 |
| abdominals | 0-100 | 0-100 | 0-100 |
| neck | 0-100 | 0-100 | 0-100 |

### 5.2 Muscle Dynamics

- **Strength Gain:** Physical activity + protein availability + rest
- **Protein Burn:** Activity consumes protein; insufficient protein = muscle breakdown
- **Fatigue Accumulation:** Activity → fatigue; rest → recovery
- **Weakness During Healing:** Injured muscle groups have reduced strength
- **Atrophy:** Extended inactivity → strength decay (Δ = -1 to -5 per day)
- **Hypertrophy:** Regular training → strength gain (Δ = +1 to +3 per day, capped)

### 5.3 Protein Metabolism

- Protein from: food with calories (meat > fish > plants)
- Protein used for: muscle maintenance, repair, growth
- Protein deficiency → muscle breakdown, weakness, slow healing

## 6. Genetics System (ext_shm_genetics)

### 6.1 Genetic Traits

Derived from persona archetype, personality traits, and random variation:

| Trait | Range | Effect |
|-------|-------|--------|
| metabolic_rate | 0.5-2.0 | Food/energy consumption rate |
| immune_potency | 0.3-2.0 | Immune system strength multiplier |
| pain_tolerance | 0.3-2.0 | Pain reduction multiplier |
| healing_factor | 0.3-2.0 | Wound healing speed multiplier |
| disease_resistance | 0.3-2.0 | Resistance to catching diseases |
| muscle_growth_rate | 0.3-2.0 | Muscle gain speed |
| toxin_resistance | 0.3-2.0 | Poison/drug resistance |
| blood_efficiency | 0.3-2.0 | Oxygen utilization |
| organ_vitality | 0.3-2.0 | Organ health retention |
| nerve_density | 0.3-2.0 | Pain sensitivity (higher = more pain) |

### 6.2 Inheritance from Persona

- **Archetype** determines genetic baseline
- **Personality traits** modify genetics:
  - `endurance` → +metabolic_rate, +muscle_growth_rate
  - `wisdom` → +immune_potency, +toxin_resistance
  - `bravery` → +pain_tolerance
  - `thrift` → +healing_factor, +organ_vitality
  - `patience` → +disease_resistance
  - `courage` → +blood_efficiency
  - `aggression` → +muscle_growth_rate, -pain_tolerance
  - `simplicity` → +organ_vitality, -nerve_density

## 7. Disease & Infection System (ext_shm_diseases)

**No viruses** (too strong for this virtual world — let immune system battle 
bacteria and environmental factors instead).

### 7.1 Disease Types

| Disease | Transmission | Severity | Duration | Symptoms |
|---------|-------------|----------|----------|----------|
| food_poisoning | Contaminated food | 1-3 | 24-72h | Vomiting, fever, dehydration |
| wound_infection | Dirty wounds | 1-4 | 48h-14d | Fever, pus, spreading redness |
| bacterial_pneumonia | Airborne (cough) | 2-4 | 7-21d | Cough, fever, breathing difficulty |
| skin_infection | Poor hygiene | 1-2 | 3-7d | Rash, itching, minor fever |
| urinary_tract | Poor hygiene | 1-2 | 3-7d | Painful urination, fever |
| dental_infection | Poor hygiene | 1-3 | 3-10d | Toothache, fever, swelling |
| dysentery | Contaminated water | 2-3 | 3-10d | Diarrhea, dehydration, weakness |
| gangrene | Severe wound + bacteria | 3-4 | 5-20d | Tissue death, sepsis risk |
| sepsis | Systemic infection | 4-5 | Variable | Organ failure, death risk |
| fungal_infection | Damp conditions | 1-2 | 7-30d | Skin issues, minor debilitation |

### 7.2 Disease Mechanics

1. **Exposure:** Persona encounters pathogen source
2. **Incubation:** Pathogen multiplies (hours-days, based on immune system)
3. **Acute Phase:** Symptoms manifest, stats drain
4. **Immune Battle:** Immune system vs pathogen (tick-by-tick)
5. **Resolution:** Either recovery (pathogen cleared) or progression (worsening)
6. **Immunity:** Temporary immunity after recovery (variable duration)

### 7.3 Bacteria Spread Simulation

Bacteria spread through:
- **Direct contact:** Touching infected person/object
- **Airborne:** Coughing/sneezing in close proximity
- **Contaminated surfaces:** Shared items, food, water
- **Poor hygiene:** Low hygiene increases susceptibility + spread radius

Spread probability: `base_rate × hygiene_factor × proximity_factor × immune_factor`

## 8. Hygiene System (ext_shm_hygiene)

### 8.1 Hygiene Metrics

| Metric | Range | Description |
|--------|-------|-------------|
| personal_cleanliness | 0-100 | Overall body cleanliness |
| oral_hygiene | 0-100 | Mouth/dental health |
| clothing_cleanliness | 0-100 | Garment condition |
| wound_care | 0-100 | How well wounds are maintained |
| environment_cleanliness | 0-100 | Living area cleanliness |

### 8.2 Hygiene Decay

- Physical activity → -personal_cleanliness
- Eating → -oral_hygiene
- Fighting/bleeding → -clothing_cleanliness, -wound_care
- Time in dirty area → -environment_cleanliness
- Rain → +clothing_cleanliness (washes away)

### 8.3 Hygiene → Disease Link

- Low hygiene (any metric < 30) → infection susceptibility +50-200%
- High hygiene (any metric > 80) → infection susceptibility -30-50%
- Dirty wounds (wound_care < 20) → nearly guaranteed infection
- Clean environment → faster recovery, less reinfection

## 9. Immune System (ext_shm_immune)

### 9.1 Immune Response Stages

1. **Recognition:** Immune cells identify pathogen (speed depends on immune_potency)
2. **Response:** Inflammation, fever, WBC production (costs energy + food)
3. **Attack:** Immune cells destroy pathogen (battle, tick-by-tick)
4. **Memory:** Some diseases leave immunity markers for future resistance
5. **Resolution:** Pathogen cleared OR immune system overwhelmed

### 9.2 Battle Resolution

Each tick, the immune system and pathogen fight:

```python
immune_damage = (white_blood_cells × immune_potency × random(0.5,1.5))
pathogen_damage = (pathogen_virulence × pathogen_count × random(0.3,1.0))

pathogen_count -= immune_damage
immune_strength -= pathogen_damage
```

- If pathogen_count reaches 0 → recovery
- If immune_strength reaches 0 → disease progresses, organ damage
- AI thinking decides tiebreaker at critical junctions

### 9.3 Autoimmune Conditions

When immune system attacks healthy tissue:
- Trigger: Severe infection, genetic predisposition, certain environmental factors
- Effect: Random organ damage, chronic inflammation
- Resolution: Difficult — requires medical intervention (medicine/herbs)

## 10. Pain & Healing System (ext_shm_pain)

### 10.1 Pain Sources

| Source | Pain Contribution | Duration |
|--------|-------------------|----------|
| Fresh wound | Wound severity × 20-40 | Until bandaged/healed |
| Fracture | 30-60 per broken bone | Until set and healing |
| Infection | 10-30 | Until treated |
| Organ damage | 20-50 | Until organ heals |
| Muscle strain | 5-20 | 1-3 days |
| Dental | 15-40 | Until treated |
| Inflammation | 5-25 | While inflammation persists |

### 10.2 Pain Effects

| Pain Level | Effect |
|------------|--------|
| 0 | No effect |
| 1-20 | -5% agility, -5% strength |
| 21-50 | -15% agility, -10% strength, -5% perception |
| 51-80 | -30% agility, -20% strength, -15% perception, sleep disruption |
| 81-100 | -50% all stats, possible fainting, severe debilitation |
| 100+ | Unconsciousness, shock risk |

### 10.3 Healing Mechanics

- **Natural Healing:** +1-5 HP/day per wound (modified by genetics + nutrition + rest)
- **Bone Healing:** Fractures heal in 3-6 weeks (simulated: 3-6 ticks)
- **Tissue Repair:** Needs protein + energy + rest
- **Scarring:** Severe wounds leave permanent scar tissue (slight stat penalty)
- **Pain During Healing:** Pain spikes during active healing (tissue regeneration)
- **Rest Requirement:** Without rest, healing speed reduces by 50-90%

### 10.4 Weakness During Healing

While the body repairs tissue:
- Strength reduced by 20-50% in affected area
- Energy drain increased by 20%
- Immune system prioritized to healing site (other areas slightly more vulnerable)
- Full recovery restores strength; incomplete rest leads to chronic weakness

## 11. Combat Skills Evolution (ext_shm_combat_skills)

### 11.1 Skill Categories

| Skill | How It Improves | Decay |
|-------|-----------------|-------|
| blades | Using swords, knives, axes | -1/day without use |
| blunt | Using maces, hammers, fists | -1/day without use |
| archery | Using bows, crossbows | -2/day without use |
| polearms | Using spears, tridents | -1/day without use |
| unarmed | Hand-to-hand combat | -0.5/day without use |
| blocking | Using shields, parrying | -1/day without use |
| dodging | Evading attacks | -1/day without use |
| critical_strike | Landing precise hits | -2/day without use |
| combat_awareness | Reading opponents | -1/day without use |
| first_aid | Treating wounds mid-combat | -1/day without use |

### 11.2 Skill Mechanics

- **Gain per use:** `skill_gain = base_rate × genetics.muscle_growth_rate × random(0.5, 1.5)`
- **Current level cap:** `hard_cap = 100`; soft cap at 50 where gains halve
- **Decay formula:** `decay = base_decay × (1 - skill/200) × genetics` (higher skills decay faster)
- **Synergy:** High related skills boost each other (e.g., blades + critical_strike)

### 11.3 Proficiency Levels

| Level | Title | Effect |
|-------|-------|--------|
| 0 | Unskilled | Basic attacks, high miss chance |
| 1-10 | Novice | +5% hit, +0% damage |
| 11-25 | Apprentice | +10% hit, +5% damage |
| 26-40 | Journeyman | +15% hit, +10% damage, basic combos |
| 41-55 | Expert | +20% hit, +15% damage, special attacks |
| 56-70 | Master | +25% hit, +20% damage, advanced combos |
| 71-85 | Grandmaster | +30% hit, +25% damage, signature moves |
| 86-100 | Legendary | +35% hit, +30% damage, unique techniques |

### 11.4 Negative Traits

When a skill is neglected or used poorly, negative traits develop:

| Negative Trait | Cause | Effect |
|----------------|-------|--------|
| sluggish_reflexes | Low dodging | -10% dodge chance |
| clumsy_strikes | Low all weapon skills | +10% fumble chance |
| shield_dependent | Over-reliance on blocking | -20% dodge when unshielded |
| reckless_swing | Low critical_strike + high aggression | +5% self-damage chance |
| panic_parry | Stress from combat | -15% parry effectiveness |
| tunnel_vision | Low combat_awareness | -20% perception in combat |
| weak_grip | Low practice | Chance to drop weapon |
| slow_healer | Poor first_aid | Injuries heal 25% slower |
| predictable_pattern | Low skill diversity | +10% chance opponent predicts moves |
| combat_fatigue | Over-exertion | Energy drains 30% faster |

## 12. AI Thinking for Outcome Resolution

When health events reach critical junctions, the `sequentialthinking` tool 
is used to decide the outcome. This occurs for:

1. **Death events:** "Should this persona die, or survive with severe damage?"
2. **Disease tipping points:** "Does the immune system win or lose?"
3. **Combat resolutions:** "What is the narrative outcome of this fight?"
4. **Organ failure:** "Which organs fail, and can they be saved?"
5. **Genetic expression:** "How do genetics influence this specific crisis?"

The AI receives:
- Current persona state (health, genetics, combat skills, active diseases)
- Context of the situation (combat, disease exposure, injury)
- Simulation history (recent events, wounds, treatments)

And returns:
- Decision with reasoning
- Outcome modifiers
- Narrative description

## 13. Integration with Existing Systems

### 13.1 With sp_health.py

The SHM extension **replaces** the health processing:
- `process_persona_health()` in sp_health.py → SHM health tick
- Health stats (food, hydration, energy) still flow into SHM as inputs
- SHM provides more granular health outputs (blood, muscles, organs)

### 13.2 With sp_combat.py

- Combat wounds now reference specific bones and body parts
- `get_combat_stats()` uses SHM muscle strength + pain + blood loss
- Bleeding calculated from blood volume, not abstract "bleed_rate"
- Fractures tracked on actual bones

### 13.3 With sp_behavior.py

- Persona purchase decisions include health-driven needs:
  - Buying medicine if sick
  - Buying food if protein low
  - Buying bandages/clean water if wounded
- Urgency modified by health crisis level

### 13.4 With sp_ecosystem.py

- Foraging success modified by muscle strength + fatigue
- Hunting modified by combat skills + energy
- Drinking water quality affects bacteria exposure chance

## 14. Database Schema

All tables prefixed with `ext_shm_`:

### ext_shm_blood
### ext_shm_anatomy_bones (206 bones)
### ext_shm_anatomy_organs (10+ organs)
### ext_shm_muscles (12 muscle groups)
### ext_shm_genetics (10 genetic traits)
### ext_shm_diseases (active infections)
### ext_shm_disease_spread (tracking spread vectors)
### ext_shm_hygiene (5 hygiene metrics)
### ext_shm_pain (pain tracking)
### ext_shm_combat_skills (10 combat skills)
### ext_shm_negative_traits (10 negative traits)
### ext_shm_healing_log (healing progress tracking)
### ext_shm_ai_decisions (AI thinking outcomes)
