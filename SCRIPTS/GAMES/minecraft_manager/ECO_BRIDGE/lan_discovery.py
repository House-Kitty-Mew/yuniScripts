"""
lan_discovery.py — LAN service discovery for YuniScripts standalone components.

Designed for zero-dependency (stdlib only) so it can be copied alongside
standalone deployment scripts (server_stats_daemon.py, eco_bridge_server.py).

Architecture:
   Service (beacon broadcaster)                    Client (discovery listener)
   ┌──────────────────────────┐                    ┌────────────────────────┐
   │  ServiceBeacon           │─ UDP broadcast ──► │  ServiceFinder         │
   │  • type: "server_stats"  │   port 25574       │  • listens for beacons │
   │  • port: 5559            │   every 5s         │  • calls callback      │
   │  • extra_data            │                    │  • falls back to cfg   │
   └──────────────────────────┘                    └────────────────────────┘

Usage (broadcaster — standalone server):
    from engine.lan_discovery import ServiceBeacon
    beacon = ServiceBeacon("eco_bridge", port=7200,
                           extra={"version": "1.0"})
    beacon.start()
    # ... do server work ...
    beacon.stop()

Usage (listener — managed script):
    from engine.lan_discovery import ServiceFinder

    def on_service_found(service_type, host, port, extra):
        print(f"Discovered {service_type} at {host}:{port}")

    finder = ServiceFinder("eco_bridge", callback=on_service_found,
                           fallback={"host": "192.168.1.100", "port": 7200})
    finder.start()
    discovered = finder.get_best()
    # discovered = {"host": "192.168.1.50", "port": 7200}  or fallback
"""

import socket
import json
import threading
import time
import os
from typing import Optional, Callable

# ── Constants ──────────────────────────────────────────────────────
# Local define — stdlib-only copy for standalone deployment
DISCOVERY_PORT = 25574  # matches engine.ports.LAN_DISCOVERY_PORT
BROADCAST_INTERVAL = 5    # seconds between beacon broadcasts
LISTENER_TIMEOUT = 30     # seconds before declaring a service lost
BEACON_TTL = 2            # TTL for broadcast packets

# ── Beacon broadcast — for standalone servers ─────────────────────

class ServiceBeacon:
    """Periodically broadcasts a UDP beacon advertising this service on the LAN.

    Args:
        service_type: Short service identifier (e.g. "server_stats", "eco_bridge")
        port: The port the service is actually listening on
        extra: Optional dict with additional metadata (version, capabilities, etc.)
        interval: Seconds between beacons (default 5)
    """

    def __init__(self, service_type: str, port: int,
                 extra: Optional[dict] = None,
                 interval: int = BROADCAST_INTERVAL):
        self.service_type = service_type
        self.port = port
        self.extra = extra or {}
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None

    def start(self):
        """Start broadcasting beacons in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Allow reuse even if we restart
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except (AttributeError, OSError):
            pass
        self._thread = threading.Thread(target=self._broadcast_loop,
                                        daemon=True)
        self._thread.start()

    def stop(self):
        """Stop broadcasting and close the socket."""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread:
            self._thread.join(timeout=3)

    def _broadcast_loop(self):
        """Send beacon packets in a loop."""
        # Build the beacon payload once
        hostname = socket.gethostname()
        # Try to get a LAN IP
        local_ip = _get_lan_ip()
        beacon = {
            "type": "lan_discovery",
            "service": self.service_type,
            "host": local_ip,
            "hostname": hostname,
            "port": self.port,
            "extra": self.extra,
            "ts": 0,  # filled each loop
        }
        payload_base = json.dumps(beacon)
        sock = self._sock
        while self._running and sock:
            try:
                beacon["ts"] = time.time()
                payload = json.dumps(beacon).encode("utf-8")
                # Broadcast to all network interfaces
                for bcast_addr in ["255.255.255.255", "<broadcast>"]:
                    try:
                        sock.sendto(payload, (bcast_addr, DISCOVERY_PORT))
                    except OSError:
                        pass
                # Also try common subnet broadcast
                if local_ip and local_ip != "127.0.0.1":
                    parts = local_ip.rsplit(".", 1)
                    if len(parts) == 2:
                        try:
                            sock.sendto(payload,
                                        (f"{parts[0]}.255", DISCOVERY_PORT))
                        except OSError:
                            pass
            except OSError:
                pass
            time.sleep(self.interval)


# ── Discovery listener — for managed scripts ──────────────────────

class ServiceFinder:
    """Listens for UDP beacons from services on the LAN.

    Args:
        service_type: The service type to listen for
        callback: Called when a service is found/lost. Signature:
                  callback(service_type, host, port, extra, is_new)
        fallback: Dict with "host" and "port" keys to use if no beacon
                  received after LISTENER_TIMEOUT seconds
        timeout: Seconds before declaring a service lost (default 30)
    """

    def __init__(self, service_type: str,
                 callback: Optional[Callable] = None,
                 fallback: Optional[dict] = None,
                 timeout: int = LISTENER_TIMEOUT):
        self.service_type = service_type
        self.callback = callback
        self.fallback = fallback or {}
        self.timeout = timeout
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        # Track discovered services: {hostname: {"host": ..., "port": ..., "ts": ...}}
        self._found: dict = {}
        self._lock = threading.Lock()
        self._last_callback_host: Optional[str] = None

    def start(self):
        """Start listening for beacons in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind(("", DISCOVERY_PORT))
        except OSError as e:
            # Port might be in use — try any available port
            self._sock.close()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.bind(("", 0))
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._listen_loop,
                                        daemon=True)
        self._thread.start()

    def stop(self):
        """Stop listening and close the socket."""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread:
            self._thread.join(timeout=3)

    def get_best(self) -> dict:
        """Return the best discovered service, or the fallback config.

        Returns a dict with at least "host" and "port" keys.
        """
        now = time.time()
        with self._lock:
            # Remove stale entries
            stale = [k for k, v in self._found.items()
                     if now - v["ts"] > self.timeout]
            for k in stale:
                del self._found[k]

            if self._found:
                # Return the most recently seen service
                best = max(self._found.values(), key=lambda v: v["ts"])
                return {"host": best["host"], "port": best["port"]}

        # Fallback to config
        return self.fallback.copy() if self.fallback else {"host": "", "port": 0}

    def _listen_loop(self):
        """Receive beacon packets and update discovered services."""
        sock = self._sock
        while self._running and sock:
            try:
                data, addr = sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") != "lan_discovery":
                    continue
                if msg.get("service") != self.service_type:
                    continue

                host = msg.get("host", addr[0])
                port = msg.get("port", 0)
                extra = msg.get("extra", {})
                hostname = msg.get("hostname", "")
                ts = msg.get("ts", time.time())

                key = hostname or host
                with self._lock:
                    is_new = key not in self._found
                    self._found[key] = {
                        "host": host,
                        "port": port,
                        "extra": extra,
                        "hostname": hostname,
                        "addr": addr[0],
                        "ts": ts,
                    }

                if self.callback:
                    try:
                        self.callback(self.service_type, host, port,
                                      extra, is_new)
                    except Exception:
                        pass

            except socket.timeout:
                continue
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue


# ── Helpers ───────────────────────────────────────────────────────

def _get_lan_ip() -> str:
    """Get the machine's LAN IP address (not 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Doesn't actually connect — just used to determine which interface
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except (OSError, socket.error):
        pass
    # Fallback: try hostname resolution
    try:
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        for ip in ips:
            if not ip.startswith("127."):
                return ip
    except (socket.gaierror, OSError):
        pass
    return "127.0.0.1"


# ── Convenience — add to standalone scripts easily ────────────────

def add_beacon_to_server(service_type: str, port: int, **extra) -> ServiceBeacon:
    """Create and start a beacon.  Returns the beacon so you can stop() it."""
    beacon = ServiceBeacon(service_type, port, extra=extra)
    beacon.start()
    print(f"[lan-discovery] Broadcasting '{service_type}' on port {DISCOVERY_PORT} "
          f"(service port: {port})")
    return beacon
