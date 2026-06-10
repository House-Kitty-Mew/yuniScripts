# Architectural Design Review: Combat System Unification

**Review Date:** 2026-06-09  
**Reviewer:** AI Architectural Review  
**Subject:** `combat_unify_analysis.md` — Bridge Layer Between sp_combat.py ↔ SHM  
**Work Order:** #1015 (P3)  
**Status:** ⚠️ **Conditional Approval with Required Changes**

---

## Executive Summary

The analysis document is thorough, well-structured, and correctly identifies 8 divergence points between the two systems. The proposed bridge-layer architecture is the **correct pattern** for this integration — it avoids the fatal mistake of modifying sp_combat.py's core loop while enabling bidirectional data flow.

**However**, the bridge design as specified has **3 critical gaps**, **5 moderate issues**, and **2 non-trivial performance concerns** that must be addressed before implementation begins.

**Overall Verdict:** APPROVED with conditions. The bridge-layer concept is sound. The implementation sketch needs revision in the areas noted below.

---

## 1. What the Analysis Gets Right ✅

### 1.1 Architecture Pattern
The bridge layer (`shm_combat_bridge.py`) sitting between sp_combat and SHM tables is the **correct architectural choice**:
- sp_combat remains fully operational when SHM is not loaded
- No changes to sp_combat's 911-line core loop
- SHM reads ext_sp_wounds (already works) — this completes the reverse direction
- Feature flags allow incremental rollout

### 1.2 Overlap Map
The 6 overlap areas (wounds, pain, health, skills, infection, first aid) are accurately identified. The fact that both systems already share `ext_sp_wounds` and call `sp_health.modify_health()` means partial integration exists — the bridge completes it.

### 1.3 Three-Phase Migration
Phase 1 (read-only augmentation) → Phase 2 (optional stat replacement) → Phase 3 (full bidirectional sync) is the correct rollout order. The config flags in §4.4 are well-conceived.

### 1.4 Terrain Boundary
Keeping terrain/weather entirely in sp_combat (§4.3) is correct. SHM has no environmental awareness and should not acquire any — that's a clean architectural boundary.

---

## 2. Critical Gaps ❌

### 2.1 GAP #1: No Transactional Consistency Mechanism

**Severity:** CRITICAL

The analysis proposes that sp_combat writes wounds to `ext_sp_wounds` and the bridge then creates SHM-side records (pain sources, fractures, blood loss). **There is no transaction guard between these operations.**

**The problem:** If `bridge_on_wound_created()` fails (DB connection lost, genetics row missing, constraint violation), the wound is already committed to `ext_sp_wounds` but no SHM side-effects exist. The systems desynchronize silently.

**Required change — Use eventual consistency pattern:**
The bridge should maintain a `shm_bridge_pending` table for retryable actions, and SHM's `_on_simulation_cycle_start` processes pending actions. This avoids coupling two independent DB connections in a transaction:

```python
# In bridge module
PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS shm_bridge_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wound_uuid TEXT UNIQUE,
    owner_uuid TEXT,
    action_type TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TEXT,
    last_error TEXT
)
"""
```

sp_combat's `_create_wound()` should wrap the wound INSERT + bridge hook in a **single DB transaction** (using `BEGIN`/`COMMIT` on sp_combat's own DB connection). If the bridge hook fails, the transaction rolls back the wound INSERT as well.

### 2.2 GAP #2: Wound → Bone Mapping Is Lossy

**Severity:** CRITICAL

The analysis says sp_combat's 6 body parts map to 206 bones via `_LOCATION_MAP` (§3.1), calling it "lossy." This understates the problem.

**Current state:** `_LOCATION_MAP` maps "left_arm" → ALL 30 bones in the arm, "torso" → every rib, vertebra, pelvis, etc.

**The issue:** When `bridge_on_wound_created()` calls `fracture_from_impact()`, **which bone fractures?** Mapping "left_arm" to ALL bones means either all 30 fracture (ridiculous) or one random bone fractures but the mapping doesn't say which.

**Required change — Per-wound-type bone target table:**
```python
WOUND_BONE_MAP = {
    "left_arm": {
        "crush":     ["radius", "ulna"],       # Forearm bones from crushing blows
        "fracture":  ["humerus", "radius", "ulna"],
        "puncture":  ["radius"],                # Deep stab can hit radius
        "cut":       [],                        # No bone fracture from cuts
        "laceration": [],
        "burn":      [],
    },
    "torso": {
        "crush":     ["rib_3", "rib_4", "rib_5", "sternum"],
        "fracture":  ["rib_5", "rib_6", "rib_7"],
        "puncture":  ["rib_5", "liver", "lung_right"],
        # ...
    },
}
```

### 2.3 GAP #3: No Tick Ordering Contract

**Severity:** HIGH

The analysis says SHM's `_on_simulation_cycle_start` runs "after" sp_combat, but the execution order depends on the AH plugin registry's hook priority system — **not specified in the analysis.**

**Problem:** If SHM runs before sp_combat, then `bridge_on_healing_tick()` runs before `process_wounds_tick()`, and pain sources decay before new wounds are registered.

**Required change — Explicit registry priorities:**
```python
# sp_combat/__init__.py: priority=10 (run first)
registry.register("on_simulation_cycle_start", "SIMULATED_PEOPLE",
                  sp_combat.process_wounds_tick, priority=10)

# SHM/__init__.py: priority=20 (run after sp_combat)
registry.register("on_simulation_cycle_start", "SIMULATED_HEALTH_MECHANICS",
                  shm_combat_bridge.bridge_on_healing_tick, priority=20)
```

---

## 3. Moderate Issues ⚠️

### 3.1 `bridge_get_combat_stats()` Performance

The proposed function makes **7 separate SHM queries** per call, and is called **twice per attack** (attacker + defender). With 20 personas and 5 attacks per tick: **1,400 queries/tick**.

**Fix:** Add per-tick LRU cache:
```python
_bridge_cache = {}
_last_tick = -1

def bridge_get_combat_stats(persona_uuid, current_tick):
    global _last_tick
    if current_tick != _last_tick:
        _bridge_cache.clear()
        _last_tick = current_tick
    key = persona_uuid
    if key not in _bridge_cache:
        _bridge_cache[key] = _compute_stats(persona_uuid)
    return _bridge_cache[key]
```

### 3.2 `bridge_get_skill_level()` Weapon Type Resolution

sp_combat determines weapon type from equipped items via `get_equipped_stats()` which returns Minecraft item IDs. The bridge needs an item-ID-to-SHM-skill resolver:

```python
ITEM_TO_SKILL = {
    "iron_sword": "blades", "diamond_sword": "blades",
    "iron_axe": "blades", "diamond_axe": "blades",
    "bow": "archery", "crossbow": "archery",
    "trident": "polearms",
    # default: "unarmed"
}
```

### 3.3 Pain Scale Mismatch — Double-Count

The analysis' `bridge_get_pain_effects()` in §4.2.3 calls `get_total_pain()` (which includes wound pain) AND then adds `get_fracture_pain()` separately. **This double-counts** because `get_total_pain()` already aggregates wound + fracture + infection + organ pain.

**Fix:** Use `get_total_pain()` as the single source of truth, then apply genetics tolerance multiplier.

### 3.4 Infection Authority

Both systems track wound infection independently. The analysis acknowledges this (§2.5) but doesn't specify authority.

**Recommendation:** SHM owns infection outcomes. sp_combat sets `infection_chance` and `infection_progress`, the bridge converts `infection_progress > threshold` → SHM disease trigger. SHM's disease progression determines is_infected.

### 3.5 Impact Force From Severity

`bridge_on_wound_created()` needs an `impact_force` for fracture probability, but sp_combat doesn't track force — only severity and weapon type. Add a severity-to-force conversion table:

```python
SEVERITY_TO_FORCE = {
    1: {"penetration": 0.3, "blunt": 0.2},
    2: {"penetration": 0.6, "blunt": 0.4},
    3: {"penetration": 0.8, "blunt": 0.7},
    4: {"penetration": 1.0, "blunt": 1.0},
}
```

---

## 4. Performance & Migration 🚀

### 4.1 Existing Wound Backfill
When SHM is first loaded, existing active wounds have no corresponding SHM records. Add a `bridge_migrate_existing_wounds()` function that runs on SHM load to create pain sources, fractures, etc. for all unhealed wounds.

### 4.2 Skill Tracking From Combat
Phase 3 needs `bridge_log_skill_use()` to call SHM's `use_skill()` when sp_combat registers a hit. This is the **only required modification** to sp_combat.py — add a 3-line hook call after successful hits in `resolve_melee_attack()`.

---

## 5. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-----------|--------|------------|
| Data desync between ext_sp_wounds and SHM side-effects | Medium | High | Eventual consistency via pending table (§2.1) |
| Performance: 7 queries per combat stat call | High | Medium | Per-tick LRU cache (§3.1) |
| Double-counted pain from wound + fracture | High | Medium | Use get_total_pain() as single source (§3.3) |
| Hook ordering: SHM runs before sp_combat | Medium | High | Explicit registry priorities (§2.3) |
| Wrong bone fracture from lossy body part map | High | Medium | Per-wound-type bone target table (§2.2) |
| Existing wound backfill missing | High | Medium | Migration function on SHM load (§4.1) |

---

## 6. Implementation Plan

### File Creation Order
1. `shm_combat_bridge.py` — Core bridge functions (Phase 1-2)
2. `shm_bridge_pending.py` — Pending actions table + retry logic
3. Modify `SIMULATED_HEALTH_MECHANICS/__init__.py` — Import bridge, register hook with priority=20
4. Modify `SIMULATED_PEOPLE/sp_combat.py` — 3 minimal changes (see below)

### sp_combat.py Changes (3 sites, ~16 lines total)
| Site | Change | Lines |
|------|--------|-------|
| `_create_wound()` (L385) | Add `bridge_hook=True`, wrap in transaction with rollback on bridge failure | ~10 |
| `resolve_melee_attack()` after hit (L~340) | Call `bridge_log_skill_use()` for SHM skill tracking | ~3 |
| `process_wounds_tick()` before sepsis check (L~570) | Call `bridge_sync_infection_to_shm()` | ~3 |

### Testing Requirements
1. **Consistency tests** — wound created → SHM pain source exists → both decay in sync
2. **Failure tests** — bridge failure at every possible point (DB down, missing genetics row)
3. **Performance regression** — measure queries/tick before/after bridge
4. **Backward compatibility** — sp_combat works without SHM, identical results

### Priority Recommendation
→ **Upgrade to P2.** Two systems tracking the same data already creates inconsistency bugs. SHM already reads ext_sp_wounds — completing the loop is safer than leaving it half-done.

---

## 7. Summary

| Category | Verdict |
|----------|---------|
| Architecture pattern | ✅ Bridge layer is correct |
| Analysis completeness | ✅ Thorough (8 divergence points, 6 overlaps) |
| Transaction safety | ❌ **CRITICAL** — Need eventual consistency |
| Body part mapping | ❌ **CRITICAL** — Need per-type bone targets |
| Hook ordering | ❌ **HIGH** — Need registry priority |
| Performance | ⚠️ Cache needed for stat queries |
| Pain double-count | ⚠️ Bridge sketch has arithmetic error |
| Infection authority | ⚠️ Need to declare SHM as owner |
| Migration path | ✅ Three-phase plan is correct |
| Backward compatibility | ✅ Config-flagged, graceful fallback |

---

*Review performed by AI Architectural Review. Analysis document author: Subagent (2026-06-09). Work Order #1015.*
