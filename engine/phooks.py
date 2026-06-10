"""Phooks Hub – inter‑script event bus (CANONICAL implementation).
LAN v3: Broadcasts a beacon so clients can discover the hub automatically.
Also provides a lightweight functional hook registry for engine-level use.

Usage (as a managed script — import PhooksHub, instantiate, start):
    from engine.phooks import PhooksHub
    hub = PhooksHub(port=PHOOKS_HUB_PORT, lan_broadcast=True, log_func=my_logger)
    hub.start()
    # ... keep alive ...
    hub.stop()
"""
import json
import socket
import threading
import time
from typing import Optional, Callable

from engine.ports import PHOOKS_HUB_PORT
from engine.hooks import create_hook_registry, register_hook, call_hooks  # noqa: F401


class PhooksHub:
    """Central Phooks event bus for inter-script communication.

    HARDENED v2:
      - Stale client cleanup (removes clients that haven't been heard from
        in STALE_CLIENT_TIMEOUT seconds)
      - Socket send timeout for forward delivery
      - Periodic health check in the main loop
    LAN v3:
      - Optionally broadcasts a LAN beacon so PhooksClient can auto-discover
        the hub without hardcoded IP addresses.
    """

    STALE_CLIENT_TIMEOUT = 120  # seconds without contact -> auto-unregister

    def __init__(self, port: int = PHOOKS_HUB_PORT, lan_broadcast: bool = True,
                 log_func: Optional[Callable[[str, str], None]] = None):
        """Initialize hub.

        Args:
            port: UDP port to listen on.
            lan_broadcast: Whether to broadcast LAN beacon via ServiceBeacon.
            log_func: Optional callable(msg, level) for custom logging.
                      If None, uses print().
        """
        self.port = port
        self.lan_broadcast = lan_broadcast
        self.log_func = log_func
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, 5)
        except (AttributeError, OSError):
            pass
        self.sock.bind(("", self.port))
        self.subscriptions: dict = {}
        self.script_registry: dict = {}
        self.lock = threading.Lock()
        self.running = False
        self._last_cleanup = 0.0
        self._beacon = None
        self._thread: Optional[threading.Thread] = None
        self._log("Hub listening on UDP {}".format(self.port))

    def _log(self, msg: str, level: str = "INFO"):
        if self.log_func:
            self.log_func(msg, level)
        else:
            print("[phooks] {}".format(msg))

    def start(self, daemon: bool = True):
        """Start the hub's main loop in a background thread.

        Args:
            daemon: If True (default), the thread won't block process exit.
                    Set False when running as a managed script that needs
                    graceful shutdown.
        """
        self.running = True
        # -- Start LAN beacon broadcast ----------------------------------
        if self.lan_broadcast:
            try:
                from engine.lan_discovery import ServiceBeacon
                self._beacon = ServiceBeacon(
                    "phooks_hub",
                    port=self.port,
                    extra={"lan_mode": True, "version": 3},
                )
                self._beacon.start()
                self._log("LAN beacon broadcasting for 'phooks_hub'")
            except ImportError:
                self._log("engine.lan_discovery not available -- no beacon", "WARN")
            except Exception as e:
                self._log("Beacon start failed: {}".format(e), "ERROR")

        self._thread = threading.Thread(target=self._main_loop, daemon=daemon)
        self._thread.start()

    def stop(self):
        """Stop the hub, close socket, and join thread."""
        self.running = False
        if self._beacon:
            try:
                self._beacon.stop()
            except Exception:
                pass
            self._beacon = None
        try:
            self.sock.close()
        except OSError:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _main_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
                self._handle_packet(data, addr)
            except socket.timeout:
                now = time.time()
                if now - self._last_cleanup >= 10.0:
                    self._cleanup_stale_clients(now)
                    self._last_cleanup = now
                continue
            except OSError as exc:
                if self.running:
                    self._log("Socket error: {}".format(exc), "ERROR")
                break
            except Exception as exc:
                self._log("Error: {}".format(exc), "ERROR")

    def _cleanup_stale_clients(self, now):
        stale = []
        with self.lock:
            for sid, (addr, last_seen) in list(self.script_registry.items()):
                if now - last_seen > self.STALE_CLIENT_TIMEOUT:
                    stale.append(sid)
                    del self.script_registry[sid]
                    for subs in self.subscriptions.values():
                        subs.discard(sid)
        if stale:
            self._log("Cleaned up stale clients: {}".format(stale))

    def _handle_packet(self, data, addr):
        try:
            msg = json.loads(data.decode('utf-8'))
            cmd = msg.get("command")
            if cmd == "REGISTER":
                self._register(msg, addr)
            elif cmd == "UNREGISTER":
                self._unregister(msg, addr)
            elif cmd == "EMIT":
                self._emit(msg, addr)
            elif cmd == "PING":
                try:
                    self.sock.sendto(
                        json.dumps({"response": "PONG"}).encode('utf-8'), addr
                    )
                except OSError:
                    pass
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._log("Invalid packet from {}".format(addr), "WARN")
        except Exception as e:
            self._log("Packet error from {}: {}".format(addr, e), "WARN")

    def _register(self, msg, addr):
        sid = msg["script_id"]
        listen_events = msg.get("listen_events", [])
        with self.lock:
            self.script_registry[sid] = (addr, time.time())
            for event in listen_events:
                self.subscriptions.setdefault(event, set()).add(sid)
        self._log("{} registered for {} at {}".format(sid, listen_events, addr))

    def _unregister(self, msg, addr):
        sid = msg["script_id"]
        with self.lock:
            if sid in self.script_registry:
                del self.script_registry[sid]
            for subs in self.subscriptions.values():
                subs.discard(sid)
        self._log("{} unregistered".format(sid))

    def _emit(self, msg, addr):
        event = msg["event"]
        data = msg.get("data", {})
        sender = msg["sender"]
        with self.lock:
            recipients = self.subscriptions.get(event, set()).copy()
        for sid in recipients:
            if sid == sender:
                continue
            if sid in self.script_registry:
                dest_addr = self.script_registry[sid][0]
                forward = json.dumps({
                    "event": event,
                    "data": data,
                    "sender": sender
                }).encode('utf-8')
                try:
                    self.sock.sendto(forward, dest_addr)
                except OSError as exc:
                    self._log("Failed to forward to {}: {}".format(sid, exc), "ERROR")


