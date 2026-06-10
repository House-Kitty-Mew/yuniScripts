"""
test_ah_config.py — Tests for Auction House configuration.

Tests cover:
  - Config singleton and loading
  - Value types and ranges
  - Alias function cfg()
  - Default values
"""

import unittest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["AH_DB_PATH"] = ":memory:"

from AUCTIONHOUSE.ah_config import get_config, cfg


class TestConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = get_config(reload=True)

    def test_01_config_exists(self):
        """Config should load and return a Config instance."""
        self.assertIsNotNone(self.cfg)

    def test_02_deepseek_config(self):
        """DeepSeek config values should have defaults."""
        self.assertEqual(self.cfg.deepseek_model, "deepseek-chat")
        self.assertGreater(self.cfg.deepseek_temperature, 0)
        self.assertLess(self.cfg.deepseek_temperature, 2)
        self.assertGreater(self.cfg.deepseek_max_tokens, 100)
        self.assertGreater(self.cfg.deepseek_timeout_seconds, 0)

    def test_03_simulation_config(self):
        """Simulation config should have reasonable defaults."""
        self.assertIsInstance(self.cfg.simulation_enabled, bool)
        self.assertGreater(self.cfg.simulation_interval_minutes, 0)
        self.assertLess(self.cfg.simulation_interval_minutes, 10080)  # < 1 week
        self.assertGreater(self.cfg.simulation_retry_count, 0)
        self.assertLess(self.cfg.simulation_retry_count, 10)
        self.assertGreater(self.cfg.simulation_retry_delay_base, 0)

    def test_04_event_config(self):
        """Event frequency config should have reasonable intervals."""
        self.assertLess(self.cfg.event_small_interval_hours, self.cfg.event_medium_interval_hours)
        self.assertLess(self.cfg.event_medium_interval_hours, self.cfg.event_rare_interval_hours)
        self.assertLess(self.cfg.event_rare_interval_hours, self.cfg.event_major_interval_hours)
        self.assertGreater(self.cfg.event_major_cooldown_days, 0)
        self.assertLess(self.cfg.event_major_cooldown_days, 365)

    def test_05_auction_rules(self):
        """Auction rule defaults should be sensible."""
        self.assertGreater(self.cfg.max_listings_per_player, 0)
        self.assertLess(self.cfg.max_listings_per_player, 100)
        self.assertGreater(self.cfg.min_bid_increment_pct, 0)
        self.assertLess(self.cfg.min_bid_increment_pct, 100)
        self.assertGreater(self.cfg.auction_duration_default_hours, 0)
        self.assertLess(self.cfg.auction_duration_default_hours, 168)
        self.assertGreater(self.cfg.listing_fee_emeralds, 0)

    def test_06_fee_config(self):
        """Fee percentages should be valid."""
        self.assertGreaterEqual(self.cfg.sale_fee_pct, 0)
        self.assertLess(self.cfg.sale_fee_pct, 100)
        self.assertGreaterEqual(self.cfg.bin_fee_pct, 0)
        self.assertLess(self.cfg.bin_fee_pct, 100)
        self.assertGreater(self.cfg.cancel_after_bids_fee, 0)

    def test_07_rare_item_config(self):
        """Rare item generation probabilities should sum to < 1."""
        total = (self.cfg.rare_item_ultra_rare_chance +
                 self.cfg.rare_item_rare_chance +
                 self.cfg.rare_item_uncommon_chance +
                 self.cfg.rare_item_none_chance_per_cycle)
        self.assertAlmostEqual(total, 0.96, places=2)

    def test_08_currency_config(self):
        """Currency defaults should be sensible."""
        self.assertEqual(self.cfg.currency_item_id, "minecraft:emerald")
        self.assertGreater(len(self.cfg.currency_name), 0)
        self.assertGreater(len(self.cfg.currency_symbol), 0)

    def test_09_stale_threshold(self):
        """Stale threshold should be positive and reasonable."""
        self.assertGreater(self.cfg.stale_hours_threshold, 0)
        self.assertLess(self.cfg.stale_hours_threshold, 720)  # < 30 days

    def test_10_cfg_alias(self):
        """cfg() alias should return the same config."""
        self.assertEqual(cfg(), get_config())
        self.assertEqual(cfg().deepseek_model, get_config().deepseek_model)

    def test_11_config_to_dict(self):
        """Config should convert to dict."""
        d = self.cfg.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("deepseek_model", d)
        self.assertIn("simulation_interval_minutes", d)

    def test_12_config_reload(self):
        """Reloading config should work."""
        cfg2 = get_config(reload=True)
        self.assertIsNotNone(cfg2)
        self.assertEqual(cfg2.deepseek_model, "deepseek-chat")

    def test_13_rcon_defaults(self):
        """RCON config should have defaults."""
        self.assertGreater(len(self.cfg.rcon_host), 0)
        self.assertGreater(self.cfg.rcon_port, 0)
        self.assertLess(self.cfg.rcon_port, 65536)

    def test_14_stale_interval(self):
        """stale_hours_threshold should be less than auction_duration_max_hours."""
        self.assertLess(self.cfg.stale_hours_threshold, self.cfg.auction_duration_max_hours)

    def test_15_sim_price_bounds(self):
        """Simulated price bounds should be sensible."""
        self.assertGreater(self.cfg.sim_price_min, 0)
        self.assertGreater(self.cfg.sim_price_max, self.cfg.sim_price_min)
        self.assertLess(self.cfg.sim_price_max, 1000000)

    def test_16_event_max_overlap(self):
        """Event max overlap should be > 0 and < 10."""
        self.assertGreater(self.cfg.event_max_active_overlap, 0)
        self.assertLess(self.cfg.event_max_active_overlap, 10)

    def test_17_auction_duration_max(self):
        """Max auction duration should be at least default."""
        self.assertGreater(self.cfg.auction_duration_max_hours, self.cfg.auction_duration_default_hours)


if __name__ == "__main__":
    unittest.main()
