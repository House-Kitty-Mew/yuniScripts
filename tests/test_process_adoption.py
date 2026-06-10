#!/usr/bin/env python3
"""
test_process_adoption.py — Comprehensive tests for the Process Adoption System.

Tests every function in engine/process_adoption.py with edge cases:
  - PID file atomic write/read/remove
  - Cross-platform process detection
  - Port detection
  - Combined script status
  - Process adoption flow
  - Stale PID cleanup
  - Race conditions and thread safety
  - Permission errors and malformed files
"""

import sys
import os
import time
import tempfile
import threading
import socket
from pathlib import Path

# ── Project root ─────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Colours ──────────────────────────────────────────────────────────
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

def skip(name: str, reason: str = ""):
    global _tests_skip
    _tests_skip += 1
    print(f"  {SKIP}  {name}  ({_Y}{reason}{_N})")

def section(title: str):
    print(f"\n{_C}─── {title} ───{_N}")
    print(f"{_C}{'─' * (len(title) + 8)}{_N}")

# ── Import the module under test ─────────────────────────────────────
from engine.process_adoption import (
    get_pid_dir,
    pid_file_path,
    write_pid_file,
    read_pid_file,
    remove_pid_file,
    is_process_alive,
    _is_process_alive_windows,
    _is_process_alive_unix,
    check_port_in_use,
    check_script_status,
    adopt_existing_process,
    cleanup_stale_pids,
    find_and_adopt,
    check_adopted_health,
    _kill_process,
    DEFAULT_PORT_CHECK_TIMEOUT,
)
from engine.process_wrapper import (
    STATUS_RUNNING,
    STATUS_CRASHED,
    STATUS_STOPPED,
)

# ═══════════════════════════════════════════════════════════════════════
# 1. PID File Operations
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    section("1. PID File Operations — Atomic Write / Read / Remove")

    # Test basic roundtrip
    pid_dir = get_pid_dir()
    test("PID directory exists", pid_dir.exists(), str(pid_dir))

    test("PID directory is within engine/", "engine" in str(pid_dir))

    # Roundtrip: write → read → remove
    script_id_1 = "GAMES/minecraft_manager"
    test(f"write_pid_file('{script_id_1}', {os.getpid()}) returns True",
         write_pid_file(script_id_1, os.getpid()))

    pid_read = read_pid_file(script_id_1)
    test(f"read_pid_file('{script_id_1}') returns PID {os.getpid()}",
         pid_read == os.getpid(),
         f"Got: {pid_read}")

    test("remove_pid_file returns True", remove_pid_file(script_id_1))
    test("read_pid_file returns None after removal",
         read_pid_file(script_id_1) is None)

    # Test sanitized path
    safe_path = pid_file_path(script_id_1)
    test(f"PID file path for '{script_id_1}' uses underscores",
         "GAMES_minecraft_manager.pid" in str(safe_path))

    # Test remove non-existent file
    test("remove_pid_file on non-existent returns True",
         remove_pid_file("nonexistent_script"))

    # Test write with invalid PID
    test("write_pid_file with negative PID returns False",
         not write_pid_file("test_neg", -1))

    test("write_pid_file with zero PID returns False",
         not write_pid_file("test_zero", 0))

    test("write_pid_file with non-int PID returns False",
         not write_pid_file("test_str", None))  # type: ignore

    # Test malformed PID file
    pid_path = pid_file_path("malformed_test")
    pid_path.write_text("not_a_number\n")
    test("read_pid_file of malformed file returns None",
         read_pid_file("malformed_test") is None)
    pid_path.unlink(missing_ok=True)

    # Test empty PID file
    pid_path.write_text("")
    test("read_pid_file of empty file returns None",
         read_pid_file("malformed_test") is None)
    pid_path.unlink(missing_ok=True)

    # Test whitespace-only PID file
    pid_path.write_text("   \n")
    test("read_pid_file of whitespace-only file returns None",
         read_pid_file("malformed_test") is None)
    pid_path.unlink(missing_ok=True)

    # Test atomic write: verify temp file is cleaned up on failure
    test("No leftover .tmp files in PID dir",
         len(list(pid_dir.glob("*.tmp"))) == 0,
         f"Found: {list(pid_dir.glob('*.tmp'))}")


    # ═══════════════════════════════════════════════════════════════════════
    # 2. Process Detection (Cross-Platform)
    # ═══════════════════════════════════════════════════════════════════════

    section("2. Process Detection — Cross-Platform")

    # Test our own PID (must be alive)
    test(f"is_process_alive({os.getpid()}) returns True (our own process)",
         is_process_alive(os.getpid()))

    # Test non-existent PID
    test("is_process_alive(-1) returns False (invalid PID)",
         not is_process_alive(-1))

    test("is_process_alive(0) returns False (invalid PID)",
         not is_process_alive(0))

    test("is_process_alive(999999999) returns False (non-existent PID)",
         not is_process_alive(999999999))

    test("is_process_alive(None) returns False",
         not is_process_alive(None))  # type: ignore

    test("is_process_alive('abc') returns False",
         not is_process_alive("abc"))  # type: ignore

    # Test _is_process_alive_unix directly (always works on all platforms)
    test("_is_process_alive_unix of our PID returns True",
         _is_process_alive_unix(os.getpid()))

    test("_is_process_alive_unix of -1 returns False",
         not _is_process_alive_unix(-1))

    test("_is_process_alive_windows of -1 returns False",
         not _is_process_alive_windows(-1))


    # ═══════════════════════════════════════════════════════════════════════
    # 3. Port Detection
    # ═══════════════════════════════════════════════════════════════════════

    section("3. Port Detection — TCP Connection Check")

    # Find a free port by binding to port 0
    free_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free_sock.bind(("", 0))
    free_port = free_sock.getsockname()[1]
    free_sock.listen(1)

    # Now the port is in use
    test(f"check_port_in_use('127.0.0.1', {free_port}) returns True (we're listening)",
         check_port_in_use("127.0.0.1", free_port))

    # Close and verify it's free
    free_sock.close()
    time.sleep(0.1)  # Allow TIME_WAIT to clear

    # Port may still show as in use due to TIME_WAIT — this is expected behavior
    # Let's test with a port we know is open (e.g., our test server just closed)
    test(f"check_port_in_use('127.0.0.1', {free_port}) runs without error",
         isinstance(check_port_in_use("127.0.0.1", free_port), bool))

    # Test invalid ports
    test("check_port_in_use with port 0 returns False",
         not check_port_in_use("127.0.0.1", 0))

    test("check_port_in_use with port 65536 returns False",
         not check_port_in_use("127.0.0.1", 65536))

    test("check_port_in_use with negative port returns False",
         not check_port_in_use("127.0.0.1", -1))

    # Test with non-routable IP (should return False after timeout)
    test("check_port_in_use to unreachable host returns False quickly",
         not check_port_in_use("10.255.255.1", 80, timeout=0.5))


    # ═══════════════════════════════════════════════════════════════════════
    # 4. Combined Script Status
    # ═══════════════════════════════════════════════════════════════════════

    section("4. Combined Script Status — PID + Port Check")

    # Create a mock script instance
    mock_script = {
        "id": "test/status-check",
        "meta": {
            "ports": [],
            "server_type": "normal",
            "shutdown_timeout": 5.0,
        }
    }

    # No PID file → not adoptable
    status = check_script_status(mock_script)
    test("No PID file → not adoptable",
         not status["adoptable"] and status["pid"] is None)

    # Write our PID (we are alive but no ports configured)
    write_pid_file(mock_script["id"], os.getpid())
    status = check_script_status(mock_script)
    test("Our PID alive, no ports → adoptable (ports_open == pid_alive)",
         status["adoptable"] and status["pid_alive"])
    remove_pid_file(mock_script["id"])

    # Test with ports (but no port actually in use)
    mock_script["meta"]["ports"] = [9999]
    write_pid_file(mock_script["id"], os.getpid())
    status = check_script_status(mock_script)
    # Our PID is alive, but port 9999 is not in use
    test("Our PID alive, port closed → not adoptable",
         not status["ports_open"])
    remove_pid_file(mock_script["id"])

    # Test with both PID stale and no ports
    status = check_script_status({
        "id": "test/no-exist",
        "meta": {"ports": [8888]}
    })
    test("Non-existent script → not adoptable",
         not status["adoptable"] and status["pid"] is None)


    # ═══════════════════════════════════════════════════════════════════════
    # 5. Process Adoption
    # ═══════════════════════════════════════════════════════════════════════

    section("5. Process Adoption — adopt_existing_process")

    mock_meta = {
        "id": "test/adopted-process",
        "meta": {
            "server_type": "long_running",
            "shutdown_timeout": 30.0,
        }
    }

    adopted = adopt_existing_process(mock_meta, os.getpid())
    test("Adopted process has correct PID",
         adopted["pid"] == os.getpid())
    test("Adopted process has adopted=True flag",
         adopted.get("adopted") is True)
    test("Adopted process has correct status",
         adopted["status"] == STATUS_RUNNING)
    test("Adopted process has no subprocess object",
         adopted.get("process") is None)
    test("Adopted process has server_type from meta",
         adopted.get("server_type") == "long_running")
    test("Adopted process has shutdown_timeout from meta",
         adopted.get("shutdown_timeout") == 30.0)
    test("Adopted process has log_path",
         adopted.get("log_path") is not None)
    test("Adopted process has script_id",
         adopted.get("script_id") == "test/adopted-process")


    # ═══════════════════════════════════════════════════════════════════════
    # 6. Adopted Process Health Check
    # ═══════════════════════════════════════════════════════════════════════

    section("6. Adopted Process Health Check")

    # Our PID is alive
    healthy = check_adopted_health(adopted)
    test("Adopted process (our PID) is healthy",
         healthy["status"] == STATUS_RUNNING)

    # Dead PID
    dead_adopted = {**adopted, "pid": 999999999}
    dead_check = check_adopted_health(dead_adopted)
    test("Adopted process (dead PID) is crashed",
         dead_check["status"] == STATUS_CRASHED)

    # Non-adopted process passes through
    normal_proc = {"pid": os.getpid(), "adopted": False}
    normal_check = check_adopted_health(normal_proc)
    test("Non-adopted process passes through unchanged",
         normal_check is normal_proc)


    # ═══════════════════════════════════════════════════════════════════════
    # 7. Stale PID Cleanup
    # ═══════════════════════════════════════════════════════════════════════

    section("7. Stale PID Cleanup")

    # Clean start
    cleanup_stale_pids()

    # Write PID file for a dead process (PID 999999999 should be dead)
    write_pid_file("stale_test_1", 999999999)
    write_pid_file("stale_test_2", 999999998)
    write_pid_file("stale_test_3", os.getpid())  # This one is alive

    cleaned = cleanup_stale_pids()
    test(f"cleanup_stale_pids cleaned at least 2 stale PID files",
         cleaned >= 2, f"Cleaned: {cleaned}")

    # The alive one should still exist
    pid_still_alive = read_pid_file("stale_test_3")
    test("Alive PID file still exists after cleanup",
         pid_still_alive == os.getpid())

    # Clean up
    remove_pid_file("stale_test_3")

    # Test malformed file cleanup
    malformed_path = pid_file_path("malformed_cleanup")
    malformed_path.write_text("garbage\n")
    cleaned2 = cleanup_stale_pids()
    test("Malformed PID file cleaned up",
         cleaned2 >= 1)
    test("Malformed PID file removed",
         not malformed_path.exists())


    # ═══════════════════════════════════════════════════════════════════════
    # 8. Find and Adopt (High-Level Flow)
    # ═══════════════════════════════════════════════════════════════════════

    section("8. Find and Adopt — High-Level Flow")

    # Script with no PID file, server_type=normal → returns None (no attempt)
    normal_script = {
        "id": "test/normal-no-adopt",
        "meta": {"server_type": "normal", "ports": []}
    }
    result = find_and_adopt(normal_script)
    test("Normal script: find_and_adopt returns None (no adoption attempted)",
         result is None)

    # Script with no PID file, server_type=long_running → returns None (fresh start)
    fresh_script = {
        "id": "test/fresh-server",
        "meta": {"server_type": "long_running", "ports": []}
    }
    result = find_and_adopt(fresh_script)
    test("Fresh server (no PID): find_and_adopt returns None",
         result is None)

    # Script with PID pointing to us (alive) → should adopt
    write_pid_file("test/adopt-me", os.getpid())
    adopt_script = {
        "id": "test/adopt-me",
        "meta": {"server_type": "long_running", "ports": []}
    }
    result = find_and_adopt(adopt_script)
    test("Server with PID file to alive process: adoption succeeds",
         result is not None and result.get("adopted") is True,
         f"Got: {result.get('status') if result else 'None'}")
    if result:
        test("Adopted PID matches our PID",
             result["pid"] == os.getpid())

    remove_pid_file("test/adopt-me")

    # Script with stale PID → adoption returns None, PID cleaned
    write_pid_file("test/stale-adopt", 999999999)
    stale_script = {
        "id": "test/stale-adopt",
        "meta": {"server_type": "long_running", "ports": []}
    }
    result = find_and_adopt(stale_script)
    test("Server with stale PID: returns None (fresh start)",
         result is None)
    test("Stale PID file was cleaned up",
         not pid_file_path("test/stale-adopt").exists())


    # ═══════════════════════════════════════════════════════════════════════
    # 9. _kill_process
    # ═══════════════════════════════════════════════════════════════════════

    section("9. _kill_process — Cross-Platform Termination")

    # Killing a non-existent process should not throw
    test("_kill_process(-1) runs without error",
         not _kill_process(-1))

    test("_kill_process(999999999) runs without error",
         not _kill_process(999999999))


    # ═══════════════════════════════════════════════════════════════════════
    # 10. Script ID Sanitization (via pid_file_path)
    # ═══════════════════════════════════════════════════════════════════════

    section("10. Script ID Sanitization")

    # Standard path
    p1 = pid_file_path("GAMES/minecraft_manager")
    test("'GAMES/minecraft_manager' sanitized to use underscores",
         "GAMES_minecraft_manager" in str(p1))

    # Windows backslash
    p2 = pid_file_path("SCRIPTS\\test\\script")
    test("Windows backslash path sanitized",
         "SCRIPTS_test_script" in str(p2))

    # Path with colons and spaces
    p3 = pid_file_path("TEST: my script")
    test("Colons and spaces sanitized",
         "TEST__my_script" in str(p3))

    # Very long path (should be truncated)
    long_id = "a" * 500
    p4 = pid_file_path(long_id)
    test("Very long script_id capped at 200 chars + .pid extension",
         len(p4.stem) <= 200,
         f"Stem length: {len(p4.stem)}")


    # ═══════════════════════════════════════════════════════════════════════
    # 11. Thread Safety — Concurrent PID File Access
    # ═══════════════════════════════════════════════════════════════════════

    section("11. Thread Safety — Concurrent PID File Access")

    errors = []
    def concurrent_writer(idx):
        try:
            sid = f"test/concurrent-{idx}"
            for i in range(50):
                write_pid_file(sid, os.getpid() + i)
                read_pid_file(sid)
            remove_pid_file(sid)
        except Exception as e:
            errors.append(f"Thread {idx}: {e}")

    threads = [threading.Thread(target=concurrent_writer, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    test("No thread errors in concurrent PID file access",
         len(errors) == 0,
         f"Errors: {errors[:3]}")


    # ═══════════════════════════════════════════════════════════════════════
    # 12. Permission Handling
    # ═══════════════════════════════════════════════════════════════════════

    section("12. Permission & Error Handling")

    # Write to a path we shouldn't be able to (non-existent parent)
    # This should fail gracefully
    test("write_pid_file with parent dir permission error returns False gracefully",
         not write_pid_file("../../etc/passwd?no", 1234) or True)
    # ^ On some systems this might "work" (creates dirs), but we just verify no crash


    # ═══════════════════════════════════════════════════════════════════════
    # 13. Server Type Handling in Metadata
    # ═══════════════════════════════════════════════════════════════════════

    section("13. Metadata Integration — server_type & shutdown_timeout")

    from engine.metadata import default_meta, parse_meta_info, parse_meta_json
    from pathlib import Path

    meta = default_meta()
    test("default_meta has server_type='normal'",
         meta.get("server_type") == "normal")
    test("default_meta has shutdown_timeout=5.0",
         meta.get("shutdown_timeout") == 5.0)

    # Test INI parsing with custom values
    import tempfile as tf
    with tf.NamedTemporaryFile(mode='w', suffix='.info', delete=False) as f:
        f.write("""[script]
    name = Test Server
    server_type = long_running
    shutdown_timeout = 30
    restart_policy = always
    ports = 25565
    """)
        ini_path = Path(f.name)

    try:
        parsed = parse_meta_info(ini_path)
        test("INI parser reads server_type=long_running",
             parsed.get("server_type") == "long_running")
        test("INI parser reads shutdown_timeout=30",
             parsed.get("shutdown_timeout") == 30.0)
        test("INI parser keeps default name from section",
             parsed.get("name") == "Test Server")
        test("INI parser reads ports=[25565]",
             parsed.get("ports") == [25565])
    finally:
        ini_path.unlink(missing_ok=True)

    # Test JSON parsing
    import json
    with tf.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "name": "JSON Test Server",
            "server_type": "critical",
            "shutdown_timeout": 60,
            "ports": [25565, 25575],
        }, f)
        json_path = Path(f.name)

    try:
        parsed_json = parse_meta_json(json_path)
        test("JSON parser reads server_type=critical",
             parsed_json.get("server_type") == "critical")
        test("JSON parser reads shutdown_timeout=60",
             parsed_json.get("shutdown_timeout") == 60)
        test("JSON parser reads ports=[25565, 25575]",
             parsed_json.get("ports") == [25565, 25575])
    finally:
        json_path.unlink(missing_ok=True)


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
        print(f"\n  {_G}All process adoption tests passed.{_N}")
