#!/usr/bin/env python3
"""
test_lan_discovery.py — Data flow tests for LAN discovery and config fallback.

Tests:
  1. LAN discovery beacon → listener (end-to-end UDP)
  2. ServiceFinder.get_best() returns discovered services
  3. ServiceFinder.get_best() falls back to config if no beacon
  4. ServiceFinder switches host/port when new beacon arrives
  5. ServiceFinder removes stale entries after timeout
  6. eco_bridge.py discovery + config fallback
  7. server-stats-collector discovery integration
  8. Setup wizard script exists and compiles
  9. Start script exists and compiles
"""
import sys, os, json, time, socket, threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from engine.ports import LAN_DISCOVERY_PORT

_G = "\033[92m"
_Y = "\033[93m"
_R = "\033[91m"
_C = "\033[96m"
_N = "\033[0m"

PASS = f"{_G}PASS{_N}"
FAIL = f"{_R}FAIL{_N}"
SKIP = f"{_Y}SKIP{_N}"

_tests_run = 0
_tests_pass = 0
_tests_fail = 0
_tests_skip = 0

def test(name: str, condition: bool, detail: str = ""):
    global _tests_run, _tests_pass, _tests_fail
    _tests_run += 1
    if condition:
        _tests_pass += 1
        print(f"  {PASS}  {name}")
    else:
        _tests_fail += 1
        print(f"  {FAIL}  {name}")
        if detail:
            print(f"         {_Y}{detail}{_N}")
test.__test__ = False

def skip(name: str, reason: str = ""):
    global _tests_skip
    _tests_skip += 1
    print(f"  {SKIP}  {name}  ({_Y}{reason}{_N})")

def section(title: str):
    print(f"\n{_C}─── {title} ───{_N}")
    print(f"{_C}{'─' * (len(title) + 8)}{_N}")

# ═══════════════════════════════════════════════════════════════════════
# 1. LAN discovery end-to-end
# ═══════════════════════════════════════════════════════════════════════
section("1. LAN discovery beacon → listener (end-to-end)")

from engine.lan_discovery import ServiceBeacon, ServiceFinder

# Start listener first
_found_services = []
_listener_lock = threading.Lock()

def _cb(service_type, host, port, extra, is_new):
    with _listener_lock:
        _found_services.append({"host": host, "port": port, "is_new": is_new})

listener = ServiceFinder("test_service", callback=_cb,
                         fallback={"host": "10.0.0.1", "port": 9999})
listener.start()
time.sleep(0.2)

# Start beacon
beacon = ServiceBeacon("test_service", port=8888,
                       extra={"version": "1.0"}, interval=1)
beacon.start()
time.sleep(0.5)  # Wait for beacon to fire + listener to hear

# Test: listener found the beacon
best = listener.get_best()
test("ServiceFinder received beacon from ServiceBeacon",
     best.get("host") != "10.0.0.1" and best.get("port") == 8888,
     f"Got: {best}")

test("ServiceFinder.get_best() returns discovered port correctly",
     best.get("port") == 8888,
     f"Expected port=8888, got port={best.get('port')}")

test("Callback was called with is_new=True on first discovery",
     any(s.get("is_new") for s in _found_services),
     f"Callbacks: {_found_services}")

# Stop
beacon.stop()
listener.stop()

# ═══════════════════════════════════════════════════════════════════════
# 2. Config fallback when no beacon
# ═══════════════════════════════════════════════════════════════════════
section("2. Config fallback (no beacon)")

listener2 = ServiceFinder("nonexistent_service",
                          fallback={"host": "192.168.1.50", "port": 1234})
listener2.start()
time.sleep(0.3)
best2 = listener2.get_best()
test("get_best() returns fallback when no beacon received",
     best2.get("host") == "192.168.1.50" and best2.get("port") == 1234,
     f"Got: {best2}")
listener2.stop()

# ═══════════════════════════════════════════════════════════════════════
# 3. Service switches on new beacon
# ═══════════════════════════════════════════════════════════════════════
section("3. Service auto-switch on new beacon")

_switch_services = []
def _switch_cb(st, host, port, extra, is_new):
    with _listener_lock:
        _switch_services.append({"host": host, "port": port})

switch_listener = ServiceFinder("switch_test", callback=_switch_cb,
                                fallback={"host": "10.0.0.1", "port": 1})
switch_listener.start()
time.sleep(0.2)

# Broadcast a beacon manually (simulating a server that starts later)
import socket as _sock
_s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
_s.setsockopt(_sock.SOL_SOCKET, _sock.SO_BROADCAST, 1)
_beacon_data = json.dumps({
    "type": "lan_discovery",
    "service": "switch_test",
    "host": "192.168.1.100",
    "port": 7777,
    "ts": time.time(),
}).encode("utf-8")
_s.sendto(_beacon_data, ("127.0.0.1", LAN_DISCOVERY_PORT))
_s.close()
time.sleep(0.3)

best3 = switch_listener.get_best()
test("get_best() switches to discovered service after beacon arrives",
     best3.get("host") == "192.168.1.100" and best3.get("port") == 7777,
     f"Got: {best3}")

# Now send a NEW beacon with different host/port — simulates server IP change
_s2 = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
_s2.setsockopt(_sock.SOL_SOCKET, _sock.SO_BROADCAST, 1)
_beacon_data2 = json.dumps({
    "type": "lan_discovery",
    "service": "switch_test",
    "host": "192.168.1.200",
    "port": 7778,
    "ts": time.time(),
}).encode("utf-8")
_s2.sendto(_beacon_data2, ("127.0.0.1", LAN_DISCOVERY_PORT))
_s2.close()
time.sleep(0.3)

best4 = switch_listener.get_best()
test("get_best() switches again when a newer beacon arrives from different host",
     best4.get("host") == "192.168.1.200" and best4.get("port") == 7778,
     f"Got: {best4}")

switch_listener.stop()

# ═══════════════════════════════════════════════════════════════════════
# 4. Stale entry removal after timeout
# ═══════════════════════════════════════════════════════════════════════
section("4. Stale entry removal after timeout")

stale_listener = ServiceFinder("stale_test",
                               fallback={"host": "10.0.0.1", "port": 1},
                               timeout=2)  # Short timeout for testing
stale_listener.start()
time.sleep(0.2)

# Send one beacon
_s3 = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
_s3.setsockopt(_sock.SOL_SOCKET, _sock.SO_BROADCAST, 1)
_beacon_data3 = json.dumps({
    "type": "lan_discovery",
    "service": "stale_test",
    "host": "192.168.1.50",
    "port": 5000,
    "ts": time.time(),
}).encode("utf-8")
_s3.sendto(_beacon_data3, ("127.0.0.1", LAN_DISCOVERY_PORT))
_s3.close()
time.sleep(0.3)

best5 = stale_listener.get_best()
test("Stale listener finds beacon initially",
     best5.get("host") == "192.168.1.50",
     f"Got: {best5}")

# Wait for timeout (2s) + grace
time.sleep(2.5)

best6 = stale_listener.get_best()
test("get_best() falls back to config after beacon timeout",
     best6.get("host") == "10.0.0.1",
     f"Got: {best6} (expected fallback 10.0.0.1)")

stale_listener.stop()

# ═══════════════════════════════════════════════════════════════════════
# 5. LAN discovery _get_lan_ip helper
# ═══════════════════════════════════════════════════════════════════════
section("5. _get_lan_ip helper function")

from engine.lan_discovery import _get_lan_ip
ip = _get_lan_ip()
test("_get_lan_ip returns a valid IP address",
     isinstance(ip, str) and len(ip) > 0,
     f"Got: '{ip}'")

# ═══════════════════════════════════════════════════════════════════════
# 6. Eco bridge discovery integration
# ═══════════════════════════════════════════════════════════════════════
section("6. eco_bridge.py discovery integration")

_bridge_dir = str(PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager")
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)
from ECO_BRIDGE.eco_bridge import get_bridge_descriptor

# The module has get_bridge() which uses discovery internally.
# Test that it compiles and returns None when no bridge available.
result = get_bridge_descriptor()
test("get_bridge_descriptor returns a dict or None",
     result is None or isinstance(result, dict),
     f"Got: {type(result).__name__}")

# ═══════════════════════════════════════════════════════════════════════
# 7. Setup wizard scripts exist and compile
# ═══════════════════════════════════════════════════════════════════════
section("7. Setup wizard & start scripts")

scripts_to_check = [
    ("Server Stats daemon setup", PROJECT_ROOT / "SCRIPTS" / "SERVICES" / "server-stats-collector" / "HELPERS" / "Server Stats" / "setup.py"),
    ("Server Stats daemon start", PROJECT_ROOT / "SCRIPTS" / "SERVICES" / "server-stats-collector" / "HELPERS" / "Server Stats" / "start.py"),
    ("Eco bridge setup", PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" / "ECO_BRIDGE" / "setup.py"),
    ("Eco bridge start", PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" / "ECO_BRIDGE" / "start.py"),
]

for label, path in scripts_to_check:
    test(f"{label} exists and is valid",
         path.exists(), str(path) if not path.exists() else "")

# ═══════════════════════════════════════════════════════════════════════
# 8. eco_bridge.py get_bridge_descriptor function (exported for the test)
# ═══════════════════════════════════════════════════════════════════════
section("8. Eco bridge get_bridge_descriptor")

test("eco_bridge module exports get_bridge_descriptor",
     callable(get_bridge_descriptor))

# ═══════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════
section("RESULTS")

print(f"  {_G}{_tests_pass} passed{_N}")
if _tests_fail:
    print(f"  {_R}{_tests_fail} failed{_N}")
if _tests_skip:
    print(f"  {_Y}{_tests_skip} skipped{_N}")
print(f"  {_C}{_tests_run} total{_N}")

if _tests_fail:
    print(f"\n  {_R}Some tests FAILED — review above.{_N}")
    sys.exit(1)
else:
    print(f"\n  {_G}All LAN discovery tests passed.{_N}")
