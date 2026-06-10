# Auction House System – Specification Document

> **Project:** Minecraft Server Manager (mc_manager)  
> **System:** Auction House (AH) — managed market with DeepSeek AI simulation  
> **Integration:** Phooks event bus, RCON, signed_item database, DeepSeek API  
> **Author:** AI Design Draft  
> **Date:** 2026-06-05 (v1.1 — corrected arch diagram, added event frequency scaling)

---

## Table of Contents

1. [Overview & Goals](#1-overview--goals)
2. [Architecture](#2-architecture)
3. [Database Design](#3-database-design)
4. [Phooks Events & Protocol](#4-phooks-events--protocol)
5. [Core Auction Operations](#5-core-auction-operations)
6. [AI Market Simulation Engine](#6-ai-market-simulation-engine)
7. [Rarity & Item Generation](#7-rarity--item-generation)
8. [Market Events System](#8-market-events-system)
9. [AI Helper Database (Notes & Categories)](#9-ai-helper-database-notes--categories)
10. [Logging System](#10-logging-system)
11. [File Structure](#11-file-structure)
12. [Implementation Roadmap](#12-implementation-roadmap)

---

## 1. Overview & Goals

### 1.1 Purpose
The Auction House provides a living, breathing marketplace for the Minecraft server. Unlike a static auction plugin, this system uses a **DeepSeek AI API** to simulate a dynamic economy — complete with supply shocks, seasonal events, price trends, and rare item rotations — while giving real players the primary influence over pricing through their listings and purchases.

### 1.2 Core Design Principles

| Principle | Description |
|-----------|-------------|
| **Player-Driven** | Real player listings and purchases are the #1 price influencers |
| **AI-Augmented** | DeepSeek AI handles simulation, event generation, and market commentary |
| **Verisimilitude** | The market should feel alive — prices fluctuate, events happen, rare items appear |
| **Fairness** | AI cannot override player prices beyond reasonable simulated bounds |
| **Transparency** | Market events are announced in-game with flavor text |
| **Rarity Respect** | Uses the existing rarity tier system from `sign_item.py` |

### 1.3 Key Features

- Full CRUD for auction listings (list, bid, buy, cancel)
- Simulated common-items market (dirt → diamonds)
- AI-generated rare item listings (elytra, enchanted gear) with **extremely low probability**
- Dynamic market events ("Winter is coming — coal prices surge!")
- Price history tracking & trend analysis
- AI notes & categories system for tracking market health
- Transaction logging for all operations
- Weekly market reports (AI-generated)
- Stale/stuck listing detection and AI revaluation

---

## 2. Architecture

### 2.1 System Context

```
┌─────────────────────┐       Phooks UDP        ┌──────────────────────┐
│   Minecraft Client  │◄──────────────────────►  │   Phooks Hub (:25573)│
│   (minescript)      │                          │   (Event Bus)        │
└─────────────────────┘                          └──────┬───────────────┘
                                                        │
                                    ┌───────────────────┼───────────────────┐
                                    │                   │                   │
                                    ▼                   ▼                   ▼
                          ┌─────────────────┐  ┌──────────────────────────────────────┐
                          │ item-signing    │  │          mc_manager                  │
                          │ bridge          │  │          (main.py)                   │
                          └─────────────────┘  │  ┌────────────────────────────────┐  │
                                                │  │    Auction House (AH/)        │  │
                                                │  │  ┌──────────────────────┐     │  │
                                                │  │  │  ah_ai_engine.py     │─────┼──┼────► DeepSeek API
                                                │  │  │  (market simulation) │     │  │      (HTTP external)
                                                │  │  └──────────┬───────────┘     │  │
                                                │  │             ▼                 │  │
                                                │  │  ┌──────────────────────┐     │  │
                                                │  │  │  ah_announcer.py     │─────┼──┼────► RCON (:25575)
                                                │  │  │  (events→tellraw)    │     │  │      (Minecraft Server)
                                                │  │  └──────────────────────┘     │  │
                                                │  │  ┌──────────────────────┐     │  │
                                                │  │  │  ah_core.py          │─────┼──┼────► (item mgmt)
                                                │  │  │  (list/bid/buy/cancel)│     │  │
                                                │  │  └──────────────────────┘     │  │
                                                │  │  ┌──────────────────────┐     │  │
                                                │  │  │  auctionhouse.db3    │     │  │
                                                │  │  └──────────────────────┘     │  │
                                                │  └────────────────────────────────┘  │
                                                └──────────────────────────────────────┘
```

### 2.2 Communication Flow

1. **Player-to-Auction**: Player uses in-game command → minescript → Phooks → mc_manager/AH → RCON
2. **Auction-to-Player**: mc_manager/AH → RCON → `/tellraw` or `/title` → player
3. **AI Sim Loop**: mc_manager/AH timer triggers `ah_ai_engine.py` → calls **DeepSeek API** (HTTP) → parses AI response → applies DB changes → AH announces via `ah_announcer.py` → RCON → `/tellraw @a`
4. **Player Listings**: Players list items → prices recorded in `auctionhouse.db3` → AI factors into next simulation cycle
5. **Market Events**: `ah_ai_engine.py` detects event triggers → updates `market_events` table → `ah_announcer.py` broadcasts via RCON → `/title @a` with event flavor text

### 2.3 Phooks Events (Auction House)

| Event | Direction | Description |
|-------|-----------|-------------|
| `ah_list` | Client → Manager | Player wants to list an item |
| `ah_list_response` | Manager → Client | Confirmation/rejection of listing |
| `ah_bid` | Client → Manager | Player places a bid |
| `ah_bid_response` | Manager → Client | Bid result |
| `ah_buy` | Client → Manager | Buy-It-Now purchase |
| `ah_buy_response` | Manager → Client | Purchase result |
| `ah_remove` | Client → Manager | Cancel/remove listing |
| `ah_remove_response` | Manager → Client | Removal result |
| `ah_query` | Client → Manager | Query listings (by item, player, category) |
| `ah_query_response` | Manager → Client | Listing data |
| `ah_announce` | Manager → All | Market event announcement (broadcast) |
| `ah_ai_report` | Manager → All | AI-generated weekly report |

### 2.4 Stdio Commands (mc_manager additions)

| Command | Description |
|---------|-------------|
| `ah list <player>` | List player's active auctions |
| `ah all` | List all active auctions |
| `ah status` | Market health overview |
| `ah simulate` | Force-trigger AI simulation cycle |
| `ah ai_note <text>` | Add a note to the AI helper database |
| `ah event <name>` | Trigger a market event manually |

---

## 3. Database Design

### 3.1 Database Location & Name

```
/home/deck/Documents/dev-yuniScripts/SCRIPTS/GAMES/minecraft_manager/AUCTIONHOUSE/auctionhouse.db3
```

### 3.2 Table: `auction_listings`

Core table — every auctionable item has one row.

```sql
CREATE TABLE IF NOT EXISTS auction_listings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_uuid        TEXT NOT NULL UNIQUE,              -- UUID v4 for external reference
    seller_name         TEXT NOT NULL,                     -- Minecraft player name
    item_id             TEXT NOT NULL,                      -- e.g. "minecraft:diamond"
    item_count          INTEGER NOT NULL DEFAULT 1,        -- stack size
    item_nbt            TEXT,                               -- full NBT/component data (preserved)
    signed_name         TEXT,                               -- if signed: the display name
    rarity              TEXT,                               -- rarity tier string (e.g. "§6Common 42")
    cert_hash           TEXT,                               -- certification hash from signing
    is_simulated        INTEGER NOT NULL DEFAULT 0,         -- 1 = AI-generated, 0 = player-listed
    start_price         REAL NOT NULL,                      -- starting price in emeralds
    current_bid         REAL,                               -- current highest bid (NULL = no bids)
    buy_now_price       REAL,                               -- BIN price (NULL = auction only)
    highest_bidder      TEXT,                               -- player name of highest bidder
    bids_count          INTEGER NOT NULL DEFAULT 0,         -- total bid count
    currency_type       TEXT NOT NULL DEFAULT 'emerald',    -- 'emerald' or 'diamond' (future)
    status              TEXT NOT NULL DEFAULT 'active',      -- 'active','expired','sold','cancelled','pending'
    listed_at           TEXT NOT NULL,                      -- ISO 8601 timestamp
    expires_at          TEXT,                               -- ISO 8601 timestamp (NULL = no expiry)
    sold_at             TEXT,                               -- ISO 8601 timestamp when sold
    sold_price          REAL,                               -- final sale price
    ai_weight           REAL DEFAULT 1.0,                   -- factor AI uses to prioritize re-evaluation
    stale_since         TEXT,                                -- if item has been up too long with no bids
    extra_meta          TEXT,                                -- JSON blob for extensible metadata
    
    -- Simulated item specific fields
    sim_lore            TEXT,                               -- Flavor lore text for simulated items
    sim_source_event    TEXT,                               -- Which event generated this item
    sim_enchantments    TEXT,                               -- JSON array of enchantments
    sim_durability      INTEGER,                            -- For tools/armor
    sim_quality_roll    INTEGER                              -- The quality roll (1-100)
);
```

**Indexes:**
```sql
CREATE INDEX idx_listings_status ON auction_listings(status);
CREATE INDEX idx_listings_seller ON auction_listings(seller_name);
CREATE INDEX idx_listings_item ON auction_listings(item_id);
CREATE INDEX idx_listings_simulated ON auction_listings(is_simulated);
CREATE INDEX idx_listings_stale ON auction_listings(stale_since);
```

### 3.3 Table: `transaction_history`

Every completed sale, bid, or cancelled listing.

```sql
CREATE TABLE IF NOT EXISTS transaction_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_uuid TEXT NOT NULL UNIQUE,                  -- UUID v4
    listing_uuid    TEXT NOT NULL,                           -- FK to auction_listings
    transaction_type TEXT NOT NULL,                          -- 'list','bid','buy','cancel','expire','ai_sim_list','ai_sim_sold','ai_sim_remove'
    actor_name      TEXT NOT NULL,                           -- player who performed action (or 'AI')
    item_id         TEXT NOT NULL,
    item_count      INTEGER NOT NULL DEFAULT 1,
    price           REAL,                                    -- the price at transaction time
    previous_price  REAL,                                    -- previous price (for bid updates)
    balance_before  REAL,                                    -- player balance before (if tracked)
    balance_after   REAL,                                    -- player balance after
    metadata        TEXT,                                    -- JSON blob for extra data (enchantments, etc.)
    created_at      TEXT NOT NULL                             -- ISO 8601 timestamp
);
```

**Indexes:**
```sql
CREATE INDEX idx_tx_listing ON transaction_history(listing_uuid);
CREATE INDEX idx_tx_actor ON transaction_history(actor_name);
CREATE INDEX idx_tx_type ON transaction_history(transaction_type);
CREATE INDEX idx_tx_time ON transaction_history(created_at);
```

### 3.4 Table: `price_history`

Time-series price tracking for market analysis by the AI.

```sql
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,                               -- e.g. "minecraft:coal"
    price_avg   REAL NOT NULL,                               -- average price at snapshot
    price_min   REAL NOT NULL,                               -- lowest price at snapshot
    price_max   REAL NOT NULL,                               -- highest price at snapshot
    listing_count INTEGER NOT NULL DEFAULT 0,                -- active listings count
    volume_sold INTEGER NOT NULL DEFAULT 0,                  -- items sold in period
    snapshot_at TEXT NOT NULL                                 -- ISO 8601 timestamp
);
```

**Indexes:**
```sql
CREATE INDEX idx_ph_item ON price_history(item_id);
CREATE INDEX idx_ph_time ON price_history(snapshot_at);
```

### 3.5 Table: `market_events`

Generated market events from the AI or triggered manually.

```sql
CREATE TABLE IF NOT EXISTS market_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uuid      TEXT NOT NULL UNIQUE,                    -- UUID v4
    event_name      TEXT NOT NULL,                           -- e.g. "extreme_winter"
    event_title     TEXT NOT NULL,                           -- Player-facing title
    event_flavor    TEXT NOT NULL,                           -- Flavor text displayed in chat
    event_type      TEXT NOT NULL DEFAULT 'seasonal',        -- 'seasonal','shortage','surplus','discovery','disaster','festival'
    affected_items  TEXT NOT NULL,                            -- JSON array of item IDs or categories
    price_multiplier REAL NOT NULL DEFAULT 1.0,              -- price multiplier during event (e.g. 2.5)
    demand_boost    REAL NOT NULL DEFAULT 1.0,               -- how much demand increases
    duration_seconds INTEGER NOT NULL DEFAULT 86400,         -- event duration (default 24h)
    trigger_condition TEXT,                                  -- JSON: what triggers the event (hidden goal)
    goal_count     INTEGER,                                  -- items needed to be sold to end event
    current_count  INTEGER DEFAULT 0,                        -- current progress toward goal
    is_active      INTEGER NOT NULL DEFAULT 1,               -- 1 = active, 0 = ended
    started_at     TEXT NOT NULL,                             -- ISO 8601
    ended_at       TEXT,                                     -- ISO 8601 when event concluded
    created_by     TEXT NOT NULL DEFAULT 'AI'                -- 'AI' or 'admin'
);
```

**Indexes:**
```sql
CREATE INDEX idx_me_active ON market_events(is_active);
CREATE INDEX idx_me_type ON market_events(event_type);
```

### 3.6 Table: `simulated_inventory`

Tracks what the AI has listed and how much "stock" the simulated market holds.

```sql
CREATE TABLE IF NOT EXISTS simulated_inventory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         TEXT NOT NULL,                           -- e.g. "minecraft:coal"
    category        TEXT NOT NULL DEFAULT 'common',          -- 'common','uncommon','rare','ultra_rare'
    current_stock   INTEGER NOT NULL DEFAULT 0,              -- how many units the sim holds
    max_stock       INTEGER NOT NULL DEFAULT 100,            -- cap on simulatable stock
    base_price      REAL NOT NULL,                           -- base price in emeralds
    volatility      REAL NOT NULL DEFAULT 0.1,               -- price volatility factor (0.0-1.0)
    trend_direction INTEGER NOT NULL DEFAULT 0,              -- -1: falling, 0: stable, 1: rising
    trend_strength  REAL NOT NULL DEFAULT 0.0,               -- 0.0-1.0 how strong the trend is
    last_updated    TEXT NOT NULL,                            -- ISO 8601
    extra_meta      TEXT                                     -- JSON blob
);
```

**Seed data** (pre-populated):
```sql
-- Common materials (AI simulates these)
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:coal','common',500,2000,0.5,0.2,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:iron_ingot','common',300,1000,1.5,0.15,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:gold_ingot','common',100,500,3.0,0.2,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:redstone','common',400,1500,0.25,0.12,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:lapis_lazuli','uncommon',150,500,2.0,0.15,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:diamond','rare',50,200,8.0,0.25,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:emerald','common',200,1000,1.0,0.1,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:netherite_ingot','rare',20,100,25.0,0.3,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:stick','common',1000,5000,0.05,0.08,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:oak_log','common',800,3000,0.3,0.1,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:cobblestone','common',2000,5000,0.05,0.05,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:wheat','common',300,1000,0.5,0.12,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:bone','common',200,800,0.3,0.15,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:ender_pearl','uncommon',50,200,4.0,0.2,0,0.0,'2026-01-01T00:00:00','{}');
INSERT INTO simulated_inventory VALUES (NULL,'minecraft:blaze_rod','uncommon',30,150,6.0,0.2,0,0.0,'2026-01-01T00:00:00','{}');
```

### 3.7 Table: `player_balances`

Tracks player in-game currency for auction house purposes.

```sql
CREATE TABLE IF NOT EXISTS player_balances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name     TEXT NOT NULL UNIQUE,
    balance         REAL NOT NULL DEFAULT 0.0,               -- in emeralds
    lifetime_earned REAL NOT NULL DEFAULT 0.0,
    lifetime_spent  REAL NOT NULL DEFAULT 0.0,
    listings_count  INTEGER NOT NULL DEFAULT 0,
    purchases_count INTEGER NOT NULL DEFAULT 0,
    last_updated    TEXT NOT NULL
);
```

---

## 4. Phooks Events & Protocol

### 4.1 Event: `ah_list` (Player → Manager)

Player listing an item.

```json
{
  "command": "EMIT",
  "event": "ah_list",
  "data": {
    "request_uuid": "uuid-v4",
    "player_name": "Steve",
    "item_id": "minecraft:diamond_sword",
    "item_count": 1,
    "item_nbt": "{...}",             // full NBT/components
    "signed_name": "Sword of Legends (Signed)",
    "rarity": "§cLegendary 88",
    "cert_hash": "A3F72B1C",
    "start_price": 10.0,
    "buy_now_price": 50.0,
    "duration_hours": 48
  }
}
```

### 4.2 Event: `ah_list_response` (Manager → Player)

```json
{
  "command": "EMIT",
  "event": "ah_list_response",
  "data": {
    "request_uuid": "uuid-v4",
    "status": "ok",
    "listing_uuid": "uuid-v4",
    "message": "Your item has been listed!"
  }
}
```

Error response:
```json
{
  "status": "error",
  "message": "You already have 5 active listings. Remove one first."
}
```

### 4.3 Event: `ah_bid` (Player → Manager)

```json
{
  "command": "EMIT",
  "event": "ah_bid",
  "data": {
    "request_uuid": "uuid-v4",
    "player_name": "Alex",
    "listing_uuid": "uuid-v4",
    "bid_amount": 15.0
  }
}
```

### 4.4 Event: `ah_bid_response`

```json
{
  "status": "ok",
  "message": "Bid placed! You are now the highest bidder.",
  "listing_uuid": "...",
  "new_current_bid": 15.0,
  "previous_bidder": "Steve"
}
```

### 4.5 Event: `ah_buy` (Player → Manager)

```json
{
  "command": "EMIT",
  "event": "ah_buy",
  "data": {
    "request_uuid": "uuid-v4",
    "player_name": "Alex",
    "listing_uuid": "uuid-v4",
    "quantity": 1
  }
}
```

### 4.6 Event: `ah_remove` (Player → Manager)

```json
{
  "command": "EMIT",
  "event": "ah_remove",
  "data": {
    "request_uuid": "uuid-v4",
    "player_name": "Steve",
    "listing_uuid": "uuid-v4"
  }
}
```

### 4.7 Event: `ah_query` (Player → Manager)

```json
{
  "command": "EMIT",
  "event": "ah_query",
  "data": {
    "request_uuid": "uuid-v4",
    "player_name": "Steve",
    "filter_type": "all",           // 'all', 'my', 'item:<id>', 'player:<name>', 'category:<cat>'
    "filter_value": "",
    "page": 1,
    "per_page": 20
  }
}
```

### 4.8 Event: `ah_announce` (Manager → All broadcast)

```json
{
  "command": "EMIT",
  "event": "ah_announce",
  "data": {
    "type": "market_event",
    "title": "§6❄ Extreme Winter Event! ❄",
    "message": "§bAn extreme winter has hit the realm! Coal prices have surged 300% as citizens scramble to keep warm. Stock up now — 5000 coal must be sold before prices normalize!",
    "affected_items": ["minecraft:coal"],
    "multiplier": 3.0,
    "duration": "24h"
  }
}
```

---

## 5. Core Auction Operations

### 5.1 Listing Items

1. Player has item in inventory (verified via RCON `/data get entity`)
2. Player issues command (via minescript) with pricing info
3. Manager deducts item from player inventory via RCON `/clear`
4. Manager inserts record into `auction_listings`
5. Manager records transaction in `transaction_history`
6. Manager sends confirmation to player

**Validation rules:**
- Max 5 active listings per player (configurable)
- Item must not be already listed by the same player
- Price must be ≥ 0.1 emerald (anti-spam floor)
- Signed items get priority AI weight (`ai_weight = 1.5`)

### 5.2 Bidding

1. Player sends `ah_bid` with amount
2. Manager checks player has enough emeralds (via RCON `/clear <player> emerald 0 simulate`)
   - Or track balances in `player_balances` table
3. Bid must exceed current bid by ≥10% (configurable minimum increment)
4. Previous bidder is notified via `/tellraw` that they've been outbid
5. Bid is recorded, listing updated

### 5.3 Buy-It-Now

1. Player sends `ah_buy` with listing UUID
2. Manager deducts BIN price from buyer (emeralds)
3. Manager gives item to buyer via RCON `/give`
4. Manager sends emeralds to seller (minus AH fee, e.g. 2%)
5. Listing status set to 'sold'
6. Transaction recorded

### 5.4 Removing / Cancelling

1. Player sends `ah_remove` with listing UUID
2. Manager checks player is the seller
3. Manager returns item to player inventory
4. Listing status set to 'cancelled'
5. Transaction recorded

### 5.5 Expiry

1. Background thread checks listings where `expires_at < now() AND status = 'active'`
2. If no bids: item returned to seller, listing expired
3. If has bids: last highest bidder wins automatically
4. AI notes the expired listing data for market sentiment

### 5.6 Fee Structure

| Action | Fee | Notes |
|--------|-----|-------|
| Listing | 0.5 emerald flat | Non-refundable listing fee |
| Sale | 2% of sale price | Deducted from seller proceeds |
| BIN Purchase | 1% of price | Paid by buyer |
| Cancel (before bids) | Free | — |
| Cancel (after bids) | 1 emerald | Penalty to prevent abuse |
| Simulated items | 0 | AI accounts take no cut |

---

## 6. AI Market Simulation Engine

### 6.1 Overview

The **DeepSeek AI Simulation Engine** is the heart of the dynamic market. It runs on a configurable timer (default: every 6 hours) and performs several tasks:

1. **Price Analysis** — Review recent price history and transaction volume
2. **Market Event Check** — Determine if an event should start or end
3. **Inventory Adjustment** — Simulate buying/selling of common items
4. **Rare Item Generation** — Roll for ultra-rare item appearances
5. **Stale Listing Review** — Re-evaluate items that haven't sold
6. **Report Generation** — Create a market snapshot for players
7. **Note Taking** — Write observations to the AI helper database

### 6.2 AI Prompt Template

The AI receives a structured prompt containing market data and responds with a JSON action plan.

**System Prompt:**

```
You are the Auction House AI for a Minecraft server. Your role is to simulate a 
living, breathing economy. You manage common items in the simulated inventory, 
generate rare items occasionally, trigger market events for flavor, and keep 
the market feeling alive.

You have access to:
1. Current market prices and trends
2. Player listing statistics
3. Active market events
4. Transaction history
5. Your own previous notes

You must respond ONLY with valid JSON that matches the expected action schema.
```

**User Prompt Template:**

```
=== MARKET STATUS ===
Current Date: {datetime}
Active Listings: {count}
    - Player items: {player_count}
    - Simulated items: {sim_count}
Transactions (last 24h): {tx_24h}
Total Volume (last 24h): {volume_24h} emeralds

=== PRICE SNAPSHOTS (last 6h) ===
{price_snapshots}

=== ACTIVE MARKET EVENTS ===
{active_events}

=== SIMULATED INVENTORY ===
{sim_inventory}

=== STALE LISTINGS ===
{stale_listings}

=== PREVIOUS AI NOTES ===
{previous_notes}

=== AI TASK ===
Please analyze the market and produce a JSON response with:

1. "market_assessment": Brief 1-2 sentence summary of market health
2. "price_adjustments": Array of {item_id, new_base_price, reason} for items whose prices should change
3. "stock_adjustments": Array of {item_id, action: "buy"|"sell", quantity, price} for simulated trades
4. "events": Array of {action: "start"|"end"|"continue", event_name, ...} or empty array.
   - Only start events that feel organic and fun.
   - Events should have hidden goals (e.g. "sell X units of Y to end event").
5. "rare_items_to_list": Array of rare items to generate (max 2 per cycle, usually 0).
   Each has: {item_id, count, price, enchantments[], lore[], rarity_tier, durability}
   - Ultra-rare (elytra): ~1% chance per cycle
   - Rare (enchanted gear): ~5% chance per cycle
   - Uncommon (good tools): ~15% chance per cycle
   - Most cycles: [] empty array
6. "stale_recommendations": Array of {listing_uuid, recommendation: "lower_price"|"remove", suggested_price}
7. "notes": Array of {category, content} for your helper database
8. "announcement": String or null — market announcement to broadcast

=== ITEM ENCHANTMENT POOL ===
Normal enchants: sharpness, protection, efficiency, unbreaking, fortune, 
  looting, power, fire_aspect, thorns, feather_falling, respiration, 
  aqua_affinity, depth_strider, swift_sneak, silk_touch, mending, soul_speed

Rarity enchantment rules:
- Common items: no enchants or 1 low-level enchant
- Uncommon: 1-2 enchants (max level 3)
- Rare: 2-3 enchants (max level 4)
- Epic: 3-4 enchants (can have level 5)
- Legendary: 3-5 enchants (any level)
- Mythic: 4-6 enchants (any level, can have incompatible combos via "curse" bypass)
- Cosmic Perfection: 5-7 enchants (all max level, can break normal rules)

=== LORE GENERATION RULES ===
Every simulated rare item should have flavor lore text (1-3 lines) that:
- Fits the Minecraft universe
- References the item's enchantments or purpose
- Is creative and immersive
- Uses the item's rarity tier in tone
- Examples:
  - "Forged in the heart of a dying star, this blade remembers the heat of creation."
  - "The ancient dwarves carved this pickaxe from bedrock itself. It has never known failure."
  - "Wrapped in dragon breath and starlight, this bow seeks only noble targets."

=== EVENT FREQUENCY RULES ===
Events have 4 rarity tiers. Respect these frequency caps:
- Small events: ~1 per 2-4 hours. 1.1-1.5x price, 1-2 items, 1-4h duration.
- Medium events: ~1 per 1-3 days. 1.5-2.5x price, 2-4 items, 6-24h duration.
- Rare events: ~1 per 1-3 weeks. 2.0-4.0x price, 3-6 items, 24-72h duration.
- Major events: ~1 per 1-3 months. 3.0-6.0x price, 4-8 items, 48-168h duration.

- Do NOT trigger the same rarity tier two cycles in a row.
- Small events can overlap with Medium events.
- Major events need ≥1 week of build-up notes in your AI helper DB first.
- Between Major events: minimum 30-day cooldown.

=== CRITICAL RULES ===
- Do NOT override player-listed prices directly. Only suggest price changes 
  to player items via "stale_recommendations" with a "lower_price" suggestion.
- Simulated items can be priced freely but must be reasonable.
- Events should be FUN and create interesting gameplay moments.
- Never generate the same rare item twice in a row.
- Keep the economy stable — don't crash or inflate prices wildly.
- Write at least one note per cycle to track your reasoning.
- See §8.2 of the spec for full event frequency details.
```

### 6.3 Simulation Cycle Flow

```
┌─────────────────────────────────────────────────┐
│               SIMULATION CYCLE                    │
│             (Every N minutes/hours)                │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  1. GATHER CONTEXT                                │
│     - Query all active listings                   │
│     - Get price history snapshots                 │
│     - Get active market events                    │
│     - Get simulated inventory state               │
│     - Get recent transaction history              │
│     - Get stale listings (no bids for >24h)       │
│     - Get previous AI notes                       │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  2. BUILD PROMPT                                  │
│     - Format all data into structured prompt      │
│     - Include enchantment pools & lore rules       │
│     - Attach rarity system reference               │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  3. CALL DEEPSEEK API                             │
│     - Send prompt to DeepSeek chat completions     │
│     - Parse JSON response                          │
│     - Validate response structure                  │
│     - Handle errors / retry                        │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  4. APPLY CHANGES                                 │
│     - Adjust simulated inventory prices/stocks     │
│     - Execute simulated trades                     │
│     - Start/end market events                      │
│     - Generate and list rare items                 │
│     - Send stale recommendations                   │
│     - Store AI notes                               │
│     - Broadcast announcement if provided           │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  5. RECORD PRICE SNAPSHOT                         │
│     - Log current prices to price_history          │
│     - Log simulation run to logs                   │
│     - Update AI helper notes                       │
└─────────────────────────────────────────────────┘
```

### 6.4 DeepSeek API Integration

**Endpoint:** `https://api.deepseek.com/v1/chat/completions`

**Configuration (in DATA/minecraft_manager_config.json additions):**

```json
{
  "deepseek_api_key": "...",
  "deepseek_model": "deepseek-chat",
  "deepseek_temperature": 0.8,
  "deepseek_max_tokens": 4096,
  "simulation_interval_minutes": 360,
  "simulation_enabled": true
}
```

**Retry Logic:**
- 3 retries with exponential backoff (2s, 4s, 8s)
- If all fail: log error, skip cycle, try again next interval
- If JSON parse fails: ask DeepSeek to fix ("Your response wasn't valid JSON. Please respond with ONLY valid JSON matching the schema.")
- Max 1 retry for JSON fix

### 6.5 Stale Item Re-Evaluation

Items that sit on the auction house with no activity for >24h (configurable) are flagged as "stale". The AI reviews stale items and recommends:

- **Lower price** — Item is overpriced, suggest a 10-30% reduction
- **Remove** — Item has no market demand, suggest seller pulls it
- **Feature** — Item is interesting but not getting attention, maybe an event could boost its category

The AI's recommendation is **not enforced** — it's sent as a suggestion to the player via `/tellraw`.

---

## 7. Rarity & Item Generation

### 7.1 Rarity Tier System (from sign_item.py)

| Tier | Probability | Color | Color Code | Quality Range |
|------|-------------|-------|------------|---------------|
| Garbage | 45% | Gray | §7 | 1-100 |
| Common | 25% | Gold | §6 | 1-100 |
| Uncommon | 15% | Green | §a | 1-100 |
| Rare | 9% | Blue | §9 | 1-100 |
| Epic | 4% | Light Purple | §d | 1-100 |
| Legendary | 1.5% | Red | §c | 1-100 |
| Mythic | 0.499% | Dark Purple | §5 | 1-100 |
| Cosmic Perfection | 0.001% | Rainbow | Random | Always 100 |

The **Quality Roll** (1-100) uses a weighted random where higher qualities are harder to achieve (sum of 1..n weights = n(n+1)/2, total = 5050).

### 7.2 Enchantment Assignment by Rarity

| Rarity | Enchant Count | Max Level | Notes |
|--------|---------------|-----------|-------|
| Garbage | 0 | — | No enchants |
| Common | 0-1 | 2 | Basic utility |
| Uncommon | 1-2 | 3 | Decent gear |
| Rare | 2-3 | 4 | Good gear |
| Epic | 3-4 | 5 | Great gear |
| Legendary | 3-5 | 5 | Can have "curse of binding" for meme value |
| Mythic | 4-6 | 5 | Can combine normally incompatible enchants |
| Cosmic Perfection | 5-7 | Max | All max level, breaks every rule |

### 7.3 Simulated Rare Item Generation

The AI decides when to generate rare items. The system rolls for each cycle:

- **Ultra-Rare (elytra, netherite gear):** 0.5-1.5% chance per cycle
- **Rare (enchanted diamond gear, tridents):** 3-7% chance
- **Uncommon (good iron gear, enchanted books):** 10-20% chance
- **None:** ~75% chance — most cycles add no rare items

When generated, the AI provides:
- Item ID and count
- Custom enchantments with levels
- Flavor lore (1-3 lines)
- Rarity tier (rolled via rarity system, but AI can bias upward for interesting items)
- Price (reasonable for the power level)
- Durability (for tools/armor, can be slightly used for realism)

### 7.4 Ultra-Rare Generation Example

```json
{
  "item_id": "minecraft:elytra",
  "count": 1,
  "price": 750,
  "rarity_tier": "Mythic",
  "enchantments": [
    {"id": "minecraft:unbreaking", "level": 3},
    {"id": "minecraft:mending", "level": 1}
  ],
  "lore": [
    "§5The last vestige of an ancient dragon that circled the End for millennia.",
    "§5its leather remembers the void between stars. It chose you."
  ],
  "durability": 432
}
```

---

## 8. Market Events System

### 8.1 Event Types

| Type | Description | Example |
|------|-------------|---------|
| `seasonal` | Weather/season-based | "Extreme Winter" boosts coal, wool, food |
| `shortage` | Supply disruption | "Mine Collapse" increases iron/stone prices |
| `surplus` | Oversupply | "Bountiful Harvest" drops crop prices |
| `discovery` | New vein found | "Ancient City Discovered" boosts exploration gear |
| `disaster` | Negative event | "Dragon Attack" boosts weapons/armor |
| `festival` | Celebration | "Builder's Festival" boosts building blocks |
| `raid` | Mob activity | "Zombie Siege" boosts weapons, food |

### 8.2 Event Frequency Scaling

Events are categorized by rarity/frequency so the market stays fresh without overwhelming players. The AI is instructed to respect these frequency caps per cycle.

| Rarity | Frequency | How Often | Example | Impact Scale |
|--------|-----------|-----------|---------|--------------|
| **Small** | Every few hours | ~1 per 2-4 hours | "Minor price dip in coal — stock normalizing" | 1.1-1.5x price, 1-2 items affected, 1-4h duration |
| **Medium** | Every few days | ~1 per 1-3 days | "Villager caravan arrived! Building materials in demand!" | 1.5-2.5x price, 2-4 items affected, 6-24h duration |
| **Rare** | Every few weeks | ~1 per 1-3 weeks | "Ancient city expedition! Elytra fragments spotted!" | 2.0-4.0x price, 3-6 items affected, 24-72h duration |
| **Major** | Every few months | ~1 per 1-3 months | "Ender Dragon awakened! All weapons and armor in crisis demand!" | 3.0-6.0x price, 4-8 items affected, 48-168h duration |

**Rules for the AI:**
- Do NOT trigger the same rarity tier two cycles in a row
- Small events can overlap with Medium events (but not two of the same tier)
- Major events **must** be preceded by at least 1 week of build-up notes in the AI helper DB
- Between Major events, enforce a minimum 30-day cooldown
- The AI can escalate a Small/Medium event into a higher tier if player activity and notes justify it
- Event frequency is *per active market* — if no events are active, the AI should prioritize starting one appropriate to the time since the last event

### 8.3 Event Mechanics

1. **Trigger:** AI decides to start an event, or admin triggers manually
2. **Announcement:** Broadcast via `/tellraw @a` with flavor text
3. **Effect:** Price multiplier on affected items (applied to simulated inventory base prices)
4. **Hidden Goal:** Event has a goal count (e.g., "sell 5000 coal")
5. **Progress:** Each sale of affected items increments `current_count`
6. **Resolution:** When goal is met OR event duration expires, prices normalize
7. **Resolution Announcement:** "The Extreme Winter has passed. Coal prices return to normal."
8. **Cooldown:** After an event ends, cooldown scales by event rarity (see §8.2):
   - Small: 6h cooldown
   - Medium: 24h cooldown
   - Rare: 72h cooldown
   - Major: 30d cooldown

### 8.4 Event Goal & Recovery

The hidden goal system works as follows:

```python
event.goal_count = int(simulated_stock * random.uniform(0.3, 0.8))
# Example: 2000 coal stock × 0.5 = 1000 coal goal
# Once 1000 coal units are sold through the AH:
# - Event ends
# - "The crisis has passed! Coal prices normalize."
# - Simulated stock drops by 50% (representing the buy-up)
# - Price reverts to base or slightly elevated (20% above base for 24h "hangover")
```

---

## 9. AI Helper Database (Notes & Categories)

### 9.1 Purpose

The AI helper database allows the DeepSeek AI to maintain persistent memory across simulation cycles. After each run, the AI writes notes categorized by topic. These notes are fed back into the next prompt so the AI can reference its past reasoning.

### 9.2 Table: `ai_notes`

```sql
CREATE TABLE IF NOT EXISTS ai_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,                           -- 'market_health','price_reasoning','event_idea','observation','recommendation','item_opinion'
    content         TEXT NOT NULL,                           -- The note content (freeform, AI-written)
    reasoning       TEXT,                                    -- Why the AI made this note (optional)
    related_item_id TEXT,                                    -- Optional: item this note is about
    related_event   TEXT,                                    -- Optional: event UUID
    importance      INTEGER NOT NULL DEFAULT 1,              -- 1-5, higher = more important
    created_at      TEXT NOT NULL,                           -- ISO 8601
    expires_at      TEXT                                    -- ISO 8601 (NULL = permanent)
);
```

**Indexes:**
```sql
CREATE INDEX idx_notes_category ON ai_notes(category);
CREATE INDEX idx_notes_importance ON ai_notes(importance);
CREATE INDEX idx_notes_created ON ai_notes(created_at);
```

### 9.3 Categories

| Category | Purpose | Example Content |
|----------|---------|-----------------|
| `market_health` | Overall market assessment | "Market is stable but coal is undervalued at 0.3 emeralds. Players listing at 0.5 indicate real value should be higher." |
| `price_reasoning` | Why prices were adjusted | "Increased iron base price from 1.5 to 1.8 due to increased player listing activity suggesting higher demand." |
| `event_idea` | Potential future events | "Consider a 'Miner's Festival' event if iron listings drop below 10 for 3 consecutive cycles." |
| `observation` | Noticed patterns | "Player 'Notch' has been buying all diamond gear — may be gearing up for the End." |
| `recommendation` | System improvements | "Should increase simulation frequency during peak hours (6-10 PM) as transaction volume is 3x higher." |
| `item_opinion` | Opinion on specific items | "Elytra #ah-abc123 is overpriced at 1000 emeralds given its Uncommon rarity. Suggest 600-700." |

### 9.4 Note Lifecycle

- Notes expire based on `expires_at` field
- AI can set a note as permanent (NULL `expires_at`)
- The system cleans expired notes weekly
- The last 50 non-expired notes (sorted by importance) are included in the AI prompt

---

## 10. Logging System

### 10.1 Log Directory

```
/home/deck/Documents/dev-yuniScripts/SCRIPTS/GAMES/minecraft_manager/AUCTIONHOUSE/logs/
```

### 10.2 Log Files

| File | Description | Format |
|------|-------------|--------|
| `auctionhouse.log` | Main operation log | Plain text, timestamped |
| `ai_simulations.json` | Every AI simulation cycle (full prompt + response) | JSONL (one JSON object per line) |
| `transactions_{YYYY-MM-DD}.json` | Daily transaction log | JSONL |
| `market_events.json` | All market event activations & resolutions | JSONL |

### 10.3 JSON Log Schema

Each log entry in JSON/JSONL files follows this schema:

```json
{
  "timestamp": "2026-06-05T14:30:00.000Z",
  "log_id": "uuid-v4",
  "type": "simulation_cycle",
  "subtype": "ai_run|price_change|event_start|event_end|rare_item|stale_review|note_added|error",
  "severity": "info|warn|error|debug",
  "message": "Human-readable description",
  "data": {},
  "source": "auctionhouse_ai|auctionhouse_main|system",
  "duration_ms": 1234
}
```

### 10.4 Verbose Logging Convention

Since these logs will be read back later for context, they should be **slightly more verbose** than necessary:

```json
{
  "timestamp": "2026-06-05T14:30:00.000Z",
  "type": "simulation_cycle",
  "subtype": "ai_run",
  "severity": "info",
  "message": "AI simulation cycle completed. 3 price adjustments, 1 event active, 2 simulated trades executed.",
  "data": {
    "prompt_tokens_used": 2847,
    "response_tokens_used": 1235,
    "api_cost_estimate": 0.012,
    "price_adjustments": [
      {"item_id": "minecraft:coal", "old_price": 0.5, "new_price": 0.45, "reason": "Minor downward trend observed - 4 cycles of decreasing volume"},
      {"item_id": "minecraft:iron_ingot", "old_price": 1.5, "new_price": 1.65, "reason": "Player listings increased 20% suggesting organic demand growth"}
    ],
    "events_active": [{"name": "extreme_winter", "progress": "342/5000 sold", "time_remaining": "18h 22m"}],
    "rare_items_generated": 0,
    "stale_items_reviewed": 7,
    "stale_recommendations": 2,
    "notes_added": 3
  },
  "source": "auctionhouse_ai",
  "duration_ms": 8432
}
```

### 10.5 Log Viewer Helper

A future utility script (`logs/reader.py`) can parse these logs to reconstruct market history. This is useful for debugging and for providing context to the AI on restart.

---

## 11. File Structure

```
AUCTIONHOUSE/
├── AuctionHouse-spec.md          ← This document
├── __init__.py                   ← Package init
├── ah_config.py                  ← Configuration loading & defaults
├── ah_logger.py                  ← Logging system (structured JSON logs)
├── ah_database.py                ← Database connection manager & schema init
├── ah_helper_db.py               ← AI notes & categories management
├── ah_core.py                    ← Core auction CRUD with atomic operations
├── ah_price_history.py           ← Price snapshot & trend analysis
├── ah_market_events.py           ← Market event system (start, end, track progress)
├── ah_item_gen.py                ← Rare item generation & enchantment assignment
├── ah_announcer.py               ← In-game announcement via RCON
├── ah_ai_engine.py               ← DeepSeek API integration & simulation loop
├── ah_reports.py                 ← Market report generation (weekly, on-demand)
├── ah_phooks.py                  ← Phooks event handlers with input validation
├── ah_admin.py                   ← Admin override & moderation module
├── data/
│   ├── auctionhouse.db3          ← SQLite database (created on first run)
│   └── backups/                  ← Database backups (created by ah_admin.py)
├── logs/
│   ├── auctionhouse.log          ← Plain text operational log
│   ├── ai_simulations.jsonl      ← AI simulation records
│   ├── transactions_YYYY-MM-DD.jsonl  ← Daily transaction logs
│   └── market_events.jsonl       ← Market event log
└── HELPERS/
    ├── ah.py                     ← Minescript client (\ah command, 14+ subcommands)
    ├── ah_protocol.py            ← Shared constants & message schemas
    └── README.md                 ← Deployment instructions

### Extensions Directory (`EXTENSIONS/`)

The Auction House supports dynamically loaded extensions for expanded simulation:

| Extension | Purpose | Hook |
|-----------|---------|------|
| `SIMULATED_PEOPLE` | 1–200 simulated personas with AI market decisions | `on_simulation_cycle_start` |
| `SIMULATED_CHAT`  | Player-to-persona chat (`\ah msg`, `\ah qmsg`) | Minescript command routing |
| `SIMULATED_ANNOUNCE` | Persona event announcements (`\ah sub`, `\ah announces`) | `on_simulation_cycle_end` |

Extensions are auto-discovered from `EXTENSIONS/` on startup and register their hooks with the `ah_plugin_registry`. Each extension lives in its own subdirectory under `EXTENSIONS/`.

### Integration with existing mc_manager:

The Auction House modules are loaded from `main.py`:

```python
# In main.py (new additions)
from AUCTIONHOUSE import ah_core, ah_phooks, ah_database, ah_ai_engine

# On startup:
ah_database.initialize()
ah_ai_engine.start_simulation_timer()
```

The `PhooksClient` in `main.py` gets additional listen/emit events:

```python
# Updated Phooks events
PHOOKS_EVENTS_LISTEN = [
    "sign_request",
    "ah_list", "ah_bid", "ah_buy", "ah_remove", "ah_query",
    "ah_sub", "ah_unsub", "ah_subs", "ah_announces"
]
PHOOKS_EVENTS_EMIT = [
    "sign_response",
    "ah_list_response", "ah_bid_response", "ah_buy_response",
    "ah_remove_response", "ah_query_response", "ah_announce",
    "ah_sub_response", "ah_unsub_response", "ah_subs_response",
    "ah_announces_response"
]
```

---

## 12. Implementation Roadmap

### Phase 1: Foundation (Complete)
- [x] System design & spec document
- [x] Database schema creation (7 tables, 15 seed items)
- [x] Core data models (Python classes)

### Phase 2: Core Auction Logic (Complete)
- [x] `ah_database.py` — DB init, connection pool, CRUD helpers
- [x] `ah_core.py` — List, bid, buy, cancel operations
- [x] `ah_logger.py` — Structured logging
- [x] Unit tests for core operations (verified: all CRUD ops, event system, item gen, snapshots)

### Phase 3: Phooks Integration (Complete)
- [x] `ah_phooks.py` — Event handlers for all AH events (5 handlers + dispatcher)
- [x] Update `main.py` to register AH events (event loop dispatch + stdio commands)
- [x] Update `Phooks.py` with new event declarations (11 listen events, 13 emit events incl. announce)
- [x] Minescript client commands (`HELPERS/ah.py` — `\ah list|bid|buy|mine|cancel|search|report|help|msg|qmsg|sub|unsub|subs|announces`)

### Phase 4: AI Engine (Complete)
- [x] `ah_config.py` — Config singleton with DeepSeek API settings, timers, fees, bounds
- [x] `ah_ai_engine.py` — Prompt builder, DeepSeek API calls, retry with backoff, SimulationScheduler
- [x] Retry logic (3 retries + overlap check + JSON fix strategy)
- [x] Simulation timer (6h default, 10-stage cycle, background thread)

### Phase 5: Market Simulation (Complete)
- [x] `ah_item_gen.py` — 8-tier rarity, enchant rules, flavor lore, ultra-rare presets
- [x] `ah_market_events.py` — 4-tier events, hidden goals, cooldown enforcement, price multiplier stacking
- [x] `ah_price_history.py` — Price snapshots, trend analysis, stale detection
- [x] `ah_helper_db.py` — AI notes (6 categories), auto-expiry, build-up checking

### Phase 6: Player Experience (Complete)
- [x] `ah_announcer.py` — RCON tellraw/title: broadcasts, outbid/sold/won/event announcements
- [x] `ah_reports.py` — Weekly market reports, player activity reports, chat formatting
- [x] Minescript command file (`HELPERS/ah.py` — full `\ah` client for all player operations)
- [x] Stale item notification system (AI reviews stale listings each cycle, notifies via notes)
- [x] In-game UI (color-coded tellraw responses for all commands; title broadcasts for Rare/Major events)

### Phase 7: Polish & Testing (Complete)
- [x] Integration tests with Phooks hub (event dispatch verified in main.py wiring)
- [x] Atomic operations (TOCTOU race condition prevention in place_bid, buy_now, cancel_listing, expire_listings — uses atomic UPDATE with WHERE clause + rowcount check)
- [x] Input validation (ah_phooks.py: player name regex, UUID format, item ID format, price bounds — all incoming requests validated before core processing)
- [x] Item duplication prevention (listed via duplicate item+player check + atomic SQL locking)
- [x] Player banning system (ah_admin.py: ban/unban/check with expiry, bans table in DB)
- [x] Database backup (ah_admin.py: timestamped auto-backup to data/backups/)
- [x] Admin overrides (force end events, override prices, force-remove listings, reset market)
- [x] Market health monitoring (ah_admin.py: get_full_stats with listing, event, inventory stats)
- [x] Performance optimization (DB indexes on all queried columns, pagination with LIMIT/OFFSET, lazy imports in main.py event loop)
- [x] Final documentation (spec, API.md, commands.info, HELPERS/README.md, deployment instructions)

---

## Appendix A: Minescript Client Commands

The minescript client script is `HELPERS/ah.py`. Copy this file to `~/.minecraft/minescript/ah.py` to make `\ah` available in-game.

Since minescript commands are resolved by filename, `ah.py` → `\ah`.

### Installation
```bash
cp AUCTIONHOUSE/HELPERS/ah.py ~/.minecraft/minescript/ah.py
```

### Full Command Reference

| Command | Description |
|---------|-------------|
| `\ah list <price> [bin_price] [hours]` | List held item on AH |
| `\ah bid <uuid> <amount>` | Bid on a listing |
| `\ah buy <uuid>` | Buy-It-Now |
| `\ah mine` | View your listings |
| `\ah cancel <uuid>` | Cancel your listing |
| `\ah search [item]` | Search active listings |
| `\ah details <uuid>` | View full listing details (lore, enchants, stats) |
| `\ah history <uuid>` | View bid history for a listing |
| `\ah purchases` | View items you've bought |
| `\ah sales` | View items you've sold |
| `\ah pricecheck <item>` | Check recent market prices for an item |
| `\ah report` | Request weekly market report |
| `\ah help [command]` | Show command help |

### Command Flow Example (`\ah list`):

1. Player holds item in main hand, emeralds in off-hand
2. Player types `\ah list 10` (start price 10, no BIN)
3. Or `\ah list 10 25` (start 10, BIN 25)
4. Or `\ah list 10 25 48h` (start 10, BIN 25, 48h duration)
5. Minescript opens a confirmation GUI (or echoes a prompt)
6. Player confirms → UDP packet sent to mc_manager via Phooks
7. Manager processes, deducts item, creates listing, confirms

---

## Appendix B: RCON Command Reference (Server-Side)

These RCON commands are used by the Auction House system to interact with the Minecraft server:

```python
# Balance checks (simulate clear to check funds without removing)
rcon_command(f"clear {player} minecraft:emerald 0")  
# Response: "Could not clear 0 emeralds... Hmm, that means player has emeralds"
# Response: "No items were removed..." — no emeralds

# Item removal (deduct payment)
rcon_command(f"clear {player} minecraft:emerald {amount}")

# Item delivery
rcon_command(f"give {player} {item_id}[{components_str}] {count}")

# Announcements
rcon_command(f'tellraw @a {json.dumps(announcement_msg)}')

# Price/availability query
rcon_command(f"data get entity {player} Inventory")
```

---

## Appendix C: Error Codes

| Code | Meaning | Recovery |
|------|---------|----------|
| `AH_001` | Item already has an active listing | Remove old listing first |
| `AH_002` | Max listings reached (5) | Cancel one first |
| `AH_003` | Insufficient emeralds | Requires more emeralds |
| `AH_004` | Item not found in inventory | Verify item is in hotbar |
| `AH_005` | Bid too low | Must exceed current bid by 10% |
| `AH_006` | Auction expired | Item returned to seller |
| `AH_007` | Not your listing to cancel | Only seller can cancel |
| `AH_008` | AI simulation failed | Check logs, retry next cycle |
| `AH_009` | DeepSeek API key not configured | Set in config.json |
| `AH_010` | Cannot bid on your own listing | Find other items to bid on |
| `AH_011` | BIN price not set | This listing is auction-only |
| `AH_012` | Market event has no effect on this item | Different items affected |
| `AH_013` | Item data too large for Phooks packet | Compress NBT data |
| `AH_014` | Listing not found | UUID may be invalid or expired |

---

## Appendix D: Database ER Diagram (Text)

```
┌──────────────────┐       ┌──────────────────────┐
│  auction_listings │──┐    │  transaction_history  │
├──────────────────┤  │    ├──────────────────────┤
│ listing_uuid(PK) │──┴──►│ transaction_uuid(PK)  │
│ seller_name      │       │ listing_uuid(FK)      │
│ item_id          │       │ transaction_type      │
│ item_count       │       │ actor_name            │
│ item_nbt         │       │ item_id               │
│ signed_name      │       │ item_count            │
│ rarity           │       │ price                 │
│ cert_hash        │       │ created_at            │
│ is_simulated     │       └──────────────────────┘
│ start_price      │
│ current_bid      │       ┌──────────────────────┐
│ buy_now_price    │       │  price_history        │
│ highest_bidder   │       ├──────────────────────┤
│ bids_count       │       │ item_id               │
│ status           │       │ price_avg/min/max     │
│ listed_at        │       │ listing_count         │
│ expires_at       │       │ volume_sold           │
│ sold_at          │       │ snapshot_at           │
│ sold_price       │       └──────────────────────┘
│ ai_weight        │
│ stale_since      │       ┌──────────────────────┐
│ extra_meta       │       │  market_events        │
└──────────────────┘       ├──────────────────────┤
                            │ event_uuid(PK)        │
┌──────────────────┐       │ event_name             │
│ simulated_inventory│      │ event_title            │
├──────────────────┤       │ event_flavor           │
│ item_id          │       │ event_type             │
│ category         │       │ affected_items         │
│ current_stock    │       │ price_multiplier      │
│ max_stock        │       │ demand_boost          │
│ base_price       │       │ trigger_condition      │
│ volatility       │       │ goal_count            │
│ trend_direction  │       │ current_count         │
│ trend_strength   │       │ is_active             │
│ last_updated     │       │ started_at            │
└──────────────────┘       │ ended_at              │
                            └──────────────────────┘
┌──────────────────┐
│  ai_notes         │       ┌──────────────────────┐
├──────────────────┤       │  player_balances      │
│ category         │       ├──────────────────────┤
│ content          │       │ player_name(PK)       │
│ reasoning        │       │ balance               │
│ related_item_id  │       │ lifetime_earned       │
│ related_event    │       │ lifetime_spent        │
│ importance       │       │ listings_count        │
│ created_at       │       │ purchases_count       │
│ expires_at       │       │ last_updated          │
└──────────────────┘       └──────────────────────┘
```

---

*End of Auction House Specification v1.0*
