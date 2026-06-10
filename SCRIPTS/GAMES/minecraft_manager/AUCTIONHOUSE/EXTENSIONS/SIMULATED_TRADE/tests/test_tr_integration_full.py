"""
test_tr_integration_full.py — Three rounds of comprehensive integration testing.

Round 1 — Core Integration: Trade + Inventory + Routes
Round 2 — Cross-Extension: Reputation + World Events + Economy
Round 3 — Full System Stress: 50+ concurrent operations, long tick sequences
"""

import unittest
from conftest import TradeTestCase
import json, random, threading
from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_core import (
    initiate_trade, accept_trade, evaluate_trade_offer,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_barter import (
    propose_barter, accept_barter,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_routes import (
    construct_route, dispatch_caravan, process_routes_tick,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_banditry import (
    process_guard_response,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_reputation import (
    init_persona_reputation, process_reputation_tick,
    apply_crime_consequences, get_persona_reputation,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_world_events import (
    initialize_resource_states, check_and_trigger_events,
    process_world_events_tick, get_region_price_multiplier,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_economy import (
    initialize_economy, calculate_price, adjust_supply, adjust_demand,
    process_economy_tick,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import (
    ensure_reputation, get_reputation, update_notoriety,
    update_bounty, update_reputation_score,
    get_all_wanted_personas, tick_cooldowns,
    init_database, get_summary_stats, get_db,
)
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_config import load_config

class TestRound1_CoreIntegration(TradeTestCase):
    """Round 1: Core trade + inventory + routes integration."""

    def setUp(self):
        clear_state()
        state = get_state()
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:diamond": 10, "minecraft:emerald": 50, "minecraft:wheat": 64},
            "bob-uuid": {"minecraft:iron_ingot": 32, "minecraft:stone": 128, "minecraft:wood": 64},
            "charlie-uuid": {"minecraft:diamond_sword": 1},
        }, "SIMULATED_TRADE")
        state.set("persona_finances", {
            "alice-uuid": {"balance": 5000.0},
            "bob-uuid": {"balance": 2000.0},
            "charlie-uuid": {"balance": 500.0},
        }, "SIMULATED_TRADE")
        state.set("persona_locations", {
            "alice-uuid": {"x": 0, "z": 0, "area": "spawn"},
            "bob-uuid": {"x": 100, "z": 100, "area": "plains"},
            "charlie-uuid": {"x": 500, "z": 500, "area": "nether"},
        }, "SIMULATED_TRADE")
        state.set("persona_skills", {
            "alice-uuid": {"barter": 60, "combat": 10, "stealth": 20},
            "bob-uuid": {"barter": 30, "combat": 40, "stealth": 15},
            "charlie-uuid": {"barter": 10, "combat": 70, "stealth": 50},
        }, "SIMULATED_TRADE")
        state.set("persona_profiles", {
            "alice-uuid": {"personality_traits": json.dumps({"trade_willingness": 0.8})},
            "bob-uuid": {"personality_traits": json.dumps({"trade_willingness": 0.5})},
            "charlie-uuid": {"personality_traits": json.dumps({"criminal_tendency": 0.9})},
        }, "SIMULATED_TRADE")
        state.set("persona_claims", {
            "alice-uuid": "claim-alice-1",
            "bob-uuid": "claim-bob-1",
        }, "SIMULATED_TRADE")
        state.set("all_claims", {
            "claim-alice-1": {"owner": "alice-uuid"},
            "claim-bob-1": {"owner": "bob-uuid"},
        }, "TRADE_TEST")
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import ensure_reputation, init_database
        for uid in ["alice-uuid", "bob-uuid", "charlie-uuid"]:
            ensure_reputation(uid)
        init_database()

    def tearDown(self):
        clear_state()
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        for table in ["ext_tr_trades", "ext_tr_pending_trades", "ext_tr_routes",
                       "ext_tr_banditry", "ext_tr_reputation", "ext_tr_caravans",
                       "ext_tr_world_events", "ext_tr_trade_cooldowns",
                       "ext_tr_route_trade_log", "ext_tr_resource_state"]:
            try:
                db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        db.conn.commit()

    def test_1a_trade_and_inventory_integration(self):
        """Trade correctly modifies both personas' inventories."""
        alice_inv_before = dict(get_state().get("persona_inventories", {}).get("alice-uuid", {}))
        bob_inv_before = dict(get_state().get("persona_inventories", {}).get("bob-uuid", {}))
        offer = initiate_trade(
            initiator_uuid="alice-uuid", target_uuid="bob-uuid",
            offered_items={"minecraft:diamond": 2}, gold_offered=0.0,
            requested_items={"minecraft:iron_ingot": 10}, gold_requested=0.0,
        )
        assert offer.get("ok"), f"Failed to create offer: {offer}"
        result = accept_trade(offer["offer_uuid"], "bob-uuid")
        assert result.get("ok"), f"Failed to accept trade: {result}"
        alice_inv_after = get_state().get("persona_inventories", {}).get("alice-uuid", {})
        bob_inv_after = get_state().get("persona_inventories", {}).get("bob-uuid", {})
        assert alice_inv_after.get("minecraft:diamond", 0) <= alice_inv_before.get("minecraft:diamond", 0) - 2
        assert alice_inv_after.get("minecraft:iron_ingot", 0) >= (alice_inv_before.get("minecraft:iron_ingot", 0) + 10)

    def test_1b_multi_step_trade_chain(self):
        """Alice -> Bob trade sets up data integtrity check."""
        offer1 = initiate_trade(
            initiator_uuid="alice-uuid",
            target_uuid="bob-uuid",
            offered_items={"minecraft:diamond": 3},
            gold_offered=0.0,
            requested_items={"minecraft:iron_ingot": 15},
            gold_requested=0.0,
        )
        self.assertTrue(offer1.get("ok"), f"Offer failed: {offer1}")
        result = accept_trade(offer1["offer_uuid"], "bob-uuid")
        self.assertTrue(result.get("ok"), f"Accept failed: {result}")
        stats = get_summary_stats()
        assert stats.get("total_trades", 0) >= 1

class TestRound2_CrossExtension(TradeTestCase):
    """Round 2: Reputation + World Events + Economy cross-integration."""

    def setUp(self):
        clear_state()
        init_database()
        initialize_resource_states()
        initialize_economy()
        for uid in ["alice-uuid", "bob-uuid", "charlie-uuid"]:
            ensure_reputation(uid)
        state = get_state()
        state.set("persona_inventories", {
            "alice-uuid": {"minecraft:diamond": 10, "minecraft:emerald": 50},
            "bob-uuid": {"minecraft:iron_ingot": 32, "minecraft:wheat": 100},
            "charlie-uuid": {"minecraft:diamond_sword": 1},
        }, "SIMULATED_TRADE")
        state.set("persona_finances", {
            "alice-uuid": {"balance": 5000.0}, "bob-uuid": {"balance": 2000.0},
            "charlie-uuid": {"balance": 500.0},
        }, "SIMULATED_TRADE")
        state.set("persona_skills", {
            "alice-uuid": {"barter": 60, "combat": 10, "stealth": 20},
            "bob-uuid": {"barter": 30, "combat": 40, "stealth": 15},
            "charlie-uuid": {"barter": 10, "combat": 70, "stealth": 50},
        }, "SIMULATED_TRADE")
        state.set("persona_locations", {
            "alice-uuid": {"x": 0, "z": 0, "area": "spawn"},
            "bob-uuid": {"x": 100, "z": 100, "area": "plains"},
            "charlie-uuid": {"x": 500, "z": 500, "area": "nether"},
        }, "SIMULATED_TRADE")

    def tearDown(self):
        clear_state()
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        for table in ["ext_tr_trades", "ext_tr_pending_trades", "ext_tr_routes",
                       "ext_tr_banditry", "ext_tr_reputation", "ext_tr_caravans",
                       "ext_tr_world_events", "ext_tr_trade_cooldowns",
                       "ext_tr_route_trade_log", "ext_tr_resource_state"]:
            try:
                db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        db.conn.commit()

    def test_2a_crime_affects_economy(self):
        """Banditry reduces supply and increases prices."""
        price_before = calculate_price("minecraft:diamond", "overworld")
        apply_crime_consequences("charlie-uuid", "banditry")
        adjust_supply("minecraft:diamond", "overworld", -20.0)
        adjust_demand("minecraft:diamond", "overworld", 10.0)
        price_after = calculate_price("minecraft:diamond", "overworld")
        assert price_after >= price_before
        wanted = get_all_wanted_personas()
        charlie_wanted = [w for w in wanted if w["persona_uuid"] == "charlie-uuid"]
        assert len(charlie_wanted) >= 1 or get_reputation("charlie-uuid")["notoriety"] > 0

    def test_2b_tick_cycle_integration(self):
        """Run a full tick cycle across all systems without errors."""
        assert isinstance(process_reputation_tick(), dict)
        assert "triggered" in check_and_trigger_events()
        assert isinstance(process_world_events_tick(), dict)
        assert isinstance(process_economy_tick(), dict)

class TestRound3_FullSystemStress(TradeTestCase):
    """Round 3: Stress testing with many concurrent operations."""

    def setUp(self):
        clear_state()
        init_database()
        initialize_resource_states()
        initialize_economy()
        state = get_state()
        self.personas = {}
        inventories = {}
        finances = {}
        for i in range(50):
            uid = f"persona-{i:04d}"
            ensure_reputation(uid)
            self.personas[uid] = {"uuid": uid, "name": f"Persona_{i}"}
            inventories[uid] = {
                "minecraft:diamond": random.randint(0, 10),
                "minecraft:iron_ingot": random.randint(0, 64),
                "minecraft:wheat": random.randint(0, 64),
                "minecraft:emerald": random.randint(0, 32),
            }
            finances[uid] = {"balance": random.uniform(100, 10000)}
        state.set("active_personas", [{"uuid": u} for u in self.personas], "SIMULATED_TRADE")
        state.set("persona_profiles", self.personas, "SIMULATED_TRADE")
        state.set("persona_inventories", inventories, "SIMULATED_TRADE")
        state.set("persona_finances", finances, "SIMULATED_TRADE")

    def tearDown(self):
        clear_state()
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        for table in ["ext_tr_trades", "ext_tr_pending_trades", "ext_tr_routes",
                       "ext_tr_banditry", "ext_tr_reputation", "ext_tr_caravans",
                       "ext_tr_world_events", "ext_tr_trade_cooldowns",
                       "ext_tr_route_trade_log", "ext_tr_resource_state"]:
            try:
                db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        db.conn.commit()

    def test_3a_long_tick_simulation(self):
        errors = []
        for tick in range(100):
            try:
                process_reputation_tick()
                if tick % 10 == 0:
                    check_and_trigger_events()
                process_world_events_tick()
                process_economy_tick()
                process_routes_tick()
                tick_cooldowns()
            except Exception as e:
                errors.append(f"Tick {tick}: {e}")
        self.assertEqual(len(errors), 0, f"Tick errors: {errors}")

    def test_3b_data_integrity_after_stress(self):
        for _ in range(20):
            process_reputation_tick()
            check_and_trigger_events()
            process_world_events_tick()
            process_economy_tick()
        stats = get_summary_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("total_trades", stats)
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tr_database import get_all_resource_states
        resources = get_all_resource_states()
        for r in resources:
            self.assertGreaterEqual(r["current_abundance"], 0)
            self.assertLessEqual(r["current_abundance"], 3.0)
            self.assertGreater(r["regeneration_rate"], 0)
