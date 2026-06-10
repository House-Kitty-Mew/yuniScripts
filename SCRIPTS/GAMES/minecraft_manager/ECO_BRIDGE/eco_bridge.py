"""
eco_bridge.py — Economy Bridge loader.

Auto-detects whether to use the local Otters Civ database or the
remote bridge server.  Provides a unified EconomyBridge API that
the AH system uses.

Resolution priority:
  1. LAN discovery (beacon from eco_bridge_server on the LAN)
  2. Remote bridge (static config)
  3. Local database (if project_ooga.db is found on this machine)
  4. Fallback (None — AH uses internal player_balances table)

Usage (in ah_core.py):
    from ECO_BRIDGE.eco_bridge import get_bridge
    bridge = get_bridge()
    if bridge:
        balance = bridge.get_balance("Steve")
"""

import os, threading, time
from pathlib import Path
from typing import Optional

from ECO_BRIDGE.eco_config import get_config as get_eco_config


# ── Global singleton ────────────────────────────────────────────────
_global_bridge = None
_bridge_lock = threading.Lock()
_eco_finder = None
_eco_finder_running = False
_eco_discovered_host = None
_eco_discovered_port = None


# ── LAN discovery for eco_bridge (continuous background listener) ──

def _start_eco_discovery(fallback_host="", fallback_port=7200):
    """Start a persistent LAN discovery listener for eco_bridge beacons.
    Updates remote_host/remote_port globals when a beacon is received.
    """
    global _eco_finder, _eco_finder_running, _eco_discovered_host, _eco_discovered_port
    if _eco_finder_running:
        return
    _eco_finder_running = True

    _eco_discovered_host = None
    _eco_discovered_port = None

    def _on_beacon(service_type, host, port, extra, is_new):
        global _eco_discovered_host, _eco_discovered_port
        _eco_discovered_host = host
        _eco_discovered_port = port
        if is_new:
            print(f"[eco-bridge] LAN discovery: found eco_bridge at {host}:{port}")

    try:
        from engine.lan_discovery import ServiceFinder
        _eco_finder = ServiceFinder(
            "eco_bridge",
            callback=_on_beacon,
            fallback={"host": fallback_host, "port": fallback_port},
        )
        _eco_finder.start()
    except ImportError:
        pass


def _get_discovered_bridge(remote_key):
    """Try connecting to a LAN-discovered bridge, if any beacon was heard."""
    global _eco_discovered_host, _eco_discovered_port
    host = _eco_discovered_host
    port = _eco_discovered_port
    if not host or not port:
        return None, None, None
    try:
        from ECO_BRIDGE.eco_bridge_client import RemoteEconomyBridge
        # LAN-discovered bridge: no encryption, plain JSON
        bridge = RemoteEconomyBridge(host=host, port=int(port), password=remote_key, lan_mode=True)
        if bridge.ping():
            print(f"[eco-bridge] Connected via LAN discovery: {host}:{port} (LAN mode, no encryption)")
            return bridge, host, port
        else:
            print(f"[eco-bridge] LAN discovery bridge at {host}:{port} not responding")
    except Exception as e:
        print(f"[eco-bridge] LAN discovery init failed: {e}")
    return None, None, None


def get_bridge() -> Optional[object]:
    """Return the best available economy bridge.

    Priority:
      1. LAN-discovered bridge (if beacon received)
      2. Remote bridge from static config
      3. Local DB
      4. None (AH uses internal balances)
    """
    global _global_bridge
    if _global_bridge is not None:
        return _global_bridge

    with _bridge_lock:
        if _global_bridge is not None:
            return _global_bridge

        config = get_eco_config()

        remote_host = getattr(config, "eco_bridge_host", "") or os.environ.get("ECO_BRIDGE_HOST", "")
        remote_port = getattr(config, "eco_bridge_port", 7200)
        remote_key = getattr(config, "eco_bridge_password", "") or os.environ.get("ECO_BRIDGE_KEY", "")

        # Start continuous LAN discovery
        _start_eco_discovery(fallback_host=remote_host, fallback_port=remote_port)

        # 1. Try LAN-discovered bridge (if we've heard a beacon)
        _bridge, _dh, _dp = _get_discovered_bridge(remote_key)
        if _bridge:
            _log("INFO", f"Connected via LAN discovery at {_dh}:{_dp}")
            _global_bridge = _bridge
            return _bridge

        # 2. Try remote bridge from static config (secure mode: encryption required)
        if remote_host:
            try:
                from ECO_BRIDGE.eco_bridge_client import RemoteEconomyBridge
                bridge = RemoteEconomyBridge(
                    host=remote_host,
                    port=int(remote_port),
                    password=remote_key,
                    lan_mode=False,  # Static config = secure mode
                )
                if bridge.ping():
                    _log("INFO", f"Connected to remote economy bridge at {remote_host}:{remote_port} (SECURE config)")
                    _global_bridge = bridge
                    return bridge
                else:
                    _log("WARN", f"Remote bridge at {remote_host}:{remote_port} not responding")
            except Exception as e:
                _log("WARN", f"Remote bridge init failed: {e}")

        # 3. Try local DB (using OogaDB from the server module)
        try:
            from ECO_BRIDGE.eco_bridge_server import OogaDB as _LocalDB
            _cfg = get_eco_config()
            _resolved = _cfg.resolve_db_path(Path.home() / "minecraft_server")
            if _resolved:
                local_db = _LocalDB(str(_resolved))
                # Wrap as a minimal bridge interface
                class _LocalBridge:
                    def __init__(self, db):
                        self._db = db
                        self.is_ready = True
                    def get_balance(self, player):
                        return self._db.handle_request({"action": "balance", "player": player}).get("balance")
                    def get_player_info(self, player):
                        return self._db.handle_request({"action": "info", "player": player})
                    def get_economy_stats(self):
                        return self._db.handle_request({"action": "stats"})
                    def get_ledger(self, uuid_str, limit=20):
                        return self._db.handle_request({"action": "ledger", "uuid": uuid_str, "limit": limit}).get("entries", [])
                    def ping(self):
                        return self._db.handle_request({"action": "ping"}).get("ok", False)
                    def deduct(self, *a, **kw): return False
                    def credit(self, *a, **kw): return False
                    def set_balance(self, *a, **kw): return False
                    def transfer(self, *a, **kw): return {"ok": False, "error": "local bridge read-only"}
                _log("INFO", f"Connected to local DB: {_resolved} (read-only)")
                _global_bridge = _LocalBridge(local_db)
                return _global_bridge
        except Exception as e:
            _log("WARN", f"Local bridge init failed: {e}")

        _log("INFO", "No economy bridge available — AH will use internal balances")
        _global_bridge = None
        return None


def get_bridge_descriptor() -> Optional[dict]:
    """Return a dict describing the bridge config (for testing/debugging).
    Returns None if bridge is not configured.
    """
    config = get_eco_config()
    host = _eco_discovered_host or getattr(config, "eco_bridge_host", "")
    if not host:
        return None
    return {
        "host": host,
        "port": _eco_discovered_port or getattr(config, "eco_bridge_port", 7200),
        "discovered": _eco_discovered_host is not None,
    }


def _log(level, message):
    """Simple log to stdout for bridge init messages."""
    try:
        ts = __import__("datetime").datetime.now().strftime("%H:%M:%S")
        print(f"[ECO:{level}] {message}", flush=True)
    except Exception:
        pass
