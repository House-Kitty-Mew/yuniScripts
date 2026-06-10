# YuniScripts Ecosystem

**Version:** 1.0.0  
**License:** MIT  
**Python:** 3.10+  
**Platform:** Linux, macOS, Windows

YuniScripts is a modular **script management engine** that discovers, launches, monitors, and orchestrates independently-developed scripts (microservices) into a cohesive ecosystem. Each script is an autonomous Python application with its own lifecycle, hooks system, virtual environment, and inter-process communication via the **Phooks** event hub.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Quick Start](#quick-start)
- [Ecosystem Components](#ecosystem-components)
- [Core Engine](#core-engine)
- [Script Architecture](#script-architecture)
- [Phooks Event System](#phooks-event-system)
- [Configuration System](#configuration-system)
- [Developer Guide](#developer-guide)
- [Testing](#testing)
- [Deployment](#deployment)
- [Contributing](#contributing)
- [License](#license)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                      YUNISCRIPTS ENGINE                          │
│  start.py ──► main.py                                           │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Engine Core (engine/)                                    │    │
│  │  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │    │
│  │  │ Process   │ │ Config   │ │ Watcher  │ │ LAN        │ │    │
│  │  │ Wrapper   │ │ Loader   │ │ (hot     │ │ Discovery  │ │    │
│  │  │ (spawn/   │ │ (central │ │  reload) │ │ (UDP       │ │    │
│  │  │  monitor) │ │  config) │ │          │ │  beacon)   │ │    │
│  │  └───────────┘ └──────────┘ └──────────┘ └────────────┘ │    │
│  │  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │    │
│  │  │ Phooks    │ │ Process  │ │ UDP      │ │ Metadaa    │ │    │
│  │  │ (event    │ │ Adoption │ │ Admin    │ │ System     │ │    │
│  │  │  hub)     │ │ (orphan  │ │ Server   │ │ (script    │ │    │
│  │  │           │ │  adopt)  │ │          │ │  info)     │ │    │
│  │  └───────────┘ └──────────┘ └──────────┘ └────────────┘ │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  MANAGED SCRIPTS (autonomous processes)                          │
│  ┌────────────────────────────────────────────────────────┐      │
│  │ SCRIPTS/                                                │      │
│  │  CLIENTS/   ──► deepsky_client (AI self-healing agent)  │      │
│  │  GAMES/     ──► minecraft_manager (Auction House,       │      │
│  │                  Economy Bridge, Lootpower)             │      │
│  │  SERVICES/  ──► fastmcp_server, phooks-hub,             │      │
│  │                  item-signing-bridge, mc-status-relay,  │      │
│  │                  multi-server-manager, script-packager, │      │
│  │                  server-stats-collector,                │      │
│  │                  work-order-auto-processor,             │      │
│  │                  datagram-engine                        │      │
│  │  TOOLS/     ──► admin tools, dashboards, monitors      │      │
│  │  LAUNCHER/  ──► config-updater                         │      │
│  │  PROGRAMS/  ──► steam-nextcloud-status-updater          │      │
│  │  SERVERS/   ──► mc-server-runner, example-game-server  │      │
│  └────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Autonomy** — Each script runs as an independent process with its own lifecycle
2. **Event-Driven** — Scripts communicate through the Phooks event hub, not direct coupling
3. **Self-Healing** — The DeepSky client monitors and auto-recovers from failures
4. **Hot-Reloadable** — File watchers detect changes and restart scripts automatically
5. **Configurable** — Centralized configuration with per-script overrides
6. **Safe** — Multiple layers of protection (process limits, orphan adoption, cgroup guards)

---

## Quick Start

### Prerequisites

- Python 3.10 or later
- pip (Python package installer)

### Installation

```bash
# Clone or extract the repository
cd yuniScripts

# Start the engine (auto-checks dependencies, runs first-time wizard)
python3 start.py

# Or run directly (if dependencies are already installed)
python3 main.py
```

### First-Run Setup

On first launch, `start.py` detects a fresh installation and runs the interactive configuration wizard (`SCRIPTS/TOOLS/first-run-setup/`). This guides you through:

- Engine configuration (ports, logging, debug settings)
- RCON and Minecraft server integration
- AI economy and Auction House settings

### Verifying the Installation

```bash
# Check all dependencies are installed
python3 start.py --check-only

# View engine status (via UDP admin)
echo "status" | nc -u localhost 23146
```

---

## Ecosystem Components

### Core Engine (`engine/`)

| Module | Purpose |
|--------|---------|
| `manager.py` | Script discovery, port conflict checking, override application |
| `process_wrapper.py` | Process spawning, lifecycle management, graceful shutdown |
| `process_adoption.py` | Adopts orphan processes when a script crashes |
| `config_loader.py` | Centralized JSON config loading with schema validation |
| `watcher.py` | File system watcher for hot-reloading scripts on code changes |
| `phooks.py` | Event hub — Publish/Subscribe system for inter-script communication |
| `hooks.py` | Hook registry — lifecycle hooks (pre_start, post_stop, etc.) |
| `venv_manager.py` | Per-script virtual environment creation and dependency management |
| `logging.py` | Structured logging with rotation |
| `ports.py` | Port allocation and conflict detection |
| `udp_admin.py` | UDP command server for runtime administration |
| `metadata.py` | Script metadata (version, author, dependencies) |
| `lan_discovery.py` | LAN-based service discovery via UDP beacons |

### Managed Scripts (`SCRIPTS/`)

Scripts are organized by category:

#### CLIENTS

- **deepsky_client** — Self-healing AI agent that connects to DeepSeek API, generates work orders, manages sessions, and heals failures autonomously

#### GAMES

- **minecraft_manager** — Full Minecraft server integration with:
  - **Auction House** (57KB core) — Complete marketplace with listing, bidding, buy-now, AI economy simulation, 200+ personas
  - **Economy Bridge** — Inter-server economy synchronization with encryption
  - **Extensions** — Simulated people, chat, trade, social, relationships, health mechanics, market announcements

#### SERVICES

| Service | Description |
|---------|-------------|
| **fastmcp_server** | FastMCP server wrapper with tool registry, God Watcher protection, THREAD cognitive memory system |
| **phooks-hub** | Central Phooks event hub for inter-service communication |
| **item-signing-bridge** | UDP bridge for Minecraft item signing (AES-256-GCM encrypted) |
| **mc-status-relay** | Relays Minecraft server status (biome, dimension, player count) |
| **multi-server-manager** | Manages multiple Minecraft server instances (Fabric, Forge, Vanilla, Paper) |
| **script-packager** | Packages YuniScripts into distributable archives |
| **server-stats-collector** | Collects and aggregates server performance metrics |
| **work-order-auto-processor** | Automates work order processing and queuing |
| **datagram-engine** | Datagram-based messaging system |

#### TOOLS

- **admin_notifier** — System notification dispatcher
- **deepsky_admin_monitor** — DeepSky ecosystem monitoring dashboard
- **gui-dashboard** — GUI dashboard for engine management
- **log-viewer** — Log browsing and filtering utility
- **server-stats-display** — Real-time server statistics display
- **first-run-setup** — Interactive configuration wizard

#### SERVERS

- **mc-server-runner** — Full Minecraft server runner with auto-download, multiple modloader support (Fabric, Forge, Vanilla, Paper)
- **example-game-server** — Template for creating new game server scripts

---

## Core Engine

### Starting the Engine

```bash
python3 start.py              # Full startup with dependency check
python3 start.py --check-only  # Only check dependencies
python3 start.py --install-only # Only install missing dependencies
python3 start.py --reset-venvs # Wipe all venvs and recreate
```

### Script Discovery

The engine discovers managed scripts by scanning `SCRIPTS/` for directories containing a `main.py` entry point and a `meta.info` metadata file. Each script must declare:

```json
{
  "id": "script-id",
  "name": "Human-Readable Name",
  "version": "1.0.0",
  "author": "Author Name",
  "description": "What this script does",
  "dependencies": ["dependency1"],
  "config_schema": { ... }
}
```

### UDP Admin Server

The engine runs a UDP command server on port `23146` for runtime administration:

```bash
# Check script status
echo "status" | nc -u localhost 23146

# Start/stop/reload scripts
echo "start script-id" | nc -u localhost 23146
echo "stop script-id"  | nc -u localhost 23146
echo "reload script-id" | nc -u localhost 23146
echo "reload-all"       | nc -u localhost 23146
echo "help"             | nc -u localhost 23146
```

### Process Lifecycle

```
discover ─► validate ─► prepare_env ─► spawn ─► monitor ─► shutdown
    │                                                    │
    └── hot-reload (on file change) ─────────────────────┘
```

Each script runs as a subprocess with:
- Configurable shutdown timeout (graceful → force kill)
- Automatic restart on crash (configurable)
- Process adoption on engine restart
- Resource limits via cgroup v2
- Memory and CPU monitoring

---

## Script Architecture

### Anatomy of a Managed Script

Every managed script must follow this structure:

```
my-script/
├── main.py             # Entry point (mandatory)
├── meta.info           # Script metadata (mandatory)
├── hooks.py            # Phooks hook registration (optional)
├── Phooks.py           # Event handlers (optional)
├── requirements.txt    # Python dependencies (optional)
├── API.md              # API documentation (optional)
├── tests/              # Test suite (optional)
├── DATA/               # Runtime data (auto-created)
├── logs/               # Log output (auto-created)
└── engine/             # Internal engine (optional, for complex scripts)
```

### main.py — Entry Point Requirements

```python
#!/usr/bin/env python3
"""My Script — brief description."""

import sys
import json

def main():
    """Script entry point. The engine calls this after spawning."""
    # Read stdin for commands (JSON protocol)
    for line in sys.stdin:
        command = json.loads(line.strip())
        # Handle commands...

if __name__ == "__main__":
    main()
```

### Communication Channels

Scripts can communicate via:

1. **Stdin/stdout JSON protocol** — Engine sends commands via stdin, scripts respond via stdout
2. **Phooks events** — Publish/subscribe event system for inter-script communication
3. **UDP** — Direct UDP messaging for low-latency operations
4. **TCP/HTTP** — REST APIs and web services
5. **File system** — Shared data through `DATA/` directories

---

## Phooks Event System

Phooks is the publish/subscribe event hub that enables decoupled communication between scripts.

### Architecture

```
┌──────────┐   ┌──────────┐   ┌──────────┐
│ Script A │   │ Script B │   │ Script C │
│ Publisher│   │ Publisher│   │Publisher │
└────┬─────┘   └────┬─────┘   └────┬─────┘
     │              │              │
     ▼              ▼              ▼
┌──────────────────────────────────────────┐
│              PHOOKS HUB                   │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │ Channel │  │ Channel │  │ Channel │  │
│  │ events  │  │ commands│  │  data   │  │
│  └─────────┘  └─────────┘  └─────────┘  │
└──────────────────────────────────────────┘
     │              │              │
     ▼              ▼              ▼
┌──────────┐   ┌──────────┐   ┌──────────┐
│ Script X │   │ Script Y │   │ Script Z │
│Subscriber│   │Subscriber│   │Subscriber│
└──────────┘   └──────────┘   └──────────┘
```

### Registering Event Handlers

In your script's `hooks.py`:

```python
def register_hooks(hook_registry):
    """Register event handlers for this script."""
    hook_registry.on("sign_request", handle_sign_request)
    hook_registry.on("ah_list", handle_auction_list)
    return hook_registry

def handle_sign_request(payload):
    """Handle an item signing request."""
    request_id = payload["request_id"]
    data = payload["data"]
    # Process the request...
    return {"status": "ok", "message": "Item signed"}
```

### Publishing Events

```python
from engine.phooks import publish

# Publish an event to the hub
publish("sign_response", {
    "request_id": "abc-123",
    "result": {"status": "ok", "signed_item": "Diamond Sword of Power"}
})
```

### Built-in Event Channels

| Channel | Direction | Description |
|---------|-----------|-------------|
| `sign_request` | Bridge → Manager | Item signing request (encrypted UDP) |
| `sign_response` | Manager → Bridge | Signing result |
| `ah_list` | Client → Manager | Auction House listing |
| `ah_bid` | Client → Manager | Bid placement |
| `ah_buy` | Client → Manager | Buy-It-Now purchase |
| `ah_query` | Client → Manager | Market query |
| `on_simulation_cycle_start` | Manager → Extensions | Economy simulation tick |

---

## Configuration System

### Configuration Files

All configuration is stored in the centralized `DATA/` directory:

| File | Description |
|------|-------------|
| `engine_config.json` | Engine settings (ports, logging, debug) |
| `minecraft_manager_config.json` | Minecraft server integration |
| `sign_item_config.json` | Item signing bridge settings |
| `eco_config.json` | Economy Bridge configuration |
| `simulated_people_config.json` | AI persona simulation parameters |
| `ah_config.json` | Auction House settings |
| `item_signing_bridge_config.json` | Signing bridge network config |
| `server_stats_collector_config.json` | Stats collection intervals |

### Configuration Schema

```json
{
  "engine": {
    "udp_admin_port": 23146,
    "phooks_hub_port": 23147,
    "lan_discovery_port": 23148,
    "debug_base_port": 25000
  },
  "logging": {
    "level": "INFO",
    "rotation": "1 day",
    "retention": "30 days"
  },
  "scripts": {
    "enabled": ["deepsky_client", "minecraft_manager", "phooks-hub"],
    "disabled": ["work-order-auto-processor"]
  }
}
```

### Config Loading Priority

1. Default values (hardcoded in engine)
2. `DATA/<service>_config.json` (centralized)
3. `<script_dir>/DATA/config.json` (script-local overrides)
4. Environment variables (`YUNISCRIPTS_*`)

---

## Developer Guide

### Creating a New Script

Follow these steps to create a new managed script:

#### Step 1: Create the Directory Structure

```bash
mkdir -p SCRIPTS/SERVICES/my-service/{tests,DATA,logs}
```

#### Step 2: Create `meta.info`

```json
{
  "id": "my-service",
  "name": "My Awesome Service",
  "version": "1.0.0",
  "author": "Your Name",
  "description": "Brief description of what this service does",
  "server_type": "normal",
  "debug": false,
  "tags": ["service", "minecraft"]
}
```

**`server_type` options:**
- `normal` — Standard script lifecycle
- `restart` — Auto-restarts on crash
- `oneshot` — Runs once and exits
- `persistent` — Always kept running

#### Step 3: Create `main.py`

```python
#!/usr/bin/env python3
"""My Service — Does amazing things."""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def handle_command(cmd: dict) -> dict:
    """Process a command and return a response."""
    action = cmd.get("action", "")
    if action == "ping":
        return {"status": "pong", "version": "1.0.0"}
    elif action == "process":
        data = cmd.get("data", {})
        result = do_something(data)
        return {"status": "ok", "result": result}
    else:
        return {"status": "error", "message": f"Unknown action: {action}"}

def main():
    """Main loop — reads commands from stdin."""
    for line in sys.stdin:
        try:
            cmd = json.loads(line.strip())
            response = handle_command(cmd)
            print(json.dumps(response), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "message": str(e)}), flush=True)
        except Exception as e:
            print(json.dumps({"status": "error", "message": f"Internal: {e}"}), flush=True)

if __name__ == "__main__":
    main()
```

#### Step 4: Add Dependencies

Create `requirements.txt`:

```
requests>=2.28.0
pycryptodome>=3.15.0
```

The engine automatically creates a virtual environment and installs these dependencies.

#### Step 5: Register Event Hooks (Optional)

Create `hooks.py`:

```python
def register_hooks(hook_registry):
    """Register event handlers."""
    hook_registry.on("my_custom_event", handle_event)
    return hook_registry

def handle_event(payload):
    """Custom event handler."""
    print(f"Received event: {payload}")
    return {"processed": True}
```

#### Step 6: Write Tests

Create `tests/test_my_service.py`:

```python
import unittest
import json
from io import StringIO

class TestMyService(unittest.TestCase):
    def setUp(self):
        # Import your module
        pass
    
    def test_ping(self):
        """Test the ping command."""
        # Test logic here
        pass
    
    def test_process(self):
        """Test the process command."""
        # Test logic here
        pass

if __name__ == "__main__":
    unittest.main()
```

#### Step 7: Enable Your Script

Add your script to `scripts.json` or the engine config:

```json
{
  "overrides": {
    "my-service": {"enabled": true}
  }
}
```

### Script Best Practices

1. **Idempotency** — Design operations to be safe to repeat
2. **Graceful Shutdown** — Handle SIGTERM and clean up resources
3. **Structured Logging** — Use JSON-formatted logs for machine parsing
4. **Error Recovery** — Catch exceptions and report via stdout JSON
5. **Resource Limits** — Be mindful of memory and CPU usage
6. **No Blocking** — Use async or threading for long operations
7. **Config Validation** — Validate config on startup, fail fast with clear messages

### Lifecycle Hooks

Scripts can register for lifecyle events:

| Hook | When | Purpose |
|------|------|---------|
| `pre_start` | Before script starts | Validate config, check prerequisites |
| `post_start` | After script starts | Notify other services |
| `pre_stop` | Before shutdown | Save state, flush buffers |
| `post_stop` | After shutdown | Clean up resources |
| `on_crash` | On unexpected exit | Log diagnostics, trigger recovery |
| `on_reload` | On hot-reload | Re-import config, reconnect |

### Environment Variables

The engine sets the following environment variables for managed scripts:

| Variable | Description |
|----------|-------------|
| `YUNISCRIPTS_ROOT` | Absolute path to the yuniScripts root |
| `YUNISCRIPTS_SCRIPT_ID` | The script's unique ID |
| `YUNISCRIPTS_SCRIPT_DIR` | The script's directory |
| `YUNISCRIPTS_DATA_DIR` | The script's DATA directory |
| `YUNISCRIPTS_LOG_DIR` | The script's logs directory |

---

## Testing

The project uses Python's built-in `unittest` framework for all tests.

### Running Tests

```bash
# Run all tests
python3 -m unittest discover -s tests -p "test_*.py"

# Run tests for a specific component
python3 -m unittest tests.test_process_wrapper

# Run with verbose output
python3 -m unittest -v tests.test_config_loading

# Run tests with coverage (if coverage is installed)
python3 -m coverage run -m unittest discover -s tests
python3 -m coverage report
```

### Test Structure

```
tests/
├── test_*.py                    # Core engine tests (~500+ tests)
├── run_all_tests.py             # Test runner script
├── test_data_rel/               # Relationship test data
├── test_data_soc/               # Social test data
└── per-component/               # Component-specific tests
    ├── deepsky_client/tests/
    ├── minecraft_manager/tests/
    │   ├── tests/test_ah_core.py
    │   ├── tests/test_ah_database.py
    │   └── tests/test_eco_bridge.py
    └── fastmcp_server/tests/
        └── test_fastmcp_adapter.py
```

### Adding Tests

```python
import unittest

class TestMyFeature(unittest.TestCase):
    """Test suite for MyFeature."""
    
    @classmethod
    def setUpClass(cls):
        """One-time setup before all tests."""
        pass
    
    def setUp(self):
        """Setup before each test."""
        pass
    
    def test_normal_operation(self):
        """Test normal operation path."""
        result = my_function("input")
        self.assertEqual(result, "expected_output")
    
    def test_edge_case_empty(self):
        """Test with empty input."""
        result = my_function("")
        self.assertEqual(result, "error_empty_input")
    
    def test_edge_case_invalid(self):
        """Test with invalid input."""
        with self.assertRaises(ValueError):
            my_function(None)

if __name__ == "__main__":
    unittest.main()
```

---

## Deployment

### Production Setup

For production deployment, additional considerations:

1. **Systemd Service** — Run the engine as a systemd service for auto-start on boot
2. **Log Rotation** — Configure logrotate for engine logs
3. **Monitoring** — Set up the server-stats-collector and dashboard
4. **Backups** — Regularly back up `DATA/` directory and any script databases
5. **Security** — Use firewalls to restrict UDP admin port access

### Systemd Service Example

```ini
[Unit]
Description=YuniScripts Engine
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/yuniScripts
ExecStart=/usr/bin/python3 /path/to/yuniScripts/main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Upgrading

```bash
# Backup existing configuration
cp -a DATA DATA.backup

# Replace with new version
# (copy over new files, preserving your DATA/)

# Re-run dependency check
python3 start.py --check-only

# Restart the engine
python3 start.py
```

---

## Contributing

### Development Workflow

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Make your changes
4. Add or update tests
5. Run the test suite (`python3 -m unittest discover`)
6. Commit with a descriptive message
7. Push and create a Pull Request

### Code Style

- Follow PEP 8 for Python code
- Use type hints for function signatures
- Document public APIs with docstrings
- Keep functions focused (one thing, well)

### Commit Convention

```
<type>: <description>

Types: feat, fix, docs, style, refactor, test, chore

Examples:
feat: add item signing bridge AES-GCM encryption
fix: correct auction house bid validation overflow
docs: update API.md with new event channels
test: add edge case tests for process wrapper timeout
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## Project Statistics

| Metric | Count |
|--------|-------|
| Python Files | ~940 |
| Total Files | ~1,100 |
| Test Files | 35+ |
| Tests | 500+ |
| Core Engine Modules | 22 |
| Managed Scripts | 18 |
| Services | 11 |
| Auction House Lines | 57KB core |

---

*YuniScripts — Modular script orchestration for autonomous ecosystems.*
