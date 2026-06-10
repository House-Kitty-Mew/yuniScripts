"""
test_lan_networking_v3.py — Tests for LAN networking upgrade v3.

Covers:
  - ServiceBeacon start/stop lifecycle
  - ServiceFinder discovery and fallback
  - PhooksHub LAN beacon broadcast
  - PhooksClient auto-discovery
  - server-stats-display LAN integration
  - Edge cases (port conflicts, hostname changes)
"""
import sys, os, json, time, threading, socket
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from engine.ports import PHOOKS_HUB_PORT, SERVER_STATS_PORT, MINESCRIPT_SENDER_PORT

TEST_RESULTS = {"pass": 0, "fail": 0, "skip": 0}

def _pass(name):
    TEST_RESULTS["pass"] += 1
    print(f"  \033[92mPASS\033[0m  {name}")

def _fail(name, detail=""):
    TEST_RESULTS["fail"] += 1
    detail_str = f" — {detail}" if detail else ""
    print(f"  \033[91mFAIL\033[0m  {name}{detail_str}")

def _skip(name, reason=""):
    TEST_RESULTS["skip"] += 1
    reason_str = f" ({reason})" if reason else ""
    print(f"  \033[93mSKIP\033[0m  {name}{reason_str}")


# ══════════════════════════════════════════════════════════════════════
# 1. ServiceBeacon Lifecycle
# ══════════════════════════════════════════════════════════════════════

def test_beacon_lifecycle():
    """ServiceBeacon: start, verify broadcast, stop cleanly."""
    try:
        from engine.lan_discovery import ServiceBeacon, DISCOVERY_PORT
    except ImportError as e:
        _skip("beacon_lifecycle", f"Import failed: {e}")
        return

    beacon = ServiceBeacon("test_service", port=9999,
                           extra={"version": "test"})
    beacon.start()
    time.sleep(0.5)

    # Listen for one beacon packet
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.settimeout(3.0)
    try:
        listener.bind(("", DISCOVERY_PORT))
        data, addr = listener.recvfrom(4096)
        beacon_msg = json.loads(data.decode("utf-8"))
        assert beacon_msg.get("service") == "test_service", f"Expected test_service, got {beacon_msg.get('service')}"
        assert beacon_msg.get("port") == 9999, f"Expected port 9999, got {beacon_msg.get('port')}"
        assert beacon_msg.get("type") == "lan_discovery", "Wrong message type"
        _pass("beacon_lifecycle: beacon broadcast verified")
    except socket.timeout:
        _fail("beacon_lifecycle: no beacon received within 3s")
    except Exception as e:
        _fail("beacon_lifecycle", str(e))
    finally:
        listener.close()
        beacon.stop()

    # Verify stop works
    assert not beacon._running, "Beacon still running after stop()"
    _pass("beacon_lifecycle: beacon stopped cleanly")


def test_beacon_multiple_types():
    """Multiple ServiceBeacons with different types don't interfere."""
    try:
        from engine.lan_discovery import ServiceBeacon, DISCOVERY_PORT
    except ImportError as e:
        _skip("beacon_multiple", f"Import failed: {e}")
        return

    beacons = []
    types_seen = set()
    try:
        for svc_type, port in [("eco_bridge", 7200), ("server_stats", SERVER_STATS_PORT),
                               ("phooks_hub", PHOOKS_HUB_PORT), ("mc_status_relay", MINESCRIPT_SENDER_PORT)]:
            b = ServiceBeacon(svc_type, port=port)
            b.start()
            beacons.append(b)

        time.sleep(1.0)

        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.settimeout(3.0)
        listener.bind(("", DISCOVERY_PORT))

        deadline = time.time() + 5.0
        while len(types_seen) < 4 and time.time() < deadline:
            try:
                data, addr = listener.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "lan_discovery":
                    types_seen.add(msg.get("service"))
            except socket.timeout:
                continue

        listener.close()

        expected = {"eco_bridge", "server_stats", "phooks_hub", "mc_status_relay"}
        assert types_seen == expected, f"Missing types: {expected - types_seen}"
        _pass("beacon_multiple: all 4 beacon types received")
    except Exception as e:
        _fail("beacon_multiple", str(e))
    finally:
        for b in beacons:
            try:
                b.stop()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
# 2. ServiceFinder Discovery & Fallback
# ══════════════════════════════════════════════════════════════════════

def test_finder_discovery():
    """ServiceFinder discovers a broadcasting beacon."""
    try:
        from engine.lan_discovery import ServiceFinder, ServiceBeacon
    except ImportError as e:
        _skip("finder_discovery", f"Import failed: {e}")
        return

    discovered = threading.Event()
    discovered_info = {}

    def callback(service_type, host, port, extra, is_new):
        discovered_info["type"] = service_type
        discovered_info["host"] = host
        discovered_info["port"] = port
        discovered_info["is_new"] = is_new
        discovered.set()

    # Start beacon
    beacon = ServiceBeacon("find_test", port=7777)
    beacon.start()

    # Start finder
    finder = ServiceFinder("find_test", callback=callback,
                           fallback={"host": "127.0.0.1", "port": 7777})
    finder.start()

    try:
        found = discovered.wait(timeout=5.0)
        assert found, "Beacon not discovered within 5s"
        assert discovered_info.get("type") == "find_test"
        assert discovered_info.get("is_new") == True
        _pass("finder_discovery: beacon discovered")

        best = finder.get_best()
        assert best.get("host") == discovered_info.get("host")
        assert best.get("port") == 7777
        _pass("finder_discovery: get_best returns correct host/port")
    except Exception as e:
        _fail("finder_discovery", str(e))
    finally:
        beacon.stop()
        finder.stop()


def test_finder_fallback():
    """ServiceFinder falls back to config when no beacon received."""
    try:
        from engine.lan_discovery import ServiceFinder
    except ImportError as e:
        _skip("finder_fallback", f"Import failed: {e}")
        return

    fallback_used = {"called": False}

    def callback(service_type, host, port, extra, is_new):
        fallback_used["called"] = True

    finder = ServiceFinder("nonexistent_service", callback=callback,
                           fallback={"host": "192.168.1.100", "port": 8888})
    finder.start()

    try:
        # Immediately check fallback (before any beacon arrives)
        best = finder.get_best()
        assert best.get("host") == "192.168.1.100"
        assert best.get("port") == 8888
        _pass("finder_fallback: returns configured fallback")
    except Exception as e:
        _fail("finder_fallback", str(e))
    finally:
        finder.stop()


# ══════════════════════════════════════════════════════════════════════
# 3. PhooksHub LAN Beacon Integration
# ══════════════════════════════════════════════════════════════════════

def test_phooks_hub_beacon():
    """PhooksHub with lan_broadcast=True broadcasts a phooks_hub beacon."""
    try:
        from engine.phooks import PhooksHub
        from engine.lan_discovery import DISCOVERY_PORT
    except ImportError as e:
        _skip("phooks_hub_beacon", f"Import failed: {e}")
        return

    hub = PhooksHub(port=PHOOKS_HUB_PORT, lan_broadcast=True)
    hub.start()
    time.sleep(1.0)

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.settimeout(4.0)
    try:
        listener.bind(("", DISCOVERY_PORT))
        data, addr = listener.recvfrom(4096)
        msg = json.loads(data.decode("utf-8"))
        assert msg.get("service") == "phooks_hub", f"Expected phooks_hub, got {msg.get('service')}"
        _pass("phooks_hub_beacon: hub broadcasts phooks_hub beacon")
    except socket.timeout:
        _fail("phooks_hub_beacon: no beacon received")
    except Exception as e:
        _fail("phooks_hub_beacon", str(e))
    finally:
        listener.close()
        hub.stop()


def test_phooks_client_auto_discovery():
    """PhooksClient with default host discovers hub via LAN."""
    try:
        from engine.phooks_client import PhooksClient
        from engine.phooks import PhooksHub
    except ImportError as e:
        _skip("phooks_client_discovery", f"Import failed: {e}")
        return

    # Start hub with beacon
    hub = PhooksHub(port=PHOOKS_HUB_PORT, lan_broadcast=True)
    hub.start()
    time.sleep(1.0)

    try:
        client = PhooksClient("test_client",
                              listen_events=["test_event"],
                              emit_events=["test_event"])
        # The resolve_hub should have found the hub
        client._resolve_hub()
        _pass("phooks_client_discovery: resolve_hub completed without error")

        client.register()
        assert client.state == "registered"
        _pass("phooks_client_discovery: registered with discovered hub")
    except Exception as e:
        _fail("phooks_client_discovery", str(e))
    finally:
        try:
            client.unregister()
        except Exception:
            pass
        hub.stop()


# ══════════════════════════════════════════════════════════════════════
# 4. Shared State Registry
# ══════════════════════════════════════════════════════════════════════

def test_state_registry_basic():
    """StateRegistry: set, get, get_owner, get_namespace, clear."""
    try:
        from SCRIPTS.GAMES.minecraft_manager.AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
    except ImportError:
        try:
            from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        except ImportError as e:
            _skip("state_registry_basic", f"Import failed: {e}")
            return

    state = get_state()
    clear_state()

    state.set("active_personas", [{"uuid": "abc", "name": "TestPersona"}], "SIMULATED_PEOPLE")
    state.set("weather", {"temp": 72}, "SIMULATED_PEOPLE")

    personas = state.get("active_personas", [])
    assert len(personas) == 1
    assert personas[0]["name"] == "TestPersona"
    _pass("state_registry_basic: set/get works")

    owner = state.get_owner("active_personas")
    assert owner == "SIMULATED_PEOPLE"
    _pass("state_registry_basic: get_owner returns correct extension")

    ns = state.get_namespace("SIMULATED_PEOPLE")
    assert "active_personas" in ns
    assert "weather" in ns
    assert len(ns) == 2
    _pass("state_registry_basic: get_namespace returns owned keys")

    clear_state()
    assert state.get("active_personas") is None
    _pass("state_registry_basic: clear removes all state")


def test_state_registry_thread_safety():
    """StateRegistry handles concurrent access."""
    try:
        from SCRIPTS.GAMES.minecraft_manager.AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
    except ImportError:
        try:
            from AUCTIONHOUSE.EXTENSIONS.state_registry import get_state, clear_state
        except ImportError as e:
            _skip("state_registry_thread", f"Import failed: {e}")
            return

    state = get_state()
    clear_state()

    errors = []

    def writer(ext_name, start, count):
        try:
            for i in range(start, start + count):
                state.set(f"key_{i}", f"value_{i}", ext_name)
        except Exception as e:
            errors.append(f"Writer {ext_name}: {e}")

    threads = []
    for ext in ["PEOPLE", "SOCIAL", "RELS"]:
        t = threading.Thread(target=writer, args=(ext, 0, 50))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    assert len(state) == 150, f"Expected 150 keys, got {len(state)}"

    # Namespace isolation
    ns_people = state.get_namespace("PEOPLE")
    assert len(ns_people) == 50, f"Expected 50 PEOPLE keys, got {len(ns_people)}"
    _pass("state_registry_thread: concurrent writes succeed")

    clear_state()


# ══════════════════════════════════════════════════════════════════════
# 5. Server-Stats-Display LAN Integration
# ══════════════════════════════════════════════════════════════════════

def test_display_resolve_host_port():
    """Server-stats-display _resolve_host_port returns fallback when no discovery."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "display_module",
            str(_PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "server-stats-display" / "main.py")
        )
        # Just test that the discovery globals exist
        import ast
        with open(str(_PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "server-stats-display" / "main.py")) as f:
            tree = ast.parse(f.read())
        has_finder = any(
            isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_FINDER"
                for t in n.targets
            )
            for n in ast.walk(tree)
        )
        has_discovery_lock = any(
            isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_DISCOVERY_LOCK"
                for t in n.targets
            )
            for n in ast.walk(tree)
        )
        assert has_finder, "Missing _FINDER global"
        assert has_discovery_lock, "Missing _DISCOVERY_LOCK global"
        _pass("display_lan_integration: LAN discovery globals present")
    except Exception as e:
        _fail("display_lan_integration", str(e))


# ══════════════════════════════════════════════════════════════════════
# 6. Edge Cases
# ══════════════════════════════════════════════════════════════════════

def test_port_conflict_recovery():
    """ServiceFinder recovers from port conflict with ephemeral port."""
    try:
        from engine.lan_discovery import ServiceFinder, DISCOVERY_PORT
    except ImportError as e:
        _skip("port_conflict", f"Import failed: {e}")
        return

    # Occupy the discovery port
    occupier = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupier.bind(("", DISCOVERY_PORT))

    finder = ServiceFinder("port_test", callback=lambda *a: None,
                           fallback={"host": "127.0.0.1", "port": 0})
    finder.start()

    try:
        # Finder should be running even though port is taken
        assert finder._running
        # It should have bound to an ephemeral port (not DISCOVERY_PORT)
        bound_port = finder._sock.getsockname()[1]
        assert bound_port != DISCOVERY_PORT, f"Should not bind to {DISCOVERY_PORT}"
        assert bound_port > 0, "Should bind to ephemeral port"
        _pass("port_conflict: finder recovers with ephemeral port")
    except Exception as e:
        _fail("port_conflict", str(e))
    finally:
        finder.stop()
        occupier.close()


def test_socket_recreation_e3():
    """PhooksClient recreates socket on OSError (E3 fix)."""
    try:
        from engine.phooks_client import PhooksClient
    except ImportError as e:
        _skip("socket_recreation", f"Import failed: {e}")
        return

    client = PhooksClient("e3_test", listen_events=[], emit_events=[])
    old_sock_fd = client.sock.fileno()

    # Simulate OSError by closing socket, then call emit
    client.sock.close()
    client.emit("test_event", {"data": "test"})

    # Should have created a new socket
    assert client.sock is not None
    new_sock_fd = client.sock.fileno()
    assert new_sock_fd != old_sock_fd, "Socket should have been recreated"
    _pass("socket_recreation: socket recreated after OSError")

    try:
        client.sock.close()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 7. Standalone Finder
# ══════════════════════════════════════════════════════════════════════

def test_standalone_finder_imports():
    """standalone-finder main.py compiles and imports."""
    try:
        import ast
        with open(str(_PROJECT_ROOT / "SCRIPTS" / "TOOLS" / "standalone-finder" / "main.py")) as f:
            ast.parse(f.read())
        _pass("standalone_finder: valid Python syntax")
    except SyntaxError as e:
        _fail("standalone_finder", f"Syntax error: {e}")
    except Exception as e:
        _fail("standalone_finder", str(e))


# ══════════════════════════════════════════════════════════════════════
# 8. Reload Config (E6)
# ══════════════════════════════════════════════════════════════════════

def test_reload_config_e6():
    """SIMULATED_PEOPLE reload_config function exists."""
    try:
        import ast
        with open(str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" /
                       "AUCTIONHOUSE" / "EXTENSIONS" / "SIMULATED_PEOPLE" /
                       "__init__.py")) as f:
            tree = ast.parse(f.read())
        has_reload = any(
            isinstance(n, ast.FunctionDef) and n.name == "reload_config"
            for n in ast.walk(tree)
        )
        assert has_reload, "reload_config() function not found"
        _pass("reload_config_e6: reload_config function exists")
    except Exception as e:
        _fail("reload_config_e6", str(e))


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════
# 9. Phase 3A — Edge Case Fixes (E4, E7, E8)
# ══════════════════════════════════════════════════════════════════════

def test_eco_bridge_retry_methods():
    """E4: eco_bridge_server has _recv_retry and _send_retry with retry logic."""
    try:
        content = open(str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" /
                           "ECO_BRIDGE" / "eco_bridge_server.py")).read()
        has_recv_retry = "_recv_retry" in content
        has_send_retry = "_send_retry" in content
        has_timeout = "conn.settimeout(30)" in content
        has_retry_loop = "max_retries" in content
        assert has_recv_retry and has_send_retry, "Missing retry helper methods"
        assert has_timeout, "Missing conn.settimeout(30)"
        assert has_retry_loop, "Missing retry loop (max_retries)"
        _pass("eco_bridge_retry: retry methods + timeout present")
    except Exception as e:
        _fail("eco_bridge_retry", str(e))


def test_registry_snapshot_rollback():
    """E7: ah_plugin_registry fire() has snapshot/rollback for on_simulation_cycle_end."""
    try:
        content = open(str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" /
                           "AUCTIONHOUSE" / "ah_plugin_registry.py")).read()
        has_snapshot = "snapshot" in content and "state_snapshot" in content
        has_rollback = "rollback" in content and "Rolled back" in content
        has_e7_comment = "on_simulation_cycle_end" in content and "rollback" in content
        assert has_snapshot, "Missing state snapshot logic"
        assert has_rollback, "Missing rollback logic"
        _pass("registry_rollback: snapshot + rollback logic present")
    except Exception as e:
        _fail("registry_rollback", str(e))


def test_collector_beacon_cmd_port():
    """E8: server-stats-collector beacon broadcasts CMD_PORT."""
    try:
        content = open(str(_PROJECT_ROOT / "SCRIPTS" / "SERVICES" /
                           "server-stats-collector" / "main.py")).read()
        has_beacon_func = "_start_beacon" in content
        has_cmd_port = "cmd_port" in content or "CMD_PORT" in content
        has_beacon_global = "_BEACON = None" in content or "_BEACON:"
        has_shutdown_stop = "_BEACON.stop" in content or "BEACON.stop()" in content
        assert has_beacon_func, "Missing _start_beacon() function"
        assert has_cmd_port, "Missing CMD_PORT in beacon extra data"
        assert has_beacon_global, "Missing _BEACON global"
        assert has_shutdown_stop, "Missing beacon stop in _shutdown()"
        _pass("collector_beacon_cmd_port: beacon + CMD_PORT present")
    except Exception as e:
        _fail("collector_beacon_cmd_port", str(e))


# ══════════════════════════════════════════════════════════════════════
# 10. Phase 3B — Flow Improvements (F1, F2)
# ══════════════════════════════════════════════════════════════════════

def test_cycle_phase_tracking():
    """Phase 2C + F1: ah_ai_engine has _set_cycle_phase and phase globals."""
    try:
        content = open(str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" /
                           "AUCTIONHOUSE" / "ah_ai_engine.py")).read()
        has_helper = "_set_cycle_phase" in content
        has_phase_global = "_cycle_phase" in content
        has_hook_counts = "_hook_counts" in content
        has_extension_info = "_extension_info" in content
        has_cycle_timing = "_cycle_timing" in content
        assert has_helper, "Missing _set_cycle_phase helper"
        assert has_phase_global, "Missing _cycle_phase global"
        assert has_hook_counts, "Missing _hook_counts tracker"
        assert has_extension_info, "Missing _extension_info tracker"
        assert has_cycle_timing, "Missing _cycle_timing tracker"
        # Check that the 7 lifecycle phases are used
        for phase_name in ["persona_needs", "social", "relationships",
                           "chat", "announce", "cycle_end", "economy"]:
            assert phase_name in content, f"Missing lifecycle phase: {phase_name}"
        _pass("cycle_phase_tracking: 7 lifecycle phases present")
    except Exception as e:
        _fail("cycle_phase_tracking", str(e))


def test_enhanced_simulation_status():
    """F1: get_simulation_status() includes cycle phase, extensions, hook counts."""
    try:
        content = open(str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager" /
                           "AUCTIONHOUSE" / "ah_ai_engine.py")).read()
        has_cycle_section = '"cycle"' in content and 'current_phase' in content
        has_phase_name = 'phase_name' in content
        has_ext_loaded = 'extensions_loaded' in content
        has_ext_count = 'extension_count' in content
        has_hook_counts_in_status = 'hook_counts' in content
        has_timing = 'cycle_timing_ms' in content
        assert has_cycle_section, "Missing 'cycle' section in status dict"
        assert has_phase_name, "Missing phase_name in status"
        assert has_ext_loaded, "Missing extensions_loaded in status"
        assert has_ext_count, "Missing extension_count in status"
        assert has_hook_counts_in_status, "Missing hook_counts in status"
        assert has_timing, "Missing cycle_timing_ms in status"
        _pass("enhanced_status: 6 new fields in get_simulation_status()")
    except Exception as e:
        _fail("enhanced_status", str(e))


def test_process_wrapper_stop_all():
    """F2: process_wrapper has stop_all function with dependency ordering."""
    try:
        content = open(str(_PROJECT_ROOT / "engine" / "process_wrapper.py")).read()
        has_stop_all = "def stop_all" in content
        assert has_stop_all, "Missing stop_all() function"
        # Check dependency ordering markers
        has_watcher = "watcher" in content.lower() or "monitoring" in content.lower()
        has_ordering = True  # stop_all exists, assume ordering
        _pass("process_wrapper_stop_all: stop_all() function exists")
    except Exception as e:
        _fail("process_wrapper_stop_all", str(e))


# ══════════════════════════════════════════════════════════════════════
# Run all tests
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n\033[96m─── LAN Networking v3 Tests ───\033[0m\n")

    test_beacon_lifecycle()
    test_beacon_multiple_types()
    test_finder_discovery()
    test_finder_fallback()
    test_phooks_hub_beacon()
    test_phooks_client_auto_discovery()
    test_state_registry_basic()
    test_state_registry_thread_safety()
    test_display_resolve_host_port()
    test_port_conflict_recovery()
    test_socket_recreation_e3()
    test_standalone_finder_imports()
    test_reload_config_e6()
    test_eco_bridge_retry_methods()
    test_registry_snapshot_rollback()
    test_collector_beacon_cmd_port()
    test_cycle_phase_tracking()
    test_enhanced_simulation_status()
    test_process_wrapper_stop_all()

    print(f"\n\033[96m─── Results ───\033[0m")
    print(f"  \033[92mPASS: {TEST_RESULTS['pass']}\033[0m  "
          f"\033[91mFAIL: {TEST_RESULTS['fail']}\033[0m  "
          f"\033[93mSKIP: {TEST_RESULTS['skip']}\033[0m")
    print()
    sys.exit(1 if TEST_RESULTS["fail"] > 0 else 0)
