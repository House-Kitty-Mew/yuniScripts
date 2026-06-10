#!/usr/bin/env python3
"""
Standalone Finder — Listen for ALL LAN beacons and report discovered services.

Scans UDP port LAN_DISCOVERY_PORT (engine.ports) for ServiceBeacon broadcasts and displays all services
running on the LAN.  Press Ctrl+C to stop.

Usage:
    python3 standalone-finder/main.py          # Discover for 30 seconds
    python3 standalone-finder/main.py --live   # Continuous mode, press Enter to stop
    python3 standalone-finder/main.py --json   # JSON output for scripting
"""
import sys, os, time, json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Discover YuniScripts services on the LAN")
    parser.add_argument("--live", action="store_true", help="Continuous mode")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    from engine.lan_discovery import DISCOVERY_PORT

    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        sock.bind(("", DISCOVERY_PORT))
    except Exception as e:
        print(f"[finder] Could not bind to discovery port: {e}", flush=True)
        return 1

    _SERVICES = {}  # service_type -> {"host": str, "port": int, "extra": dict, "last_seen": float}
    _start_time = time.time()

    def _print_services():
        if args.json:
            print(json.dumps(list(_SERVICES.values()), indent=2))
            return
        if not _SERVICES:
            print("  No services discovered yet.")
            return
        print(f"\n--- Discovered Services ({len(_SERVICES)} total) ---")
        for svc_type in sorted(_SERVICES):
            info = _SERVICES[svc_type]
            host = info["host"]
            port = info["port"]
            extra = info.get("extra", {})
            lan_mode = extra.get("lan_mode", True)
            version = extra.get("version", "?")
            mode_label = "LAN" if lan_mode else "SECURE"
            age_sec = int(time.time() - info["last_seen"])
            print(f"  [{'OK' if age_sec < 15 else '??'}]  {svc_type:20s} at {host:15s}:{port:<5d}  "
                  f"({mode_label}, v{version}, {age_sec}s ago)")

    print(f"\n{'='*60}")
    print(f"  YuniScripts LAN Service Finder")
    print(f"  Listening on UDP port {DISCOVERY_PORT}...")
    print(f"{'='*60}\n")
    print(f"  Service                  Address              Mode")
    print(f"  {'─'*56}")

    try:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
                beacon = json.loads(data.decode("utf-8"))
                if beacon.get("type") != "lan_discovery":
                    continue
                svc_type = beacon.get("service", "unknown")
                host = beacon.get("host", addr[0])
                port = beacon.get("port", 0)
                extra = beacon.get("extra", {})
                is_new = svc_type not in _SERVICES
                _SERVICES[svc_type] = {
                    "host": host,
                    "port": port,
                    "extra": extra,
                    "last_seen": time.time(),
                    "service_type": svc_type,
                }
                if is_new:
                    lan_mode = extra.get("lan_mode", True)
                    mode_label = "LAN" if lan_mode else "SECURE"
                    line = (f"  [NEW] {svc_type:20s} at {host:15s}:{port:<5d}  {mode_label}")
                    if not args.json:
                        print(line, flush=True)
            except socket.timeout:
                pass

            elapsed = time.time() - _start_time
            if not args.live and elapsed > 30:
                print(f"\nDiscovery timeout (30s). Final results:")
                break

            # In live mode, check for Enter key
            if args.live:
                import select
                if select.select([sys.stdin], [], [], 0)[0]:
                    input()  # consume enter
                    break

    except KeyboardInterrupt:
        pass
    finally:
        sock.close()

    _print_services()

    return 0


if __name__ == "__main__":
    import socket
    sys.exit(main())
