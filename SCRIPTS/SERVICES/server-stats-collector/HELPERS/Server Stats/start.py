#!/usr/bin/env python3
"""
Server Stats Daemon — Start Script
====================================
Run this to launch the daemon.  On first run, it guides you through setup.
The daemon broadcasts 'server_stats' beacons on UDP {LAN_DISCOVERY_PORT} (engine.ports.LAN_DISCOVERY_PORT) for LAN discovery.

Usage:
    python3 start.py              # Start daemon (setup wizard on first run)
    python3 start.py --setup      # Force re-run setup wizard
    python3 start.py --help       # Show this help
"""
import sys, os, subprocess, json, time
from pathlib import Path

HERE = Path(__file__).parent.resolve()

# Port constants — local defines (standalone start script)
LAN_DISCOVERY_PORT = 25574  # matches engine.ports.LAN_DISCOVERY_PORT
SERVER_STATS_PORT = 5559  # matches engine.ports.SERVER_STATS_PORT
DAEMON = HERE / "server_stats_daemon.py"
SETUP = HERE / "setup.py"
CONFIG = HERE / "config.json"
TOKENS = HERE / "tokens.coin"

_COL = "\033[96m" if sys.stdout.isatty() else ""
_NC = "\033[0m" if sys.stdout.isatty() else ""

def is_fresh():
    """Check if this is a first run."""
    return not TOKENS.exists() or not CONFIG.exists()

def check_deps():
    """Check that required packages are available."""
    missing = []
    for pkg in ["psutil"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing

def main():
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
        print(f"{_COL}Configuration found — starting daemon.{_NC}")

    # Check dependencies
    missing = check_deps()
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        print("Install with: pip install " + " ".join(missing))
        sys.exit(1)

    # Read config for mode flag
    mode_flag = "--lan-mode"
    try:
        if CONFIG.exists():
            cfg_data = json.loads(CONFIG.read_text())
            if cfg_data.get("secure", False):
                mode_flag = "--secure"
    except Exception:
        pass

    print(f"\n{_COL}═══ Starting Server Stats Daemon ═══{_NC}")
    mode_label = "SECURE" if mode_flag == "--secure" else "LAN"
    print(f"  Mode:     {mode_label}")
    print(f"  Port:     {CONFIG.read_text().split() if CONFIG.exists() else SERVER_STATS_PORT}")
    print(f"  LAN beacon: UDP {LAN_DISCOVERY_PORT} ('server_stats')")
    print(f"\n  {_COL}Ctrl+C to stop{_NC}\n")

    try:
        subprocess.run([sys.executable, str(DAEMON), mode_flag])
    except KeyboardInterrupt:
        print("\nDaemon stopped.")
    except Exception as e:
        print(f"Error starting daemon: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
