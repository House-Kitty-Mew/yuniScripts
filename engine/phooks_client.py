"""
Phooks Client – for script‑to‑script communication.

LAN v3:
  - Auto‑discovers the Phooks hub on the LAN via ServiceFinder when
    HUB_HOST is 127.0.0.1 (the default).
  - Falls back to 127.0.0.1:PHOOKS_HUB_PORT if no beacon (see engine.ports) is received within 30s.
  - Recreates the socket on OSError (E3 fix) so transient failures don't
    permanently break communication.

Usage:
    from engine.phooks_client import PhooksClient
    client = PhooksClient("my_id", ["listen_events"], ["emit_events"])
    client.register()
    client.emit("some_event", {"key":"val"})
    event = client.receive(timeout=2)
"""
import json
import socket
import threading
import queue
import time
from engine.ports import PHOOKS_HUB_PORT
HUB_HOST = "127.0.0.1"


class PhooksClient:
    """Phooks client for inter-script communication via UDP.

    HARDENED v2:
      - Socket send timeout (avoids infinite block on buffer-full)
      - Connection state tracking ('registered', 'disconnected', 'error')
      - Graceful handling of ConnectionResetError and OSError
      - Thread-safe re-registration support
    LAN v3:
      - Auto-discovers hub via LAN discovery when HUB_HOST is 127.0.0.1
      - Recreates the socket on OSError (E3)
    """

    def __init__(self, script_id, listen_events, emit_events,
                 hub_host=None, hub_port=None):
        self.script_id = script_id
        self.listen_events = listen_events
        self.emit_events = emit_events
        self._hub_host = hub_host or HUB_HOST
        self._hub_port = hub_port or PHOOKS_HUB_PORT
        self._finder = None
        self.sock = None
        self._create_socket()
        self.event_queue = queue.Queue()
        self.running = False
        self.thread = None
        self._lock = threading.Lock()
        self._state = "unregistered"

    @property
    def state(self) -> str:
        return self._state

    def _create_socket(self):
        """Create (or recreate) the UDP socket."""
        # Close old socket if any
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))
        self.sock.settimeout(1.0)

    def _resolve_hub(self):
        """Use LAN discovery to find the hub if default host is 127.0.0.1."""
        if self._hub_host != "127.0.0.1":
            return  # Explicit host provided, don't override

        if self._finder is None:
            try:
                from engine.lan_discovery import ServiceFinder

                def _on_service(service_type, host, port, extra, is_new):
                    if is_new or True:
                        print(f"[phooks-client] {self.script_id}: discovered hub at {host}:{port}")

                self._finder = ServiceFinder(
                    "phooks_hub",
                    callback=_on_service,
                    fallback={"host": "127.0.0.1", "port": PHOOKS_HUB_PORT},
                )
                self._finder.start()
            except ImportError:
                print("[phooks-client] engine.lan_discovery not available")
                return
            except Exception as e:
                print(f"[phooks-client] Finder start failed: {e}")
                return

        # Check if discovery found something
        if self._finder:
            try:
                best = self._finder.get_best()
                if best and best.get("host") and best["host"] != "127.0.0.1":
                    self._hub_host = best["host"]
                    self._hub_port = best.get("port", self._hub_port)
            except Exception:
                pass

    def register(self):
        """Register with the Phooks hub.
        
        First tries LAN discovery (if default host), then sends REGISTER.
        """
        self._resolve_hub()

        msg = json.dumps({
            "command": "REGISTER",
            "script_id": self.script_id,
            "listen_events": self.listen_events,
            "emit_events": self.emit_events
        }).encode('utf-8')
        try:
            self.sock.sendto(msg, (self._hub_host, self._hub_port))
            self._state = "registered"
        except (OSError, ConnectionResetError) as exc:
            print(f"[phooks-client] {self.script_id} register send failed: {exc}")
            # E3: recreate socket and retry once
            try:
                self._create_socket()
                self.sock.sendto(msg, (self._hub_host, self._hub_port))
                self._state = "registered"
            except Exception as retry_exc:
                self._state = "error"
                print(f"[phooks-client] {self.script_id} register retry also failed: {retry_exc}")
                raise retry_exc from retry_exc
        with self._lock:
            if not self.running:
                self.running = True
                self.thread = threading.Thread(target=self._receive_loop, daemon=True)
                self.thread.start()
        print(f"[phooks-client] {self.script_id} registered on hub {self._hub_host}:{self._hub_port}")

    def unregister(self):
        msg = json.dumps({
            "command": "UNREGISTER",
            "script_id": self.script_id
        }).encode('utf-8')
        try:
            self.sock.sendto(msg, (self._hub_host, self._hub_port))
        except Exception:
            pass
        self.running = False
        self._state = "unregistered"
        if self.thread:
            self.thread.join(timeout=2)
        self.thread = None
        try:
            self.sock.close()
        except Exception:
            pass

    def _receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
                event = json.loads(data.decode('utf-8'))
                if "response" in event:
                    continue
                self.event_queue.put(event)
            except socket.timeout:
                continue
            except (ConnectionResetError, ConnectionRefusedError) as conn_err:
                self._state = "disconnected"
                print(f"[phooks-client] {self.script_id} connection lost: {conn_err}")
                time.sleep(0.5)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[phooks-client] {self.script_id} receive error: {exc}")
                self._state = "error"
                # E3: recreate socket on OSError
                try:
                    self._create_socket()
                    self._state = "disconnected"
                except Exception:
                    pass
                time.sleep(1.0)

    def emit(self, event, data=None):
        try:
            msg = json.dumps({
                "command": "EMIT",
                "event": event,
                "data": data or {},
                "sender": self.script_id
            }).encode('utf-8')
            self.sock.sendto(msg, (self._hub_host, self._hub_port))
        except (OSError, ConnectionResetError) as exc:
            self._state = "disconnected"
            print(f"[phooks-client] {self.script_id} emit failed: {exc}")
            # E3: recreate socket
            try:
                self._create_socket()
            except Exception:
                pass

    def receive(self, timeout=None):
        try:
            return self.event_queue.get(timeout=timeout)
        except queue.Empty:
            return None
