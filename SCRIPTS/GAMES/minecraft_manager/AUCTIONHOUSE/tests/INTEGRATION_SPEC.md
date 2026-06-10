# Full Integration Simulation — Spec

## 1. Purpose

Run a multi-round integration test of the entire Auction House system
**with the Simulated People extension loaded and fully active**. This
is not a unit test—it is a live-fire endurance test that exercises every
major subsystem together:

| Component | What gets tested |
|-----------|-----------------|
| AH Core (`ah_core.py`) | `list_item`, `buy_now`, `cancel_listing`, `expire_listings`, `query_listings` |
| Database (`ah_database.py`) | Schema creation, transaction recording, index usage |
| Plugin Registry (`ah_plugin_registry.py`) | Hook registration, firing, error isolation |
| SP Extension (`__init__.py`) | `on_load`, `_on_simulation_cycle_start`, `_on_listing_created`, `_on_purchase`, `_on_cancel` |
| Persona Profiles (`sp_profile.py`) | `generate_persona`, `spawn_initial_population`, `activate_persona`, `deactivate_persona` |
| Persona Behavior (`sp_behavior.py`) | `run_persona_tick`, `process_persona` (buy, save, debt-pay, nothing) |
| Health System (`sp_health.py`) | `process_health_tick`, `modify_health`, autonomous survival (forage, hunt, drink) |
| World System (`sp_world.py`) | `generate_world`, `get_area_count` |
| Weather (`sp_weather.py`) | `init_weather`, `update_weather`, per-area state |
| Movement (`sp_movement.py`) | `process_movement_tick` |
| Ecosystem (`sp_ecosystem.py`) | `init_ecosystem`, `process_ecosystem_tick`, `persona_forage`, `persona_hunt`, `persona_drink` |
| Resources (`sp_resources.py`) | `spawn_resource_nodes`, `process_node_regrowth` |
| Crafting (`sp_crafting.py`) | `process_crafting_queue` |
| World Events (`sp_world_events.py`) | `run_world_event_tick` |
| Inventory / Items (`sp_items.py`) | `give_item`, `remove_item`, `count_item`, `consume_item`, `auto_consume`, `seed_item_defs` |
| Item Cache (`sp_item_cache.py`) | `register_item` with NBT, `resolve_item_for_market`, `log_item_removal`, `query_trash` |
| Factions, Claims, Wars | `process_guild_tick`, `process_claims_tick`, `process_wars_tick` |
| Health → Ecosystem integration | Personas forage/hunt when hungry, drink when thirsty, rest when exhausted |

## 2. Test Structure

### 2.1 Rounds (ticks)

The simulation runs **36 rounds** (configurable via `SIMULATION_ROUNDS`).

Each round = 1 simulation tick for the SP extension, which means:

1. **Player listings phase** (rounds 1, 7, 13, 19, 25, 31)
   - 2–4 "player" listings are created with random basic resources
     (coal, iron, diamond, logs, stone, wheat)
   - 1–2 "player" listings are created with tools/weapons **with NBT data**
     (diamond_sword with sharpness V, diamond_pickaxe with efficiency IV,
     iron_axe, bow with power III, etc.)
   - Items use `item_nbt` JSON strings with enchantments/durability

2. **Simulation tick** (every round)
   - The SP extension's `_on_simulation_cycle_start` hook fires
   - This runs: world ensure → persona spawn → weather update →
     world events → movement → **persona behavior (buy/save)** →
     health → ecosystem → crafting → resource regrowth → decay

3. **Verification phase** (every 6 rounds)
   - Count listings sold vs total
   - Check persona finances (some should have spent money)
   - Check trash database for discard/consume entries
   - Check item cache has NBT data preserved
   - Query recent transactions
   - No exceptions/errors logged during the tick

### 2.2 Error Recovery

If **any** exception occurs during a round:

1. The error is logged with full traceback
2. The error is analyzed to determine the root cause
3. The codebase is patched to fix the issue
4. The **entire simulation restarts from round 0**
5. This repeats until 36 rounds complete with zero errors

### 2.3 Persona Interactions Tested

| Persona Action | What triggers it | How tested |
|---------------|-----------------|------------|
| Buy urgent need | Need with urgency ≥ 7 | Verify balance decreased, need fulfilled |
| Impulse buy | Random roll + archetype impulse | Verify transaction recorded |
| Nothing (save) | Below savings goal | Verify income credited |
| Debt payment | Has debt + sufficient balance | Verify debt decreased |
| Forage/hunt | Hunger < 40 (health system) | Verify food stat didn't drop to 0 |
| Drink water | Thirst < 35 (health system) | Verify hydration stat didn't drop to 0 |
| Move biome | Health system's autonomous survival | Verify location changed |
| Buy player-listed item | Player listing matches need | Verify listing sold to persona |
| Buy with NBT item | Player listing with NBT | Verify NBT preserved in cache |

## 3. Configuration Changes

### 3.1 New `ah_config.py` setting: `reset_on_start`

Add to `_DEFAULTS` and the `Config` dataclass:

```python
"reset_on_start": False  # If True, wipe all data on next boot
```

When `reset_on_start = True` on startup:
1. Drop all AH core tables (auction_listings, transaction_history, etc.)
2. Drop all SP extension tables (ext_sp_*)
3. Log the wipe
4. Set `reset_on_start` back to `False` in the config file

This gives a clean-slate "factory reset" for the entire system.

### 3.2 Test overrides

The integration test overrides these config values for faster cycling:

```python
TEST_CFG_OVERRIDES = {
    "persona_pool_size": 25,       # Smaller population for fast init
    "max_active_personas": 15,     # Fewer active personas per tick
    "tick_interval_minutes": 1,    # Not used directly, for logging
    "min_balance_for_purchase": 1, # Allow cheaper purchases
    "ecosystem_enabled": True,     # Test ecosystem integration
    "debug_logging": True,         # Verbose logging for diagnosis
    "max_listings_per_player": 10, # Allow more listings in test
}
```

## 4. Files Modified

| File | Change |
|------|--------|
| `ah_config.py` | Add `reset_on_start` to `_DEFAULTS` and `Config` |
| `ah_database.py` | Add `reset_database()` method for clean wipe |
| `tests/test_integration_full.py` | **NEW** — The full 36-round simulation |
| `EXTENSIONS/__init__.py` | (Optional) Add reset awareness for extension tables |

## 5. Success Criteria

- [x] 36 rounds complete with zero exceptions
- [x] At least 1 persona purchase succeeded (listing marked sold)
- [x] At least 1 NBT-laden item was registered in the item cache
- [x] Trash database has at least 1 entry (from remove_item or consume_item)
- [x] Persona finances show spending (balance decreased for some)
- [x] No FOREIGN KEY constraint violations
- [x] No infinite loops or deadlocks
- [x] reset_on_start properly wipes all data
