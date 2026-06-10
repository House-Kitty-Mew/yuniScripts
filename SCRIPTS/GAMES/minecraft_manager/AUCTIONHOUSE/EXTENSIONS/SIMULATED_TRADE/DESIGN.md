# Simulated Trade Extension — Full Specification

## Version: 1.0 | Date: 2026-06-06

---

## 1. Overview

The SIMULATED_TRADE extension adds a complete tick-based merchant/trade economy
to the existing Simulated People ecosystem.  Personas can:

- **Trade** directly with other personas within range (currency or barter)
- **Build trade routes** between claimed territories by constructing roads
- **Experience resource depletion events** — rare supply shocks with slow recovery
- **Engage in banditry** — ambush trade routes for loot at severe reputation cost
- **Suffer reputation/law consequences** — notoriety, bounties, guard response

The system integrates with:
- `SIMULATED_PEOPLE` — persona profiles, finances, inventory, movement, claims
- `SIMULATED_SOCIAL` — social interactions between trading partners
- `SIMULATED_RELATIONSHIPS` — relationship changes from trades/banditry
- `SIMULATED_HEALTH_MECHANICS` — injury from banditry combat
- `AH Core` — market pricing, transaction history

---

## 2. Architecture & Tick Flow

All trade actions resolve in a strict order within each simulation tick:

```
PRE-TICK:
  1. Queue all trade/banditry commands from AI decisions
  2. Random resource depletion event check (rare)
  3. Scarcity modifier calculation

ACTION RESOLUTION (ordered):
  a. BANDITRY — ambush resolution (first, so loot can't be used same tick)
  b. BARTER — item-for-item exchanges
  c. TRADE — persona-to-persona currency trades  
  d. ROUTE CONSTRUCTION — road building on claimed territory

ECONOMY UPDATE:
  - Adjust supply/demand per region based on trade flow
  - Apply scarcity modifiers from depletion events
  - Price recalculation on trade goods

LAW ENFORCEMENT:
  - Increase notoriety from banditry
  - Guard patrol checks for wanted personas
  - Bounty posting for crimes
  - Relationship penalties for banditry

EVENT DECAY:
  - Resource regeneration (tick-based, slow)
  - Notoriety decay (1% per tick)
  - Bounty decay (0.1% per tick)
  - Bandit cooldown on routes
```

---

## 3. Database Schema (prefix: `ext_tr_`)

All tables use the `ext_tr_` prefix to namespace from core AH and other extensions.

### 3.1 `ext_tr_trades` — Completed trade records

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| trade_uuid | TEXT UNIQUE | UUID |
| seller_uuid | TEXT FK | Persona selling |
| buyer_uuid | TEXT FK | Persona buying |
| trade_type | TEXT | 'currency' or 'barter' |
| items_offered | TEXT | JSON: {item_id: qty} from seller |
| items_requested | TEXT | JSON: {item_id: qty} from buyer (barter) |
| gold_amount | REAL | Gold exchanged |
| barter_skill_used | REAL | Barter skill level used in resolution |
| location_area | TEXT | Area where trade occurred |
| distance_modifier | REAL | Distance penalty modifier |
| relationship_delta | REAL | Relationship change from fair trade |
| traded_at | TEXT | ISO timestamp |

### 3.2 `ext_tr_routes` — Trade routes / roads

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| route_uuid | TEXT UNIQUE | UUID |
| owner_uuid | TEXT FK | Persona who built it |
| from_claim_uuid | TEXT FK | Source claim |
| to_claim_uuid | TEXT FK | Destination claim |
| road_segments | TEXT | JSON array of coordinate waypoints |
| distance | REAL | Total distance in blocks |
| level | INTEGER | Road quality 1-5 (higher = faster/more efficient) |
| trade_volume | REAL | Accumulated trade value through this route |
| last_maintained | TEXT | ISO date of last maintenance |
| is_active | INTEGER | 1=active, 0=abandoned |
| bandit_activity | REAL | 0.0-1.0 recent bandit activity level |
| built_at | TEXT | ISO timestamp |

### 3.3 `ext_tr_pending_trades` — Active/queued trade offers

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| offer_uuid | TEXT UNIQUE | UUID |
| initiator_uuid | TEXT FK | Persona making offer |
| target_uuid | TEXT FK | Target persona (NULL = open offer) |
| offer_type | TEXT | 'trade' or 'barter' |
| offered_items | TEXT | JSON: {item_id: qty} |
| requested_items | TEXT | JSON: {item_id: qty} |
| gold_requested | REAL | Gold wanted (for trade type) |
| expires_at | TEXT | ISO timestamp |
| status | TEXT | 'open', 'accepted', 'declined', 'expired' |

### 3.4 `ext_tr_banditry` — Banditry incidents

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| incident_uuid | TEXT UNIQUE | UUID |
| attacker_uuid | TEXT FK | Persona attacking |
| target_route_uuid | TEXT FK | Route attacked |
| combat_power | REAL | Attacker's combat power |
| defense_power | REAL | Defender's defense (route guards) |
| outcome | TEXT | 'victory', 'costly_victory', 'defeat', 'captured' |
| loot_value | REAL | Total value of stolen goods |
| items_stolen | TEXT | JSON: {item_id: qty} |
| casualties | TEXT | JSON: casualties on both sides |
| notoriety_gained | INTEGER | Notoriety from this act |
| bounty_placed | REAL | Bounty gold amount |
| occurred_at | TEXT | ISO timestamp |

### 3.5 `ext_tr_resource_state` — Per-region resource tracking

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| region_id | TEXT | Region identifier |
| resource_id | TEXT | Resource type (e.g. 'iron', 'wheat', 'wood') |
| base_abundance | REAL | 0.0-1.0 baseline resource level |
| current_abundance | REAL | 0.0-1.0 current level after depletion/regen |
| depletion_mult | REAL | Price multiplier from scarcity |
| depleted_at | TEXT | When depletion started (NULL if normal) |
| regeneration_start | TEXT | When regen began (NULL if still depleting) |
| regeneration_rate | REAL | Abundance recovered per tick |
| event_active | INTEGER | 1=depletion event active |
| estimated_recovery_ticks | INTEGER | Ticks until full recovery |

### 3.6 `ext_tr_reputation` — Law enforcement state

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| persona_uuid | TEXT UNIQUE FK | Persona |
| notoriety | INTEGER | 0-1000 (hidden from persona) |
| bounty | REAL | Gold bounty on persona |
| reputation_score | INTEGER | -1000 to +1000 |
| arrest_count | INTEGER | Times arrested |
| jail_ticks_remaining | INTEGER | Ticks left in jail |
| is_wanted | INTEGER | 1=actively wanted by guards |
| last_crime_at | TEXT | ISO timestamp |
| disguise_active | INTEGER | 1=using disguise |

### 3.7 `ext_tr_trade_routes_active` — Active caravans on routes

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| caravan_uuid | TEXT UNIQUE | UUID |
| route_uuid | TEXT FK | Route being traveled |
| owner_uuid | TEXT FK | Persona who dispatched |
| cargo_json | TEXT | JSON: {item_id: qty} |
| gold_carried | REAL | Gold being transported |
| guard_count | INTEGER | Number of guards |
| guard_combat_power | REAL | Aggregate guard strength |
| current_segment | INTEGER | Which segment of route |
| total_segments | INTEGER | Total segments in route |
| speed | REAL | Segments per tick |
| status | TEXT | 'traveling', 'ambushed', 'arrived', 'destroyed' |

### 3.8 `ext_tr_world_events` — Active world events

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto |
| event_uuid | TEXT UNIQUE | UUID |
| event_type | TEXT | 'depletion', 'shortage', 'boom', 'disaster' |
| event_name | TEXT | Display name |
| description | TEXT | Flavor text |
| affected_region | TEXT | Region impacted |
| affected_resources | TEXT | JSON array of resource_ids |
| severity | REAL | 0.0-1.0 severity multiplier |
| duration_ticks | INTEGER | How long event lasts |
| ticks_remaining | INTEGER | Ticks until event ends |
| price_multiplier | REAL | Price impact (e.g. 2.5 = 250% prices) |
| started_at | TEXT | ISO timestamp |

---

## 4. Core Systems

### 4.1 Trade Engine (`tr_core.py`)

The trade engine handles persona-to-persona direct exchange.

**Range Check:** Two personas can trade if:
- They are in the same area (same region/zone) OR
- There is an active trade route between their territories
- Distance penalty: beyond 1000 blocks adds 10% per 1000 blocks to prices

**Trade Flow:**
1. Persona A initiates trade with Persona B
2. A places items + gold on offer
3. B reviews and counter-offers or accepts
4. Both must confirm (AI personas auto-decide based on need/value)
5. Transaction executes atomically — all items transfer or none
6. Relationship change: fair trades (+1 to +5), unfair trades (-1 to -10)
7. Hook fires: `on_trade_completed` for social/relationship systems

**AI Decision:** Persona evaluates whether to accept trade based on:
- Item need urgency (from sp_needs table)
- Price fairness (market price comparison)
- Relationship with trader (positive = more likely to accept)
- Barter skill (affects perceived value)

### 4.2 Barter System (`tr_barter.py`)

Barter exchanges items-for-items without gold.

**Valuation:** Each item valued using:
```
value = base_value * (quality/100) * barter_modifier
barter_modifier = 1.0 + (initiator.barter_skill - target.barter_skill) / 200
```

**Tolerance:** Barter accepted if:
```
|value_offered - value_requested| <= tolerance * max(value_offered, value_requested)
```
Default tolerance: 0.15 (15%)

**Skill Influence:** Higher barter skill allows:
- Better valuation of own goods
- Lower tolerance threshold (more demanding)
- Better deals when trading with low-skill personas

### 4.3 Trade Routes (`tr_routes.py`)

Personas with claimed territory can build roads to other claims.

**Construction Requirements:**
- Both claims must exist (from sp_claims)
- Builder must have resources (wood for basic, stone for better)
- Distance affects construction time and cost
- Both claim owners must agree (or route can be one-way if others allow)

**Road Levels:**
| Level | Materials | Trade Efficiency | Speed Boost |
|-------|-----------|-----------------|-------------|
| 1 | Dirt path | 1.0x | 1.0x |
| 2 | Gravel | 1.2x | 1.3x |
| 3 | Stone | 1.5x | 1.6x |
| 4 | Brick | 2.0x | 2.0x |
| 5 | Highway | 3.0x | 3.0x |

**Trade Volume:** Routes accumulate trade volume. High-volume routes:
- Attract more AI persona trade
- Can be upgraded
- Also attract bandits

**Maintenance:** Routes degrade by 1 level every 200 ticks without maintenance.
Maintenance costs resources proportional to level.

### 4.4 Banditry System (`tr_banditry.py`)

Personas can attack trade routes to steal goods.

**Requirements:**
- Attacker must be near the route (within 500 blocks)
- Route must have active traffic (caravan or recent trade)
- Attacker's combat skill >= 20

**Resolution:**
1. **Surprise Check:** Attacker stealth vs route bandit_activity + guard perception
   - Success: 1.3x combat multiplier
   - Failure: normal combat
2. **Combat:** Aggregated combat power comparison
   - Attacker_power = combat_skill * (surprise_mul) * group_size_factor
   - Defender_power = route guard_combat_power * (1 + bandit_activity * 2)
3. **Outcomes:**
   - R > 1.5: Overwhelming victory — minimal losses, all loot taken
   - 0.8 <= R <= 1.5: Costly victory — some damage, partial loot
   - R < 0.8: Defeat — attacker captured or flees
4. **Loot:** Items stolen from active caravans or recent trade records

**Consequences:**
- Notoriety gain: caravan_value / 10
- Bounty: 50% of stolen value per attacker
- Relationship loss: -50 with route owner, -20 with all peaceful personas
- If caught: jail time (proportional to crime severity)
- Guard response increases in region

### 4.5 Reputation & Law (`tr_reputation.py`)

**Reputation Score:** -1000 to +1000
- Positive: better trade prices, NPC trust, easier diplomacy
- Negative: guards hostile at -200, some NPCs refuse trade

**Notoriety:** 0-1000 (hidden)
- Accumulates from crimes
- Decays 1% per tick
- Guards investigate if notoriety > 50
- High notoriety spawns bounty hunters

**Bounty:** Gold reward for capture/kill
- Accumulates from crimes
- Guards actively pursue if bounty > 0
- Decays 0.1% per tick if no new crimes
- Can be cleared by paying 1.5x bounty to guards

**Guard Response:** Per town/region
- Patrol routes near high-bandit areas
- Investigate recent crime scenes
- Attempt arrest of wanted personas (combat check)
- Jail time scales with crime severity

### 4.6 World Events & Resource Depletion (`tr_world_events.py`)

Resource depletion events are rare but impactful world events.

**Event Types:**
| Type | Probability | Duration | Effect |
|------|-------------|----------|--------|
| Resource Depletion | 1% per 10 ticks | 500-2000 ticks | Resource abundance drops to 10-30% |
| Shortage | 0.5% per 10 ticks | 200-1000 ticks | Price multiplier 1.5-3.0x on specific goods |
| Resource Boom | 0.3% per 10 ticks | 300-1500 ticks | Abundance spikes 2-3x, prices crash |
| Natural Disaster | 0.1% per 10 ticks | 500-3000 ticks | All resources in region affected, severe |

**Resource Regeneration:**
- Normal resources regenerate 1% abundance per 10 ticks
- Depleted resources regenerate 0.5% per 10 ticks (slow recovery)
- Full recovery from depletion: ~2000 ticks (base)
- Event-triggered depletion: resources stay low for event duration + recovery

**Scarcity Effects:**
- Higher prices on affected goods
- Personas with needs for affected items become desperate (higher urgency)
- Increased banditry on routes carrying scarce goods
- Trade shifts to unaffected regions
- Crafting costs increase (when integrated with sp_crafting)

### 4.7 Economy Simulation (`tr_economy.py`)

Supply/demand model per region:

```
supply[region][item] = base_supply * abundance_modifier
demand[region][item] = base_demand * event_modifier * population_factor
price_modifier = 1.0 + (demand - supply) * elasticity
```

**Elasticity:**
- Commodities (food, wood): 0.005 — prices sensitive to supply changes
- Manufactured goods (tools, weapons): 0.002
- Luxury goods: 0.001

**Trade Flow Effects:**
- Imports increase local supply, decrease prices
- Exports decrease local supply, increase prices
- Trade routes shift supply between connected regions
- Banditry reduces supply at destination (goods never arrive)

### 4.8 AI Engine (`tr_ai_engine.py`)

Persona AI decisions for trade behavior:

**Decision Tree (per tick):**
1. Check inventory — do I have surplus to sell?
2. Check needs — do I need anything urgently?
3. Evaluate nearby personas — who has what I need?
4. Check relationship — would they trade with me?
5. Evaluate risk — is it safe to trade (bandit danger)?
6. Decide: trade, barter, send caravan, or wait

**Trade Partner Selection:**
- Prefer friends (relationship > 50)
- Prefer nearby (lower distance penalty)
- Prefer those with needed items
- Avoid rivals (relationship < -20) unless desperate

**Pricing AI:**
- Check AH average price for item
- Apply local scarcity modifier
- Add relationship discount/premium
- Decide acceptable price range

---

## 5. Hook Integration

The extension registers hooks in `ah_plugin_registry.py`:

| Hook | Handler | Purpose |
|------|---------|---------|
| `on_simulation_cycle_start` | `tr_ai_engine.before_tick()` | Stage AI decisions |
| `on_simulation_cycle_end` | `tr_ai_engine.after_tick()` | Apply decisions |
| `on_persona_activated` | `tr_reputation.init_persona()` | Initialize reputation |
| `on_social_interaction` | `tr_core.on_social_trade()` | Process trade from social context |
| `on_relationship_change` | `tr_core.adjust_trade_terms()` | Update trade willingness |
| `on_world_event` | `tr_world_events.on_world_event()` | React to world events |
| `on_persona_purchase` | `tr_economy.on_purchase()` | Update supply/demand |

---

## 6. Configuration (`config.json`)

```json
{
  "trade": {
    "max_trade_distance": 1000,
    "distance_penalty_per_1000": 0.1,
    "barter_tolerance_default": 0.15,
    "barter_skill_impact": 0.005,
    "max_pending_offers_per_persona": 5,
    "offer_expiry_ticks": 100,
    "trade_cooldown_ticks": 5,
    "max_trade_range_personas": 10
  },
  "routes": {
    "construction_cost_wood_base": 64,
    "construction_cost_stone_base": 32,
    "maintenance_interval_ticks": 200,
    "degredation_per_skip": 1,
    "max_route_level": 5,
    "bandit_attraction_per_volume": 0.001,
    "trade_efficiency_per_level": 0.3
  },
  "banditry": {
    "min_combat_skill": 20,
    "range_to_route": 500,
    "surprise_multiplier": 1.3,
    "notoriety_per_value_ratio": 0.1,
    "bounty_ratio": 0.5,
    "capture_jail_base_ticks": 50,
    "guard_response_delay_ticks": 3,
    "max_group_size": 10,
    "loot_destruction_chance": 0.1
  },
  "reputation": {
    "reputation_min": -1000,
    "reputation_max": 1000,
    "notoriety_max": 1000,
    "notoriety_decay_rate": 0.01,
    "bounty_decay_rate": 0.001,
    "guard_notoriety_threshold": 50,
    "guard_hostile_reputation": -200,
    "bounty_clear_multiplier": 1.5,
    "jail_ticks_per_notoriety": 0.5
  },
  "world_events": {
    "check_interval_ticks": 10,
    "depletion_chance": 0.01,
    "shortage_chance": 0.005,
    "boom_chance": 0.003,
    "disaster_chance": 0.001,
    "regen_rate_normal": 0.001,
    "regen_rate_depleted": 0.0005,
    "max_active_events": 3,
    "event_cooldown_ticks": 500
  },
  "economy": {
    "elasticity_commodity": 0.005,
    "elasticity_manufactured": 0.002,
    "elasticity_luxury": 0.001,
    "price_min_multiplier": 0.5,
    "price_max_multiplier": 3.0,
    "supply_regen_per_tick": 0.01,
    "demand_shift_per_trade": 0.02,
    "population_demand_factor": 0.1
  }
}
```

---

## 7. State Registry Usage

The extension reads/writes the shared state registry:

**Writes:**
- `active_trade_offers` — list of open trade offers
- `trade_volume_by_route` — accumulated volume per route
- `active_bandit_incidents` — ongoing banditry
- `resource_availability` — current resource levels per region
- `active_world_events` — current events
- `guard_patrol_routes` — guard positions
- `wanted_personas` — personas with bounties

**Reads:**
- `active_personas` — from SIMULATED_PEOPLE
- `persona_locations` — from SIMULATED_PEOPLE (sp_movement)
- `persona_claims` — from SIMULATED_PEOPLE (sp_claims)
- `persona_inventories` — from SIMULATED_PEOPLE
- `persona_relationships` — from SIMULATED_RELATIONSHIPS
- `active_social_sessions` — from SIMULATED_SOCIAL

---

## 8. Testing Strategy

### Unit Tests
- Trade execution: successful trade, insufficient items, range check
- Barter valuation: correct calculation, tolerance check
- Route construction: valid/invalid claims, resource costs
- Banditry combat: all outcome scenarios
- Reputation: decay rates, threshold checks
- World events: probability triggers, duration management
- Economy: supply/demand shifts, price calculations

### Edge Case Tests
- Trade with self (blocked)
- Barter with zero-value items
- Empty inventory trade attempt
- Route construction on unclaimed land
- Banditry on non-existent route
- Reputation overflow/underflow
- Multiple simultaneous events on same region
- Trade during depletion event
- Persona in jail trying to trade

### Data Flow Tests
- End-to-end: persona creates offer → trades → inventory updates
- Route construction → trade using route → volume tracking
- Banditry → loot acquisition → reputation impact
- Depletion event → price changes → persona behavior shifts
- Barter chain: items change hands multiple times

### Integration Tests (3 Rounds)

**Round 1 — Core + SIMULATED_PEOPLE Integration:**
- Verify trade interacts with persona inventory correctly
- Verify route construction consumes persona resources
- Verify banditry affects persona notoriety

**Round 2 — Cross-Extension Integration:**
- Trade triggers social interaction hooks
- Relationship changes propagate back to trade willingness
- World events affect persona needs urgency
- Health mechanics integrate with banditry injury

**Round 3 — Full System Stress:**
- 50+ personas trading simultaneously
- Multiple concurrent bandit incidents
- Stacking world events
- Long-running simulation (1000+ ticks)
- Data integrity verification after all operations

---

## 9. Security & Safeguards

- All hooks wrapped in try/except — extension failure never blocks core AH
- Transaction atomicity — partial transfers impossible
- Range validation — persona must be in range to trade
- Cooldown enforcement — prevents trade spam
- Resource verification — cannot spend resources persona doesn't have
- Rate limiting — max N trades per persona per tick
- Banditry failure penalty — failed banditry still increases notoriety
- Jail prevents all trade actions
- Bounty clear requires payment (gold sink)
- Configurable limits on all parameters

---

## 10. File Structure

```
EXTENSIONS/SIMULATED_TRADE/
├── __init__.py          — Extension loader, hook registration
├── DESIGN.md            — This document
├── config.json          — Default configuration
├── tr_config.py         — Config loader and defaults
├── tr_database.py       — All DB schemas and queries (ext_tr_ prefix)
├── tr_core.py           — Core trade engine
├── tr_barter.py         — Barter system
├── tr_routes.py         — Trade routes / road construction
├── tr_banditry.py       — Banditry system
├── tr_reputation.py     — Reputation & law enforcement
├── tr_world_events.py   — Resource depletion & world events
├── tr_economy.py        — Supply/demand economy simulation
├── tr_ai_engine.py      — AI trade behavior decisions
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_tr_core.py
│   ├── test_tr_barter.py
│   ├── test_tr_routes.py
│   ├── test_tr_banditry.py
│   ├── test_tr_reputation.py
│   ├── test_tr_world_events.py
│   ├── test_tr_economy.py
│   ├── test_tr_edge_cases.py
│   ├── test_tr_data_flow.py
│   └── test_tr_integration_full.py
```
