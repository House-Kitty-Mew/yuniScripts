#!/usr/bin/env python3
"""
Server Stats Daemon — Setup Wizard
====================================
Run this once before starting the daemon to configure auth tokens,
process monitoring, and LAN discovery settings.
"""
import os, sys, json
from pathlib import Path

HERE = Path(__file__).parent.resolve()

# Port constants — local defines (standalone setup script)
LAN_DISCOVERY_PORT = 25574  # matches engine.ports.LAN_DISCOVERY_PORT
SERVER_STATS_PORT = 5559  # matches engine.ports.SERVER_STATS_PORT

_COLORS = True
try:
    _G = "\033[92m"
    _Y = "\033[93m"
    _R = "\033[91m"
    _C = "\033[96m"
    _N = "\033[0m"
except Exception:
    _G = _Y = _R = _C = _N = ""

def banner():
    print(f"""
{_C}╔═══════════════════════════════════════════════╗
║     Server Stats Daemon — Setup Wizard        ║
║                                               ║
║  Configure authentication, process tracking,  ║
║  and LAN discovery for your stats daemon.     ║
╚═══════════════════════════════════════════════╝{_N}
""")

def prompt(text, default=""):
    d = f" [{_G}{default}{_N}]" if default else ""
    try:
        v = input(f"  {text}{d}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{_Y}Setup cancelled.{_N}")
        sys.exit(1)
    return v if v else default

def main():
    banner()

    print(f"{_C}Step 1: Auth Tokens{_N}")
    print("  Each YuniScripts engine instance needs a unique token")
    print("  to authenticate with this daemon.")
    tokens_path = HERE / "tokens.coin"
    existing_tokens = set()
    if tokens_path.exists():
        for line in tokens_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                existing_tokens.add(line)
        print(f"  {_G}Existing tokens: {len(existing_tokens)}{_N}")
        for t in existing_tokens:
            print(f"    - {t}")

    new_token = prompt("  Add new token (or press Enter to skip)", "")
    if new_token and new_token not in existing_tokens:
        existing_tokens.add(new_token)

    # Rewrite tokens.coin
    lines = ["# Add one token per line, no whitespace\n"]
    for t in sorted(existing_tokens):
        lines.append(f"{t}\n")
    tokens_path.write_text("".join(lines))
    print(f"  {_G}✓ tokens.coin — {len(existing_tokens)} token(s){_N}")

    print(f"\n{_C}Step 2: Port Configuration{_N}")
    port = prompt("  Stats listening port", str(SERVER_STATS_PORT))
    try:
        port = int(port)
    except Exception:
        port = SERVER_STATS_PORT
    print(f"  {_G}✓ Port: {port}{_N}")

    print(f"\n{_C}Step 3: Process Monitoring{_N}")
    print("  List processes to track (one per line, empty line to finish).")
    print("  Format: process_name.exe|Display Name")
    progs_path = HERE / "progs.p"
    if progs_path.exists():
        existing_progs = [l.strip() for l in progs_path.read_text().splitlines()
                          if l.strip() and not l.strip().startswith("#") and "|" in l.strip()]
        if existing_progs:
            print(f"  {_G}Currently tracked: {len(existing_progs)}{_N}")
            for p in existing_progs:
                print(f"    - {p}")

    while True:
        entry = prompt("  Add process (format: name.exe|Alias)", "")
        if not entry:
            break
        if "|" not in entry:
            print(f"  {_R}Format: process_name.exe|Display Name{_N}")
            continue
        existing_progs.append(entry)

    lines = ["# Add process_name|alias per line\n"]
    for p in existing_progs:
        lines.append(f"{p}\n")
    progs_path.write_text("".join(lines))
    print(f"  {_G}✓ progs.p — {len(existing_progs)} process(es){_N}")

    # Write config.json for deploy info
    print(f"\n{_C}Step 4: Security Mode{_N}")
    print(f"  {_Y}LAN mode (default):{_N} No tokens required for LAN clients.")
    print(f"    Only use when the daemon is on your trusted LAN.")
    print(f"  {_Y}Secure mode:{_N} Requires auth tokens from tokens.coin.")
    print(f"    Use when the daemon needs to be accessible from outside the LAN.\n")
    use_secure = prompt("  Enable Secure mode? (requires tokens)", "n").lower().startswith("y")
    if use_secure:
        secure_flag = "--secure"
        print(f"  {_G}✓ Secure mode enabled (token auth required){_N}")
    else:
        secure_flag = "--lan-mode"
        print(f"  {_G}✓ LAN mode enabled (no auth required on LAN){_N}")

    print(f"\n{_C}Step 5: LAN Discovery{_N}")
    print(f"  The daemon will broadcast 'server_stats' beacons on UDP 25574 (engine.ports.LAN_DISCOVERY_PORT)")
    print(f"  for automatic discovery by the YuniScripts engine.\n")
    cfg = {"port": port, "lan_discovery_port": LAN_DISCOVERY_PORT, "secure": use_secure}
    cfg_path = HERE / "config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))
    print(f"  {_G}✓ config.json written{_N}")

    print(f"\n{_C}Step 5: Requirements{_N}")
    reqs = ["psutil", "xxhash"]
    for r in reqs:
        try:
            __import__(r.replace("-", "_"))
            print(f"  {_G}✓ {r} installed{_N}")
        except ImportError:
            print(f"  {_Y}✗ {r} not installed — run: pip install {r}{_N}")

    print(f"\n{_G}═══ Setup Complete ═══════════════════════════════{_N}")
    mode_arg = "--secure" if use_secure else "--lan-mode"
    print(f"  Mode: {_C}{'SECURE' if use_secure else 'LAN'}{_N}")
    print(f"  Start the daemon:")
    print(f"    {_C}python3 server_stats_daemon.py {mode_arg}{_N}")
    print(f"  Or use the start script:")
    print(f"    {_C}python3 start.py{_N}")
    print()

if __name__ == "__main__":
    main()
