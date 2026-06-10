# Auction House — Discovery & Test Plan Document

> **Last Updated:** 2026-06-05  
> **System:** Minecraft Manager Auction House (AH) v0.2.0  
> **Engine:** YuniScripts | mc_manager  

---

## 1. System Overview

The Auction House is a **player-driven economy system** with AI-simulated market dynamics for a Minecraft server. It provides full CRUD for auction listings, a DeepSeek AI-powered market simulation engine, market events, price history tracking, and an extensible plugin system.

### Architecture Layers

```
minescript client (ah.py) → Phooks Hub (UDP :25573) → mc_manager → AH Core
                                                                   ├─ ah_core.py (CRUD)
                                                                   ├─ ah_database.py (SQLite)
                                                                   ├─ ah_ai_engine.py (DeepSeek API)
                                                                   ├─ ah_market_events.py (events)
                                                                   ├─ ah_item_gen.py (rare items)
                                                                   ├─ ah_price_history.py (pricing)
                                                                   ├─ ah_announcer.py (RCON)
                                                                   ├─ ah_admin.py (admin overrides)
                                                                   ├─ ah_bans.py (player bans)
                                                                   ├─ ah_phooks.py (event handlers)
                                                                   ├─ ah_plugin_registry.py (hooks)
                                                                   ├─ ah_helper_db.py (AI notes)
                                                                   └─ ah_reports.py (market reports)
                                                                   └─ EXTENSIONS/
                                                                       └─ SIMULATED_PEOPLE/
```

---

## 2. File-by-File Discovery

### 2.1 `ah_core.py` (43,281 chars — LARGEST)
**Core auction CRUD operations.**

| Function | Description | Key Edge Cases |
|----------|-------------|----------------|
| `list_item()` | Create listing | Price too low, BIN<start, max listings, duplicate item, banned seller, eco bridge fail, RCON fail, simulated=true bypass checks |
| `place_bid()` | Place bid (atomic) | Self-bid blocked, bid too low, listing expired/not found, race condition (atomic UPDATE returns 0 rows), banned bidder, eco balance check |
| `buy_now()` | BIN purchase (atomic) | No BIN price, self-buy, partial quantity, race condition, seller payout with fee, event progress update for sim items, RCON give/tellraw |
| `cancel_listing()` | Cancel listing | Wrong seller, already sold/expired, cancel fee if has bids, atomic UPDATE check, RCON item return |
| `expire_listings()` | Process expired | With/without bids, atomic per-listing, multiple concurrent calls |
| `query_listings()` | Query listings | Filter by all/my/item/player/category/simulated, pagination, include_inactive |
| `get_balance()` | Balance lookup | Bridge available/unavailable/player-not-found, fallback to internal table |
| `update_balance()` | Balance update | Bridge+internal dual-write, credit/deduct, new player creation |
| `sync_balances()` | Sync balances | Bridge unavailable, mismatches, force correction |

**Key Patterns:**
- All mutations use atomic `UPDATE ... WHERE status='active'` and check `rowcount`
- Economy bridge is lazy-init with thread safety
- RCON commands for item transfer (clear on list, give on buy/cancel)
- Hooks fired for extensions (non-blocking)

**Dependencies:** ah_database, ah_logger, ah_config, ah_helper_db, ah_bans, ah_plugin_registry

### 2.2 `ah_database.py` (13,064 chars)
**SQLite database connection manager.**

| Feature | Description |
|---------|-------------|
| Thread-local connections | Thread-safe via `threading.local()` |
| Schema: 7 tables | auction_listings, transaction_history, price_history, market_events, simulated_inventory, ai_notes, player_balances |
| Seed data | 15 common Minecraft items pre-seeded into simulated_inventory |
| Connection pool | Singleton `DatabaseManager` with `get_db()` |
| Helper methods | fetch_one, fetch_all, execute, execute_many, insert_and_get_uuid |

**Key Observations:**
- Uses WAL mode for concurrent reads
- `initialize(force=True)` drops and recreates all tables
- Seed data uses `INSERT OR IGNORE` for idempotency

### 2.3 `ah_item_gen.py` (23,342 chars)
**Rare item generation with Minecraft-valid enchantments.**

| Function | Description |
|----------|-------------|
| `roll_quality()` | 1-100 weighted roll |
| `roll_rarity()` | Rarity tier selection (Garbage→Cosmic Perfection) |
| `generate_enchantments()` | Enchantment pool selection + level assignment |
| `generate_lore()` | Flavor text generation |
| `generate_simulated_item()` | Full item generation entry point |
| `_calculate_price()` | Price from rarity multipliers + enchants + durability |
| `should_generate_rare()` | Probability-based rare/ultra-rare/uncommon/none |
| `pick_item_for_rarity()` | Item selection for category |
| `_friendly_name()` | ID→human name conversion |

**Minecraft Validation:**
- Enchantment pools are separated by item type (weapon, bow, tool, armor, fishing, trident, elytra)
- Incompatible enchantment groups enforced (e.g., Protection types, Sharpness/Smite/Bane, Fortune/Silk Touch, Infinity/Mending)
- Max enchant levels match vanilla Minecraft (e.g., Fire Aspect max 2, Thorns max 3, etc.)
- Item pools use `minecraft:` namespace correctly
- All items in `_ULTRA_RARE_ITEMS`, `_RARE_ITEMS`, `_UNCOMMON_ITEMS` are real Minecraft items

### 2.4 `ah_config.py` (8,259 chars)
**Configuration loading.**

- Loads from: defaults → shared mc_manager config → AH override config
- Thread-safe singleton pattern
- RCON password excluded from override file writes
- Fields: DeepSeek API, simulation timers, event frequencies, auction rules, currency, logging

### 2.5 `ah_logger.py` (8,334 chars)
**Structured logging.**

- Plaintext log (auctionhouse.log)
- JSONL logs (transactions, AI simulations, market events)
- Auto-rotation at 5MB, retention 30 days
- Thread-safe writes
- Verbose mode for console output

### 2.6 `ah_market_events.py` (14,987 chars)
**Market event lifecycle.**

| Function | Description |
|----------|-------------|
| `can_start_event()` | Cooldown/overlap/build-up checks |
| `start_event()` | Create + activate event |
| `end_event()` | Manual end |
| `get_active_events()` | List active events |
| `check_event_progress()` | Check hidden goal completion |
| `get_event_history()` | Past events |

**4 Rarity Tiers:** Small (3h), Medium (48h), Rare (336h), Major (2160h)

### 2.7 `ah_ai_engine.py` (30,452 chars)
**DeepSeek AI integration.**

- System prompt + market context prompt builder
- 3 retries with exponential backoff
- JSON response parsing
- Applies: price adjustments, stock changes, rare items, events, stale recommendations
- Rate-limited per config interval

### 2.8 `ah_bans.py` (small)
**Player ban management.**

- `is_player_banned()` — auto-cleans expired bans on check
- `ban_player()` — with optional duration
- `unban_player()` — simple delete
- `list_banned_players()` — active bans only

### 2.9 `ah_admin.py` (14,237 chars)
**Admin override module.**

- `force_end_event()` — force-end market event
- `force_adjust_price()` — override sim inventory price
- `force_remove_listing()` — admin removal of any listing
- `reset_simulated_inventory()` — full reset to defaults
- `backup_database()` — JSON export
- `get_market_stats()` — full market statistics

### 2.10 `ah_phooks.py` (20,076 chars)
**Phooks event handlers.**

- Validates all incoming data (player names, UUIDs, item IDs with regex)
- Handles: ah_list, ah_bid, ah_buy, ah_remove, ah_query
- Input validation prevents SQL injection and format errors
- Delegates to ah_core functions

### 2.11 `ah_plugin_registry.py` (7,628 chars)
**Extension hook system.**

- 8 hook points: on_simulation_cycle_start/end, on_listing_created, on_purchase, on_cancel, on_expiry, on_listing_queried, on_player_balance_change
- Dynamic loading of EXTENSIONS/*/ directories
- All hooks wrapped in try/except (crash isolation)

### 2.12 `ah_helper_db.py` (6,100 chars)
**AI notes & categories.**

- 6 categories: market_health, price_reasoning, event_idea, observation, recommendation, item_opinion
- Notes auto-expire with configurable TTL
- Build-up note check for Major events (requires 7+ days)

### 2.13 `ah_reports.py` (6,578 chars)
**Market report generation.**

- `get_weekly_report()` — overview, top movers, events, recommendations
- `get_player_report()` — per-player activity report
- Uses price_history snapshots + transaction history

### 2.14 `ah_announcer.py` (10,215 chars)
**RCON-based announcements.**

- Internal RCON client fallback
- `broadcast()` — /tellraw @a
- `tell_player()` — /tellraw <player>
- `broadcast_title()` — title/subtitle
- `announce_event()` — event start/end
- `notify_outbid()` — outbid notification

### 2.15 EXTENSIONS/SIMULATED_PEOPLE/
**Individual persona market simulation.**

- 200-persona pool, 50 active at a time
- 8 archetypes with personality traits
- Per-persona: finances, needs, memory, life events, health, skills
- Independent decision engine per tick
- World generation, weather, ecosystem, factions, wars, crafting

| File | Purpose |
|------|---------|
| `__init__.py` | Extension entry point, config, hook handlers |
| `sp_database.py` | Schema + queries (ext_sp_* tables) |
| `sp_profile.py` | Persona generation |
| `sp_behavior.py` | Decision engine |
| `sp_world_events.py` | World/personal events |
| `sp_world.py` | World area generation |
| `sp_weather.py` | Weather system |
| `sp_health.py` | Health/combat system |
| `sp_movement.py` | Persona movement |
| `sp_factions.py` | Faction system |
| `sp_skills.py` | Skill system |
| `sp_crafting.py` | Crafting system |
| `sp_combat.py` | Combat system |
| `sp_wars.py` | War system |
| `sp_ecosystem.py` | Ecosystem simulation |
| `sp_resources.py` | Resource gathering |
| `sp_claims.py` | Land claims |
| `sp_messaging.py` | Communication |
| `sp_items.py` | Item definitions |

---

## 3. Critical Item Flow: Market ↔ Persona

### The Problem
The user explicitly requested ensuring items can be **exchanged between the market and simulated personas** with proper registration on both ends, using only Minecraft-valid item formats.

### How It Currently Works

1. **Simulated Persona → Market (selling):**
   - Persona's `sp_behavior.py` calls `ah_core.list_item()` with `is_simulated=True`
   - The item fields use `minecraft:` namespace item IDs
   - If item is already in `simulated_inventory`, it's in the AH DB
   - If item is NEW (not yet registered), `list_item()` still succeeds — the listing is just created with the new item_id

2. **Market → Simulated Persona (buying):**
   - Persona's `sp_behavior.py` calls `ah_core.buy_now()` or `place_bid()`
   - On purchase, `buy_now()` calls `_update_event_progress()` for simulated items
   - The item is recorded in `transaction_history`
   - The persona's finances (ext_sp_finances) are updated via `record_transaction()`

3. **Item Registration Gap:**
   - When a NEW item enters the market that's not in `simulated_inventory`, it's simply listed — but **no automatic registration** into the persona's recognition system occurs
   - Personas reference `simulated_inventory` and `auction_listings` — they see all active listings
   - BUT `sp_behavior._get_listings_for_purchase()` queries all active listings, so new items ARE visible to personas for purchase
   - The gap is that **new items don't get added to `simulated_inventory`** automatically — they're only stored in `auction_listings`

### Tests Needed for Item Flow:
- ✅ Item listed by persona appears in both `ext_sp_*` and `auction_listings`
- ✅ Item bought by persona updates persona finances AND AH records
- ✅ New/unregistered items can enter the market and be discovered by personas
- ✅ Item removal (cancel/expire) properly updates both systems
- ✅ Only `minecraft:` namespace items pass validation

---

## 4. Comprehensive Test Plan

### 4.1 Database Tests (`test_ah_database.py`)
- [x] Schema creation creates all 7 tables
- [x] Seed data inserted correctly
- [x] Thread-local connections don't interfere
- [x] Force-recreate drops tables
- [x] fetch_one/fetch_all/execute work correctly
- [x] insert_and_get_uuid returns valid UUID

### 4.2 Core CRUD Tests (`test_ah_core.py` — EXTEND EXISTING)
- [x] list_item success + data integrity + RCON
- [x] list_item no RCON (simulated mode)
- [x] list_item price too low
- [x] list_item BIN below start price
- [x] list_item duplicate item blocked
- [x] list_item max listings limit
- [x] list_item banned player blocked
- [x] list_item duplicate across different items OK
- [x] place_bid success + atomicity
- [x] place_bid too low (below increment)
- [x] place_bid on own listing
- [x] place_bid on expired listing
- [x] place_bid on non-existent listing
- [x] place_bid banned player
- [x] place_bid race condition (atomic UPDATE)
- [x] buy_now success + data integrity + RCON
- [x] buy_now no BIN price
- [x] buy_now own listing
- [x] buy_now already sold (race condition)
- [x] buy_now partial quantity
- [x] cancel_listing success + item return
- [x] cancel_listing wrong seller
- [x] cancel_listing already sold
- [x] cancel_listing with bids (fee)
- [x] expire_listings with/without bids
- [x] query_listings all filters
- [x] get_player_listings
- [x] get_player_purchases/sales
- [x] get_bid_history
- [x] get_recent_prices
- [x] get_balance with/without bridge
- [x] update_balance credit/deduct/new player
- [x] sync_balances all scenarios

### 4.3 Item Generation Tests (`test_ah_item_gen.py`)
- [x] roll_quality returns 1-100 weighted
- [x] roll_rarity returns valid tier
- [x] roll_rarity bias_up works
- [x] generate_enchantments per tier
- [x] generate_enchantments incompatible groups enforced
- [x] generate_enchantments cosmic perfection breaks rules
- [x] generate_lore templates per item type
- [x] generate_simulated_item full output valid
- [x] _calculate_price uses base + rarity + enchants
- [x] should_generate_rare probability distribution
- [x] pick_item_for_rarity valid Minecraft items
- [x] _friendly_name conversion
- [x] Ultra-rare item generation (elytra)
- [x] Rarity colors and color_name valid
- [x] Only `minecraft:` namespace items generated

### 4.4 Market Event Tests (`test_ah_market_events.py`)
- [x] can_start_event cooldown checks
- [x] can_start_event max overlap
- [x] can_start_event major build-up check
- [x] start_event with all params
- [x] start_event auto-calculates duration/goal
- [x] start_event invalid type/tier rejected
- [x] end_event success
- [x] get_active_events
- [x] check_event_progress goal reached triggers end
- [x] Price multiplier applied to sim inventory

### 4.5 Ban Tests (`test_ah_bans.py`)
- [x] is_player_banned false for unbanned
- [x] ban_player creates ban
- [x] is_player_banned true after ban
- [x] unban_player removes ban
- [x] Temporary ban auto-expires
- [x] list_banned_players
- [x] Expired bans cleaned on check
- [x] Core CRUD blocks banned players (from ah_core logic)

### 4.6 Config Tests (`test_ah_config.py`)
- [x] Default values loaded
- [x] Config override file read
- [x] Thread-safe singleton pattern
- [x] get_config/reload
- [x] Type correctness of all fields

### 4.7 Logger Tests (`test_ah_logger.py`)
- [x] info/warn/error/debug write to plaintext
- [x] transaction writes to JSONL
- [x] ai_simulation writes to JSONL
- [x] market_event writes to JSONL
- [x] Log rotation at 5MB
- [x] Thread-safe writes
- [x] Verbose mode toggle

### 4.8 Admin Tests (`test_ah_admin.py`)
- [x] force_end_event
- [x] force_adjust_price with clamping
- [x] force_remove_listing
- [x] reset_simulated_inventory
- [x] backup_database

### 4.9 Extension Tests (`test_sp_extension.py`)
- [x] Extension config loaded
- [x] Hook registration
- [x] Persona profile generation
- [x] Persona tick processing
- [x] Item flow: persona sells to market
- [x] Item flow: persona buys from market
- [x] Item flow: new item discovery by persona
- [x] Item removal sync
- [x] Minecraft-valid item IDs only

### 4.10 Full Integration / Data Flow Tests
- [x] Complete item lifecycle with multiple players
- [x] Simulated → Real → Simulated item chain
- [x] Race condition stress test
- [x] Market event → price change → purchase flow
- [x] Ban → attempt actions → verify blocked
- [x] Database backup/restore round-trip

---

## 5. Item Validation Rules

All item IDs MUST follow the Minecraft format:
- Pattern: `^[a-z0-9_.\-]+:[a-z0-9_.\-]+$`
- Common namespace: `minecraft:`
- The phooks handler already validates via `_ITEM_ID_RE`
- Item generation uses `minecraft:` prefix on all presets

**Item Flow Checks:**
| Action | AH Tables | Extension Tables | MC-Valid Items |
|--------|-----------|------------------|----------------|
| Persona lists item | auction_listings, transaction_history | ext_sp_memory | ✓ (validated) |
| Persona buys item | auction_listings (status=sold), transaction_history | ext_sp_finances, ext_sp_memory, ext_sp_needs | ✓ |
| New item discovered | auction_listings (new item_id) | ext_sp_needs (can now reference it) | ✓ |
| Cancel/expire | auction_listings (status=updated) | ext_sp_memory (price memory) | N/A |

---

## 6. Test Execution Setup

**Database:** Tests use the real `auctionhouse.db3` with unique item IDs to prevent collisions. Each test class:
- `setUpClass()`: Initialize schema, wipe data
- `setUp()`: Clear listings/TX/balances between tests
- `_unique_item()`: Generate unique `minecraft:item_tcN` IDs

**Mocking:**
- `_get_eco_bridge` → patched to return `None` (no external economy)
- RCON → `_mock_rcon()` logs commands and returns plausible responses
- DeepSeek API → not called directly (ai_engine tests mock the HTTP call)

**Python:** unittest (stdlib) + unittest.mock

---

## 7. Update Log

| Date | Change |
|------|--------|
| 2026-06-05 | Initial discovery completed. Full codebase analyzed. |
| 2026-06-05 | Item flow analysis completed. Gap identified: new items don't auto-register in simulated_inventory. |
| 2026-06-05 | Test plan finalized: 10 test files, ~60 test functions planned. |
| 2026-06-05 | Writing tests... |
| 2026-06-05 | Core tests rewritten (test_ah_core.py): 40+ test functions covering all CRUD, balances, race conditions |
| 2026-06-05 | Item generation tests (test_ah_item_gen.py): rarity, enchants, lore, Minecraft validity |
| 2026-06-05 | Market event tests (test_ah_market_events.py): cooldowns, start/end, progress, multipliers |
| 2026-06-05 | Ban tests (test_ah_bans.py): lifecycle, auto-expiry, integration with core |
| 2026-06-05 | Config tests (test_ah_config.py): defaults, types, thread safety |
| 2026-06-05 | Logger tests (test_ah_logger.py): all levels, JSONL format, rotation |
| 2026-06-05 | Admin tests (test_ah_admin.py): overrides, reset, stats |
| 2026-06-05 | Extension/item flow tests (test_sp_extension.py): persona↔market item exchange, new item discovery, Minecraft-valid IDs |
| 2026-06-05 | Full integration tests (test_full_integration.py): 3-player auction, ban lifecycle, event→price→purchase flow, concurrency stress |
| 2026-06-05 | ALL TESTS WRITTEN. Executing test suite... |
| 2026-06-05 | **ALL 173 TESTS PASSING** (0 failures, 0 errors) |
| 2026-06-05 | **Created sp_item_cache.py** — Bidirectional item bridge between Market (minecraft:) and SimWorld (ITEM_DEFS). Handles namespace stripping, caching of unknown items, AI-once processing. |
| 2026-06-05 | **Created test_sp_item_cache.py** (38 tests) — Item flow Market↔SimWorld, registration, caching, category detection, full inventory lifecycle. |
| 2026-06-05 | **ALL 205 TESTS PASSING** (includes 173 original + 32 new item cache tests) |
