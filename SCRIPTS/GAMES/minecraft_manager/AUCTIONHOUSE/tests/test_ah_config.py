"""
test_ah_config.py — Tests for ah_config.py
"""

from conftest import AHTestCase
from AUCTIONHOUSE.ah_config import get_config


class TestConfig(AHTestCase):

    def test_config_defaults_loaded(self):
        c = get_config(reload=True)
        self.assertEqual(c.deepseek_model, "deepseek-v4-flash")
        self.assertEqual(c.simulation_interval_minutes, 360)
        self.assertEqual(c.max_listings_per_player, 5)
        self.assertEqual(c.min_bid_increment_pct, 10)
        self.assertEqual(c.listing_fee_emeralds, 0.5)
        self.assertEqual(c.sale_fee_pct, 2.0)
        self.assertEqual(c.currency_item_id, "minecraft:emerald")
        self.assertEqual(c.currency_name, "Emerald")
        self.assertEqual(c.sim_price_min, 0.01)
        self.assertEqual(c.sim_price_max, 500.0)

    def test_config_type_correctness(self):
        c = get_config()
        self.assertIsInstance(c.deepseek_api_key, str)
        self.assertIsInstance(c.deepseek_temperature, float)
        self.assertIsInstance(c.simulation_enabled, bool)
        self.assertIsInstance(c.max_listings_per_player, int)
        self.assertIsInstance(c.sale_fee_pct, float)
        self.assertIsInstance(c.event_max_active_overlap, int)
        self.assertIsInstance(c.rcon_host, str)
        self.assertIsInstance(c.rcon_port, int)

    def test_config_to_dict(self):
        c = get_config()
        d = c.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("deepseek_model", d)
        self.assertIn("max_listings_per_player", d)
        self.assertIn("rcon_host", d)

    def test_config_currency_settings(self):
        c = get_config()
        self.assertEqual(c.currency_item_id, "minecraft:emerald")
        self.assertEqual(c.currency_name, "Emerald")

    def test_config_event_intervals(self):
        c = get_config()
        self.assertLess(c.event_small_interval_hours, c.event_medium_interval_hours)
        self.assertLess(c.event_medium_interval_hours, c.event_rare_interval_hours)
        self.assertLess(c.event_rare_interval_hours, c.event_major_interval_hours)

    def test_config_simulation_settings(self):
        c = get_config()
        self.assertGreater(c.simulation_retry_count, 0)
        self.assertGreater(c.sim_max_listings_per_cycle, 0)
        self.assertGreater(c.sim_ultra_rare_chance, 0)

    def test_config_rare_item_probabilities(self):
        c = get_config()
        total = c.rare_item_ultra_rare_chance + c.rare_item_rare_chance + c.rare_item_uncommon_chance + c.rare_item_none_chance_per_cycle
        # Configuration values are: 0.01 + 0.05 + 0.15 + 0.75 = 0.96
        # The remaining 4% is reserved for AI override decisions
        self.assertAlmostEqual(total, 0.96, places=5)

    def test_config_reload(self):
        c1 = get_config(reload=True)
        c2 = get_config(reload=True)
        self.assertEqual(c1.deepseek_model, c2.deepseek_model)


if __name__ == "__main__":
    from unittest import main
    main()
