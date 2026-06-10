#!/usr/bin/env python3
"""
test_phooks_event_flow.py — Phooks Event Propagation Integration Tests

Tests the inter-script Phooks event system:
  - Event emission and subscriber notification
  - Multiple subscribers to the same event
  - Complex event payloads
  - Error handling in subscribers
  - Cross-platform compatibility
"""

import os
import sys
import time
import json
import socket
import threading
import unittest
from pathlib import Path
from typing import Dict, List, Any

# ── Ensure engine is importable ──────────────────────────────────────
_ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE_DIR))

from engine.phooks import (
    PhooksEvent,
    PhooksHub,
    PhooksError,
    PhooksTimeout,
    PhooksConnectionError,
)
from engine.phooks_client import (
    PhooksClient,
)
from engine.ports import PHOOKS_HUB_PORT


# ══════════════════════════════════════════════════════════════════════
# Test Configuration
# ══════════════════════════════════════════════════════════════════════

# Port range for test hub to avoid conflicts
_TEST_HUB_PORT_BASE = 19870
_used_ports = set()


def _pick_port() -> int:
    """Pick an unused test port."""
    port = _TEST_HUB_PORT_BASE
    while port in _used_ports:
        port += 1
    _used_ports.add(port)
    return port


# ══════════════════════════════════════════════════════════════════════
# Hub Fixture
# ══════════════════════════════════════════════════════════════════════

class _TestHubManager:
    """Manages a PhooksHub for testing."""

    def __init__(self, port: int):
        self.port = port
        self.hub: PhooksHub = None
        self._thread: threading.Thread = None

    def start(self):
        """Start the hub in a background thread."""
        self.hub = PhooksHub(host="127.0.0.1", port=self.port)
        self._thread = threading.Thread(target=self.hub.run, daemon=True)
        self._thread.start()
        time.sleep(0.2)  # Give hub time to start

    def stop(self):
        """Stop the hub."""
        if self.hub:
            self.hub.stop()
        if self._thread:
            self._thread.join(timeout=5.0)


# ══════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════

class TestPhooksEventFlow(unittest.TestCase):
    """Tests for basic Phooks event propagation."""

    @classmethod
    def setUpClass(cls):
        cls.hub_port = _pick_port()
        cls.hub_mgr = _TestHubManager(cls.hub_port)
        cls.hub_mgr.start()

    @classmethod
    def tearDownClass(cls):
        cls.hub_mgr.stop()

    def setUp(self):
        self.client = PhooksClient(host="127.0.0.1", port=self.hub_port)
        self.client.connect()

    def tearDown(self):
        try:
            self.client.disconnect()
        except Exception:
            pass

    # ── Basic Event Flow ──────────────────────────────────────────

    def test_emit_and_receive(self):
        """Emit an event → subscriber receives it with correct payload."""
        received = []

        def handler(payload: dict):
            received.append(payload)

        self.client.subscribe("test.basic", handler)
        time.sleep(0.1)

        self.client.emit("test.basic", {"msg": "hello"})
        time.sleep(0.2)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].get("msg"), "hello")

    def test_event_without_payload(self):
        """Emit without payload still notifies subscriber."""
        received = []

        def handler(payload: dict):
            received.append(payload)

        self.client.subscribe("test.no_payload", handler)
        time.sleep(0.1)

        self.client.emit("test.no_payload", {})
        time.sleep(0.2)

        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], dict)

    def test_multiple_events(self):
        """Multiple sequential events are all received."""
        received = []

        def handler(payload: dict):
            received.append(payload.get("n"))

        self.client.subscribe("test.multi", handler)
        time.sleep(0.1)

        for i in range(5):
            self.client.emit("test.multi", {"n": i})
        time.sleep(0.3)

        self.assertEqual(len(received), 5)
        self.assertEqual(received, [0, 1, 2, 3, 4])

    # ── Multiple Subscribers ──────────────────────────────────────

    def test_multiple_subscribers_same_event(self):
        """All subscribers to the same event are notified."""
        received1 = []
        received2 = []

        def handler1(payload: dict):
            received1.append(payload.get("val"))

        def handler2(payload: dict):
            received2.append(payload.get("val"))

        self.client.subscribe("test.broadcast", handler1)
        self.client.subscribe("test.broadcast", handler2)
        time.sleep(0.1)

        self.client.emit("test.broadcast", {"val": 42})
        time.sleep(0.2)

        self.assertIn(42, received1)
        self.assertIn(42, received2)

    def test_subscriber_independence(self):
        """One subscriber's error does not affect other subscribers."""
        received = []

        def failing_handler(payload: dict):
            raise ValueError("Intentional failure")

        def good_handler(payload: dict):
            received.append(payload.get("val"))

        self.client.subscribe("test.errors", failing_handler)
        self.client.subscribe("test.errors", good_handler)
        time.sleep(0.1)

        # Emit — should not throw, good handler should still fire
        self.client.emit("test.errors", {"val": "ok"})
        time.sleep(0.2)

        self.assertIn("ok", received)

    # ── Payload Types ─────────────────────────────────────────────

    def test_complex_payload(self):
        """Complex nested payload is passed intact."""
        payload = {
            "string": "hello",
            "number": 42,
            "list": [1, 2, 3],
            "nested": {"a": 1, "b": [4, 5, 6]},
            "bool": True,
            "null": None,
        }
        received = []

        def handler(p: dict):
            received.append(p)

        self.client.subscribe("test.complex", handler)
        time.sleep(0.1)

        self.client.emit("test.complex", payload)
        time.sleep(0.2)

        self.assertEqual(len(received), 1)
        r = received[0]
        self.assertEqual(r["string"], "hello")
        self.assertEqual(r["number"], 42)
        self.assertEqual(r["list"], [1, 2, 3])
        self.assertEqual(r["nested"]["a"], 1)
        self.assertEqual(r["bool"], True)

    def test_large_payload(self):
        """Large payload (10KB) is transmitted without truncation."""
        large_text = "x" * 10000
        payload = {"data": large_text}

        received = []

        def handler(p: dict):
            received.append(p)

        self.client.subscribe("test.large", handler)
        time.sleep(0.1)

        self.client.emit("test.large", payload)
        time.sleep(0.3)

        self.assertEqual(len(received), 1)
        self.assertEqual(len(received[0]["data"]), 10000)


class TestPhooksEdgeCases(unittest.TestCase):
    """Edge cases for Phooks event system."""

    @classmethod
    def setUpClass(cls):
        cls.hub_port = _pick_port()
        cls.hub_mgr = _TestHubManager(cls.hub_port)
        cls.hub_mgr.start()

    @classmethod
    def tearDownClass(cls):
        cls.hub_mgr.stop()

    def setUp(self):
        self.client = PhooksClient(host="127.0.0.1", port=self.hub_port)
        self.client.connect()

    def tearDown(self):
        try:
            self.client.disconnect()
        except Exception:
            pass

    def test_unsubscribe(self):
        """Unsubscribing stops event delivery."""
        received = []

        def handler(payload: dict):
            received.append(payload.get("n"))

        sub_id = self.client.subscribe("test.unsub", handler)
        time.sleep(0.1)

        self.client.emit("test.unsub", {"n": 1})
        time.sleep(0.1)
        self.client.unsubscribe(sub_id)
        self.client.emit("test.unsub", {"n": 2})
        time.sleep(0.1)

        self.assertEqual(len(received), 1)

    def test_unknown_event(self):
        """Emitting to an event with no subscribers does nothing."""
        try:
            self.client.emit("test.no_subscribers", {"val": "orphan"})
        except Exception as e:
            self.fail(f"Emitting to unsubscribed event raised: {e}")

    def test_disconnect_reconnect(self):
        """Client disconnect then reconnect works."""
        received = []

        def handler(payload: dict):
            received.append(payload)

        # First connection
        self.client.subscribe("test.reconnect", handler)
        time.sleep(0.1)

        # Disconnect
        self.client.disconnect()

        # Reconnect
        self.client.connect()
        self.client.subscribe("test.reconnect", handler)
        time.sleep(0.1)

        self.client.emit("test.reconnect", {"phase": "after_reconnect"})
        time.sleep(0.2)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["phase"], "after_reconnect")

    def test_multiple_clients_same_event(self):
        """Two clients subscribe to same event; both receive."""
        client2 = PhooksClient(host="127.0.0.1", port=self.hub_port)
        client2.connect()

        recv1 = []
        recv2 = []

        def handler1(p: dict):
            recv1.append(p.get("val"))

        def handler2(p: dict):
            recv2.append(p.get("val"))

        self.client.subscribe("test.two_clients", handler1)
        client2.subscribe("test.two_clients", handler2)
        time.sleep(0.1)

        self.client.emit("test.two_clients", {"val": "both"})
        time.sleep(0.2)

        self.assertIn("both", recv1)
        self.assertIn("both", recv2)

        client2.disconnect()

    def test_client_disconnect_cleanup(self):
        """Disconnecting a client cleans up its subscriptions on hub."""
        recv_after = []

        def handler(p: dict):
            recv_after.append(p)

        self.client.subscribe("test.cleanup", handler)
        self.client.disconnect()

        # A new client should receive events normally
        client2 = PhooksClient(host="127.0.0.1", port=self.hub_port)
        client2.connect()
        client2.subscribe("test.cleanup", handler)
        time.sleep(0.1)

        client2.emit("test.cleanup", {"after": True})
        time.sleep(0.2)

        self.assertEqual(len(recv_after), 1)
        client2.disconnect()

    def test_event_name_validation(self):
        """Event names with invalid characters are rejected."""
        from engine.phooks import PhooksError

        with self.assertRaises((PhooksError, ValueError, Exception)):
            self.client.emit("", {"x": 1})  # Empty name

    def test_rapid_fire_events(self):
        """Rapid event emission does not drop events or hang."""
        received = []
        EVENT_COUNT = 50

        def handler(p: dict):
            received.append(p.get("i"))

        self.client.subscribe("test.rapid", handler)
        time.sleep(0.1)

        for i in range(EVENT_COUNT):
            self.client.emit("test.rapid", {"i": i})

        time.sleep(0.5)

        self.assertEqual(len(received), EVENT_COUNT,
                         f"Expected {EVENT_COUNT} events, got {len(received)}")

    def test_timeout_on_unreachable_hub(self):
        """Connecting to a non-existent hub raises timeout."""
        client = PhooksClient(host="127.0.0.1", port=1)  # Port 1 = always closed
        with self.assertRaises((PhooksTimeout, PhooksConnectionError, ConnectionRefusedError, OSError)):
            client.connect(timeout=1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
