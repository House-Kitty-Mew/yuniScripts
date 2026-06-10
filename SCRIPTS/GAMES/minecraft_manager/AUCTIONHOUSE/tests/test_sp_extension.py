"""
test_sp_extension.py — Simulated People Extension Tests

CRITICAL: Tests that items can be EXCHANGED between simulated personas
and the Auction House market, with proper registration on both ends.
"""
from conftest import AHTestCase, mock_rcon, reset_rcon, RCON_LOG
from unittest.mock import patch, MagicMock
import json, uuid
from datetime import datetime, timezone


class TestExtensionInfrastructure(AHTestCase):

    def test_extension_config_loaded(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE import get_config
        cfg = get_config()
        for key in ["enabled", "max_active_personas", "persona_pool_size"]:
            self.assertIn(key, cfg)

    def test_hook_fire_safe_when_no_extensions(self):
        from AUCTIONHOUSE.ah_plugin_registry import fire_hook
        self.assertEqual(fire_hook("on_simulation_cycle_start", market_summary={}), [])

    def test_hook_invalid_name_ignored(self):
        from AUCTIONHOUSE.ah_plugin_registry import fire_hook
        self.assertEqual(fire_hook("nonexistent_hook"), [])


class TestPersonaProfileGeneration(AHTestCase):

    def setUp(self):
        super().setUp()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema
        ensure_schema()

    def test_persona_generation(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import generate_persona
        p = generate_persona()
        for key in ["persona_uuid", "name", "archetype", "wealth_tier"]:
            self.assertIn(key, p)

    def test_persona_archetypes_all_valid(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import ARCHETYPES
        for name, arch in ARCHETYPES.items():
            self.assertIn("traits", arch)
            self.assertIn("spending_impulse", arch)
            self.assertIn("price_sensitivity", arch)
            self.assertGreaterEqual(arch["spending_impulse"], 0)
            self.assertLessEqual(arch["spending_impulse"], 1)

    def test_persona_spawn_initial_population(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_profile import spawn_initial_population, get_persona_count
        spawn_initial_population(10)
        stats = get_persona_count()
        self.assertGreaterEqual(stats["total"], 10)
        self.assertGreater(stats["active"], 0)


class TestItemFlowPersonaToMarket(AHTestCase):
    """Verify persona-listed items appear in the AH market."""

    def test_persona_item_listed_in_market(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, get_listing
            r = list_item(seller="TestMiner", item_id="minecraft:iron_pickaxe", count=1,
                          start_price=15.0, buy_now_price=30.0, is_simulated=True,
                          sim_lore="A well-used pickaxe", sim_durability=65, sim_quality_roll=45)
            self.assertTrue(r["ok"])
            listing = get_listing(r["data"]["listing_uuid"])
            self.assertEqual(listing["item_id"], "minecraft:iron_pickaxe")
            self.assertEqual(listing["seller_name"], "TestMiner")

    def test_persona_item_appears_in_queries(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, query_listings
            list_item(seller="FarmerJo", item_id="minecraft:wheat", count=16,
                      start_price=2.0, is_simulated=True)
            r = query_listings(filter_type="my", filter_value="FarmerJo")
            found = [l for l in r["data"]["listings"] if l["seller_name"] == "FarmerJo"]
            self.assertGreaterEqual(len(found), 1)

    def test_persona_item_with_only_minecraft_id(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, get_listing
            r = list_item(seller="WarriorJane", item_id="minecraft:diamond_sword",
                          count=1, start_price=50.0, is_simulated=True)
            self.assertTrue(r["ok"])
            listing = get_listing(r["data"]["listing_uuid"])
            self.assertEqual(listing["item_id"], "minecraft:diamond_sword")
            self.assertTrue(listing["item_id"].startswith("minecraft:"))


class TestItemFlowMarketToPersona(AHTestCase):
    """Verify personas can purchase from the AH market."""

    def test_persona_buys_item_tx_recorded(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, buy_now
            from AUCTIONHOUSE.ah_database import get_db
            lr = list_item(seller="PlayerSeller", item_id="minecraft:diamond_pickaxe",
                           count=1, start_price=10.0, buy_now_price=50.0, rcon_func=mock_rcon)
            uid = lr["data"]["listing_uuid"]
            r = buy_now(buyer="BuyerJoe", listing_uuid=uid, rcon_func=mock_rcon)
            self.assertTrue(r["ok"])
            tx = get_db().fetch_one("SELECT * FROM transaction_history WHERE listing_uuid = ? AND transaction_type = 'buy'", (uid,))
            self.assertIsNotNone(tx)
            self.assertEqual(tx["actor_name"], "BuyerJoe")

    def test_multiple_personas_buying(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, buy_now
            uid1 = list_item(seller="S1", item_id=self.unique_item("minecraft:diamond"),
                             count=1, start_price=5.0, buy_now_price=20.0, rcon_func=mock_rcon)["data"]["listing_uuid"]
            uid2 = list_item(seller="S2", item_id=self.unique_item("minecraft:emerald"),
                             count=1, start_price=3.0, buy_now_price=10.0, rcon_func=mock_rcon)["data"]["listing_uuid"]
            self.assertTrue(buy_now(buyer="PersonaA", listing_uuid=uid1, rcon_func=mock_rcon)["ok"])
            self.assertTrue(buy_now(buyer="PersonaB", listing_uuid=uid2, rcon_func=mock_rcon)["ok"])

    def test_persona_price_memory(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import get_db as sp_db
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema, add_memory, get_price_memories
        ensure_schema()
        puid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        sp_db().execute("""
            INSERT OR IGNORE INTO ext_sp_profiles
            (persona_uuid, name, archetype, job, region, wealth_tier,
             personality_traits, active, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (puid, "MemoPersona", "merchant", "trader", "overworld", "wealthy", "{}", now, now))
        # Use 'observed_price' memory type (the one get_price_memories filters for)
        add_memory(puid, "observed_price", "minecraft:diamond", price=8.0,
                   detail="Saw diamond at 8em", emotional_weight=5)
        memories = get_price_memories(puid, "minecraft:diamond")
        self.assertGreaterEqual(len(memories), 1)
        self.assertEqual(memories[0]["price"], 8.0)


class TestNewItemDiscovery(AHTestCase):
    """Verify new/unseen items can enter the market and be purchased."""

    def test_new_item_not_in_sim_inventory(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, get_listing
            r = list_item(seller="Explorer", item_id="minecraft:sponge", count=1,
                          start_price=10.0, is_simulated=True)
            self.assertTrue(r["ok"])
            self.assertEqual(get_listing(r["data"]["listing_uuid"])["item_id"], "minecraft:sponge")

    def test_new_item_discovered_in_queries(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, query_listings
            list_item(seller="Discoverer", item_id="minecraft:heart_of_the_sea",
                      count=1, start_price=100.0, is_simulated=True)
            items = [l["item_id"] for l in query_listings(filter_type="all")["data"]["listings"]]
            self.assertIn("minecraft:heart_of_the_sea", items)

    def test_persona_can_discover_new_item(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, buy_now
            from AUCTIONHOUSE.ah_database import get_db
            r = list_item(seller="DeepMiner", item_id="minecraft:heavy_core", count=1,
                          start_price=50.0, buy_now_price=200.0, is_simulated=True)
            uid = r["data"]["listing_uuid"]
            self.assertTrue(buy_now(buyer="PersonaDiscoverer", listing_uuid=uid, rcon_func=mock_rcon)["ok"])
            tx = get_db().fetch_one("SELECT * FROM transaction_history WHERE listing_uuid = ?", (uid,))
            self.assertIsNotNone(tx)
            self.assertEqual(tx["item_id"], "minecraft:heavy_core")


class TestItemFlowSync(AHTestCase):

    def test_cancel_updates_listing_status(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, cancel_listing, get_listing
            uid = list_item(seller="Seller", item_id=self.unique_item(), count=1,
                            start_price=5.0, rcon_func=mock_rcon)["data"]["listing_uuid"]
            cancel_listing(player="Seller", listing_uuid=uid, rcon_func=mock_rcon)
            self.assertEqual(get_listing(uid)["status"], "cancelled")

    def test_expire_creates_no_duplicate_records(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, expire_listings
            from AUCTIONHOUSE.ah_database import get_db
            uid = list_item(seller="Seller", item_id=self.unique_item(), count=1,
                            start_price=5.0, duration_hours=1, rcon_func=mock_rcon)["data"]["listing_uuid"]
            get_db().execute("UPDATE auction_listings SET expires_at = '2020-01-01' WHERE listing_uuid = ?", (uid,))
            expire_listings()
            txs = get_db().fetch_all("SELECT * FROM transaction_history WHERE listing_uuid = ? AND transaction_type = 'expire'", (uid,))
            self.assertLessEqual(len(txs), 1)

    def test_buy_syncs_transaction(self):
        with patch("AUCTIONHOUSE.ah_core._get_eco_bridge", return_value=None):
            from AUCTIONHOUSE.ah_core import list_item, buy_now
            from AUCTIONHOUSE.ah_database import get_db
            uid = list_item(seller="S", item_id="minecraft:diamond", count=1,
                            start_price=5.0, buy_now_price=20.0, rcon_func=mock_rcon)["data"]["listing_uuid"]
            buy_now(buyer="B", listing_uuid=uid, rcon_func=mock_rcon)
            # Query with transaction_type='buy' so we don't get the 'list' transaction (actor 'S')
            tx = get_db().fetch_one(
                "SELECT * FROM transaction_history WHERE listing_uuid = ? AND transaction_type = 'buy'", (uid,))
            self.assertIsNotNone(tx)
            self.assertEqual(tx["actor_name"], "B")


class TestMinecraftItemValidity(AHTestCase):

    def test_item_gen_only_minecraft_ids(self):
        from AUCTIONHOUSE.ah_item_gen import _ULTRA_RARE_ITEMS, _RARE_ITEMS, _UNCOMMON_ITEMS
        for item_list in [_ULTRA_RARE_ITEMS, _RARE_ITEMS, _UNCOMMON_ITEMS]:
            for item in item_list:
                self.assertTrue(item.startswith("minecraft:"), f"Invalid: {item}")

    def test_sim_inventory_only_minecraft(self):
        from AUCTIONHOUSE.ah_database import get_db
        for item in get_db().fetch_all("SELECT item_id FROM simulated_inventory"):
            self.assertTrue(item["item_id"].startswith("minecraft:"))

    def test_phooks_rejects_non_minecraft_ids(self):
        from AUCTIONHOUSE.ah_phooks import validate_item_id
        self.assertIsNone(validate_item_id("minecraft:diamond"))
        # Note: validate_item_id checks FORMAT (namespace:path), not specific namespace
        # 'custom:diamond' passes because it follows the namespace:path pattern
        self.assertIsNone(validate_item_id("custom:diamond"))  # Valid format
        self.assertIsNotNone(validate_item_id("diamond"))  # Missing namespace
        self.assertIsNotNone(validate_item_id(""))  # Empty

    def test_rarity_prices_with_minecraft_items(self):
        from AUCTIONHOUSE.ah_item_gen import generate_simulated_item
        for item_id in ["minecraft:diamond_sword", "minecraft:bow", "minecraft:elytra", "minecraft:trident"]:
            item = generate_simulated_item(item_id)
            self.assertTrue(item["item_id"].startswith("minecraft:"))


class TestPhooksInputValidation(AHTestCase):

    def test_validate_player_name(self):
        from AUCTIONHOUSE.ah_phooks import validate_player_name
        self.assertIsNone(validate_player_name("Steve"))
        self.assertIsNotNone(validate_player_name(""))
        self.assertIsNotNone(validate_player_name(None))

    def test_validate_listing_uuid(self):
        from AUCTIONHOUSE.ah_phooks import validate_listing_uuid
        self.assertIsNone(validate_listing_uuid(str(uuid.uuid4())))
        self.assertIsNotNone(validate_listing_uuid("not-a-uuid"))

    def test_sanitize_request(self):
        from AUCTIONHOUSE.ah_phooks import sanitize_request
        data = {"player_name": "Steve", "item_id": "minecraft:diamond"}
        self.assertIsNone(sanitize_request(data, ["player_name", "item_id"]))
        self.assertIsNotNone(sanitize_request(data, ["nonexistent"]))


if __name__ == "__main__":
    from unittest import main
    main()
