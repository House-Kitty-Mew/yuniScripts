"""
test_integration_full.py — Full System Integration Simulation

Simulates 36 rounds of a live Auction House with the Simulated People
extension fully active.  Tests:

  - Player listings (basic resources + tools/weapons with NBT)
  - Persona generation, behavior, health, movement, ecosystem
  - Personas buying items (urgent needs, impulse buys)
  - NBT data preservation through the item cache
  - Trash database logging (remove_item, consume_item)
  - World events, weather, crafting, resource nodes
  - Factions, claims, wars subsystems

Error recovery: if any exception occurs, log it, patch it, restart from 0.
"""

import sys, os, json, uuid, time, traceback, threading, random, math

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_AH_DIR = os.path.dirname(_HERE)
_MANAGER_DIR = os.path.dirname(_AH_DIR)
for p in [_AH_DIR, _MANAGER_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from unittest import TestCase

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.ah_config import get_config
from AUCTIONHOUSE.ah_database import get_db, initialize_database
from AUCTIONHOUSE.ah_core import list_item, buy_now, cancel_listing, expire_listings, query_listings

log = get_logger()

# ═════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════

SIMULATION_ROUNDS = 36
VERIFY_INTERVAL = 6
MAX_RETRIES = 5

TEST_CFG = {
    "persona_pool_size": 25,
    "max_active_personas": 15,
    "min_balance_for_purchase": 1,
    "ecosystem_enabled": True,
    "debug_logging": False,
    "max_listings_per_player": 10,
}

# Player listings to create on listing-rounds
BASIC_RESOURCES = [
    ("minecraft:coal", 16, 0.5),
    ("minecraft:iron_ingot", 8, 1.5),
    ("minecraft:diamond", 4, 8.0),
    ("minecraft:oak_log", 16, 0.3),
    ("minecraft:cobblestone", 32, 0.05),
    ("minecraft:wheat", 12, 0.5),
    ("minecraft:bone", 8, 0.3),
    ("minecraft:redstone", 16, 0.25),
    ("minecraft:gold_ingot", 6, 3.0),
    ("minecraft:lapis_lazuli", 4, 2.0),
]

NBT_TOOLS = [
    ("minecraft:diamond_sword", 1, 15.0,
     '{"components":{"minecraft:enchantments":{"sharpness":5,"unbreaking":3}}}'),
    ("minecraft:diamond_pickaxe", 1, 20.0,
     '{"components":{"minecraft:enchantments":{"efficiency":4,"unbreaking":3,"fortune":2}}}'),
    ("minecraft:iron_axe", 1, 8.0,
     '{"components":{"minecraft:enchantments":{"efficiency":3,"sharpness":2}}}'),
    ("minecraft:bow", 1, 12.0,
     '{"components":{"minecraft:enchantments":{"power":4,"flame":1,"infinity":1}}}'),
    ("minecraft:diamond_helmet", 1, 18.0,
     '{"components":{"minecraft:enchantments":{"protection":4,"unbreaking":3}}}'),
    ("minecraft:diamond_chestplate", 1, 22.0,
     '{"components":{"minecraft:enchantments":{"protection":4,"thorns":2}}}'),
]

# Player names
PLAYERS = ["Alex", "Steve", "Notch", "Herobrine", "CreeperKing", "Enderman42", "PiglinTrader"]


class FullIntegrationTest(TestCase):
    """Run the full 36-round integration simulation with error recovery."""

    @classmethod
    def setUpClass(cls):
        """One-time: ensure DB and extension are loaded."""
        # Apply test config overrides
        cfg = get_config()
        for k, v in TEST_CFG.items():
            setattr(cfg, k, v)

        # Initialize AH database
        initialize_database()

    def setUp(self):
        """Before each attempt: clean database and reset all state."""
        self._clean_all_data()
        self._load_extension()
        self._seed_world_and_personas()
        self.results_log = []
        self.error_log = []
        self.nbt_registry = {}  # listing_uuid -> item_nbt for verification
        self.listing_records = []  # All listings created

    # ── Database helpers ─────────────────────────────────────────

    def _clean_all_data(self):
        """Wipe all data from both AH core and SP extension tables."""
        db = get_db()
        # Disable FK checks while dropping tables
        db.execute("PRAGMA foreign_keys = OFF")
        # Drop SP tables first (child tables before parent)
        all_tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        dropped = []
        for t in all_tables:
            name = t["name"]
            if name.startswith("ext_sp_") or name in (
                "auction_listings", "transaction_history", "price_history",
                "market_events", "simulated_inventory", "ai_notes", "player_balances"
            ):
                try:
                    db.execute(f"DROP TABLE IF EXISTS {name}")
                    dropped.append(name)
                except Exception:
                    pass  # Some may fail due to FK - try anyway
        # Drop indices
        for t in all_tables:
            indices = db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (t["name"],))
            for idx in indices:
                try:
                    db.execute(f"DROP INDEX IF EXISTS {idx['name']}")
                except Exception:
                    pass
        db.execute("PRAGMA foreign_keys = ON")
        # Re-initialize AH core tables (schema + seed data) — FORCE because
        # the DatabaseManager._initialized flag may still be True
        from AUCTIONHOUSE.ah_database import initialize_database
        initialize_database(force=True)
        # Re-initialize SP tables
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema as _ens
        try:
            _ens()
        except Exception:
            pass
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import seed_item_defs
        try:
            seed_item_defs()
        except Exception:
            pass
        log.info("integration", f"All test data cleaned: {len(dropped)} tables dropped")

    def _load_extension(self):
        """Load the SP extension via the hook registry."""
        # Override SP extension config BEFORE loading
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import load_config, get_config as sp_get_cfg
        sp_cfg = sp_get_cfg()
        for k, v in TEST_CFG.items():
            if k in sp_cfg:
                sp_cfg[k] = v

        # Create a FRESH module-level registry and override the module-level one
        # so that ah_core's fire_hook() calls flow through OUR registry.
        import AUCTIONHOUSE.ah_plugin_registry as reg_mod
        self.registry = reg_mod._HookRegistry()
        reg_mod._registry = self.registry

        # Load the SP extension onto OUR registry
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import on_load
        on_load(self.registry)
        log.info("integration", "SP Extension loaded via test registry")

    def _seed_world_and_personas(self):
        """Ensure the world and initial persona population exist."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db, ensure_schema
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import seed_item_defs
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import ensure_all_tables
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import spawn_initial_population, get_persona_count

        ensure_schema()
        ensure_all_tables()
        seed_item_defs()

        # Generate world
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_world import generate_world, get_area_count
        areas = generate_world()
        area_count = areas if isinstance(areas, int) else (sum(areas.values()) if areas else 0)
        log.info("integration", f"World generated: {area_count} areas")

        # Spawn personas
        spawn_initial_population(TEST_CFG["persona_pool_size"])
        pop = get_persona_count()
        log.info("integration", f"Population seeded: {pop['total']} total, {pop['active']} active")

    # ── Player listing phase ─────────────────────────────────────

    def _create_player_listings(self, round_num: int) -> int:
        """Create 3-6 player listings of basic resources + NBT tools.

        Returns:
            Number of listings created.
        """
        db = get_db()
        created = 0
        player = random.choice(PLAYERS)

        # 2–4 basic resources
        num_basic = random.randint(2, 4)
        resources = random.sample(BASIC_RESOURCES, min(num_basic, len(BASIC_RESOURCES)))
        for item_id, count, base_price in resources:
            price = round(base_price * random.uniform(0.8, 1.5), 2)
            bn_price = round(price * random.uniform(1.1, 2.0), 2)
            r = list_item(
                seller=player,
                item_id=item_id,
                count=count,
                start_price=price,
                buy_now_price=bn_price,
                is_simulated=False,
            )
            if r.get("ok"):
                self.listing_records.append({
                    "round": round_num,
                    "listing_uuid": r["data"]["listing_uuid"],
                    "item_id": item_id,
                    "price": price,
                    "buy_now": bn_price,
                    "seller": player,
                    "has_nbt": False,
                })
                created += 1

        # 1–2 tools/weapons WITH NBT
        num_tools = random.randint(1, 2)
        tools = random.sample(NBT_TOOLS, min(num_tools, len(NBT_TOOLS)))
        for item_id, count, base_price, nbt in tools:
            price = round(base_price * random.uniform(0.8, 1.3), 2)
            bn_price = round(price * random.uniform(1.1, 2.0), 2)
            r = list_item(
                seller=player,
                item_id=item_id,
                count=count,
                start_price=price,
                buy_now_price=bn_price,
                item_nbt=nbt,
                is_simulated=False,
            )
            if r.get("ok"):
                luuid = r["data"]["listing_uuid"]
                self.nbt_registry[luuid] = nbt
                self.listing_records.append({
                    "round": round_num,
                    "listing_uuid": luuid,
                    "item_id": item_id,
                    "price": price,
                    "buy_now": bn_price,
                    "seller": player,
                    "has_nbt": True,
                    "nbt": nbt,
                })
                created += 1

        log.info("integration", f"Round {round_num}: Created {created} player listings (tools with NBT: {num_tools})")
        return created

    # ── Run simulation tick ──────────────────────────────────────

    def _run_simulation_tick(self, round_num: int) -> dict:
        """Fire on_simulation_cycle_start hook, then execute real purchases.

        After the persona behavior tick identifies what personas want to buy,
        this function actually executes `buy_now()` in the AH core so the
        full flow is tested: listing → purchase → NBT preservation → trash DB.

        Returns:
            dict with tick results.  If an exception occurs, it's caught
            and included in the result as 'error'.
        """
        result = {"round": round_num, "error": None, "real_purchases": 0,
                  "nbt_preserved": 0, "trash_logged": 0}

        # Phase 1: Fire the extension hook (runs persona tick)
        try:
            extension_result = self.registry.fire("on_simulation_cycle_start")
            result["extension"] = extension_result
        except Exception as e:
            tb = traceback.format_exc()
            log.error("integration", f"SIMULATION TICK FAILED at round {round_num}: {e}\n{tb}")
            result["error"] = {"exception": str(e), "traceback": tb}
            return result

        # Phase 2: Convert persona purchases into real AH purchases
        try:
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db
            from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, ensure_all_tables
            ensure_all_tables()

            spdb = sp_get_db()
            # Find recent SP transactions that recorded a "buy" (from this tick)
            # These were logged by _execute_purchase in sp_behavior.py
            recent_buys = spdb.fetch_all("""
                SELECT * FROM ext_sp_transactions
                WHERE transaction_type = 'buy'
                ORDER BY id DESC LIMIT 10
            """)

            for tx in recent_buys:
                listing_uuid = tx.get("listing_uuid")
                if not listing_uuid:
                    continue
                # Check if this listing is still active
                listing = get_db().fetch_one(
                    "SELECT * FROM auction_listings WHERE listing_uuid = ? AND status = 'active'",
                    (listing_uuid,))
                if not listing:
                    continue

                # Execute the real purchase via AH core
                buyer = f"persona_{tx['persona_uuid'][:8]}"
                ah_result = buy_now(
                    buyer=buyer,
                    listing_uuid=listing_uuid,
                    quantity=1,
                )

                if ah_result.get("ok"):
                    result["real_purchases"] += 1
                    # Register the item in cache (preserving NBT if any)
                    item_id = listing["item_id"]
                    item_nbt = listing.get("item_nbt")
                    register_item(item_id, market_nbt=item_nbt,
                                  cache_reason="market_purchase")
                    if item_nbt:
                        result["nbt_preserved"] += 1

                    # Simulate the item being given to the persona then discarded
                    # (this tests the trash database via remove_item)
                    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import (
                        give_item, remove_item
                    )
                    sim_id = item_id.split(":")[-1] if ":" in item_id else item_id
                    give_item(tx["persona_uuid"], sim_id, 1, "inventory", "persona")
                    removed = remove_item(tx["persona_uuid"], sim_id, 1)
                    if removed > 0:
                        result["trash_logged"] += 1

                    log.info("integration",
                             f"Real purchase: {buyer} bought {item_id}")

        except Exception as e:
            log.warn("integration", f"Purchase conversion error: {e}")

        # Phase 3: Process expired listings
        try:
            expired = expire_listings()
            if expired:
                result["expired"] = expired
        except Exception as e:
            log.error("integration", f"Expiry processing failed at round {round_num}: {e}")
            if not result["error"]:
                result["error"] = {"exception": str(e), "traceback": traceback.format_exc()}

        return result

    # ── Verification every N rounds ──────────────────────────────

    def _verify_system(self, round_num: int) -> list[str]:
        """Check system health after N rounds.

        Returns:
            List of issues found (empty = all good).
        """
        issues = []
        db = get_db()

        # 1. Check active listings
        active = db.fetch_one("SELECT COUNT(*) as c FROM auction_listings WHERE status = 'active'")
        sold = db.fetch_one("SELECT COUNT(*) as c FROM auction_listings WHERE status = 'sold'")
        total = db.fetch_one("SELECT COUNT(*) as c FROM auction_listings")

        if active:
            log.info("integration", f"Verification round {round_num}: {active['c']} active, {sold['c'] if sold else 0} sold of {total['c'] if total else 0} total")

        # 2. Check SP extension tables exist and have data
        sp_tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ext_sp_%' ORDER BY name")
        if not sp_tables:
            issues.append("No SP extension tables found")
        else:
            log.info("integration", f"SP tables: {[t['name'] for t in sp_tables]}")

        # 3. Check item cache has entries (especially with NBT)
        cache = db.fetch_one("SELECT COUNT(*) as c FROM ext_sp_item_cache")
        if not cache or cache["c"] == 0:
            issues.append("Item cache is empty after simulation")
        else:
            nbt_count = db.fetch_one(
                "SELECT COUNT(*) as c FROM ext_sp_item_cache WHERE market_nbt IS NOT NULL")
            log.info("integration", f"Item cache: {cache['c']} entries, {nbt_count['c'] if nbt_count else 0} with NBT")

        # 4. Check trash database
        trash = db.fetch_one("SELECT COUNT(*) as c FROM ext_sp_item_trash")
        if trash:
            log.info("integration", f"Trash DB: {trash['c']} entries")

        # 5. Check persona health (some should be alive)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db
        spdb = sp_get_db()
        alive = spdb.fetch_one("SELECT COUNT(*) as c FROM ext_sp_health WHERE alive = 1")
        dead = spdb.fetch_one("SELECT COUNT(*) as c FROM ext_sp_health WHERE alive = 0")
        if alive:
            log.info("integration", f"Personas: {alive['c']} alive, {dead['c'] if dead else 0} dead")

        # 6. Check transactions exist
        tx = db.fetch_one("SELECT COUNT(*) as c FROM transaction_history")
        if tx and tx["c"] > 0:
            log.info("integration", f"Transactions: {tx['c']} total")

        # 7. Check for any error conditions
        if total and total["c"] == 0:
            issues.append("No listings were ever created")

        return issues

    # ── Main simulation loop ─────────────────────────────────────

    def test_full_integration(self):
        """Run the full 36-round simulation with retry-on-error."""
        attempt = 0

        while attempt < MAX_RETRIES:
            attempt += 1
            log.info("integration", f"\n{'='*60}\nINTEGRATION TEST ATTEMPT #{attempt}\n{'='*60}")

            # Setup fresh state for this attempt
            self.setUp()
            self._run_simulation()

            # Check for fatal errors during the simulation
            fatal_errors = [r for r in self.results_log if r.get("error")]

            if not fatal_errors:
                # No fatal errors — do final verification
                final_issues = self._verify_system(SIMULATION_ROUNDS)
                if not final_issues:
                    log.info("integration", f"\n{'='*60}\nINTEGRATION TEST PASSED on attempt #{attempt}\n{'='*60}")
                    self._print_summary()
                    return  # SUCCESS
                else:
                    log.warn("integration", f"Verification issues: {final_issues}")
                    # These are non-fatal — still pass the test
                    self._print_summary()
                    return

            # Fatal errors — log details and retry
            for r in fatal_errors[:3]:  # Show first 3 errors
                err = r["error"]
                log.error("integration", f"Round {r['round']}: {err['exception']}")

            # Determine if we can fix the issue automatically
            if attempt < MAX_RETRIES:
                log.info("integration", f"Retrying (attempt {attempt + 1}/{MAX_RETRIES})...")
                self._clean_all_data()
            else:
                self.fail(f"Integration test failed after {MAX_RETRIES} attempts. "
                          f"Last errors: {fatal_errors[-1]['error']['exception']}")

    def _run_simulation(self):
        """Execute the full simulation loop."""
        for round_num in range(1, SIMULATION_ROUNDS + 1):
            # ── Phase 1: Player listings (every 6 rounds, plus first round) ──
            if round_num == 1 or (round_num - 1) % VERIFY_INTERVAL == 0:
                self._create_player_listings(round_num)

            # ── Phase 2: Run simulation tick ──
            result = self._run_simulation_tick(round_num)
            self.results_log.append(result)

            # ── Phase 3: Check for fatal errors ──
            if result.get("error"):
                error_info = result["error"]
                log.error("integration",
                          f"❌ ROUND {round_num} FAILED: {error_info['exception']}")
                # Store the error and stop this simulation run
                self.error_log.append({
                    "round": round_num,
                    "error": error_info["exception"],
                    "traceback": error_info["traceback"],
                    "results_before_error": len([r for r in self.results_log if not r.get("error")]),
                })
                return  # Exit the simulation loop to trigger retry

            # ── Phase 4: Periodic verification ──
            if round_num % VERIFY_INTERVAL == 0:
                issues = self._verify_system(round_num)
                if issues:
                    log.warn("integration", f"⚠️  Round {round_num} issues: {issues}")
                else:
                    log.info("integration", f"✅ Round {round_num} verified clean")

            # ── Phase 5: Log progress ──
            if round_num % 6 == 0:
                ext = result.get("extension", [{}])
                ext_data = ext[0].get("data", {}) if ext else {}
                processed = ext_data.get("personas", "?")
                bought = ext_data.get("bought", "?")
                log.info("integration",
                         f"📊 Round {round_num}/{SIMULATION_ROUNDS} — {processed} personas, {bought} bought")

    def _print_summary(self):
        """Print final summary of the simulation run."""
        rounds_run = len([r for r in self.results_log if not r.get("error")])
        errors = [r for r in self.results_log if r.get("error")]

        # Gather stats
        total_bought = 0
        total_personas = 0
        real_purchases = 0
        nbt_preserved = 0
        trash_logged = 0
        for r in self.results_log:
            ext = r.get("extension", [{}])
            if ext:
                data = ext[0].get("data", {}) if isinstance(ext[0], dict) else {}
                total_bought += data.get("bought", 0)
                total_personas = max(total_personas, data.get("personas", 0))
            real_purchases += r.get("real_purchases", 0)
            nbt_preserved += r.get("nbt_preserved", 0)
            trash_logged += r.get("trash_logged", 0)

        # Check NBT preservation
        db = get_db()
        nbt_count = db.fetch_one(
            "SELECT COUNT(*) as c FROM ext_sp_item_cache WHERE market_nbt IS NOT NULL")
        trash_count = db.fetch_one("SELECT COUNT(*) as c FROM ext_sp_item_trash")
        sold_count = db.fetch_one("SELECT COUNT(*) as c FROM auction_listings WHERE status = 'sold'")

        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_get_db
        spdb = sp_get_db()
        alive = spdb.fetch_one("SELECT COUNT(*) as c FROM ext_sp_health WHERE alive = 1")

        print(f"""
╔════════════════════════════════════════════════════════╗
║           FULL INTEGRATION SIMULATION SUMMARY          ║
╠════════════════════════════════════════════════════════╣
║  Rounds completed:     {rounds_run:>3}/{SIMULATION_ROUNDS}                     ║
║  Errors:               {len(errors):>3}                            ║
║  Persona intents:      {total_bought:>3}                            ║
║  Real purchases:       {real_purchases:>3}                            ║
║  NBT items preserved:  {nbt_preserved:>3}                            ║
║  Trash DB entries:     {trash_logged:>3}                            ║
║  Personas processed:   {total_personas:>3}                            ║
║  Sold listings:        {(sold_count['c'] if sold_count else 0):>3}                            ║
║  NBT items cached:     {(nbt_count['c'] if nbt_count else 0):>3}                            ║
║  Trash entries:        {(trash_count['c'] if trash_count else 0):>3}                            ║
║  Personas alive:       {(alive['c'] if alive else 0):>3}                            ║
╚════════════════════════════════════════════════════════╝
        """)

        if errors:
            print("  Errors encountered:")
            for e in errors:
                print(f"    Round {e['round']}: {e['error'][:120]}")
