#!/usr/bin/env python3
"""
admin_config_gui.py — Standalone admin config GUI server.

A single-file web server that provides a visual interface for viewing
and editing all dynamic configs across the YuniScripts ecosystem.

Usage:
    python3 admin_config_gui.py                    # Port 8180, localhost
    python3 admin_config_gui.py --port 8282        # Custom port
    python3 admin_config_gui.py --host 0.0.0.0     # All interfaces
    
Then open: http://localhost:8180
"""

import sys
import os

# Ensure tools/ is in path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))

from dynamic_config_loader import start_admin_gui, register_configs, get_config_sources

# ── Register built-in configs for the GUI itself ────────────────
register_configs("admin_gui", [
    {"key": "port", "type": "int", "default": 8180,
     "description": "Admin GUI HTTP port", "valid_range": (1024, 65535),
     "category": "general"},
    {"key": "auto_refresh_interval", "type": "int", "default": 5,
     "description": "Dashboard auto-refresh seconds", "valid_range": (1, 60),
     "category": "general"},
    {"key": "theme", "type": "str", "default": "dark",
     "description": "UI theme",
     "valid_options": ["dark", "light"],
     "category": "general"},
])


def main():
    """Start the admin config GUI server."""
    port = 8180
    host = "127.0.0.1"
    
    # Parse CLI args
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif arg == "--host" and i + 1 < len(args):
            host = args[i + 1]
        elif arg == "--help" or arg == "-h":
            print(__doc__)
            return
    
    print(f"Starting Admin Config GUI on http://{host}:{port}")
    print("Press Ctrl+C to stop")
    print()
    
    server = start_admin_gui(port=port, host=host)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
