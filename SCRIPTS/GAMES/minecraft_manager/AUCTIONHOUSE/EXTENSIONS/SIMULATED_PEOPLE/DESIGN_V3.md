# Simulated People v3 — Health & Physiology System

## 1. Design Philosophy

Personas are not abstract economic agents — they are **living bodies** with
physiological needs that must be met or they decay, sicken, and die.

The health system drives everything:
- A hungry persona **must** spend money on food (not gear)
- A dehydrated persona in the Nether **must** move to a safer area
- A wounded persona with low immunity **will** die from a simple scratch
- Death removes personas from the pool (they become inactive permanently)
- New personas spawn to replace them (the world keeps turning)

This makes the economy feel alive — prices on food spike during shortages,
weapon prices spike during wars, and players who list basic survival items
(dirt for shelter, food, water) find steady buyers.

---

## 2. The 7 Vital Stats

Each stat is tracked 0–100 for every persona.  The *decay rate* is how fast
it drops per tick if neglected.  The *danger threshold* is where visible
decay starts.

### 2.1 Food (Calories)

| Property | Value |
|----------|-------|
| Starting value | 70–95 (based on wealth) |
| Decay per tick | 1–3 (higher if active/cold) |
| Danger threshold | < 30 |
| Critical threshold | < 10 |
| Death if at 0 for | 3 consecutive ticks |

**Decay effects:**
- < 50: Mood drops, less likely to buy non-food items
- < 30: Energy drops, moves slower, less productive
- < 10: Muscle wasting begins, immune system weakens
- 0 + 3 ticks: Personality starves to death

### 2.2 Hydration (Water)

| Property | Value |
|----------|-------|
| Starting value | 70–95 |
| Decay per tick | 2–5 (higher in hot biomes like Nether/Desert) |
| Danger threshold | < 25 |
| Critical | < 10 |
| Death at 0 after | 2 consecutive ticks |

**Decay effects:**
- < 50: Thirst, reduced decision-making
- < 25: Confusion, sunken appearance, kidneys struggling
- < 10: Delirium, organ failure imminent
- 0 + 2 ticks: Death from dehydration

### 2.3 Energy (Sleep/Rest)

| Property | Value |
|----------|-------|
| Starting value | 60–90 |
| Decay per tick | 2–3 (higher during combat/movement) |
| Danger threshold | < 20 |
| Critical | < 5 |
| Death at 0 after | 5 consecutive ticks |

**Decay effects:**
- < 40: Microsleeps, cognitive fog
- < 20: Hallucinations, immune system plummets
- < 5: Multi-system failure beginning
- 0 + 5 ticks: Death from exhaustion

### 2.4 Temperature (Thermoregulation)

| Property | Value |
|----------|-------|
| Starting value | 100 (always starts normal) |
| Decay varies | Based on area biome |
| Danger threshold | < 40 (hypothermia) or > 80 (hyperthermia) |
| Critical | < 20 or > 95 |
| Death at extremes | 1–3 ticks depending on severity |

**Biome effects (per tick):**
- Plains/Forest: ±0 (neutral)
- Desert: +5 (heat)
- Tundra/Snow: −5 (cold)
- Nether Wastes: +8 (extreme heat)
- Crimson Forest: +4 (warm)
- End Islands: −3 (cold)
- Deep Dark: −2 (cool)
- Ocean: −1 (cooling)
- Swamp: +1 (warm, humid)

**With shelter/clothing:** Reduce biome effect by 50–80%.

### 2.5 Waste (Elimination)

| Property | Value |
|----------|-------|
| Starting value | 0 (0 = empty, 100 = critical) |
| Accumulation per tick | 5–15 |
| Danger threshold | > 70 |
| Critical | > 90 |
| Death if maxed for | 2 ticks |

**Decay effects:**
- > 50: Discomfort, reduced mood
- > 70: Toxic buildup, skin sallowness, headache
- > 90: Perforation risk, sepsis imminent
- 100 + 2 ticks: Death from toxic shock

### 2.6 Hygiene (Barrier Integrity)

| Property | Value |
|----------|-------|
| Starting value | 60–90 |
| Decay per tick | 1–2 (higher in swamps, nether) |
| Danger threshold | < 30 |
| Critical | < 10 |
| Death | Linked to immune system + injuries |

**Decay effects:**
- < 50: Visible dirt, lower social standing
- < 30: Small wounds become infected, risk of sepsis
- < 10: Widespread infection, necrosis risk
- 0: Every minor scratch is a potential death sentence

### 2.7 Immune System

| Property | Value |
|----------|-------|
| Starting value | 50–90 (based on wealth/archetype) |
| Decay per tick | 0.5–2 (increases when other stats are low) |
| Danger threshold | < 25 |
| Critical | < 10 |
| Death | Triggers when immune = 0 and any wound exists |

**Synergistic decay:** Immune drops faster when:
- Food < 30: +1 extra decay
- Hydration < 25: +2 extra decay  
- Energy < 20: +3 extra decay
- Hygiene < 30: +1 extra decay
- Waste > 70: +1 extra decay
- Temperature outside safe range: +2 extra decay

---

## 3. Decay Interaction Map

```
                    ┌──────────┐
                    │  Hunger  │──► Muscle wasting, immune drop
                    └──────────┘
                         │
                         ▼
                    ┌──────────┐
              ┌─────│ Dehydration│──► Blood thickens, kidneys fail
              │     └──────────┘
              │          │
              │          ▼
              │     ┌──────────┐
              │     │Exhaustion│──► Hallucinations, immune crash
              │     └──────────┘
              │          │
    ┌─────────┴──────────▼──────────┐
    │       IMMUNE SYSTEM           │
    │  (acts as the "fail-deadly")   │
    └─────────┬──────────▲──────────┘
              │          │
              ▼          │
     ┌────────────┐      │
     │  Infection  │──────┘
     │  / Sepsis   │
     └────────────┘
```

When any combo of 3+ stats are in danger, the persona enters **critical
state** and will prioritize survival over all other decisions.

---

## 4. Persona Decision Influence

### 4.1 Survival Priorities (in order)
When any stat is in danger, the persona's behavior changes:

1. **Temperature critical** → Move to safer biome immediately
2. **Waste critical** → Stop, eliminate (effect costs time/money)
3. **Hydration critical** → Buy water/food, move to wet biome
4. **Food critical** → Buy food, stop all non-essential spending
5. **Energy critical** → Rest (skip 1-2 ticks of activity)
6. **Hygiene critical** → Clean up (costs money for supplies)
7. **Immune critical** → Rest, buy medicine/food, avoid all conflict

### 4.2 Economic Effects
- Hungry personas won't buy luxury items — only food
- Dehydrated personas in the Nether will migrate out
- Sick personas spend savings on recovery, not investments
- Dead personas are removed from the market entirely
- Survivors who recover remember their hardship (memory)

### 4.3 Death & Replacement
- Death occurs when a combined score calculation hits zero
- Formula: `survival_score = (food + hydration + energy + temperature + waste + hygiene + immune) / 7`
- When survival_score < 5 AND any single stat is at 0: **death**
- Dead personas: `active = 0`, `alive = 0`, `cause_of_death = "starvation"`
- A new persona spawns to replace them within 1-3 ticks
- The world population stays stable

---

## 5. Database Schema

### `ext_sp_health`

| Column | Type | Range | Default | Description |
|--------|------|-------|---------|-------------|
| persona_uuid | TEXT FK | — | — | References ext_sp_profiles |
| food | INTEGER | 0–100 | 80 | Calorie/nutrient level |
| hydration | INTEGER | 0–100 | 80 | Water level |
| energy | INTEGER | 0–100 | 75 | Sleep/rest level |
| temperature | INTEGER | 0–100 | 50 | Core temp (50=normal) |
| waste | INTEGER | 0–100 | 0 | Waste buildup (0=empty) |
| hygiene | INTEGER | 0–100 | 70 | Cleanliness/wound care |
| immune | INTEGER | 0–100 | 70 | Immune system strength |
| alive | INTEGER | 0/1 | 1 | 1=alive, 0=dead |
| cause_of_death | TEXT | — | NULL | Why they died |
| decay_timer | INTEGER | 0–10 | 0 | Ticks since crossed critical |

---

## 6. Implementation

### Files
| File | Purpose |
|------|---------|
| `EXTENSIONS/SIMULATED_PEOPLE/sp_health.py` | Health engine (decay, death, recovery) |
| `EXTENSIONS/SIMULATED_PEOPLE/sp_database.py` | Added `ext_sp_health` table |
| `EXTENSIONS/SIMULATED_PEOPLE/sp_profile.py` | Init health on persona creation |
| `EXTENSIONS/SIMULATED_PEOPLE/sp_behavior.py` | Health-seeking behavior prios |
| `EXTENSIONS/SIMULATED_PEOPLE/__init__.py` | Process health in tick loop |

### Health Processing Per Tick
```
for each active persona:
    1. Apply biome environmental effects to temperature
    2. Apply base decay to food, hydration, energy, waste, hygiene
    3. Calculate synergistic immune decay from other low stats
    4. Check each stat against danger thresholds
    5. If any stat at 0 → increment decay_timer
    6. If decay_timer > death_threshold → mark persona as dead
    7. If alive and conscious → continue normal behavior loop
```

### Death Consequences
- `active = 0`, `alive = 0`, `cause_of_death = stored`
- All needs/territories/items associated with persona remain frozen
- A new persona will spawn within 1-3 ticks to keep population stable
- Market sees a slight dip in that persona type's economic activity
