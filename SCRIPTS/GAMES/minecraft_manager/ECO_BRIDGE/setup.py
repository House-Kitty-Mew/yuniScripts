#!/usr/bin/env python3
"""
Eco Bridge Server — Setup Wizard
==================================
Run this once before starting the bridge server to configure encryption keys,
database paths, and LAN discovery settings.
"""
import os, sys, json, signal
from pathlib import Path

# Port constant — local define (standalone setup script)
LAN_DISCOVERY_PORT = 25574  # matches engine.ports.LAN_DISCOVERY_PORT

HERE = Path(__file__).parent.resolve()
PROJECT_ROOT = HERE.parent.parent.parent.parent

_has_colors = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
_G = "\033[92m" if _has_colors else ""
_Y = "\033[93m" if _has_colors else ""
_R = "\033[91m" if _has_colors else ""
_C = "\033[96m" if _has_colors else ""
_N = "\033[0m" if _has_colors else ""


def banner():
    print(f"""
{_C}╔═══════════════════════════════════════════════╗
║       Eco Bridge Server — Setup Wizard        ║
║                                               ║
║  Configure encryption, database, and LAN      ║
║  discovery for the economy bridge server.     ║
╚═══════════════════════════════════════════════╝{_N}
""")


def prompt(text, default="", secret=False):
    d = f" [{_G}{default}{_N}]" if default else ""
    try:
        if secret:
            from getpass import getpass
            v = getpass(f"  {text}{d}: ").strip()
        else:
            v = input(f"  {text}{d}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{_Y}Setup cancelled.{_N}")
        sys.exit(1)
    return v if v else default


def main():
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(1))
    banner()

    print(f"{_C}Step 1: Encryption Key{_N}")
    print("  All traffic between the engine and bridge is AES-256-GCM encrypted.")
    print("  Generate a strong key — the engine must use the same key.")
    key = prompt("  Encryption key", secret=True)
    if not key:
        import uuid
        key = str(uuid.uuid4()).replace("-", "")
        print(f"  {_Y}Generated key: {key}{_N}")
    print(f"  {_G}✓ Encryption key set{_N}")

    print(f"\n{_C}Step 2: Port{_N}")
    print("  TCP port for encrypted bridge connections.")
    port = prompt("  Bridge port", "7200")
    try:
        port = int(port)
    except Exception:
        port = 7200
    print(f"  {_G}✓ Port: {port}{_N}")

    print(f"\n{_C}Step 3: Database Path{_N}")
    print("  Path to the Otters Civ economy database (project_ooga.db).")
    print(f"  Leave empty to auto-search on startup.")
    db_path = prompt("  Database path", "")
    if db_path:
        p = Path(db_path)
        if not p.exists():
            print(f"  {_Y}⚠ Warning: {db_path} does not exist yet{_N}")
        else:
            print(f"  {_G}✓ Found: {p.resolve()}{_N}")

    print(f"\n{_C}Step 4: LAN Discovery{_N}")
    print(f"  The bridge will broadcast 'eco_bridge' beacons on UDP {LAN_DISCOVERY_PORT} (engine.ports.LAN_DISCOVERY_PORT)")
    print(f"  so the YuniScripts engine can auto-discover it on the LAN.\n")
    print(f"  No configuration needed — just start the server.")

    # Write eco_config.json alongside the bridge server
    cfg = {
        "eco_bridge_password": key,
        "eco_bridge_port": port,
    }
    if db_path:
        cfg["ooga_db_path"] = str(Path(db_path).resolve())

    cfg_path = HERE / "eco_config.json"
    # Load existing first to preserve other fields
    if cfg_path.exists():
        try:
            import json as _j
            existing = _j.loads(cfg_path.read_text())
            existing.update(cfg)
            cfg = existing
        except Exception:
            pass

    import json
    cfg_path.write_text(json.dumps(cfg, indent=2))
    print(f"  {_G}✓ eco_config.json written{_N}")

    print(f"\n{_C}Step 5: Requirements{_N}")
    for r in ["pycryptodome"]:
        try:
            __import__(r.replace("-", "_"))
            print(f"  {_G}✓ {r} installed{_N}")
        except ImportError:
            print(f"  {_Y}✗ {r} not installed — run: pip install pycryptodome{_N}")
            print(f"     (Pure Python fallback will be used)")

    print(f"\n{_G}═══ Setup Complete ═══════════════════════════════{_N}")
    print(f"  Start the bridge server:")
    print(f"    {_C}python3 eco_bridge_server.py{_N}")
    print(f"  Or use the start script:")
    print(f"    {_C}python3 start.py{_N}")
    print(f"\n  On the YuniScripts engine side, run the first-run-setup")
    print(f"  wizard or set eco_bridge_password in DATA/eco_config.json.")
    print()

if __name__ == "__main__":
    main()
