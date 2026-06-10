#!/usr/bin/env python3
"""
Master test runner for YuniScripts.
Runs ALL tests in isolated subprocess batches to prevent module contamination.
"""

import subprocess, sys, os, json, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Test batches (isolated subprocesses) ──────────────────────────────
# Each batch runs in its own Python process to prevent sys.modules leakage.
# Order within a batch matters: import-heavy tests first, mock-heavy last.

BATCHES = [
    {
        "name": "Batch 1: Engine Core",
        "modules": [
            "tests.test_process_wrapper",
            "tests.test_process_adoption",
            "tests.test_config_loading",
            "tests.test_config_centralization",
            "tests.test_config_loader_exhaustive",
        ]
    },
    {
        "name": "Batch 2: AH Core & Plugin Registry",
        "modules": [
            "tests.test_ah_core_exhaustive",
            "tests.test_ah_plugin_registry",
            "tests.test_phooks_event_flow",
        ]
    },
    {
        "name": "Batch 3: Ecosystem Integration (mocked modules)",
        "modules": [
            "tests.test_ecosystem_integration",
        ]
    },
    {
        "name": "Batch 4: Adoption & Shutdown",
        "modules": [
            "tests.test_adoption_restart_cycle",
            "tests.test_shutdown_graceful",
        ]
    },
    {
        "name": "Batch 5: Cross-Platform & Networking",
        "modules": [
            "tests.test_cross_platform",
            "tests.test_lan_discovery",
            "tests.test_lan_networking_v3",
        ]
    },
    {
        "name": "Batch 6: DeepSky Client Tests",
        "modules": [
            "SCRIPTS.CLIENTS.deepsky_client.tests.test_api_client",
            "SCRIPTS.CLIENTS.deepsky_client.tests.test_session_manager",
            "SCRIPTS.CLIENTS.deepsky_client.tests.test_healing_agent",
            "SCRIPTS.CLIENTS.deepsky_client.tests.test_work_order_engine",
            "SCRIPTS.CLIENTS.deepsky_client.tests.test_system_prompt_gen",
            "SCRIPTS.CLIENTS.deepsky_client.tests.test_integration",
        ]
    },
    {
        "name": "Batch 7: Simulation Extensions",
        "modules": [
            "SCRIPTS.GAMES.minecraft_manager.AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.tests.test_shm_unit",
            "SCRIPTS.GAMES.minecraft_manager.AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.tests.test_shm_dataflow",
            "SCRIPTS.GAMES.minecraft_manager.AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tests.test_tr_banditry",
            "SCRIPTS.GAMES.minecraft_manager.AUCTIONHOUSE.EXTENSIONS.SIMULATED_TRADE.tests.test_tr_barter",
        ]
    },
    {
        "name": "Batch 8: Social & Relationships & Announce",
        "modules": [
            "tests.test_simulated_social",
            "tests.test_simulated_relationships",
            "tests.test_simulated_announce",
            "tests.test_player_chat",
        ]
    },
    {
        "name": "Batch 9: System Integration Rounds",
        "modules": [
            "tests.test_integration_round1_unit",
            "tests.test_integration_round2_component",
            "tests.test_integration_round3_system",
            "tests.test_integration_round4_stress_edge",
        ]
    },
    {
        "name": "Batch 10: Services",
        "modules": [
            "SCRIPTS.SERVICES.fastmcp_server.tests.test_fastmcp_adapter",
            "SCRIPTS.SERVICES.fastmcp_server.tests.test_phooks_bridge",
            "SCRIPTS.SERVICES.fastmcp_server.tests.test_tool_registry_proxy",
            "SCRIPTS.SERVICES.fastmcp_server.tests.test_integration_fastmcp",
            "SCRIPTS.SERVICES.fastmcp_server.tests.test_debug_hooks",
        ]
    },
    {
        "name": "Batch 11: Misc",
        "modules": [
            "tests.test_simulated_server",
            "tests.test_gui_dashboard",
            "SCRIPTS.SERVICES.item_signing_bridge.test_sign",
        ]
    },
    {
        "name": "Batch 12: SHM Integration (slow)",
        "modules": [
            "SCRIPTS.GAMES.minecraft_manager.AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.tests.test_shm_integration",
        ]
    },
]


def run_batch(batch: dict) -> dict:
    """Run a batch of tests in a subprocess. Returns result summary."""
    modules = batch["modules"]
    result = {
        "name": batch["name"],
        "modules": modules,
        "exit_code": -1,
        "ran": 0,
        "failures": 0,
        "errors": 0,
        "ok": False,
        "stdout_preview": "",
    }

    cmd = [sys.executable, "-m", "unittest", "-v"] + modules

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), env=env, timeout=120
        )
        result["exit_code"] = proc.returncode

        # Parse output for test counts
        stdout = proc.stdout
        stderr = proc.stderr
        result["stdout_preview"] = stdout[-500:] if len(stdout) > 500 else stdout

        # Count tests from output
        import re
        for line in stdout.split('\n'):
            m = re.search(r'Ran (\d+) tests', line)
            if m:
                result["ran"] = int(m.group(1))
            m = re.search(r'FAILED \(failures=(\d+), errors=(\d+)\)', line)
            if m:
                result["failures"] = int(m.group(1))
                result["errors"] = int(m.group(2))
            m = re.search(r'FAILED \(errors=(\d+)\)', line)
            if m:
                result["errors"] = int(m.group(1))
            m = re.search(r'FAILED \(failures=(\d+)\)', line)
            if m:
                result["failures"] = int(m.group(1))

        # Determine overall status
        result["ok"] = (proc.returncode == 0 and "FAILED" not in stdout)
        result["stdout_raw"] = stdout[-1000:] if len(stdout) > 1000 else stdout
        if stderr:
            result["stderr"] = stderr[-500:] if len(stderr) > 500 else stderr

    except subprocess.TimeoutExpired:
        result["stdout_preview"] = "TIMEOUT after 120s"
    except Exception as e:
        result["stdout_preview"] = f"ERROR: {e}"

    return result


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run all YuniScripts tests in batches")
    parser.add_argument("--batch", type=int, default=None,
                        help="Run only this batch number (1-12)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    total_ran = 0
    total_failures = 0
    total_errors = 0
    batch_results = []

    start_time = time.time()

    for i, batch in enumerate(BATCHES, 1):
        if args.batch and args.batch != i:
            continue

        print(f"\n{'='*70}")
        print(f"🚀 Batch {i}/{len(BATCHES)}: {batch['name']}")
        print(f"   Modules: {', '.join(batch['modules'])}")
        print(f"{'='*70}")

        result = run_batch(batch)
        batch_results.append(result)

        total_ran += result["ran"]
        total_failures += result["failures"]
        total_errors += result["errors"]

        if result["ok"]:
            print(f"  ✅ PASSED ({result['ran']} tests)")
        else:
            print(f"  ❌ FAILED ({result['ran']} ran, {result['failures']} failures, {result['errors']} errors)")
            if result.get("stdout_raw"):
                print(f"  Last output: {result['stdout_raw']}")
            if result.get("stderr"):
                print(f"  STDERR: {result['stderr']}")

    elapsed = time.time() - start_time

    # Summary
    print(f"\n{'='*70}")
    print(f"📊 GRAND SUMMARY")
    print(f"{'='*70}")
    print(f"   Total tests run: {total_ran}")
    print(f"   Total failures:  {total_failures}")
    print(f"   Total errors:    {total_errors}")
    print(f"   Time elapsed:    {elapsed:.1f}s")

    if total_failures == 0 and total_errors == 0:
        print(f"\n🎉 ALL TESTS PASSED!")
    else:
        print(f"\n⚠️  SOME TESTS FAILED!")
        failed_batches = [r for r in batch_results if not r["ok"]]
        for fb in failed_batches:
            print(f"   ❌ {fb['name']}: {fb['failures']}F/{fb['errors']}E")
            if fb.get("stdout_preview"):
                print(f"      {fb['stdout_preview'][:200]}")

    if args.json:
        import json as _json
        summary = {
            "total_ran": total_ran,
            "total_failures": total_failures,
            "total_errors": total_errors,
            "elapsed_seconds": elapsed,
            "batches": batch_results
        }
        print("\n" + _json.dumps(summary, indent=2))
