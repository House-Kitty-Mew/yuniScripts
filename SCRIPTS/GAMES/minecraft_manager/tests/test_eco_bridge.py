"""Test economy bridge operations with the live remote server."""
import unittest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ECO_BRIDGE.eco_bridge_client import RemoteEconomyBridge

BRIDGE_HOST = os.environ.get("ECO_BRIDGE_HOST", "192.168.1.118")
BRIDGE_PORT = int(os.environ.get("ECO_BRIDGE_PORT", "7200"))
BRIDGE_KEY = os.environ.get("ECO_BRIDGE_KEY", "change-me-bridge-key-2026!!")

class TestEconomyBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = RemoteEconomyBridge(
            host=BRIDGE_HOST, port=BRIDGE_PORT,
            password=BRIDGE_KEY, timeout=3.0)
    def test_01_ping(self):
        ok = self.bridge.ping()
        self.assertTrue(ok)
    def test_02_stats(self):
        stats = self.bridge.get_economy_stats()
        self.assertIn("player_count", stats)
        self.assertGreaterEqual(stats.get("player_count", 0), 0)
        self.assertIn("total_coins", stats)
    def test_03_balance_known_player(self):
        bal = self.bridge.get_balance("Coochie_Tickler")
        self.assertIsNotNone(bal)
        self.assertGreater(bal, 0)
    def test_04_balance_unknown_player(self):
        bal = self.bridge.get_balance("NobodyHere123")
        self.assertIsNone(bal)
    def test_05_balance_by_name_case(self):
        bal1 = self.bridge.get_balance("Coochie_Tickler")
        bal2 = self.bridge.get_balance("coochie_tickler")
        self.assertEqual(bal1, bal2)
    def test_06_player_info(self):
        info = self.bridge.get_player_info("Coochie_Tickler")
        self.assertIsNotNone(info)
        self.assertIn("player_uuid", info)
        self.assertIn("balance", info)
        self.assertIn("name_hint", info)
    def test_07_deduct_and_credit(self):
        info = self.bridge.get_player_info("Coochie_Tickler")
        before = info["balance"]
        uuid = info["player_uuid"]
        # Deduct 1
        ok = self.bridge.deduct(uuid, 1, "AH_TEST")
        self.assertTrue(ok)
        mid = self.bridge.get_balance("Coochie_Tickler")
        self.assertEqual(mid, before - 1)
        # Credit back
        ok = self.bridge.credit(uuid, 1, "AH_TEST")
        self.assertTrue(ok)
        after = self.bridge.get_balance("Coochie_Tickler")
        self.assertEqual(after, before)
    def test_08_deduct_insufficient(self):
        info = self.bridge.get_player_info("Minewolf3D")
        uuid = info["player_uuid"]
        before = info["balance"]
        # Try to deduct more than balance
        ok = self.bridge.deduct(uuid, before + 999999, "AH_TEST_LIMIT")
        self.assertFalse(ok)
        after = self.bridge.get_balance("Minewolf3D")
        self.assertEqual(after, before, "Balance should not change")
    def test_09_ledger(self):
        info = self.bridge.get_player_info("Coochie_Tickler")
        uuid = info["player_uuid"]
        entries = self.bridge.get_ledger(uuid, limit=5)
        self.assertGreater(len(entries), 0)
        self.assertIn("reason", entries[0])
        self.assertIn("delta", entries[0])
    def test_10_name_resolution(self):
        """Test that deduct/credit work with player names, not just UUIDs."""
        info = self.bridge.get_player_info("Coochie_Tickler")
        before = info["balance"]
        ok = self.bridge.deduct("Coochie_Tickler", 1, "AH_TEST_NAME")
        self.assertTrue(ok, "Name-based deduct should work")
        self.bridge.credit("Coochie_Tickler", 1, "AH_TEST_NAME")
        after = self.bridge.get_balance("Coochie_Tickler")
        self.assertEqual(after, before)

if __name__ == "__main__":
    unittest.main()
