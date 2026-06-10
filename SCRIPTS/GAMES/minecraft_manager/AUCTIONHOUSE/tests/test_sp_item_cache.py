"""
test_sp_item_cache.py — Comprehensive tests for the Item Cache bridge.

Tests the bidirectional item flow between the AH market and the Simulated
People world, with proper namespace preservation for modded items.
"""

from conftest import AHTestCase
from unittest.mock import patch
from AUCTIONHOUSE.ah_database import get_db

# Items in ITEM_DEFS: stone, wood_log, iron_ingot, etc.
# Items NOT in ITEM_DEFS: diamond, emerald, sponge, heavy_core (any minecraft: item)
# Use unique_item() for items that get REGISTERED to avoid test collisions


class TestItemCache(AHTestCase):

    def setUp(self):
        super().setUp()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema
        ensure_schema()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import clear_cache
        clear_cache()

    # ── market_to_sim() — Market → SimWorld conversion ────────────

    def test_market_to_sim_strips_minecraft(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim
        self.assertEqual(market_to_sim("minecraft:diamond"), "diamond")

    def test_market_to_sim_minecraft_netherite(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim
        self.assertEqual(market_to_sim("minecraft:netherite_ingot"), "netherite_ingot")

    def test_market_to_sim_modded_preserved(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim
        self.assertEqual(market_to_sim("mymod:custom_sword"), "mymod:custom_sword")

    def test_market_to_sim_modded_another(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim
        self.assertEqual(market_to_sim("custom:magic_ring"), "custom:magic_ring")

    def test_market_to_sim_no_namespace(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim
        self.assertEqual(market_to_sim("stone"), "stone")

    def test_market_to_sim_empty(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim
        self.assertEqual(market_to_sim(""), "")

    def test_market_to_sim_multi_colon(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim
        self.assertEqual(market_to_sim("minecraft:custom:sword"), "custom:sword")
        self.assertEqual(market_to_sim("mymod:test:item"), "mymod:test:item")

    # ── sim_to_market() — SimWorld → Market conversion ─────────────

    def test_sim_to_market_cached_vanilla(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, sim_to_market
        i = self.unique_item("minecraft:test")
        register_item(i)
        bare = i.split(":")[-1]
        self.assertEqual(sim_to_market(bare), i)

    def test_sim_to_market_cached_modded(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, sim_to_market
        i = "mymod:" + self.unique_item("test").split(":")[-1]
        register_item(i)
        self.assertEqual(sim_to_market(i), i)

    def test_sim_to_market_native(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import sim_to_market
        self.assertEqual(sim_to_market("stone"), "minecraft:stone")

    def test_sim_to_market_cached_then_native(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, sim_to_market
        register_item(self.unique_item("minecraft:test"))
        self.assertEqual(sim_to_market("wood_log"), "minecraft:wood_log")

    def test_sim_to_market_already_namespaced(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import sim_to_market
        self.assertEqual(sim_to_market("minecraft:diamond"), "minecraft:diamond")
        self.assertEqual(sim_to_market("mymod:item"), "mymod:item")

    # ── Roundtrip Tests ────────────────────────────────────────────

    def test_roundtrip_vanilla_with_register(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim, sim_to_market, register_item
        orig = self.unique_item("minecraft:test")
        sim = market_to_sim(orig)
        register_item(orig)
        back = sim_to_market(sim)
        self.assertEqual(back, orig)

    def test_roundtrip_modded(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import market_to_sim, sim_to_market, register_item
        orig = "mymod:" + self.unique_item("test").split(":")[-1]
        sim = market_to_sim(orig)
        self.assertEqual(sim, orig)
        register_item(orig)
        back = sim_to_market(sim)
        self.assertEqual(back, orig)

    def test_roundtrip_native_sim_item(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import sim_to_market
        self.assertEqual(sim_to_market("stone"), "minecraft:stone")

    # ── Registration ───────────────────────────────────────────────

    def test_register_known_item(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item
        result = register_item("minecraft:stone", cache_reason="test")
        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["was_new"])
        self.assertEqual(result["data"]["source"], "ITEM_DEFS")
        self.assertEqual(result["data"]["market_id"], "minecraft:stone")

    def test_register_unknown_creates_cache(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        i = self.unique_item("minecraft:test_reg")
        result = register_item(i, cache_reason="market_import", source_listing="test-listing")
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["was_new"])
        self.assertEqual(result["data"]["market_id"], i)
        cached = get_cached_item(i)
        self.assertIsNotNone(cached)
        bare = i.split(":")[-1]
        self.assertEqual(cached["item_id"], bare)
        self.assertEqual(cached["market_id"], i)

    def test_register_modded_item(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        i = "mymod:" + self.unique_item("test").split(":")[-1]
        result = register_item(i, cache_reason="market_import")
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["was_new"])
        self.assertEqual(result["data"]["market_id"], i)
        cached = get_cached_item(i)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["item_id"], i)
        self.assertEqual(cached["market_id"], i)

    def test_register_idempotent(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item
        i = self.unique_item("minecraft:test")
        r1 = register_item(i)
        self.assertTrue(r1["data"]["was_new"])
        r2 = register_item(i)
        self.assertFalse(r2["data"]["was_new"])

    def test_register_increments_usage(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        i = self.unique_item("minecraft:test")
        register_item(i)
        c1 = get_cached_item(i)
        register_item(i)
        c2 = get_cached_item(i)
        self.assertGreater(c2["usage_count"], c1["usage_count"])

    def test_register_adds_to_item_defs(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import get_item_def
        i = self.unique_item("minecraft:test_def")
        register_item(i)
        bare = i.split(":")[-1]
        item_def = get_item_def(bare)
        self.assertIsNotNone(item_def)
        self.assertIn("category", item_def)

    # ── ensure_item_known ──────────────────────────────────────────

    def test_ensure_known_in_defs(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import ensure_item_known
        result = ensure_item_known("minecraft:stone")
        self.assertFalse(result["data"]["was_new"])
        self.assertEqual(result["data"]["source"], "ITEM_DEFS")

    def test_ensure_unknown_registers(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import ensure_item_known
        i = self.unique_item("minecraft:test")
        result = ensure_item_known(i)
        self.assertTrue(result["data"]["was_new"])

    def test_ensure_known_twice(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import ensure_item_known
        i = self.unique_item("minecraft:test")
        ensure_item_known(i)
        result = ensure_item_known(i)
        self.assertFalse(result["data"]["was_new"])

    # ── resolve_item_for_persona ───────────────────────────────────

    def test_resolve_known(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        result = resolve_item_for_persona("test", "minecraft:stone")
        self.assertEqual(result["data"]["sim_item_id"], "stone")
        self.assertIsNotNone(result["data"]["item_def"])

    def test_resolve_unknown(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        i = self.unique_item("minecraft:test")
        result = resolve_item_for_persona("test", i)
        self.assertEqual(result["data"]["sim_item_id"], i.split(":")[-1])
        self.assertTrue(result["data"]["was_cached"])

    def test_resolve_modded(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        i = "mymod:" + self.unique_item("unique_axe").split(":")[-1]
        result = resolve_item_for_persona("test", i)
        self.assertEqual(result["data"]["sim_item_id"], i)
        self.assertTrue(result["data"]["was_cached"])

    # ── Category Detection ─────────────────────────────────────────

    def test_category_tool(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import _category_for_item
        self.assertEqual(_category_for_item("diamond_sword"), "tool")
        self.assertEqual(_category_for_item("bow"), "tool")

    def test_category_armor(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import _category_for_item
        self.assertEqual(_category_for_item("diamond_helmet"), "armor")

    def test_category_food(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import _category_for_item
        self.assertEqual(_category_for_item("cooked_beef"), "food")

    def test_category_material(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import _category_for_item
        self.assertEqual(_category_for_item("iron_ingot"), "material")

    def test_category_medicine(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import _category_for_item
        self.assertEqual(_category_for_item("potion"), "medicine")

    # ── Cache Stats ────────────────────────────────────────────────

    def test_cache_stats_empty(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import get_cache_stats, clear_cache
        clear_cache()
        self.assertEqual(get_cache_stats()["total_cached"], 0)

    def test_cache_stats_with_data(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cache_stats, clear_cache
        clear_cache()
        register_item(self.unique_item("minecraft:test_a"))
        register_item(self.unique_item("minecraft:test_b"))
        self.assertEqual(get_cache_stats()["total_cached"], 2)

    # ── Full Flow: Market → SimWorld → Inventory ──────────────────

    def test_market_item_given_to_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, count_item
        import uuid
        puid = str(uuid.uuid4())
        resolve_item_for_persona(puid, "minecraft:diamond")
        give_item(puid, "diamond", 5, "inventory", "persona")
        self.assertEqual(count_item(puid, "diamond", "persona"), 5)

    def test_unknown_cached_and_given(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, count_item
        import uuid
        puid = str(uuid.uuid4())
        i = self.unique_item("minecraft:test")
        r = resolve_item_for_persona(puid, i, source_listing="test")
        self.assertTrue(r["data"]["was_cached"])
        bare = i.split(":")[-1]
        give_item(puid, bare, 3, "inventory", "persona")
        self.assertEqual(count_item(puid, bare, "persona"), 3)

    def test_modded_item_flows_to_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, count_item
        import uuid
        puid = str(uuid.uuid4())
        i = "mymod:" + self.unique_item("custom_sword").split(":")[-1]
        r = resolve_item_for_persona(puid, i)
        self.assertTrue(r["data"]["was_cached"])
        give_item(puid, i, 1, "inventory", "persona")
        self.assertEqual(count_item(puid, i, "persona"), 1)

    # ── Full Flow: SimWorld → Market ──────────────────────────────

    def test_sim_item_lists_on_market(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import sim_to_market
        from AUCTIONHOUSE.ah_core import list_item, get_listing
        market_item = sim_to_market("stone")
        self.assertEqual(market_item, "minecraft:stone")
        r = list_item(seller="P", item_id=market_item, count=1,
                      start_price=10.0, is_simulated=True)
        self.assertTrue(r["ok"])
        self.assertEqual(get_listing(r["data"]["listing_uuid"])["item_id"], "minecraft:stone")

    def test_cached_vanilla_item_reconstructs(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, sim_to_market
        from AUCTIONHOUSE.ah_core import list_item, get_listing
        i = self.unique_item("minecraft:test")
        register_item(i)
        bare = i.split(":")[-1]
        market_item = sim_to_market(bare)
        self.assertEqual(market_item, i)
        r = list_item(seller="P", item_id=market_item, count=1,
                      start_price=10.0, is_simulated=True)
        self.assertTrue(r["ok"])
        self.assertEqual(get_listing(r["data"]["listing_uuid"])["item_id"], i)

    def test_cached_modded_item_reconstructs(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, sim_to_market
        from AUCTIONHOUSE.ah_core import list_item, get_listing
        i = "mymod:" + self.unique_item("custom_sword").split(":")[-1]
        register_item(i)
        market_item = sim_to_market(i)
        self.assertEqual(market_item, i)
        r = list_item(seller="P", item_id=market_item, count=1,
                      start_price=10.0, is_simulated=True)
        self.assertTrue(r["ok"])
        self.assertEqual(get_listing(r["data"]["listing_uuid"])["item_id"], i)

    def test_buy_now_triggers_flow(self):
        from AUCTIONHOUSE.ah_core import list_item, buy_now
        from AUCTIONHOUSE.ah_database import get_db as ah_db
        lr = list_item(seller="S", item_id="minecraft:diamond", count=1,
                       start_price=10.0, buy_now_price=20.0, is_simulated=True)
        result = buy_now(buyer="B", listing_uuid=lr["data"]["listing_uuid"])
        self.assertTrue(result["ok"])
        tx = ah_db().fetch_one("SELECT * FROM transaction_history WHERE listing_uuid = ?",
                               (lr["data"]["listing_uuid"],))
        self.assertIsNotNone(tx)
        self.assertIn("diamond", tx["item_id"])

    # ── Edge Cases ─────────────────────────────────────────────────

    def test_cache_persistence(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, get_cached_item, clear_cache
        )
        clear_cache()
        register_item("minecraft:heavy_core")
        c1 = get_cached_item("heavy_core")
        c2 = get_cached_item("minecraft:heavy_core")
        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)
        self.assertEqual(c1["item_id"], c2["item_id"])
        self.assertEqual(c1["market_id"], "minecraft:heavy_core")

    def test_clear_cache(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, clear_cache, get_cache_stats
        )
        register_item(self.unique_item("minecraft:test"))
        clear_cache()
        self.assertEqual(get_cache_stats()["total_cached"], 0)

    def test_namespace_preserved_in_cache(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        register_item("minecraft:coal")
        register_item("mymod:ruby")
        c1 = get_cached_item("coal")
        c2 = get_cached_item("mymod:ruby")
        self.assertEqual(c1["market_id"], "minecraft:coal")
        self.assertEqual(c2["market_id"], "mymod:ruby")
        self.assertEqual(c2["item_id"], "mymod:ruby")


# ═══════════════════════════════════════════════════════════════════════
# NBT Preservation Tests
# ═══════════════════════════════════════════════════════════════════════

class TestNbtPreservation(AHTestCase):

    def setUp(self):
        super().setUp()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import seed_item_defs
        ensure_schema()
        seed_item_defs()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import clear_cache, clear_trash, ensure_all_tables
        ensure_all_tables()
        clear_cache()
        clear_trash()

    # ── register_item with NBT ──────────────────────────────────

    def test_register_with_nbt_new_item(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        nbt = '{{"components":{{"minecraft:unbreakable":{{}},"minecraft:enchantments":{{"sharpness":5}}}}}}'
        i = self.unique_item('minecraft:diamond_sword')
        r = register_item(i, market_nbt=nbt)
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_nbt'], nbt)
        cached = get_cached_item(i)
        self.assertEqual(cached['market_nbt'], nbt)

    def test_register_with_nbt_known_item(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        nbt = '{{"components":{{"minecraft:unbreakable":{{}}}}}}'
        r = register_item('minecraft:stone', market_nbt=nbt)
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_nbt'], nbt)
        cached = get_cached_item('minecraft:stone')
        self.assertEqual(cached['market_nbt'], nbt)

    def test_register_with_nbt_none(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        i = self.unique_item('minecraft:test')
        r = register_item(i)
        self.assertIsNone(r['data']['market_nbt'])
        cached = get_cached_item(i)
        self.assertIsNone(cached['market_nbt'])

    def test_register_updates_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        i = self.unique_item('minecraft:diamond')
        register_item(i, market_nbt='{{"version":1}}')
        register_item(i, market_nbt='{{"version":2,"enchantments":["sharpness"]}}')
        cached = get_cached_item(i)
        self.assertEqual(cached['market_nbt'], '{{"version":2,"enchantments":["sharpness"]}}')

    def test_register_modded_with_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, get_cached_item
        nbt = '{{"components":{{"custom:mana":500,"custom:level":10}}}}'
        i = 'mymod:magic_ring_' + self.unique_item('test').split(':')[-1]
        r = register_item(i, market_nbt=nbt)
        self.assertTrue(r['ok'])
        cached = get_cached_item(i)
        self.assertEqual(cached['market_nbt'], nbt)
        self.assertEqual(cached['market_id'], i)

    # ── resolve_item_for_persona with NBT ───────────────────────

    def test_resolve_persona_passes_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        nbt = '{{"components":{{"minecraft:enchantments":{{"sharpness":3}}}}}}'
        r = resolve_item_for_persona('test', 'minecraft:diamond', market_nbt=nbt)
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_nbt'], nbt)

    def test_resolve_persona_unknown_with_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        i = self.unique_item('minecraft:custom_item')
        nbt = '{{"custom":"data"}}'
        r = resolve_item_for_persona('test', i, market_nbt=nbt)
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_nbt'], nbt)

    def test_resolve_persona_no_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_persona
        r = resolve_item_for_persona('test', 'minecraft:stone')
        self.assertIsNone(r['data']['market_nbt'])

    # ── resolve_item_for_market with NBT ────────────────────────

    def test_resolve_market_with_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, resolve_item_for_market
        )
        nbt = '{{"components":{{"minecraft:enchantments":{{"sharpness":5,"unbreaking":3}}}}}}'
        register_item('minecraft:diamond_sword', market_nbt=nbt)
        r = resolve_item_for_market('diamond_sword')
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_id'], 'minecraft:diamond_sword')
        self.assertEqual(r['data']['market_nbt'], nbt)

    def test_resolve_market_native_no_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_market
        r = resolve_item_for_market('stone')
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_id'], 'minecraft:stone')
        self.assertIsNone(r['data']['market_nbt'])

    def test_resolve_market_modded_with_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, resolve_item_for_market
        )
        nbt = '{{"display":{{"Name":"\"Excalibur\""}},"enchantments":["sharpness"]}}'
        register_item('mymod:excalibur', market_nbt=nbt)
        r = resolve_item_for_market('mymod:excalibur')
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_id'], 'mymod:excalibur')
        self.assertEqual(r['data']['market_nbt'], nbt)

    def test_resolve_market_no_cache_fallsback(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import resolve_item_for_market
        r = resolve_item_for_market('wood_log')
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_id'], 'minecraft:wood_log')
        self.assertIsNone(r['data']['market_nbt'])

    # ── NBT Roundtrip (Full Flow) ───────────────────────────────

    def test_full_nbt_roundtrip(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, market_to_sim, resolve_item_for_market
        )
        nbt = '{{"components":{{"minecraft:enchantments":{{"sharpness":5}}}}}}'
        market_id = self.unique_item('minecraft:enchanted_sword')
        register_item(market_id, market_nbt=nbt)
        sim_id = market_to_sim(market_id)
        bare = market_id.split(':')[-1]
        self.assertEqual(sim_id, bare)
        r = resolve_item_for_market(sim_id)
        self.assertTrue(r['ok'])
        self.assertEqual(r['data']['market_id'], market_id)
        self.assertEqual(r['data']['market_nbt'], nbt)

    def test_nbt_survives_multiple_roundtrips(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, resolve_item_for_market
        )
        nbt = '{{"components":{{"custom:durability":100,"custom:sharpness":7}}}}'
        market_id = 'mymod:' + self.unique_item('mythic_blade').split(':')[-1]
        register_item(market_id, market_nbt=nbt)
        for _ in range(3):
            r = resolve_item_for_market(market_id)
            self.assertEqual(r['data']['market_nbt'], nbt)

    # ── Cache Stats with NBT ────────────────────────────────────

    def test_cache_stats_shows_nbt_count(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, get_cache_stats, clear_cache
        )
        clear_cache()
        register_item(self.unique_item('minecraft:item_a'), market_nbt='{{"a":1}}')
        register_item(self.unique_item('minecraft:item_b'), market_nbt='{{"b":2}}')
        register_item(self.unique_item('minecraft:item_c'))
        stats = get_cache_stats()
        self.assertEqual(stats['total_cached'], 3)
        self.assertEqual(stats['with_nbt'], 2)

    def test_cache_stats_zero_nbt(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, get_cache_stats, clear_cache
        )
        clear_cache()
        register_item(self.unique_item('minecraft:test'))
        stats = get_cache_stats()
        self.assertEqual(stats['total_cached'], 1)
        self.assertEqual(stats['with_nbt'], 0)


# ═══════════════════════════════════════════════════════════════════════
# Trash Database Tests
# ═══════════════════════════════════════════════════════════════════════

class TestTrashDatabase(AHTestCase):

    def setUp(self):
        super().setUp()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import seed_item_defs
        ensure_schema()
        seed_item_defs()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            clear_trash, clear_cache, ensure_all_tables
        )
        ensure_all_tables()
        clear_trash()
        clear_cache()

    # ── log_item_removal ────────────────────────────────────────

    def test_log_removal_basic(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal
        r = log_item_removal('pers_a', 'stone', 5, reason='discarded')
        self.assertTrue(r['ok'])
        self.assertIsInstance(r['data']['trash_id'], int)

    def test_log_removal_all_fields(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal
        r = log_item_removal(
            persona_uuid='pers_a',
            item_id='diamond_sword',
            quantity=1,
            reason='crafted',
            market_id='minecraft:diamond_sword',
            market_nbt='{{"enchantments":["sharpness"]}}',
            container='inventory',
            container_id=42,
            details={"recipe": "diamond_sword_from_ingots"},
        )
        self.assertTrue(r['ok'])

    def test_log_removal_auto_resolves_market_id(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            log_item_removal, register_item, query_trash
        )
        register_item('minecraft:diamond', market_nbt='{{"sparkle":true}}')
        r = log_item_removal('pers_a', 'diamond', 2, reason='discarded')
        self.assertTrue(r['ok'])
        entries = query_trash(persona_uuid='pers_a', limit=10)
        found = [e for e in entries if e['item_id'] == 'diamond']
        self.assertTrue(len(found) >= 1)
        self.assertEqual(found[0]['market_id'], 'minecraft:diamond')
        self.assertEqual(found[0]['market_nbt'], '{{"sparkle":true}}')

    def test_log_removal_unknown_item_no_auto_resolve(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, query_trash
        r = log_item_removal('pers_a', 'mystery_ore', 3, reason='admin')
        self.assertTrue(r['ok'])
        entries = query_trash(item_id='mystery_ore')
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]['market_id'])
        self.assertIsNone(entries[0]['market_nbt'])

    # ── query_trash ─────────────────────────────────────────────

    def test_query_trash_empty(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import query_trash
        entries = query_trash()
        self.assertEqual(entries, [])

    def test_query_trash_all(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, query_trash
        log_item_removal('pers_a', 'stone', 5, reason='discarded')
        log_item_removal('pers_a', 'diamond', 1, reason='sold')
        log_item_removal('pers_b', 'wood_log', 10, reason='crafted')
        entries = query_trash()
        self.assertEqual(len(entries), 3)

    def test_query_trash_by_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, query_trash
        log_item_removal('pers_a', 'stone', 5, reason='discarded')
        log_item_removal('pers_b', 'diamond', 1, reason='sold')
        entries = query_trash(persona_uuid='pers_a')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['item_id'], 'stone')

    def test_query_trash_by_item(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, query_trash
        log_item_removal('pers_a', 'stone', 5, reason='discarded')
        log_item_removal('pers_a', 'diamond', 1, reason='sold')
        log_item_removal('pers_b', 'stone', 3, reason='crafted')
        entries = query_trash(item_id='stone')
        self.assertEqual(len(entries), 2)

    def test_query_trash_by_reason(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, query_trash
        log_item_removal('pers_a', 'stone', 5, reason='discarded')
        log_item_removal('pers_a', 'diamond', 1, reason='sold')
        log_item_removal('pers_b', 'wood', 3, reason='discarded')
        entries = query_trash(reason='discarded')
        self.assertEqual(len(entries), 2)

    def test_query_trash_pagination(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, query_trash
        for i in range(10):
            log_item_removal('pers_a', f'item_{i}', 1, reason='discarded')
        all_entries = query_trash(limit=100)
        self.assertEqual(len(all_entries), 10)
        first_page = query_trash(limit=3, offset=0)
        self.assertEqual(len(first_page), 3)
        second_page = query_trash(limit=3, offset=3)
        self.assertEqual(len(second_page), 3)
        self.assertNotEqual(
            [e['id'] for e in first_page],
            [e['id'] for e in second_page],
        )

    # ── get_trash_stats ─────────────────────────────────────────

    def test_trash_stats_empty(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import get_trash_stats
        stats = get_trash_stats()
        self.assertEqual(stats['total_entries'], 0)
        self.assertEqual(stats['total_quantity'], 0)

    def test_trash_stats_all(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, get_trash_stats
        log_item_removal('pers_a', 'stone', 5, reason='discarded')
        log_item_removal('pers_a', 'diamond', 1, reason='sold')
        log_item_removal('pers_b', 'wood', 10, reason='crafted')
        stats = get_trash_stats()
        self.assertEqual(stats['total_entries'], 3)
        self.assertEqual(stats['total_quantity'], 16)

    def test_trash_stats_by_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, get_trash_stats
        log_item_removal('pers_a', 'stone', 5, reason='discarded')
        log_item_removal('pers_a', 'diamond', 1, reason='sold')
        log_item_removal('pers_b', 'wood', 10, reason='crafted')
        stats_a = get_trash_stats(persona_uuid='pers_a')
        self.assertEqual(stats_a['total_entries'], 2)
        self.assertEqual(stats_a['total_quantity'], 6)
        stats_b = get_trash_stats(persona_uuid='pers_b')
        self.assertEqual(stats_b['total_entries'], 1)
        self.assertEqual(stats_b['total_quantity'], 10)

    def test_trash_stats_by_reason(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, get_trash_stats
        log_item_removal('pers_a', 'stone', 5, reason='discarded')
        log_item_removal('pers_a', 'diamond', 1, reason='sold')
        log_item_removal('pers_b', 'wood', 3, reason='discarded')
        stats = get_trash_stats()
        self.assertIn('discarded', stats['by_reason'])
        self.assertIn('sold', stats['by_reason'])
        self.assertEqual(stats['by_reason']['discarded']['count'], 2)
        self.assertEqual(stats['by_reason']['discarded']['quantity'], 8)
        self.assertEqual(stats['by_reason']['sold']['count'], 1)

    def test_trash_stats_top_items(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import log_item_removal, get_trash_stats
        log_item_removal('pers_a', 'stone', 5, reason='discarded')
        log_item_removal('pers_a', 'stone', 3, reason='discarded')
        log_item_removal('pers_a', 'diamond', 1, reason='sold')
        stats = get_trash_stats()
        self.assertIn('stone', stats['top_items'])
        self.assertIn('diamond', stats['top_items'])
        self.assertEqual(stats['top_items']['stone']['quantity'], 8)

    # ── get_recent_trash ────────────────────────────────────────

    def test_get_recent_trash(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            log_item_removal, get_recent_trash
        )
        log_item_removal('pers_a', 'stone', 1, reason='discarded')
        log_item_removal('pers_a', 'diamond', 1, reason='sold')
        recent = get_recent_trash('pers_a', limit=2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]['item_id'], 'diamond')

    # ── Integration: remove_item() auto-logs to trash ───────────

    def test_remove_item_logs_to_trash(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, remove_item, count_item
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import query_trash
        import uuid
        puid = str(uuid.uuid4())
        give_item(puid, 'stone', 10, 'inventory', 'persona')
        self.assertEqual(count_item(puid, 'stone', 'persona'), 10)
        removed = remove_item(puid, 'stone', 3)
        self.assertEqual(removed, 3)
        self.assertEqual(count_item(puid, 'stone', 'persona'), 7)
        entries = query_trash(persona_uuid=puid, item_id='stone')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 3)
        self.assertEqual(entries[0]['reason'], 'discarded')

    def test_remove_item_multiple_stacks_logs_all(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, remove_item
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import query_trash
        import uuid
        puid = str(uuid.uuid4())
        give_item(puid, 'wood_log', 10, 'inventory', 'persona')
        give_item(puid, 'wood_log', 5, 'storage', 'persona')
        removed = remove_item(puid, 'wood_log', 12)
        entries = query_trash(persona_uuid=puid, item_id='wood_log')
        self.assertEqual(len(entries), 2)
        total_qty = sum(e['quantity'] for e in entries)
        self.assertEqual(total_qty, 12)

    def test_remove_item_partial_stack(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, remove_item, count_item
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import query_trash
        import uuid
        puid = str(uuid.uuid4())
        give_item(puid, 'stone', 5, 'inventory', 'persona')
        removed = remove_item(puid, 'stone', 2)
        self.assertEqual(removed, 2)
        self.assertEqual(count_item(puid, 'stone', 'persona'), 3)
        entries = query_trash(persona_uuid=puid)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 2)

    # ── Integration: consume_item() auto-logs to trash ──────────

    def test_consume_item_logs_to_trash(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, consume_item, get_inventory
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import query_trash
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        import json, uuid
        puid = generate_persona(archetype_override="farmer", region_override="plains")["persona_uuid"]
        give_item(puid, 'cooked_meat', 5, 'inventory', 'persona')
        inv = get_inventory(puid)
        food_item = [i for i in inv if i['item_id'] == 'cooked_meat'][0]
        r = consume_item(puid, food_item['id'], 1)
        self.assertGreater(r['calories'], 0)
        entries = query_trash(persona_uuid=puid, item_id='cooked_meat', reason='consumed')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 1)
        self.assertIsNotNone(entries[0]['details_json'])
        details = json.loads(entries[0]['details_json'])
        self.assertIn('calories', details)

    def test_consume_item_multiple_units(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, consume_item, get_inventory
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import query_trash
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        import uuid
        puid = generate_persona(archetype_override="farmer", region_override="plains")["persona_uuid"]
        give_item(puid, 'clean_water', 10, 'inventory', 'persona')
        inv = get_inventory(puid)
        water = [i for i in inv if i['item_id'] == 'clean_water'][0]
        r = consume_item(puid, water['id'], 3)
        self.assertGreater(r['hydration'], 0)
        entries = query_trash(persona_uuid=puid, item_id='clean_water')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 3)

    # ── Integration: Trash + NBT together ───────────────────────

    def test_trash_logs_nbt_when_cached(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            register_item, log_item_removal, query_trash
        )
        nbt = '{{"enchantments":["sharpness","unbreaking"]}}'
        register_item('minecraft:diamond_pickaxe', market_nbt=nbt)
        log_item_removal('pers_a', 'diamond_pickaxe', 1, reason='broke')
        entries = query_trash(item_id='diamond_pickaxe')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['market_nbt'], nbt)

    def test_trash_with_nbt_via_remove_item(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import register_item, query_trash
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_items import give_item, remove_item
        import uuid
        puid = str(uuid.uuid4())
        nbt = '{{"components":{{"minecraft:enchantments":{{"sharpness":3}}}}}}'
        register_item('minecraft:diamond_sword', market_nbt=nbt)
        give_item(puid, 'diamond_sword', 1, 'inventory', 'persona')
        remove_item(puid, 'diamond_sword', 1)
        entries = query_trash(persona_uuid=puid, item_id='diamond_sword')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['market_nbt'], nbt)
        self.assertEqual(entries[0]['market_id'], 'minecraft:diamond_sword')

    # ── clear_trash ─────────────────────────────────────────────

    def test_clear_trash(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_item_cache import (
            log_item_removal, clear_trash, get_trash_stats
        )
        log_item_removal('pers_a', 'stone', 1)
        log_item_removal('pers_a', 'diamond', 1)
        self.assertEqual(get_trash_stats()['total_entries'], 2)
        clear_trash()
        self.assertEqual(get_trash_stats()['total_entries'], 0)
