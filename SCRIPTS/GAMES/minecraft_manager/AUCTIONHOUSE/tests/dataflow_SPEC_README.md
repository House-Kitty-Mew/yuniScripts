# 🧪 Auction House Comprehensive Data Flow Test System

## Overview

This document is the full specification for a **comprehensive data flow test system** covering every possible data path through the Auction House system and its world simulation extensions. The test system is designed to catch regressions, race conditions, data corruption, and integration failures before they reach production.

## Architecture

The Auction House has these major subsystems whose data flows must be traced:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     DATA FLOW ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  PLAYER (via Phooks) → ah_phooks → ah_core → ah_database           │
│       ↓                       ↓              ↓                      │
│  VALIDATION                ATOMIC OPS     SCHEMA (7 tables)         │
│       ↓                       ↓              ↓                      │
│  player_name regex      UPDATE WHERE     auction_listings           │
│  uuid format check      status='active'  transaction_history        │
│  item_id validation     rowcount check   price_history              │
│  price range check      rollback on 0    market_events              │
│       ↓                       ↓          simulated_inventory        │
│  RCON clear/give            FIRE HOOKS   ai_notes                   │
│  → response to player       → EXTENSIONS player_balances            │
│                                                                     │
│  AI ENGINE (timer) → gather context → DeepSeek API → parse → apply  │
│       ↓                       ↓              ↓              ↓       │
│  MarketSummary            PRICE_ADJUST   STOCK_CHANGE   RARE_ITEMS  │
│  ActiveListings           EVENT_START    STALE_FIX      NOTES       │
│  PriceHistory             ANNOUNCEMENT   ANNOUNCE                   │
│  AI Notes                                                           │
│       ↓                       ↓              ↓              ↓       │
│  ah_price_history        ah_market_events      ah_item_gen          │
│  ah_helper_db                                                    │
│                                                                     │
│  WORLD SUBSYSTEMS (Phase 2C ordering):                              │
│  1. SIMULATED_PEOPLE  → state_registry (personas, needs, finances)  │
│  2. SIMULATED_SOCIAL  → reads state, writes interactions            │
│  3. SIMULATED_RELS    → reads interactions, writes relationships    │
│  4. SIMULATED_CHAT    → reads relationships, writes messages        │
│  5. SIMULATED_ANNOUNCE→ reads everything, writes announcements      │
│  6. SIMULATED_TRADE   → reads/writes trade routes, banditry         │
│  7. SIMULATED_HEALTH  → per-persona health tick (blood, disease...) │
│                                                                     │
│  ECONOMY BRIDGE (ECO_BRIDGE) → balance check → deduct → credit      │
│       ↓                       ↓              ↓              ↓       │
│  OttersCiv DB             BRIDGE.found?   BRIDGE.deduct  RCON fallbk│
│  → fallback: player_balances table                                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Test Framework Components

### 1. Data Flow Probe (`probes/trace_probe.py`)

A decorator-based system that wraps every hook point and traces the data path:

```python
class DataFlowTrace:
    """Records every data flow step with timestamps for verification."""
    
    trace: list[FlowStep] = []
    
    def record(self, path: str, action: str, metadata: dict):
        """Record a flow step."""
        self.trace.append(FlowStep(
            path=path, action=action, metadata=metadata,
            timestamp=time.time(), thread_id=threading.get_ident()
        ))
    
    def verify_path_taken(self, expected_paths: list[str]):
        """Assert that specific paths were taken in the execution."""
    
    def verify_path_not_taken(self, forbidden_paths: list[str]):
        """Assert that certain safety paths were NOT triggered."""
```

**All hook points instrumented:**
- `on_simulation_cycle_start` / `on_simulation_cycle_end`
- `on_listing_created` / `on_purchase` / `on_cancel` / `on_expiry`
- `on_persona_activated` / `on_persona_deactivated`
- `on_social_interaction` / `on_relationship_change`
- `on_chat_message` / `on_announcement` / `on_world_event`
- `on_persona_purchase`
- Economy bridge check/deduct/credit

### 2. Database Probe (`probes/db_probe.py`)

Wraps the database connection to trace every SQL operation:

```python
class DatabaseProbe:
    """Wraps get_db() to log and verify all SQL operations."""
    
    queries: list[SQLTrace] = []
    
    def assert_table_state(self, table: str, expected_rows: int):
        """Verify a table has the expected number of rows after a flow."""
    
    def assert_no_orphaned_rows(self, parent_table: str, child_table: str, fk_column: str):
        """Verify FK integrity - no child rows without a parent."""
    
    def assert_atomic_update_succeeded(self, listing_uuid: str):
        """Verify an atomic UPDATE affected exactly 1 row."""
```

### 3. State Probe (`probes/state_probe.py`)

Monitors the shared state_registry to verify extension communication:

```python
class StateProbe:
    """Traces all writes/reads to the shared extension state."""
    
    writes: dict[str, list] = {}
    reads: dict[str, list] = {}
    
    def assert_phase2c_ordering(self):
        """Assert that extensions wrote/read in the correct Phase 2C order."""
    
    def assert_no_state_leakage(self, extension: str):
        """Assert an extension didn't read state that hadn't been written yet."""
```

### 4. Data Flow Path Constants

Defined as string constants for path tracing:

```python
# Core AH paths
PATH_LISTING = "core.listing"
PATH_BIDDING = "core.bidding" 
PATH_BUYNOW = "core.buynow"
PATH_CANCEL = "core.cancel"
PATH_EXPIRY = "core.expiry"
PATH_QUERY = "core.query"

# AI Engine paths
PATH_AI_CONTEXT = "ai.context_gathering"
PATH_AI_API_CALL = "ai.api_call"
PATH_AI_PARSE = "ai.parse_response"
PATH_AI_PRICE_ADJUST = "ai.price_adjustment"
PATH_AI_STOCK_ADJUST = "ai.stock_adjustment"
PATH_AI_EVENT = "ai.event_trigger"
PATH_AI_RARE_ITEM = "ai.rare_item_gen"
PATH_AI_STALE_FIX = "ai.stale_recommendation"
PATH_AI_SNAPSHOT = "ai.price_snapshot"
PATH_AI_NOTES = "ai.notes_save"

# Extension paths
PATH_PEOPLE_BEHAVIOR = "people.behavior_tick"
PATH_PEOPLE_FINANCE = "people.finance_tick"
PATH_PEOPLE_NEEDS = "people.needs_check"
PATH_PEOPLE_PURCHASE = "people.purchase"
PATH_SOCIAL_BOREDOM = "social.boredom_tick"
PATH_SOCIAL_EXHAUSTION = "social.exhaustion_tick"
PATH_SOCIAL_ACTIVITY = "social.activity_planning"
PATH_SOCIAL_CRISIS = "social.crisis_mode"
PATH_RELS_CONTENTION = "rels.cell_contention"
PATH_RELS_SKILL_DECAY = "rels.skill_decay"
PATH_RELS_MARRIAGE = "rels.marriage_check"
PATH_CHAT_MESSAGE = "chat.message_processing"
PATH_CHAT_AI_RESPONSE = "chat.ai_response_gen"
PATH_CHAT_INTEREST = "chat.interest_tracking"
PATH_ANNOUNCE_FILTER = "announce.ai_filter"
PATH_ANNOUNCE_DELIVERY = "announce.delivery"
PATH_TRADE_ROUTES = "trade.route_processing"
PATH_TRADE_BANDITRY = "trade.banditry"
PATH_TRADE_WORLD_EVENT = "trade.world_event_impact"
PATH_HEALTH_BLOOD = "health.blood_tick"
PATH_HEALTH_MUSCLE = "health.muscle_tick"
PATH_HEALTH_DISEASE = "health.disease_tick"
PATH_HEALTH_IMMUNE = "health.immune_tick"
PATH_HEALTH_HYGIENE = "health.hygiene_tick"
PATH_HEALTH_PAIN = "health.pain_tick"
PATH_HEALTH_COMBAT = "health.combat_skill_tick"

# Economy paths
PATH_ECO_BRIDGE_CHECK = "economy.bridge_check"
PATH_ECO_BRIDGE_DEDUCT = "economy.bridge_deduct"
PATH_ECO_BRIDGE_CREDIT = "economy.bridge_credit"
PATH_ECO_FALLBACK = "economy.fallback_table"
PATH_ECO_RCON_FALLBACK = "economy.rcon_fallback"

# Registry paths
PATH_STATE_REGISTRY_WRITE = "state_registry.write"
PATH_STATE_REGISTRY_READ = "state_registry.read"
PATH_STATE_REGISTRY_CLEAR = "state_registry.clear"
```

---

## Detailed Data Flow Tests

### FLOW 1: Player Listing (`tests/dataflow/flows/test_flow_listing.py`)

**Path:** Player → `ah_phooks.handle_ah_list()` → validation → `ah_core.list_item()` → DB insert → RCON clear → `ah_list_response`

**State Transitions:**
```
Input: {player_name, item_id, count, start_price, buy_now_price, duration_hours}
  → Validate player_name (regex ^[A-Za-z0-9_]{2,32}$)
  → Validate item_id (regex ^[a-z0-9_.\-]+:[a-z0-9_.\-]+$)
  → Validate UUID formats
  → CHECK: is_player_banned(seller) == False
  → CHECK: seller listing count < max_listings_per_player
  → CHECK: start_price > 0
  → CHECK: buy_now_price (if given) > start_price
  → Atomic: INSERT INTO auction_listings
  → Fire hook: on_listing_created
  → RCON: clear seller's item
  → Emit: ah_list_response {status: "ok", listing_uuid}
```

**Test Cases:**

| # | Test | Input | Expected |
|---|------|-------|----------|
| 1.1 | Basic listing | `seller="Alice", item="minecraft:diamond", count=3, start=10.0` | `ok=True`, listing in DB, RCON clear called |
| 1.2 | Listing with buy-now | `buy_now_price=50.0` | buy_now_price stored correctly |
| 1.3 | Listing with NBT data | `item_nbt='{"components":{"enchantments":{"sharpness":5}}}'` | NBT preserved in DB |
| 1.4 | Listing with signed name | `signed_name="§6Blade of Heroes"` | signed_name preserved |
| 1.5 | Listing with rarity | `rarity="Legendary"` | rarity stored |
| 1.6 | Listing with cert_hash | `cert_hash="abc123"` | cert_hash stored |
| 1.7 | Simulated listing | `is_simulated=True, sim_lore="...", sim_enchantments="...", sim_durability=100, sim_quality_roll=85` | sim fields stored, no RCON clear |
| 1.8 | Banned player listing | `seller="BannedSteve"` | `ok=False`, error contains "banned" |
| 1.9 | Max listings exceeded | Create 6 listings for same player (max=5) | 6th returns `ok=False` |
| 1.10 | Zero start price | `start_price=0` | `ok=False` |
| 1.11 | Negative start price | `start_price=-5.0` | `ok=False` |
| 1.12 | Buy-now < start price | `start_price=10.0, buy_now_price=5.0` | `ok=False` |
| 1.13 | Invalid player name | `player_name="x"` (too short) | `ok=False` |
| 1.14 | Invalid item ID | `item_id="not_a_valid_id"` | `ok=False` |
| 1.15 | NULL item_nbt | `item_nbt=None` | Listing succeeds, nbt=NULL in DB |
| 1.16 | Special chars in player name | `player_name="Test_Player-123"` | `ok=False` (hyphen not allowed) |
| 1.17 | Unicode in signed name | `signed_name="★Sword of Power★"` | Preserved in DB |
| 1.18 | Very long duration | `duration_hours=1000` (max=168) | Clamped to max 168h |
| 1.19 | RCON failure | `rcon_func` raises exception | Listing still succeeds (non-fatal) |
| 1.20 | Concurrent duplicate | Same listing inserted twice concurrently | Both succeed (different UUIDs) |
| 1.21 | Very large count | `count=9999999` | Stored and works |
| 1.22 | Listing during market event | Event with price multiplier active | Listing unaffected (events affect sim only) |
| 1.23 | Economy bridge listing fee | Eco bridge says balance too low | `ok=False`, error about insufficient funds |
| 1.24 | Hook firing | `on_listing_created` fires | All registered extensions called |
| 1.25 | Hook failure isolation | One extension hook throws | Other hooks still fire, listing succeeds |

### FLOW 2: Bidding (`tests/dataflow/flows/test_flow_bidding.py`)

**Path:** Player → `ah_phooks.handle_ah_bid()` → validation → `ah_core.place_bid()` → atomic UPDATE → record transaction → outbid notification → response

**State Transitions:**
```
Input: {player_name, listing_uuid, bid_amount}
  → Validate all inputs
  → CHECK: player not banned
  → CHECK: listing exists and status='active'
  → CHECK: bid_amount > current_bid (or start_price if first bid)
  → CHECK: bid_amount >= current_bid * (1 + min_bid_increment_pct/100)
  → ATOMIC: UPDATE auction_listings SET current_bid=?, highest_bidder=?, bids_count++
    WHERE listing_uuid=? AND status='active' AND current_bid < ?
  → IF rowcount == 0: return TOCTOU error
  → Record transaction in transaction_history
  → IF previous_bidder exists and != bidder: notify outbid
  → Fire hook: on_listing_queried
  → Emit: ah_bid_response
```

**Test Cases:**

| # | Test | Description | Expected |
|---|------|-------------|----------|
| 2.1 | First bid on listing | `bid_amount=15.0` on listing at 10.0 | ok, new current_bid=15.0 |
| 2.2 | Outbid | Charlie bids 20.0 after Bob's 15.0 | ok, previous_bidder=Bob |
| 2.3 | Bid too low | `bid=18.0` when current=20.0 | ok=False, error=bid too low |
| 2.4 | Bid exactly at minimum increment | current=10.0, min_increment=10%, bid=11.0 | ok (11.0 >= 11.0) |
| 2.5 | Bid just below minimum increment | current=10.0, min_increment=10%, bid=10.99 | ok=False |
| 2.6 | Self-bid (player bids on own listing) | Alice bids on Alice's listing | ok=False |
| 2.7 | Banned player bids | `bidder="BannedSteve"` | ok=False |
| 2.8 | Bid on non-existent listing | random UUID | ok=False |
| 2.9 | Bid on expired listing | Set status='sold', then bid | ok=False |
| 2.10 | Bid on cancelled listing | Set status='cancelled', then bid | ok=False |
| 2.11 | Negative bid | `bid_amount=-5.0` | ok=False |
| 2.12 | Zero bid | `bid_amount=0` | ok=False |
| 2.13 | NaN bid | `bid_amount=float('nan')` | ok=False |
| 2.14 | Infinity bid | `bid_amount=float('inf')` | ok=False or clamped |
| 2.15 | Extremely large bid | `bid_amount=1e100` | ok=False or safety cap |
| 2.16 | Concurrent bids on same listing | 3 threads bidding simultaneously | Exactly 1 succeeds, 2 get TOCTOU errors |
| 2.17 | Outbid notification | Bob is outbid by Charlie | Notification sent to Bob |
| 2.18 | Bid history recorded | Multiple bids on same listing | get_bid_history returns all in order |
| 2.19 | Bid after listing cancelled mid-process | Cancel while bid being processed | Atomic check prevents bid on cancelled |
| 2.20 | Listing fee deduction with bridge | Eco bridge deducts listing fee | Balance checked and deducted |
| 2.21 | Hook firing on bid | `on_listing_queried` fires | Extensions notified |
| 2.22 | Buyer seller notification | Bid placed → seller notified | tellraw sent to seller |
| 2.23 | Price exceeds balance check | Bridge says balance too low | ok=False |

### FLOW 3: Buy Now (`tests/dataflow/flows/test_flow_buynow.py`)

**Path:** Player → `ah_phooks.handle_ah_buy()` → validation → `ah_core.buy_now()` → atomic UPDATE → balance deduct → DB update → RCON give → response

**State Transitions:**
```
Input: {player_name, listing_uuid, quantity}
  → Validate all inputs
  → CHECK: player not banned
  → CHECK: listing exists, status='active', buy_now_price IS NOT NULL
  → CHECK: buyer != seller
  → ATOMIC: UPDATE auction_listings SET status='sold', sold_at=now, 
    sold_price=buy_now_price, highest_bidder=buyer
    WHERE listing_uuid=? AND status='active'
  → IF rowcount == 0: TOCTOU error (someone else bought it)
  → Check/update balance (bridge or table)
  → Calculate fees
  → Record transaction
  → RCON: give item to buyer
  → Fire hook: on_purchase
  → Emit: ah_buy_response
```

**Test Cases:**

| # | Test | Expected |
|---|------|----------|
| 3.1 | Basic buy-now | ok, status='sold', sold_price=buy_now_price |
| 3.2 | Buyer is seller | ok=False |
| 3.3 | Banned player buy | ok=False |
| 3.4 | Buy non-existent listing | ok=False |
| 3.5 | Buy listing without buy-now price | ok=False (no BIN price set) |
| 3.6 | Buy already-sold listing | ok=False (atomic check) |
| 3.7 | Buy own item (if self-bid logic differs) | ok=False |
| 3.8 | Concurrent buy-now (2 players buy same) | 1 succeeds, 1 gets TOCTOU |
| 3.9 | Buy-now while bid exists | Overrides bids, goes to buyer |
| 3.10 | Buy with quantity | Single-quantity only (usually) |
| 3.11 | RCON give fails | Still marks as sold, error logged |
| 3.12 | Fee calculation | sale_fee correctly calculated |
| 3.13 | Seller payout | seller receives (price - fees) |
| 3.14 | Transaction history | tx record has correct type, prices, participants |
| 3.15 | Hook firing on purchase | on_purchase fires with listing/buyer data |
| 3.16 | Economy bridge deduct | balance deducted from buyer |

### FLOW 4: Cancel (`tests/dataflow/flows/test_flow_cancel.py`)

**Path:** Player → `ah_phooks.handle_ah_remove()` → validation → `ah_core.cancel_listing()` → fee check → DB update → RCON return → response

**Test Cases:**

| # | Test | Expected |
|---|------|----------|
| 4.1 | Cancel with no bids | status='cancelled', no fee |
| 4.2 | Cancel with bids | status='cancelled', cancel fee deducted |
| 4.3 | Non-owner tries to cancel | ok=False |
| 4.4 | Cancel already-sold listing | ok=False |
| 4.5 | Cancel already-cancelled listing | ok=False |
| 4.6 | Concurrent cancel vs buy | Only one succeeds |
| 4.7 | RCON return on cancel | Item returned to seller |
| 4.8 | Cancel simulated listing | ok=False (can't cancel sim items) |
| 4.9 | Fee calculation with bids | cancel_after_bids_fee applied |
| 4.10 | Transaction history for cancel | tx record created |

### FLOW 5: Expiry (`tests/dataflow/flows/test_flow_expiry.py`)

**Path:** Timer → `ah_core.expire_listings()` → scan expired → sold/unsold → DB update → RCON → hooks

**Test Cases:**

| # | Test | Expected |
|---|------|----------|
| 5.1 | Expire with bids → sold to highest | status='sold', winner gets item |
| 5.2 | Expire without bids → expired | status='expired', returned to seller |
| 5.3 | Expire before expiry time | No listings affected |
| 5.4 | Concurrent expire + bid processing | Atomic, no double-sale |
| 5.5 | Multiple listings expire at once | All processed correctly |
| 5.6 | RCON give on expiry-sold | Winner gets item |
| 5.7 | RCON return on expiry-no-bids | Seller gets item back |
| 5.8 | Hook firing on expiry | on_expiry fires for each listing |

### FLOW 6: AI Simulation Cycle (`tests/dataflow/flows/test_flow_ai_simulation.py`)

**Path:** Timer → `ah_ai_engine` → gather context → build prompt → DeepSeek API → parse JSON → apply changes

**Test Cases:**

| # | Test | Expected |
|---|------|----------|
| 6.1 | AI cycle with valid response | All changes applied correctly |
| 6.2 | AI returns malformed JSON | Retry logic, fallback, error logged |
| 6.3 | AI returns valid JSON but invalid values | Graceful handling, defaults used |
| 6.4 | AI API call timeout | Retry with exponential backoff |
| 6.5 | AI API call returns 429 (rate limit) | Backoff and retry |
| 6.6 | AI returns empty arrays for all actions | Cycle completes, no changes, notes saved |
| 6.7 | Price adjustment applied | simulated_inventory.current_price updated |
| 6.8 | Stock adjustment (buy/sell) | Simulated inventory stock changes |
| 6.9 | Event triggered by AI | start_event called, cooldowns checked |
| 6.10 | Rare item generated | Item listed via list_item with sim flags |
| 6.11 | Stale recommendation applied | Price lowered or listing cancelled |
| 6.12 | Notes saved to ai_notes | Notes accessible for next prompt |
| 6.13 | Price snapshot taken | take_snapshot called |
| 6.14 | Market summary gathered | get_market_summary, get_stale_listings called |
| 6.15 | AI context includes event data | Active events in prompt |
| 6.16 | AI context includes previous notes | Notes from helper DB included |
| 6.17 | max rare items respected | No more than 2 rare items per cycle |
| 6.18 | Ultra-rare generation probability | ~1% chance, tested over 1000 cycles |
| 6.19 | Event frequency caps respected | Can't start same rarity too soon |
| 6.20 | Hook firing on cycle | on_simulation_cycle_start + end |
| 6.21 | All extensions processed in order | Phase 2C ordering verified |
| 6.22 | Cycle with no active personas | Graceful handling, no crashes |
| 6.23 | Cycle with no listings | Graceful handling |

### FLOW 7: Market Events (`tests/dataflow/flows/test_flow_market_events.py`)

**Path:** AI/Admin → `ah_market_events.start_event()` → cooldown check → DB insert → active → progress check → end

**Test Cases:**

| # | Test | Expected |
|---|------|----------|
| 7.1 | Start small event | ok, event active, price multiplier applied |
| 7.2 | Start medium event | ok, longer duration |
| 7.3 | Start rare event | ok, multi-day duration |
| 7.4 | Start major event (with build-up notes) | ok, 30d cooldown |
| 7.5 | Start major without build-up notes | ok=False |
| 7.6 | Exceed max active overlap (3 events) | ok=False |
| 7.7 | Same rarity already active | ok=False |
| 7.8 | Cooldown not elapsed | ok=False |
| 7.9 | Manual end event | status changed, cleanup runs |
| 7.10 | Event with hidden goals | check_event_progress tracks them |
| 7.11 | Goal met → auto-end | Event ends when goal reached |
| 7.12 | Event expiration | Auto-ends after duration |
| 7.13 | Price multiplier on sim items | Items in affected_items get multiplier |
| 7.14 | Multiple overlapping events | Both active, combined effects |
| 7.15 | Event affects transaction prices | Buy/sell within event uses multiplied prices |
| 7.16 | Hook firing on event | on_world_event fires |

### FLOW 8: Persona Lifecycle (`tests/dataflow/flows/test_flow_persona.py`)

**Path:** Generate → Activate → Behavior Tick → Finance → Needs → Purchase → Memory

**Test Cases:**

| # | Test | Expected |
|---|------|----------|
| 8.1 | Persona generation | Profile, finances, health initialized |
| 8.2 | Persona activation | active=1, last_active_at updated |
| 8.3 | Persona deactivation | active=0 |
| 8.4 | Behavior tick | Needs refreshed, purchase decisions |
| 8.5 | Finance income tick | balance += income_per_tick |
| 8.6 | Need generation | Item needs created with urgency |
| 8.7 | Persona evaluates listings | Scans AH for needed items |
| 8.8 | Persona buys item (via AH) | purchase made, balance deducted |
| 8.9 | Persona impulse buy | Reasonable purchase outside needs |
| 8.10 | Insufficient balance bypass | Persona skips, notes need |
| 8.11 | Memory recording | Purchase recorded in memory |
| 8.12 | Memory decay/expiry | Old memories pruned |
| 8.13 | Life event triggers | Random life events applied |
| 8.14 | World event affects persona | Financial multiplier applied |
| 8.15 | Hook firing | on_persona_activated/deactivated fires |
| 8.16 | Max active personas cap | Can't exceed max_active_personas |
| 8.17 | Max needs per persona | Can't exceed max_needs_per_persona |
| 8.18 | Persona spawn rate | new_persona_spawn_chance respected |

### FLOW 9: Economy Bridge (`tests/dataflow/flows/test_flow_economy.py`)

**Path:** Operation → `_check_eco_balance()` → `_eco_deduct()` → `_eco_credit()` → fallback

**Test Cases:**

| # | Test | Expected |
|---|------|----------|
| 9.1 | Bridge available, sufficient balance | Check passes, deduct succeeds |
| 9.2 | Bridge available, insufficient balance | Check fails, error returned |
| 9.3 | Bridge available, player not found | Check passes (skipped), deduct skipped |
| 9.4 | Bridge not available (ImportError) | Falls back to player_balances table |
| 9.5 | Bridge init fails | is_ready=False, fallback used |
| 9.6 | Bridge deduct returns False | Player not found, operation continues |
| 9.7 | Bridge credit works | Seller credited correctly |
| 9.8 | Fallback table balance ops | player_balances table used correctly |
| 9.9 | Balance sync | sync_balances reconciles bridge and table |
| 9.10 | Concurrent bridge balance ops | Thread-safe via lock |

### FLOW 10: Cross-Extension Phase 2C Ordering (`tests/dataflow/integrations/test_integ_extension_phase2c.py`)

**Path:** PE → state_registry → SOC → state_registry → RELS → state_registry → CHAT → ANNOUNCE → TRADE → HEALTH

This is the most critical integration test. It verifies the exact Phase 2C ordering:

| Order | Extension | Writes | Reads |
|-------|-----------|--------|-------|
| 1 | PEOPLE | active_personas, needs, finances | - |
| 2 | SOCIAL | social_interactions, activities | active_personas, weather, world_events |
| 3 | RELS | relationship_changes, contentions | social_interactions |
| 4 | CHAT | chat_messages, interest_levels | relationships |
| 5 | ANNOUNCE | announcements, events | chat_messages, events |
| 6 | ALL | on_simulation_cycle_end | Everything |
| 7 | TRADE | trade_routes, banditry | economy state |
| 8 | HEALTH | health_stats | persona data |

**Test Cases:**

| # | Test | Expected |
|---|------|----------|
| 10.1 | Correct ordering verified | Extensions fire in exact Phase 2C order |
| 10.2 | State available when read | Each extension finds data written by previous |
| 10.3 | State cleared between cycles | clear_state() called at cycle start |
| 10.4 | No stale data between cycles | Previous cycle state doesn't leak |
| 10.5 | Extension crash doesn't cascade | One extension failure doesn't block others |
| 10.6 | Concurrent state reads are safe | Thread safety of state_registry |
| 10.7 | State namespace isolation | Extension only reads its namespace correctly |

---

## Edge Cases (`tests/dataflow/edge_cases/`)

### Race Conditions

| # | Test | Description |
|---|------|-------------|
| EC1 | TOCTOU listing + bid | Thread A cancels listing while Thread B bids → atomic check prevents both |
| EC2 | TOCTOU bid + buy-now | Thread A bids while Thread B buy-nows → exactly 1 succeeds |
| EC3 | TOCTOU double buy-now | Two threads buy same listing → 1 succeeds |
| EC4 | TOCTOU expire + bid | List expires while being bid on → atomic |
| EC5 | Concurrent persona ticks | Multiple persona behaviors run in parallel |
| EC6 | Database pool exhaustion | 100 concurrent connections |
| EC7 | Thread safety of eco bridge | Concurrent balance checks on same player |

### Data Corruption

| # | Test | Description |
|---|------|-------------|
| EC8 | SQL injection in player name | `player_name="'; DROP TABLE auction_listings; --"` |
| EC9 | SQL injection in item_id | `item_id="minecraft:diamond'; UPDATE ..."` |
| EC10 | Extremely long strings | player_name=10KB, item_id=100KB |
| EC11 | Unicode normalization | Different unicode for same player name |
| EC12 | JSON injection in NBT | `item_nbt` containing rogue SQL or control chars |
| EC13 | Zero-width characters | Invisible chars in player names |
| EC14 | DB file corruption | Simulate partial write → recovery |

### Boundary Values

| # | Test | Description |
|---|------|-------------|
| EC15 | Zero quantity listing | `count=0` |
| EC16 | Max quantity | Very large count |
| EC17 | Min price (0.01) | Exactly the minimum price |
| EC18 | Max price (500.0) | Exactly the maximum price |
| EC19 | Duration = 0 | `duration_hours=0` |
| EC20 | Duration = max (168) | Exactly 7 days |
| EC21 | Empty string item_id | `item_id=""` |
| EC22 | Empty string player name | `player_name=""` |
| EC23 | Multiple edge cases simultaneously | Combination of edge inputs |

### Null Safety

| # | Test | Description |
|---|------|-------------|
| EC24 | All optional params = None | listing works with defaults |
| EC25 | None bid_amount | TypeError handled gracefully |
| EC26 | None listing_uuid | TypeError handled |
| EC27 | None rcon_func | Falls back to internal RCON |
| EC28 | None item_nbt | Works, NBT remains NULL in DB |

### Extension Crash Safety

| # | Test | Description |
|---|------|-------------|
| EC29 | Extension throws in on_load | Other extensions load fine |
| EC30 | Extension throws in hook | Other hooks fire, core unaffected |
| EC31 | Extension corrupts shared state | Other extensions ignore bad data |
| EC32 | Extension unregistered mid-cycle | Missing hooks don't crash |
| EC33 | Circular hook dependencies | Detected and prevented |

### DB Integrity (`tests/dataflow/edge_cases/test_edge_db_integrity.py`)

| # | Test | Description |
|---|------|-------------|
| EC34 | Orphaned transaction_history | No records with invalid listing_uuid |
| EC35 | FK constraints maintained | All references valid |
| EC36 | Index coverage | All queries use indexes |
| EC37 | No double-counting | Transaction totals match listing state |
| EC38 | Balance consistency | Player balance == sum(transactions) |
| EC39 | Time ordering | created_at monotonically increasing |
| EC40 | UUID uniqueness | No duplicate listing_uuid or transaction_uuid |

---

## Stress Tests (`tests/dataflow/stress/`)

| # | Test | Description | Metric |
|---|------|-------------|--------|
| S1 | 10,000 listings | Mass create listings | < 60s |
| S2 | 50,000 bids | Mass bid on listings | < 120s |
| S3 | 1,000 concurrent transactions | Thread pool | No deadlocks |
| S4 | 200 personas active | Full persona simulation | Memory < 500MB |
| S5 | 100 simulation cycles | Long running | No state leaks |
| S6 | DB file growth | Simulate 1 week of activity | < 100MB |
| S7 | RCON flood | 1000 RCON commands | No connection leaks |
| S8 | Extension cascade | All 7 extensions max load | Completes in < 5 min |

---

## Implementation Plan

### Phase 1: Framework (conftest_dataflow.py + probes/)
1. Create `conftest_dataflow.py` with enhanced base class
2. `probes/trace_probe.py` - Data flow tracing
3. `probes/db_probe.py` - Database operation tracing
4. `probes/state_probe.py` - State registry tracing
5. `probes/mock_ai.py` - Mock DeepSeek API for testing

### Phase 2: Core Flow Tests (flows/)
1. `test_flow_listing.py` - 25 test cases
2. `test_flow_bidding.py` - 22 test cases
3. `test_flow_buynow.py` - 16 test cases
4. `test_flow_cancel.py` - 10 test cases
5. `test_flow_expiry.py` - 8 test cases
6. `test_flow_ai_simulation.py` - 23 test cases
7. `test_flow_market_events.py` - 16 test cases
8. `test_flow_persona.py` - 18 test cases
9. `test_flow_economy.py` - 10 test cases

### Phase 3: Integration Tests (integrations/)
1. `test_integ_extension_phase2c.py` - 8 test cases
2. `test_integ_full_simulation.py` - 36-100 round simulation
3. `test_integ_concurrent.py` - Thread safety
4. `test_integ_state_registry.py` - State integrity

### Phase 4: Edge Cases (edge_cases/)
1. `test_edge_race_conditions.py` - 7 test cases
2. `test_edge_data_corruption.py` - 7 test cases
3. `test_edge_boundary_values.py` - 8 test cases
4. `test_edge_null_safety.py` - 5 test cases
5. `test_edge_concurrent_extensions.py` - 5 test cases
6. `test_edge_db_integrity.py` - 7 test cases

### Phase 5: Stress Tests (stress/)
1. `test_stress_high_volume.py`
2. `test_stress_many_personas.py`
3. `test_stress_long_running.py`

### Phase 6: Runner
1. `run_all_dataflow_tests.py` - Orchestrator with reporting

---

## Expected Issues to Log as Work Orders

During implementation and execution of these tests, the following classes of issues are expected and should be logged as work orders:

1. **Race Condition Failures** - Any test in EC1-EC7 that fails reveals a real TOCTOU bug
2. **Extension Isolation Failures** - Any test where one extension's crash affects another
3. **State Leakage** - Phase 2C ordering violations discovered by state_probe
4. **Edge Case Crashes** - Unhandled None/NaN/boundary values causing exceptions
5. **Performance Regressions** - Stress tests exceeding time/memory budgets
6. **FK Constraint Violations** - Orphaned records from incomplete cleanup
7. **Double-Spend Bugs** - Items being sold twice (the most critical bug to catch)
8. **Thread Safety Issues** - Non-atomic operations causing data races
9. **Logger Failures** - Logger exceptions that silently crash operations
10. **Config Edge Cases** - Missing/invalid config values causing startup failures
11. **Missing Hook Coverage** - Core operations that don't fire hooks
12. **Hook Order Violations** - Extensions firing in wrong Phase 2C order
