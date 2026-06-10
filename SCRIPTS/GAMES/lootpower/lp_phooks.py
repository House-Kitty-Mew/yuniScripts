"""
LootPower Phooks Integration — event hooks for cross-script communication.

Exposes LootPower events so external scripts (Advanced Market System,
Chance System, etc.) can subscribe and interact.

Events emitted:
  lootpower:loot_dropped    - When a loot item drops
  lootpower:adventure_start - Before an adventure turn
  lootpower:adventure_end   - After adventure resolves
  lootpower:craft_attempt   - Before craft attempt
  lootpower:craft_complete  - After craft resolves
  lootpower:mine_hit        - Mining result
  lootpower:turn_replenish  - Turn update tick
  lootpower:player_join     - Player login
  lootpower:market_query    - Request market data
  lootpower:watcher_event   - Observer activity

External scripts can also CALL INTO LootPower via:
  lootpower:action          - Execute game actions remotely
"""
import json
import socket
import threading
import time
from typing import Optional, Callable, Any

import lp_config


class LootPowerPhooks:
    """
    Phooks event hub for LootPower ecosystem integration.

    Operates as a lightweight UDP event bus that other scripts
    can discover and subscribe to via LAN beaconing.
    """

    def __init__(self, port: int = None, log_func: Optional[Callable] = None):
        self.port = port or lp_config.PHOOKS_PORT
        self.log_func = log_func or (lambda msg, lvl="INFO": None)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.5)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("", self.port))
        except OSError:
            self.port = 0  # Let OS assign
            self.sock.bind(("", 0))
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._subscriptions: dict = {}  # event_name -> [callback]
        self._event_history: list = []
        self._lock = threading.Lock()

        # Registered action handlers (for lootpower:action)
        self._action_handlers: dict = {}

        self.beacon_sock = None
        self.log(f"LootPower Phooks listening on UDP {self.port}")

    def log(self, msg: str, level: str = "INFO"):
        if self.log_func:
            self.log_func(msg, level)

    # --- Lifecycle ---
    def start(self, daemon: bool = True):
        """Start the phooks listener thread."""
        self.running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=daemon)
        self._thread.start()
        self._start_beacon()
        self.log("Phooks hub started")

    def stop(self):
        """Stop the phooks listener."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._stop_beacon()
        try:
            self.sock.close()
        except OSError:
            pass
        self.log("Phooks hub stopped")

    def _start_beacon(self):
        """Start LAN beacon for auto-discovery."""
        try:
            self.beacon_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.beacon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.beacon_sock.settimeout(0.1)
        except Exception:
            pass

    def _stop_beacon(self):
        if self.beacon_sock:
            try:
                self.beacon_sock.close()
            except OSError:
                pass
            self.beacon_sock = None

    # --- Event emission ---
    def emit(self, event: str, data: dict, sender: str = "lootpower"):
        """
        Emit an event to all subscribers.

        Also broadcasts via UDP for external phooks clients.
        """
        payload = json.dumps({
            "event": event,
            "data": data,
            "sender": sender,
            "timestamp": time.time(),
        })

        # Local subscriptions
        with self._lock:
            callbacks = list(self._subscriptions.get(event, []))
            self._event_history.append({
                "event": event, "data": data, "sender": sender,
                "time": time.time()
            })
            if len(self._event_history) > 1000:
                self._event_history = self._event_history[-500:]

        for cb in callbacks:
            try:
                cb(event, data, sender)
            except Exception as e:
                self.log(f"Callback error for {event}: {e}", "ERROR")

        # UDP broadcast for external subscribers
        try:
            self.sock.sendto(payload.encode("utf-8"),
                             ("<broadcast>", self.port))
        except OSError:
            pass

        self.log(f"Emit: {event} from {sender}")

    # --- Subscription ---
    def subscribe(self, event: str, callback: Callable):
        """Register a local callback for an event."""
        with self._lock:
            self._subscriptions.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: Callable):
        """Remove a local subscription."""
        with self._lock:
            if event in self._subscriptions:
                self._subscriptions[event] = [
                    cb for cb in self._subscriptions[event]
                    if cb is not callback
                ]

    # --- Action handlers (for lootpower:action events) ---
    def register_action(self, action_name: str, handler: Callable):
        """Register a handler for an external action command."""
        self._action_handlers[action_name] = handler

    def _handle_action(self, action: str, params: dict,
                       requester: str) -> dict:
        """Execute an action and return result."""
        handler = self._action_handlers.get(action)
        if not handler:
            return {"error": f"Unknown action: {action}"}
        try:
            result = handler(params, requester)
            return {"result": result}
        except Exception as e:
            return {"error": str(e)}

    # --- Listen loop ---
    def _listen_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
                msg = json.loads(data.decode("utf-8"))

                # Handle action requests
                if msg.get("command") == "action":
                    response = self._handle_action(
                        msg.get("action", ""),
                        msg.get("params", {}),
                        msg.get("requester", str(addr))
                    )
                    reply = json.dumps(response).encode("utf-8")
                    try:
                        self.sock.sendto(reply, addr)
                    except OSError:
                        pass

                # Handle subscribe/unsubscribe requests
                elif msg.get("command") == "subscribe":
                    event = msg["event"]
                    cb = lambda ev, dt, sd: self._forward_to(ev, dt, sd, addr)
                    self.subscribe(event, cb)
                    self.log(f"External subscription: {addr} -> {event}")

            except socket.timeout:
                continue
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            except OSError:
                if self.running:
                    continue
                break
            except Exception as e:
                self.log(f"Listen error: {e}", "ERROR")

    def _forward_to(self, event: str, data: dict, sender: str, addr: tuple):
        """Forward an event to an external subscriber."""
        payload = json.dumps({
            "event": event, "data": data, "sender": sender
        }).encode("utf-8")
        try:
            self.sock.sendto(payload, addr)
        except OSError:
            pass

    # --- Event history ---
    def get_recent_events(self, count: int = 10) -> list:
        """Get recent event history (for watchers/debug)."""
        with self._lock:
            return list(self._event_history[-count:])