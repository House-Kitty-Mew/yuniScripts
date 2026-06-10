"""
test_ah_logger.py — Tests for ah_logger.py
"""

from conftest import AHTestCase
import json, os
from datetime import datetime


class TestLogger(AHTestCase):

    def setUp(self):
        super().setUp()
        from AUCTIONHOUSE.ah_logger import get_logger, LOG_DIR
        import AUCTIONHOUSE.ah_logger as logger_mod
        logger_mod._instances.clear()
        self.log = get_logger(verbose=False)
        self.log_dir = LOG_DIR

    def test_info_log(self):
        self.log.info("test_source", "Test info message")
        content = open(self.log_dir / "auctionhouse.log").read()
        self.assertIn("INFO", content)
        self.assertIn("test_source", content)
        self.assertIn("Test info message", content)

    def test_warn_log(self):
        self.log.warn("test", "Warning!")
        content = open(self.log_dir / "auctionhouse.log").read()
        self.assertIn("WARN", content)
        self.assertIn("Warning!", content)

    def test_error_log(self):
        self.log.error("test", "Error!")
        content = open(self.log_dir / "auctionhouse.log").read()
        self.assertIn("ERROR", content)
        self.assertIn("Error!", content)

    def test_info_with_data(self):
        self.log.info("test", "Has data", {"key": "value", "num": 42})
        content = open(self.log_dir / "auctionhouse.log").read()
        self.assertIn("Has data", content)
        self.assertIn("key", content)

    def test_transaction_jsonl(self):
        self.log.transaction("list", {"listing_uuid": "test-uuid", "actor": "steve",
                                       "item_id": "minecraft:diamond", "price": 50.0})
        today = datetime.now().strftime("%Y-%m-%d")
        tx_path = self.log_dir / f"transactions_{today}.jsonl"
        self.assertTrue(tx_path.exists())
        last = json.loads(open(tx_path).read().strip().split("\n")[-1])
        self.assertEqual(last["type"], "transaction")
        self.assertEqual(last["subtype"], "list")
        self.assertEqual(last["details"]["listing_uuid"], "test-uuid")
        self.assertIn("_log_id", last)
        self.assertIn("_timestamp_iso", last)

    def test_ai_simulation_jsonl(self):
        from AUCTIONHOUSE.ah_logger import AI_LOG
        self.log.ai_simulation(prompt="Test prompt", response="Test response", duration_ms=1234.5, metadata={"cycles": 1})
        self.assertTrue(AI_LOG.exists())
        last = json.loads(open(AI_LOG).read().strip().split("\n")[-1])
        self.assertEqual(last["type"], "simulation_cycle")
        self.assertIn("prompt_full", last)
        self.assertEqual(last["duration_ms"], 1234.5)

    def test_market_event_jsonl(self):
        from AUCTIONHOUSE.ah_logger import EVENTS_LOG
        self.log.market_event("start", {"event_name": "winter", "title": "Winter!"})
        self.assertTrue(EVENTS_LOG.exists())
        last = json.loads(open(EVENTS_LOG).read().strip().split("\n")[-1])
        self.assertEqual(last["subtype"], "start")
        self.assertEqual(last["details"]["event_name"], "winter")

    def test_debug_verbose_only(self):
        from AUCTIONHOUSE.ah_logger import AuctionHouseLogger
        quiet_log = AuctionHouseLogger(verbose=False)
        # Check the log content BEFORE our message (may have pre-existing content)
        log_path = self.log_dir / "auctionhouse.log"
        before_content = open(log_path).read() if log_path.exists() else ""
        # Write our debug message
        quiet_log.debug("test", "QUIET_DEBUG_SENTINEL")
        content = open(log_path).read()
        # Our sentinel should NOT be in the content (verbose=False means skip debug)
        new_content = content[len(before_content):]
        self.assertNotIn("QUIET_DEBUG_SENTINEL", new_content)

    def test_debug_verbose_true(self):
        from AUCTIONHOUSE.ah_logger import AuctionHouseLogger
        verbose_log = AuctionHouseLogger(verbose=True)
        verbose_log.debug("test", "Verbose debug")
        content = open(self.log_dir / "auctionhouse.log").read()
        self.assertIn("DEBUG", content)

    def test_log_id_and_timestamp(self):
        from AUCTIONHOUSE.ah_logger import AI_LOG
        self.log.ai_simulation("p", "r", 100)
        entry = json.loads(open(AI_LOG).read().strip().split("\n")[-1])
        self.assertIn("_log_id", entry)
        self.assertIn("_timestamp_iso", entry)
        self.assertEqual(len(entry["_log_id"]), 36)

    def test_multiple_logs_no_crosstalk(self):
        for i in range(10):
            self.log.info("test", f"Message {i}")
        content = open(self.log_dir / "auctionhouse.log").read()
        for i in range(10):
            self.assertIn(f"Message {i}", content)


if __name__ == "__main__":
    from unittest import main
    main()
