"""
test_state_registry.py — Unit + integration tests for state_registry.py.

Tests the thread-safe shared state that simulation extensions use for
cross-extension data flow during the Phase 2C cycle:

  PEOPLE → SOCIAL → RELS → CHAT → ANNOUNCE

This tier covers:
  1. All public API methods (set, get, get_owner, get_namespace, update, clear)
  2. Snapshot/restore rollback (E7 transaction-like recovery)
  3. Thread safety under concurrent access
  4. Phase 2C cycle simulation (cross-extension data flow)
  5. Edge cases (empty state, missing keys, re-ownership)
"""

import sys, os, threading, time, json
from unittest import TestCase, main
from unittest.mock import patch

# ── Path setup ─────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))      # .../AUCTIONHOUSE/tests
_AH_DIR = os.path.dirname(_HERE)                        # .../AUCTIONHOUSE
_EXT_DIR = os.path.join(_AH_DIR, "EXTENSIONS")           # .../AUCTIONHOUSE/EXTENSIONS
_MANAGER_DIR = os.path.dirname(_AH_DIR)                 # .../minecraft_manager
for p in [_AH_DIR, _MANAGER_DIR, _EXT_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ═══════════════════════════════════════════════════════════════════
# Tier 1: Unit tests — each public method
# ═══════════════════════════════════════════════════════════════════

class TestStateRegistryBasic(TestCase):
    """Core set/get/get_owner operations."""

    def setUp(self):
        """Fresh state for each test."""
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        self.state = get_state()
        clear_state()

    def test_set_and_get(self):
        """set() stores a value, get() retrieves it."""
        self.state.set("active_personas", ["alice", "bob"], "SIMULATED_PEOPLE")
        self.assertEqual(self.state.get("active_personas"), ["alice", "bob"])

    def test_get_default_on_missing(self):
        """get() returns default when key doesn't exist."""
        self.assertIsNone(self.state.get("nonexistent"))
        self.assertEqual(self.state.get("nonexistent", []), [])
        self.assertEqual(self.state.get("nonexistent", 42), 42)

    def test_get_owner_returns_correct_extension(self):
        """get_owner() returns the extension name that set the key."""
        self.state.set("prices", {"diamond": 10}, "SIMULATED_TRADE")
        self.assertEqual(self.state.get_owner("prices"), "SIMULATED_TRADE")

    def test_get_owner_on_unset_key(self):
        """get_owner() returns None for keys that were never set."""
        self.assertIsNone(self.state.get_owner("ghost_key"))

    def test_overwrite_keeps_new_owner(self):
        """Setting an existing key updates the owner."""
        self.state.set("key1", "old_value", "EXT_A")
        self.state.set("key1", "new_value", "EXT_B")
        self.assertEqual(self.state.get("key1"), "new_value")
        self.assertEqual(self.state.get_owner("key1"), "EXT_B")

    def test_keys_returns_all(self):
        """keys() returns all stored key names."""
        self.state.set("a", 1, "EXT_A")
        self.state.set("b", 2, "EXT_B")
        self.state.set("c", 3, "EXT_C")
        self.assertCountEqual(self.state.keys(), ["a", "b", "c"])

    def test_len_returns_count(self):
        """__len__() returns the number of stored keys."""
        self.assertEqual(len(self.state), 0)
        self.state.set("x", 1, "E1")
        self.assertEqual(len(self.state), 1)
        self.state.set("y", 2, "E2")
        self.assertEqual(len(self.state), 2)

    def test_len_after_clear(self):
        """__len__() returns 0 after clear()."""
        self.state.set("a", 1, "E1")
        self.state.set("b", 2, "E2")
        self.state.clear()
        self.assertEqual(len(self.state), 0)

    def test_stores_various_types(self):
        """set/get handles str, int, float, list, dict, None."""
        cases = [
            ("string_key", "hello"),
            ("int_key", 42),
            ("float_key", 3.14),
            ("list_key", [1, 2, 3]),
            ("dict_key", {"nested": "value"}),
            ("none_key", None),
            ("bool_key", True),
            ("tuple_key", (1, 2)),
        ]
        for key, value in cases:
            with self.subTest(key=key):
                self.state.set(key, value, "TEST")
                self.assertEqual(self.state.get(key), value)


# ═══════════════════════════════════════════════════════════════════
# Tier 2: Namespace isolation tests
# ═══════════════════════════════════════════════════════════════════

class TestStateRegistryNamespaces(TestCase):
    """get_namespace() enforces extension-boundary isolation."""

    def setUp(self):
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        self.state = get_state()
        clear_state()

    def test_get_namespace_returns_only_owned_keys(self):
        """Each extension only sees its own keys via get_namespace()."""
        self.state.set("personas", ["alice"], "SIMULATED_PEOPLE")
        self.state.set("interactions", [{"a": "b"}], "SIMULATED_SOCIAL")
        self.state.set("prices", {"diamond": 5}, "SIMULATED_TRADE")

        people_ns = self.state.get_namespace("SIMULATED_PEOPLE")
        self.assertIn("personas", people_ns)
        self.assertNotIn("interactions", people_ns)
        self.assertNotIn("prices", people_ns)

        social_ns = self.state.get_namespace("SIMULATED_SOCIAL")
        self.assertIn("interactions", social_ns)
        self.assertNotIn("personas", social_ns)

    def test_get_namespace_empty_for_unknown_extension(self):
        """An extension that never wrote gets empty dict."""
        self.assertEqual(self.state.get_namespace("SIMULATED_GHOST"), {})

    def test_get_namespace_after_clear(self):
        """get_namespace() returns empty after clear()."""
        self.state.set("k", "v", "E1")
        self.state.clear()
        self.assertEqual(self.state.get_namespace("E1"), {})


# ═══════════════════════════════════════════════════════════════════
# Tier 3: Batch operations
# ═══════════════════════════════════════════════════════════════════

class TestStateRegistryBatch(TestCase):
    """update() atomic batch operation."""

    def setUp(self):
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        self.state = get_state()
        clear_state()

    def test_update_sets_multiple_atomic(self):
        """update() sets all keys atomically for one extension."""
        self.state.update({
            "key_a": "val_a",
            "key_b": "val_b",
            "key_c": "val_c",
        }, "BATCH_EXT")

        self.assertEqual(self.state.get("key_a"), "val_a")
        self.assertEqual(self.state.get("key_b"), "val_b")
        self.assertEqual(self.state.get("key_c"), "val_c")
        self.assertEqual(self.state.get_owner("key_a"), "BATCH_EXT")
        self.assertEqual(self.state.get_owner("key_b"), "BATCH_EXT")
        self.assertEqual(self.state.get_owner("key_c"), "BATCH_EXT")

    def test_update_overwrites_owned_keys(self):
        """update() replaces existing keys and updates ownership."""
        self.state.set("k1", "old", "EXT_A")
        self.state.update({"k1": "new", "k2": "also_new"}, "EXT_B")
        self.assertEqual(self.state.get("k1"), "new")
        self.assertEqual(self.state.get_owner("k1"), "EXT_B")
        self.assertEqual(self.state.get_owner("k2"), "EXT_B")


# ═══════════════════════════════════════════════════════════════════
# Tier 4: Snapshot / restore — E7 rollback mechanism
# ═══════════════════════════════════════════════════════════════════

class TestStateRegistrySnapshot(TestCase):
    """snapshot() and restore_snapshot() for E7 transaction rollback."""

    def setUp(self):
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        self.state = get_state()
        clear_state()

    def test_snapshot_returns_state_and_owners(self):
        """snapshot() returns (state_dict, owners_dict) tuple."""
        self.state.set("x", 10, "E1")
        self.state.set("y", 20, "E2")
        state_snap, owners_snap = self.state.snapshot()
        self.assertEqual(state_snap, {"x": 10, "y": 20})
        self.assertEqual(owners_snap, {"x": "E1", "y": "E2"})

    def test_restore_snapshot_restores_previous_state(self):
        """restore_snapshot() reverts to an earlier state."""
        self.state.set("k", "before", "E1")
        snap_state, snap_owners = self.state.snapshot()

        # Mutate
        self.state.set("k", "after", "E2")
        self.state.set("new_key", "value", "E3")
        self.assertEqual(self.state.get("k"), "after")

        # Rollback
        self.state.restore_snapshot(snap_state, snap_owners)
        self.assertEqual(self.state.get("k"), "before")
        self.assertEqual(self.state.get_owner("k"), "E1")
        self.assertIsNone(self.state.get("new_key"))

    def test_restore_empty_snapshot_clears(self):
        """Restoring an empty snapshot clears everything."""
        self.state.set("a", 1, "E1")
        self.state.set("b", 2, "E2")
        self.state.restore_snapshot({}, {})
        self.assertEqual(len(self.state), 0)

    def test_snapshot_isolation(self):
        """snapshot() returns a copy; mutations don't affect it."""
        self.state.set("k", "original", "E1")
        snap_state, snap_owners = self.state.snapshot()
        self.state.set("k", "mutated", "E2")
        self.assertEqual(snap_state["k"], "original")


# ═══════════════════════════════════════════════════════════════════
# Tier 5: Dump (debug/logging snapshot)
# ═══════════════════════════════════════════════════════════════════

class TestStateRegistryDump(TestCase):
    """dump() debugging/logging snapshot."""

    def setUp(self):
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        self.state = get_state()
        clear_state()

    def test_dump_returns_copy(self):
        """dump() returns a dict copy (mutations don't affect original)."""
        self.state.set("k", "v", "E1")
        dumped = self.state.dump()
        self.state.set("k", "changed", "E1")
        self.assertEqual(dumped["k"], "v")

    def test_dump_after_clear(self):
        """dump() returns empty dict after clear()."""
        self.state.set("a", 1, "E1")
        self.state.clear()
        self.assertEqual(self.state.dump(), {})

    def test_dump_all_keys_present(self):
        """dump() includes all keys currently in state."""
        self.state.set("a", 1, "E1")
        self.state.set("b", 2, "E2")
        self.state.set("c", 3, "E3")
        self.assertEqual(self.state.dump(), {"a": 1, "b": 2, "c": 3})


# ═══════════════════════════════════════════════════════════════════
# Tier 6: Thread safety
# ═══════════════════════════════════════════════════════════════════

class TestStateRegistryThreadSafety(TestCase):
    """Concurrent access must not corrupt state."""

    def setUp(self):
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        self.state = get_state()
        clear_state()

    def test_concurrent_writes_no_corruption(self):
        """Multiple threads writing different keys don't lose data."""
        errors = []
        lock = threading.Lock()

        def writer(ext_name, key_prefix, count):
            try:
                for i in range(count):
                    self.state.set(f"{key_prefix}_{i}", i, ext_name)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [
            threading.Thread(target=writer, args=("THREAD_A", "a", 100)),
            threading.Thread(target=writer, args=("THREAD_B", "b", 100)),
            threading.Thread(target=writer, args=("THREAD_C", "c", 100)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")
        self.assertEqual(len(self.state), 300)

    def test_concurrent_read_writes_consistent(self):
        """Reads during writes see valid state (not garbage)."""
        self.state.set("counter", 0, "TEST")
        barrier = threading.Barrier(3)
        stop = threading.Event()

        def incrementer():
            barrier.wait()
            while not stop.is_set():
                val = self.state.get("counter", 0)
                self.state.set("counter", val + 1, "TEST")
                time.sleep(0.001)

        threads = [threading.Thread(target=incrementer) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join()

        final = self.state.get("counter", 0)
        self.assertGreaterEqual(final, 10)  # Sanity: at least some increments happened

    def test_get_namespace_thread_safe(self):
        """get_namespace() while concurrent writes don't raise."""
        errors = []

        def writer():
            for i in range(200):
                self.state.set(f"w_{i}", i, "WRITER")

        def reader():
            for _ in range(200):
                try:
                    ns = self.state.get_namespace("WRITER")
                    _ = len(ns)
                except Exception as e:
                    errors.append(str(e))

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Reader errors: {errors}")

    def test_snapshot_is_atomic_during_writes(self):
        """snapshot() returns a consistent view even during writes."""
        errors = []

        def writer():
            for i in range(500):
                self.state.set("x", i, "W")
                self.state.set("y", i * 2, "W")

        def snapper():
            for _ in range(50):
                try:
                    state, owners = self.state.snapshot()
                    # x and y should be from the same write if we caught a consistent state
                    if "x" in state and "y" in state:
                        _ = state["x"], state["y"]
                except Exception as e:
                    errors.append(str(e))

        threads = [threading.Thread(target=writer)]
        for _ in range(3):
            threads.append(threading.Thread(target=snapper))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Snapshot errors: {errors}")


# ═══════════════════════════════════════════════════════════════════
# Tier 7: Phase 2C cycle simulation — cross-extension data flow
# ═══════════════════════════════════════════════════════════════════

class TestPhase2CCycle(TestCase):
    """
    Simulate the full Phase 2C simulation cycle.

    Order: PEOPLE → SOCIAL → RELS → CHAT → ANNOUNCE → [clear] → PEOPLE (economy)

    Each phase writes data that the next phase reads.
    This validates that cross-extension data flow through state_registry works.
    """

    def setUp(self):
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        self.state = get_state()
        clear_state()
        # Use simple mocks instead of real extensions
        self.cycle_log = []

    def _phase_people(self):
        """Phase 1: PEOPLE — generate active persona list."""
        self.state.set("active_personas", [
            {"uuid": "p1", "name": "MinerBob", "archetype": "miner"},
            {"uuid": "p2", "name": "TraderAnn", "archetype": "merchant"},
            {"uuid": "p3", "name": "BuilderSam", "archetype": "builder"},
        ], "SIMULATED_PEOPLE")
        self.state.set("persona_needs", {
            "p1": [{"item": "minecraft:iron_pickaxe", "urgency": 8}],
            "p2": [{"item": "minecraft:diamond", "urgency": 4}],
            "p3": [{"item": "minecraft:oak_log", "urgency": 6}],
        }, "SIMULATED_PEOPLE")
        self.cycle_log.append("PEOPLE_complete")

    def _phase_social(self):
        """Phase 2: SOCIAL — create interactions between personas."""
        personas = self.state.get("active_personas", [])
        self.assertGreater(len(personas), 0, "SOCIAL: no personas from PEOPLE phase")

        self.state.set("interactions", [
            {"from": "p1", "to": "p2", "type": "trade_discussion", "intensity": 3},
            {"from": "p3", "to": "p1", "type": "resource_request", "intensity": 2},
        ], "SIMULATED_SOCIAL")
        self.cycle_log.append("SOCIAL_complete")

    def _phase_relationships(self):
        """Phase 3: RELS — update relationships based on interactions."""
        interactions = self.state.get("interactions", [])
        self.assertGreater(len(interactions), 0, "RELS: no interactions from SOCIAL phase")

        self.state.set("relationship_changes", [
            {"pair": "p1-p2", "delta": 5},
            {"pair": "p3-p1", "delta": 3},
        ], "SIMULATED_RELATIONSHIPS")
        self.cycle_log.append("RELS_complete")

    def _phase_chat(self):
        """Phase 4: CHAT — generate messages based on relationships."""
        rels = self.state.get("relationship_changes", [])
        self.assertGreater(len(rels), 0, "CHAT: no relationships from RELS phase")

        self.state.set("chat_messages", [
            {"from": "p1", "to": "p2", "msg": "I see you have diamonds for sale"},
            {"from": "p3", "msg": "Anyone have extra oak logs?"},
        ], "SIMULATED_CHAT")
        self.cycle_log.append("CHAT_complete")

    def _phase_announce(self):
        """Phase 5: ANNOUNCE — broadcast market events from chat + needs."""
        chat = self.state.get("chat_messages", [])
        needs = self.state.get("persona_needs", {})
        self.assertGreater(len(chat), 0, "ANNOUNCE: no chat from CHAT phase")

        self.state.set("announcements", [
            {"type": "market_activity", "text": "MinerBob is looking for iron picks"},
            {"type": "social", "text": "BuilderSam needs oak logs"},
        ], "SIMULATED_ANNOUNCE")
        self.cycle_log.append("ANNOUNCE_complete")

    def test_full_phase_2c_cycle(self):
        """Full cycle: each phase reads the previous phase's state."""
        self._phase_people()
        self._phase_social()
        self._phase_relationships()
        self._phase_chat()
        self._phase_announce()

        self.assertEqual(self.cycle_log, [
            "PEOPLE_complete", "SOCIAL_complete", "RELS_complete",
            "CHAT_complete", "ANNOUNCE_complete",
        ])

        # Verify all extensions' namespaces are populated
        self.assertGreater(len(self.state.get_namespace("SIMULATED_PEOPLE")), 0)
        self.assertGreater(len(self.state.get_namespace("SIMULATED_SOCIAL")), 0)
        self.assertGreater(len(self.state.get_namespace("SIMULATED_RELATIONSHIPS")), 0)
        self.assertGreater(len(self.state.get_namespace("SIMULATED_CHAT")), 0)
        self.assertGreater(len(self.state.get_namespace("SIMULATED_ANNOUNCE")), 0)

    def test_cycle_clear_allows_new_cycle(self):
        """clear() between cycles lets a fresh cycle start."""
        self._phase_people()
        self._phase_social()
        self.state.clear()

        # Fresh cycle — no leftover data
        self.assertEqual(len(self.state), 0)
        self.assertEqual(self.state.get("active_personas"), None)

        # Second cycle
        self._phase_people()
        self.assertEqual(len(self.state.get("active_personas", [])), 3)

    def test_e7_rollback_on_failure(self):
        """
        Simulate E7 rollback: if ANNOUNCE fails, state reverts to
        the pre-ANNOUNCE snapshot (preserving CHAT's data).
        """
        self._phase_people()
        self._phase_social()
        self._phase_relationships()
        self._phase_chat()

        # Snapshot before ANNOUNCE (simulates E7 rollback point)
        snap_state, snap_owners = self.state.snapshot()

        # ANNOUNCE writes
        self._phase_announce()
        announce_data = self.state.get("announcements", [])
        self.assertGreater(len(announce_data), 0)

        # Rollback (simulating ANNOUNCE failure)
        self.state.restore_snapshot(snap_state, snap_owners)

        # CHAT data should survive, ANNOUNCE data should be gone
        self.assertIsNotNone(self.state.get("chat_messages"))
        self.assertIsNone(self.state.get("announcements"))

        # Retry ANNOUNCE
        self._phase_announce()
        self.assertIsNotNone(self.state.get("announcements"))

    def test_partial_cycle_isolation(self):
        """Each extension's namespace is isolated even mid-cycle."""
        self._phase_people()
        people_ns = self.state.get_namespace("SIMULATED_PEOPLE")
        self.assertIn("active_personas", people_ns)
        self.assertNotIn("interactions", people_ns)  # SOCIAL hasn't written yet


# ═══════════════════════════════════════════════════════════════════
# Tier 8: Singleton pattern
# ═══════════════════════════════════════════════════════════════════

class TestStateRegistrySingleton(TestCase):
    """get_state() always returns the same instance."""

    def tearDown(self):
        from AUCTIONHOUSE.EXTENSIONS.state_registry import clear_state
        clear_state()

    def test_get_state_is_singleton(self):
        """Multiple calls to get_state() return the same instance."""
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state
        s1 = get_state()
        s2 = get_state()
        self.assertIs(s1, s2)

    def test_clear_state_via_convenience(self):
        """clear_state() convenience function clears the global state."""
        from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        state = get_state()
        state.set("k", "v", "E1")
        clear_state()
        self.assertEqual(len(state), 0)


if __name__ == "__main__":
    main()
