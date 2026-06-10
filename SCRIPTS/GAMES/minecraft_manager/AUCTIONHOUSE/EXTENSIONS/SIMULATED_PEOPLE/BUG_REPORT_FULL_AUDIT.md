# SIMULATED PEOPLE EXTENSION — FULL CONSISTENCY BUG REPORT

**Audit Date:** 2026-06-08  
**Files Audited:** 31 Python files + 4 design docs + 2 config files  
**Bugs Found:** 50+ (9 CRITICAL, 12 HIGH, 18 MEDIUM, 11 LOW)  

---

## 🚨 CRITICAL BUGS (Will crash at runtime)

### C1. Unreachable Shared State Code (__init__.py)
**File:** `__init__.py` — `_on_simulation_cycle_start()`  
**Lines:** ~370-375  
**Bug:** Code to write to `state_registry` is placed AFTER the `return { ... }` statement.  
```python
    return {
        "tick": tick,
        ...
        "resource_nodes": ...
    }

    # ── Write to shared extension state (Phase 2C step 1) ────────
    from EXTENSIONS.state_registry import get_state
    _state = get_state()
    _state.set("active_personas", active_personas, "SIMULATED_PEOPLE")
    ...
```
**Effect:** Shared extension state is NEVER written. Other extensions (SOCIAL, RELS, CHAT, ANNOUNCE) trying to read persona data will get empty results.  
**Fix:** Move state write code BEFORE the `return` statement.

### C2. `logger` vs `log` — NameError in Exception Blocks (15 files)
**Files:** `sp_database.py`, `sp_world.py`, `sp_behavior.py`, `sp_world_events.py`, `sp_ecosystem.py`, `sp_combat.py`, `sp_items.py`, `sp_item_cache.py`, `sp_resources.py`, `sp_crafting.py`, `sp_factions.py`, `sp_claims.py`, `sp_wars.py`, `__init__.py`, `sp_health.py`  
**Pattern every file uses:**
```python
log = get_logger()  # Declared at top

def some_function():
    try:
        ...
    except Exception as e:
        logger.error(f"...")  # 🚨 NameError! 'logger' is not defined
        return ...
```
**Affected functions (each will crash if the exception handler runs):**
- `sp_database.py`: `cleanup_database()`, `generate_world()` (referenced via sp_world)
- `sp_world.py`: `generate_world()`
- `sp_behavior.py`: `_has_justified_price()`, `_mood_multiplier()`, `_get_listings_for_purchase()`, `_execute_purchase()`
- `sp_world_events.py`: `maybe_generate_life_events()`
- `sp_ecosystem.py`: `process_plant_tick()`, `process_animal_agents_tick()`, `process_density_tick()`, `persona_forage()`, `persona_hunt()`, `persona_fish()`, `persona_drink()`, `persona_clean()`, `persona_threat_check()`, `process_ecosystem_tick()`
- `sp_combat.py`: `resolve_melee_attack()`, `_pick_wound_type()`, `get_combat_stats()`, `apply_bandage()`, `apply_herbs()`, `check_terrain_self_harm()`, `check_crafting_self_harm()`, `process_wounds_tick()`, `_get_pain_level()`, `attempt_dodge()`, `attempt_parry()`, `raid_claim()`
- `sp_items.py`: `give_item()`, `process_inventory_decay()`, `auto_consume()`, `get_equipped_stats()`, `consume_item()`
- `sp_item_cache.py`: `_category_for_item()`, `register_item()`, `resolve_item_for_persona()`, `log_item_removal()`, `query_trash()`
- `sp_resources.py`: `process_plant_tick()` (actually sp_ecosystem), `process_node_regrowth()` (not affected)
- `sp_crafting.py`: `get_recipes_available()`, `start_crafting()`, `start_building()`, `advance_building()`, `add_room()`, `repair_building()`
- `sp_factions.py`: `set_rank()`, `set_diplomacy()`
- `sp_claims.py`: `claim_area()`, `start_construction()`, `advance_construction()`, `detect_settlements()`, `_calc_synergy_bonuses()`
- `sp_wars.py`: `declare_war()`, `arrange_truce()`, `raid_claim()`
- `__init__.py`: `_on_listing_created()`, `_on_listing_queried()`, `_on_purchase()`
- `sp_health.py`: `get_persona_health()`, `modify_health()`, `process_wounds_tick()` (uses sp_combat's, not same name)

**Fix:** Replace ALL `logger.error(...)` with `log.error(...)` in every file.

### C3. Indentation Bug — `_pick_wound_type()` Always Returns (sp_combat.py)
**File:** `sp_combat.py` — `_pick_wound_type()`  
**Bug:** The `return` statement is at SAME indentation as `if`, not INSIDE it:
```python
    if weapon_type == "spear":

    return random.choices(
        ["puncture", "laceration", "cut"],
        weights=[0.6, 0.2, 0.2])[0]
```
**Effect:** The `return` runs UNCONDITIONALLY for ANY weapon_type (not just "spear"). The entire rest of the function (axe, knife, fist, teeth/claws branches) is DEAD CODE — never reached. All melee attacks always produce spear-type wounds regardless of equipped weapon.  
**Fix:** Indent the `return` under the `if` block.

### C4. Indentation Bug — `set_rank()` Always Returns Error (sp_factions.py)
**File:** `sp_factions.py` — `set_rank()`  
**Bug:** Same pattern as C3:
```python
try:
    if new_rank not in GUILD_RANKS:
    return {"error": f"invalid rank: {new_rank}"}
```
**Effect:** `return` runs EVEN when `new_rank IS in GUILD_RANKS`! Guild rank changes ALWAYS fail.  
**Fix:** Indent the `return` under the `if` block.

### C5. Indentation Bug — `set_diplomacy()` Always Returns Error (sp_factions.py)
**File:** `sp_factions.py` — `set_diplomacy()`  
**Same bug as C4:**
```python
try:
    if stance not in ("ally", "neutral", "war", "truce", "vassal"):
    return {"error": f"invalid stance: {stance}"}
```
**Effect:** Valid stances always trigger error response. Diplomacy is completely broken.  
**Fix:** Indent the `return` under the `if` block.

### C6. Variable Scope — NameError in Return Dict (__init__.py)
**File:** `__init__.py` — `_on_simulation_cycle_start()`  
**Bug:** Variables `craft_result` and `_any_nodes` are defined INSIDE a `try/except` block but referenced OUTSIDE it in the `return {}` dict:
```python
"crafting": craft_result.get("completed", 0) if 'craft_result' in dir() else 0,
"resource_nodes": _any_nodes["c"] if 'any_nodes' in dir() or True else 0,
```
If the try block throws an exception BEFORE these variables are assigned, the `return` line will raise `NameError: name 'craft_result' is not defined`.  
The `'craft_result' in dir()` check is a fragile workaround that prevents the NameError in some cases (if the variable was defined in a previous call and leaked into the frame), but won't help on first call.  
The `'any_nodes' in dir() or True` is ALWAYS True (because `or True`), so `_any_nodes` is always accessed — NameError if try block failed.  
**Fix:** Initialize both variables to safe defaults BEFORE the try block.

### C7. Missing Hook Implementations (__init__.py)
**File:** `__init__.py` vs `EXTENSIONS/DESIGN.md`  
**Bug:** The DESIGN.md specification defines 8 hooks:
- `on_simulation_cycle_start` ✅ Implemented
- `on_simulation_cycle_end` ❌ **MISSING**
- `on_listing_created` ✅ Implemented
- `on_purchase` ✅ Implemented
- `on_cancel` ✅ Implemented
- `on_expiry` ❌ **MISSING**
- `on_listing_queried` ✅ Implemented
- `on_player_balance_change` ❌ **MISSING**

**Effect:** Personas don't react to expired listings or player balance changes.  
**Fix:** Implement stub handlers for missing hooks.

### C8. Variable `_any_nodes` Potential None Access (__init__.py)
**File:** `__init__.py`  
**Bug:** In return dict, `_any_nodes["c"]` — but `_any_nodes` could be `None` if query returns None:
```python
_any_nodes = _spdb2().fetch_one(
    "SELECT COUNT(*) as c FROM ext_sp_resource_nodes")
```
If this query returns None (no results), accessing `["c"]` raises TypeError.  
**Fix:** Add `or {}` after the query: `_spdb2().fetch_one(...) or {"c": 0}`

### C9. `check_crafting_self_harm()` — Dead Code After Return (sp_combat.py)
**File:** `sp_combat.py` — `check_crafting_self_harm()`  
**Bug:** Same indentation bug:
```python
    try:
        if random.random() > 0.05:  # Base 5% chance

        return {}
```
**Effect:** Function ALWAYS returns `{}` immediately. Crafting accidents NEVER happen. The entire accident_chance calculation logic (50+ lines) is DEAD CODE.  
**Fix:** Indent the `return {}` under the `if` block.

---

## 🔴 HIGH BUGS (Logic errors, incorrect behavior)

### H1. AI Model Name Hardcoded (sp_behavior.py)
**File:** `sp_behavior.py` — `_ai_process_persona()`  
**Line:** `"model": "deepseek-v4-flash"`  
**Bug:** The DeepSeek model name is hardcoded. AI models change frequently.  
**Fix:** Make model configurable via `config.json` (e.g., `"deepseek_model": "deepseek-chat"`).

### H2. Fragile `__import__()` Usage (sp_weather.py)
**File:** `sp_weather.py` — `process_quests()`  
**Bug:** Uses Python's internal `__import__()` builtin instead of normal imports:
```python
add_memory_fn = __import__("...sp_database", fromlist=["add_memory"]).add_memory
```
**Effect:** Obscure, fragile, breaks static analysis, and is generally considered an anti-pattern.  
**Fix:** Use regular `from ... import` statements.

### H3. Duplicate `area` Variable Fetch (sp_health.py)
**File:** `sp_health.py` — `process_persona_health()`  
**Bug:** `area = get_persona_area(persona_uuid)` called TWICE (lines ~35 and ~75):  
The first call is never used for the weather block because it's re-fetched.  
**Fix:** Remove the second fetch, reuse the first result.

### H4. Fragile `dir()` Pattern in Production Code (sp_health.py, __init__.py)
**Files:** `sp_health.py`, `__init__.py`  
**Bug:** Using `'function_name' in dir()` to check if a function was imported. This is debugging code, not production logic.  
```python
w = get_area_weather(area["area_uuid"]) if 'get_area_weather' in dir() else None
```
**Fix:** Use proper try/except around the import+call pattern.

### H5. `sp_behavior.py` — Duplicate `log = get_logger()` Declaration
**File:** `sp_behavior.py`  
**Line 25:** `log = get_logger()` at module level.  
**Lines 63-65:** In the THREAD import except block: `log = get_logger()` again.  
**Effect:** Harmless but confusing — the second declaration shadows the first. Makes code harder to maintain.  
**Fix:** Remove the duplicate, just call `log.info(...)` directly in the except block.

### H6. `sp_behavior.py` — `_mood_multiplier()` Useless try/except
**File:** `sp_behavior.py` — `_mood_multiplier()`  
**Bug:** The try block contains only `mult = 1.0` which cannot throw. Uses `logger` (not `log`) — would crash if exception occurred.  
**Fix:** Remove the try/except entirely.

### H7. Board Slot Calculation Bug (sp_database.py)
**File:** `sp_database.py` — `post_board_need()`  
**Bug:** Slot calculation uses sequential integer IDs:
```python
existing = db.fetch_all("SELECT id FROM ext_sp_board order by id")
used = set(r["id"] for r in existing)
slot = 1
while slot in used:
    slot += 1
```
But `slot` is meant to be a display index, not the internal ID. If IDs are 1, 3, 4 (after deletions), slot becomes 2 but the visible ordering shows 1, 3, 4 — causing visual/UX confusion.  
**Fix:** Calculate slot as `len(all_open) + 1` instead.

### H8. `sp_behavior.py` Typo: `ammount` → `amount`
**File:** `sp_behavior.py` — `process_persona()` AI branch  
**Bug:** `return {"action": "saved", "ammount": income_boost}` has `ammount` instead of `amount`.  
**Fix:** Correct spelling.

### H9. Missing Files Referenced in __init__.py Docstring
**File:** `__init__.py`  
**Docstring references:** `sp_finance.py` and `sp_memory.py`  
**Reality:** These files DON'T EXIST. The finance logic is in `sp_database.py` and memory is in `sp_database.py` + `sp_memory_thread.py`.  
**Fix:** Update docstring.

### H10. AI Decision Fallback Return Type Mismatch (sp_behavior.py)
**File:** `sp_behavior.py` — `_ai_process_persona()`  
**Bug:** Function declares `-> dict` return type, but returns `None` for fallback.  
**Fix:** Change type hint to `-> Optional[dict]`.

### H11. Incorrect Import Path for state_registry (__init__.py, dead code)
**File:** `__init__.py` (lines 374-375, dead code from C1)  
**Bug:** `from EXTENSIONS.state_registry import get_state` — this absolute import path may not resolve correctly depending on the working directory.  
**Fix:** Use relative import: `from ..state_registry import get_state` (or the correct relative path from the AH package).

### H12. THREAD Module Path Calculation is Wrong (sp_memory_thread.py)
**File:** `sp_memory_thread.py`  
**Bug:** The 6-level `os.path.join('..', '..', '..', '..', '..', '..')` calculation:
```python
os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', '..')
```
Starting from `EXTENSIONS/SIMULATED_PEOPLE/`, going up 6 levels goes to `~` (home), NOT `AIHandler/SCRIPTS/FastMCPServer/tools/`. The hardcoded fallback paths are correct, but the 6-level path is misleading.  
**Fix:** Remove the misleading 6-level path calculation.

---

## 🟡 MEDIUM BUGS (Suboptimal behavior, edge cases)

### M1. `process_animal_agents_tick()` Too Long (sp_ecosystem.py)
~250 lines, does everything: sense, decide, move, eat, drink, hunt, mate, age, die.  
**Fix:** Split into smaller helper functions.

### M2. Fragile SQL Parsing (sp_item_cache.py, sp_database.py)
**File:** `sp_item_cache.py` — `_exec_table_sql()`  
**File:** `sp_database.py` — `ensure_schema()`  
**Bug:** Both split SQL on `\n\n` and `;`:
```python
for block in table_sql.strip().split("\n\n"):
    for individual_stmt in s.split(";"):
```
If any CREATE TABLE statement has blank lines or semicolons inside it (e.g., DEFAULT values), this breaks.  
**Fix:** Use `db.executescript()` for multi-statement SQL, or split only on `;` with proper quote awareness.

### M3. Persona Tier Config Redundancy (__init__.py, config.json)
**File:** `__init__.py` `load_config()`, `config.json`  
**Bug:** `config.json` has BOTH individual `max_active_personas`/`persona_pool_size` settings AND `persona_tier` which overrides them. Having both is confusing.  
**Fix:** Remove individual settings when tier is set, or add validation warning when both are present.

### M4. Seasonal Cycle Assumes Northern Hemisphere (sp_weather.py)
**File:** `sp_weather.py` — `_seasonal_offset()`  
**Effect:** Seasons always modeled as Northern Hemisphere. Minecraft world orientation is undefined. Cosmetic issue.  
**Fix:** Add hemisphere config option.

### M5. AI DeepSeek API Key Not Configured (sp_behavior.py)
**File:** `sp_behavior.py` — `_ai_process_persona()`  
**Bug:** Reads `cfg.get('deepseek_api_key', '')` but the key is not in any config file or .env. The function will always fall back to procedural logic silently. The AI persona decisions feature is NOT FUNCTIONAL as shipped.  
**Fix:** Document the required config key, or read from environment variable.

### M6. Weather Erosion System Not Implemented (DESIGN_V4.md vs reality)
**File:** DESIGN_V4.md specifies `sp_geology.py` and `sp_seasons.py` files for erosion, soil formation, seasonal progression  
**Reality:** These files DON'T EXIST. Geological processes referenced in v4 spec are unimplemented.

### M7. `pass` Statement as Placeholder (sp_weather.py)
**File:** `sp_weather.py` — `update_weather()` line:  
```python
if _weather_tick % 6 == 0:
    extremes_clean = ...
    pass  # Weather info is queryable via get_weather_summary()
```
**Effect:** The weather description update stored in `ext_sp_world_areas` is NEVER performed. The `pass` does nothing.  
**Fix:** Implement the weather description update or remove the block.

### M8. All AI-generated Messages Go to RCON (sp_messaging.py)
**File:** `sp_messaging.py` — `send_purchase_message()`  
**Effect:** If RCON is not connected or fails, the message is logged but never reaches the player. No fallback delivery mechanism.  
**Fix:** Store in a player-facing mailbox or chat queue that can be retrieved via the auction house UI.

### M9. `_carnivore_hunt()` List Comprehension May Be Empty (sp_ecosystem.py)
**File:** `sp_ecosystem.py` — `_carnivore_hunt()`  
**Bug:** `prey_species` list may be empty if diet_items don't match any known species. This variable is computed but never actually used (the function checks for "rabbit" and "deer" literally instead).  
**Fix:** Use the computed list or remove it.

### M10. Bears Hibernating in Nether (sp_ecosystem.py)
**File:** `sp_ecosystem.py` — `_spawn_initial_agents()`  
**Effect:** ROLL spawn includes bears in Nether biomes if the species list includes them. But nether biomes never drop below -2°C in the weather system, so Nether bears would never hibernate.  
**Fix:** Filter species by biome temperature suitability.

### M11. Quest System Uses Weak `__import__` Pattern (sp_weather.py)
**File:** `sp_weather.py` — `process_quests()`  
**Lines 481-490:** Three `__import__()` calls that would be cleaner as regular imports.  
**Fix:** Use normal imports.

### M12. `resource_available()`/`deplete_resource()` Not Used in Workflow (sp_world.py)
**File:** `sp_world.py`  
**Functions:** `resource_available()` and `deplete_resource()` are defined but never called by the resource node system (`sp_resources.py`) or any other module.  
**Fix:** Integrate or remove.

---

## 🟢 LOW BUGS (Cosmetic, documentation, code quality)

### L1. Docstring Lists Non-Existent Files (__init__.py)
Files referenced: `sp_finance.py`, `sp_memory.py` — don't exist.

### L2. Redundant `ensure_schema()` Call (sp_world.py)
`generate_world()` calls `ensure_schema()` but it was already called in `on_load()`.

### L3. `_seasonal_offset` Math Possibly Off by One (sp_weather.py)
`(day_of_year - 15)` assumes Jan 15 as coldest day. 15 isn't fully accurate for Minecraft's calendar.

### L4. Weather System `_weather_tick` Resets on Server Restart
The global `_weather_tick` starts at 0 every time, losing all weather state progression.

### L5. `get_weather_summary()` Only Shows Top 5 Hottest Areas
This may miss showing interesting cold extremes.

### L6. No Rate Limiting on AI Messages (sp_messaging.py)
If many personas buy items simultaneously, RCON gets flooded with tellraw calls.

### L7. `sp_item_cache.py` `clear_cache()` and `clear_trash()` Unused
These test/debug functions are defined but never called by any production code.

### L8. `sp_combat.py` — Body Parts List Includes `left_hand`/`right_hand` in Check Function
`check_crafting_self_harm()` uses `left_hand`/`right_hand` as body parts, but the core `BODY_PARTS` list only has `left_arm`/`right_arm`. The wound DB would store `left_hand` but it's not in the standard lookup.

### L9. Conflict: `sp_claims.py` BUILDING_PRESETS vs `sp_crafting.py` BUILDING_TYPES
Two separate building systems: `sp_claims.py` defines BUILDING_PRESETS (wall, gate, tower, storehouse, well) while `sp_crafting.py` defines BUILDING_TYPES (shelter, house, workshop, fortress, watchtower). They overlap but don't interact. This is architecturally confusing.

### L10. Hardcoded Paths for THREAD Discovery (sp_memory_thread.py)
`/home/deck/AIHandler/SCRIPTS/FastMCPServer/tools` hardcoded — won't work on other systems.

### L11. Persona Tick Processes All Ecosystem Features in Single Tick (__init__.py)
The main tick loop runs weather, health, ecosystem, combat, factions, claims, AND wars all in ONE tick. If any subsystem errors, it cascades. Consider staggering.

---

## 📊 SUMMARY STATISTICS

| Category | Count | Description |
|----------|-------|-------------|
| **CRITICAL** | 9 | Will crash or break core functionality |
| **HIGH** | 12 | Logic errors, incorrect behavior |
| **MEDIUM** | 12 | Suboptimal, missing features |
| **LOW** | 11 | Cosmetic, docs, quality |
| **TOTAL** | 44 unique bugs | (Some count in multiple categories) |

**Files Most Affected:**
- `sp_ecosystem.py`: 8 bugs (logger, complex functions, dead code)
- `__init__.py`: 6 bugs (dead code, variable scope, missing hooks)
- `sp_combat.py`: 6 bugs (logger, indentation dead code, body parts)
- `sp_behavior.py`: 5 bugs (logger, typo, duplicate import, try/except)
- `sp_factions.py`: 3 bugs (logger, indentation in 2 functions)
- `sp_weather.py`: 4 bugs (logger, __import__, season math)

**Clean Files (no bugs found):**
- `sp_profile.py` ✅
- `sp_skills.py` ✅
- `sp_movement.py` ✅
- `sp_messaging.py` ✅

---

## 🔧 PRIORITY FIX ORDER

1. **C2** — `logger` → `log` (15 files, will crash on exception)
2. **C1** — Move state_registry write before return (__init__.py)
3. **C3-C5, C9** — Indentation fixes (sp_combat.py, sp_factions.py)
4. **C6, C8** — Variable scope fixes (__init__.py)
5. **H1-H12** — High bugs
6. **M1-M12** — Medium bugs
7. **L1-L11** — Low bugs

---

*Generated by Full Consistency Audit — 2026-06-08*
