#!/usr/bin/env python3
"""
Eco Bridge Server — Start Script
==================================
Run this to launch the economy bridge server.  On first run, it guides you
through setting up encryption keys, database path, and LAN discovery.

The bridge broadcasts 'eco_bridge' beacons on UDP {LAN_DISCOVERY_PORT} (see engine.ports.LAN_DISCOVERY_PORT) so the YuniScripts
engine can auto-discover it on the LAN.

Usage:
    python3 start.py              # Start bridge (setup wizard on first run)
    python3 start.py --setup      # Force re-run setup wizard
    python3 start.py --key <key>  # Override encryption key
    python3 start.py --db <path>  # Override database path
    python3 start.py --help       # Show this help
"""
import sys, os, subprocess, json, time
from pathlib import Path

# Port constant — local define (standalone script)
LAN_DISCOVERY_PORT = 25574  # matches engine.ports.LAN_DISCOVERY_PORT

HERE = Path(__file__).parent.resolve()
SERVER = HERE / "eco_bridge_server.py"
SETUP = HERE / "setup.py"
CONFIG = HERE / "eco_config.json"

_COL = "\033[96m" if sys.stdout.isatty() else ""
_NC = "\033[0m" if sys.stdout.isatty() else ""

def is_fresh():
    """Check if this is a first run (no encryption key configured)."""
    if not CONFIG.exists():
        return True
    try:
        cfg = json.loads(CONFIG.read_text())
        pwd = cfg.get("eco_bridge_password", "")
        return not pwd
    except Exception:
        return True

def main():
    args = [a for a in sys.argv[1:] if a not in ("--setup", "--help", "-h")]
    force_setup = "--setup" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    if is_fresh() or force_setup:
        print(f"{_COL}First-run setup detected. Starting configuration wizard...{_NC}\n")
        if SETUP.exists():
            ret = subprocess.run([sys.executable, str(SETUP)])
            if ret.returncode != 0:
                print("Setup wizard failed. Please run it manually:")
                print(f"  python3 {SETUP}")
                sys.exit(1)
        else:
            print(f"Warning: setup wizard not found at {SETUP}")
            print("Make sure all files in this folder are present.")
    else:
        print(f"{_COL}Configuration found — starting bridge server.{_NC}")

    print(f"\n{_COL}═══ Starting Eco Bridge Server ═══{_NC}")
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text())
            print(f"  Port:      {cfg.get('eco_bridge_port', 7200)}")
            print(f"  DB:        {cfg.get('ooga_db_path', '(auto-search)')}")
        except Exception:
            pass
    print(f"  LAN beacon: UDP {LAN_DISCOVERY_PORT} ('eco_bridge')")
    print(f"\n  {_COL}Ctrl+C to stop{_NC}\n")

    try:
        cmd = [sys.executable, str(SERVER)]
        # Forward --key and --db if provided
        for i, a in enumerate(sys.argv[1:]):
            if a in ("--key", "--db") and i + 1 < len(sys.argv[1:]):
                cmd.extend([a, sys.argv[1:][i+1]])
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nBridge server stopped.")
    except Exception as e:
        print(f"Error starting bridge: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
