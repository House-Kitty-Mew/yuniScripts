# Auction House Extension System — Design Spec

## 1. Architecture Overview

The AH Extension System allows pluggable modules to hook into the simulation
cycle and real-time events.  Each extension lives in a self-contained directory
under `EXTENSIONS/<name>/` and registers callbacks via a central registry.

### Extension Lifecycle

```
AH startup
  → ah_plugin_registry.load_all()         # discovers EXTENSIONS/*/
    → extension.on_load(config, db)       # init tables, read config
      → registry.register("hook_name", fn)  # register hook callbacks
        → simulation runs, events fire
          → registry.fire("hook_name", **kwargs)
            → extension callbacks execute
```

### Hook Points

| Hook | When fired | Args |
|------|-----------|------|
| `on_simulation_cycle_start` | Before AI prompt is built | `market_summary` |
| `on_simulation_cycle_end` | After AI decisions applied | `cycle_result` |
| `on_listing_created` | Player lists item | `listing` (dict) |
| `on_purchase` | Item is bought | `transaction` (dict) |
| `on_cancel` | Listing is cancelled | `listing_uuid, seller` |
| `on_expiry` | Listing expires | `listing` (dict) |
| `on_listing_queried` | Player searches/listings | `query` (dict) |
| `on_player_balance_change` | Wallet changes | `player, delta, reason` |

## 2. Safeguards & Graceful Degradation

Every extension must:

1. **Wrap all hooks in try/except** — a crash in one extension never blocks
   the main AH or other extensions.
2. **Provide default/fallback values** for all config fields.
3. **Operate independently** — removing the extension folder is safe; the
   registry skips missing directories.
4. **Own its database tables** — extensions create tables prefixed with
   `ext_<name>_` on first load.
5. **Never modify core AH data** — extensions read `auction_listings` and
   `transaction_history` but only write to their own tables.

---

# Simulated People Extension — Full Specification

## 1. Concept

Replace the single-central-AI market simulation with dozens of individual
simulated "people" (personas), each with:

- A **personality profile** (name, archetype, job, region)
- **Personal finances** (balance, income rate, savings goal)
- **Item needs/wants** (what they're looking for, urgency)
- **Memory** (past purchases, price history they've observed)
- **Life circumstances** (random personal events that affect behavior)
- **Region & activity status** (active/inactive, affected by world events)

Every simulation tick, each **active** persona independently evaluates the
AH marketplace and decides whether to buy, sell, or wait — based on their
own personality, finances, and circumstances.

## 2. Database Tables (prefix: `ext_sp_`)

### `ext_sp_profiles` — Static persona definitions

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| persona_uuid | TEXT UNIQUE | UUID |
| name | TEXT | Display name |
| archetype | TEXT | One of 8 archetypes (see §4) |
| job | TEXT | Profession e.g. "miner", "builder" |
| region | TEXT | "overworld", "nether", "end", "deep_dark" |
| wealth_tier | TEXT | "poor", "working", "middle", "wealthy", "elite" |
| personality_traits | TEXT | JSON dict of personality scores |
| active | INTEGER | 1=active, 0=inactive |
| created_at | TEXT | ISO timestamp |
| last_active_at | TEXT | ISO timestamp |

### `ext_sp_finances` — Per-persona wallet & economy

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| persona_uuid | TEXT UNIQUE FK | References profiles |
| balance | REAL | Current coins |
| lifetime_income | REAL | Total coins earned |
| lifetime_spending | REAL | Total coins spent |
| income_per_tick | REAL | Passive income per simulation cycle |
| savings_goal | REAL | Target savings (affects spending) |
| debt | REAL | Current debt (if any) |

### `ext_sp_needs` — Current item desires

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| persona_uuid | TEXT FK | References profiles |
| item_id | TEXT | e.g. "minecraft:diamond_pickaxe" |
| urgency | INTEGER | 1-10 (10 = must have now) |
| max_price | REAL | Max willing to pay |
| desired_quantity | INTEGER | How many |
| quantity_obtained | INTEGER | How many purchased so far |
| reason | TEXT | Why they need it |
| created_at | TEXT | |

### `ext_sp_memory` — Persona memory of market activity

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| persona_uuid | TEXT FK | |
| memory_type | TEXT | "purchase", "observed_price", "missed_deal", "life_event" |
| item_id | TEXT | |
| price | REAL | |
| detail | TEXT | JSON extra context |
| emotional_weight | INTEGER | 1-10 how much this affected them |
| created_at | TEXT | |

### `ext_sp_life_events` — Personal life events

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| persona_uuid | TEXT FK | |
| event_type | TEXT | e.g. "windfall", "expense", "discovery", "accident" |
| description | TEXT | Flavor text |
| financial_impact | REAL | Positive (windfall) or negative (expense) |
| mood_impact | TEXT | "happy", "stressed", "motivated", "cautious" |
| duration_hours | INTEGER | How long the effect lasts |
| started_at | TEXT | |
| ended_at | TEXT | Null = still active |

### `ext_sp_world_events` — Global/regional events that affect personas

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| event_uuid | TEXT UNIQUE | |
| name | TEXT | |
| description | TEXT | |
| region | TEXT | Affected region, or "global" |
| severity | TEXT | "minor", "moderate", "severe" |
| financial_multiplier | REAL | Affects persona income (e.g. 0.5 = recession) |
| started_at | TEXT | |
| ended_at | TEXT | Null = ongoing |

### `ext_sp_transactions` — Persona-specific transaction log

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| persona_uuid | TEXT FK | |
| listing_uuid | TEXT | References auction_listings |
| transaction_type | TEXT | "buy" or "sell" |
| item_id | TEXT | |
| quantity | INTEGER | |
| price | REAL | |
| reason | TEXT | Why they did it |
| created_at | TEXT | |

## 3. Archetypes (8 base personalities)

Each archetype defines default personality traits, job associations, and
spending/saving behaviors:

| Archetype | Traits (JSON) | Typical Jobs | Spending Style | Income |
|-----------|---------------|-------------|----------------|--------|
| **adventurer** | `{"bravery":8,"curiosity":9,"patience":3}` | explorer, dungeon-crawler | Impulsive spender on gear | Medium |
| **merchant** | `{"shrewdness":9,"patience":7,"generosity":3}` | trader, shopkeeper | Bargain hunter, resells | High |
| **builder** | `{"creativity":8,"diligence":7,"moodiness":4}` | architect, redstoner | Bulk buyer of materials | Medium |
| **miner** | `{"endurance":9,"greed":6,"simplicity":3}` | strip-miner, caving | Saves a lot, spends on tools | High |
| **farmer** | `{"patience":8,"nurturing":7,"adventure_urge":2}` | crop/livestock farmer | Steady spender on seeds/tools | Low-Medium |
| **warrior** | `{"aggression":7,"honor":6,"discipline":8}` | guard, pvper | Spends on weapons/armor | Medium |
| **mage** | `{"wisdom":9,"eccentricity":7,"detachment":5}` | enchanter, alchemist | Spends on rare/arcane items | Medium-High |
| **vagabond** | `{"freedom":9,"impulsiveness":8,"resourcefulness":6}` | nomad, treasure-hunter | Spends when desperate, rarely saves | Low |

## 4. Plugin Configuration (`EXTENSIONS/SIMULATED_PEOPLE/config.json`)

```json
{
  "enabled": true,
  "tick_interval_minutes": 60,
  "max_active_personas": 50,
  "persona_pool_size": 200,
  "new_persona_spawn_chance": 0.1,
  "persona_activation_rate": 0.6,
  "persona_deactivation_rate": 0.15,
  "min_balance_for_purchase": 5,
  "memory_retention_days": 90,
  "debug_logging": false
}
```

## 5. World Event ↔ Persona Interaction

World events from the main AH system and the SP extension create a
feedback loop:

1. **AH market event** `"extreme_winter"` → sets `region="overworld"`
   → affects all overworld personas
2. **SP world event** `"mining_boom"` → increases income for miner personas
   → miners have more money → they buy more → market heats up
3. **Personal life event** for a persona: `"found_diamonds"` → windfall
   → they suddenly have money → buy items they've been eyeing

### Priority for persona decisions:

1. **Active life event** → overrides normal behavior
2. **Unfulfilled urgent need** (urgency ≥ 7) → will try to buy now
3. **Observed good price** (from memory) → might buy if price matches
4. **Normal behavior** based on archetype traits

## 6. Player Influence Points

Players can discover and influence personas through:

1. **Listed item matches a persona's need** → 3x more likely to be bought
2. **Competitive pricing** → persona checks their memory; if price is
   below what they remember, higher purchase chance
3. **Advertising** (future: `/ah advertise <item> <persona_type>`) →
   increases visibility

The Minescript client already shows player items alongside simulated ones.
This plugin adds a `§7[needs: ⛏]` badge to listings that match active
persona needs.

## 7. Implementation Plan

1. **`EXTENSIONS/__init__.py`** — Extension registry loader
2. **`EXTENSIONS/SIMULATED_PEOPLE/__init__.py`** — Plugin entry point
3. **`EXTENSIONS/SIMULATED_PEOPLE/config.json`** — Default config
4. **`EXTENSIONS/SIMULATED_PEOPLE/sp_profile.py`** — Persona profile generation
5. **`EXTENSIONS/SIMULATED_PEOPLE/sp_finance.py`** — Financial simulation
6. **`EXTENSIONS/SIMULATED_PEOPLE/sp_behavior.py`** — Decision engine
7. **`EXTENSIONS/SIMULATED_PEOPLE/sp_memory.py`** — Memory system
8. **`EXTENSIONS/SIMULATED_PEOPLE/sp_world_events.py`** — World event integration
9. **`EXTENSIONS/SIMULATED_PEOPLE/sp_database.py`** — DB schema and queries
10. **`ah_plugin_registry.py`** — Central hook registry

## 8. Integration with Existing AH

- `ah_ai_engine.py`: Modified to call `registry.fire("on_simulation_cycle_start")`
  before the AI call and `registry.fire("on_simulation_cycle_end")` after.
- `ah_core.py`: Modified to call `registry.fire("on_listing_created")` after a
  successful list, and `registry.fire("on_purchase")` after a buy.
- `ah_phooks.py`: Modified to call `registry.fire("on_listing_queried")` so
  personas can annotate results.

**If the extension folder is deleted or disabled:**
- The registry skips it silently
- `registry.fire()` is a no-op for unregistered hooks
- Main AH runs exactly as before
- All SP database tables remain but are never queried
