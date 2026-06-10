# YuniScripts Ecosystem

**Last Updated:** 2026-06-08  
**Location:** `/home/deck/Documents/dev-yuniScripts/`  
**Python Files:** 383 across 970 total files  
**Tests:** 24 test files (unittest-based, ~500+ tests)  
**Work Orders:** 180+ created, 332 tracked from auto-audit

## Ecosystem Overview

```
├── engine/           (17 .py)  — Core engine: process_wrapper, process_adoption, config_loader, Phooks
├── SCRIPTS/
│   ├── CLIENTS/      (deepsky_client) — DeepSky Self-Healing AI Client (YuniScript)
│   ├── GAMES/        (minecraft_manager) — Auction House, Economy Bridge, extensions
│   ├── LAUNCHER/     — Game/server launcher scripts
│   ├── PROGRAMS/     — Utility programs
│   ├── SERVICES/     (fastmcp_server) — FastMCP wrapped as YuniScript
│   └── TOOLS/        — Utility tools and scripts
├── tests/            (24 .py)  — Integration, unit, edge case, stress tests
├── minescript/       (5 .py)   — Minecraft helper scripts
└── data_packs/       — Data pack resources

Total: 383 Python files, 970 files across ecosystem
```

## Key Components

### DeepSky Self-Healing AI Client (`SCRIPTS/CLIENTS/deepsky_client/`)
- Full YuniScript with session persistence, self-healing, auto work order generation
- Components: api_client, session_manager, work_order_engine, healing_agent, system_prompt_generator
- 6 test files: ~190+ unit/integration tests
- Connects to DeepSeek API with retry, key rotation, token tracking
- Session persistence: memory buffer → SQLite flush on all triggers

### FastMCP Server YuniScript (`SCRIPTS/SERVICES/fastmcp_server/`)
- Wraps the existing FastMCP server as a YuniScript
- Phooks-based tool call routing between DeepSky clients and FastMCP
- Debug hooks for work order generation
- 5 test modules: 217+ tests (unittest-based)

### Auction House System (`SCRIPTS/GAMES/minecraft_manager/AUCTIONHOUSE/`)
- Full auction house CRUD: ah_core.py (57KB), ah_database.py, ah_plugin_registry.py
- AI simulation engine: ah_ai_engine.py
- Extensions: SIMULATED_PEOPLE, SIMULATED_TRADE, SIMULATED_SOCIAL, SIMULATED_HEALTH_MECHANICS
- Edge case matrix: 44+ identified edge cases (EC1-EC44)

## Agent Safety Configuration

Stored in Documentation.db config table:
- `agent_safety.max_depth` = 2 (max subagent spawn depth)
- `agent_safety.parallel_enabled` = false (serial-only agent execution)
- `agent_safety.confidence_threshold` = 1.0 (100% required for parallel/chain ops)
- `agent_safety.chain_max_length` = 10 (max chain steps with full confidence)
- `agent_safety.subagent_parallel_requires_review` = true (human review for parallel)

## Documentation

- `DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md` — Master spec for self-healing ecosystem
- `DEEPSKY_AGENT_CONSTRAINTS_INTEGRATION_SPEC.md` — Agent constraints spec
- `TEST_SUITE_SPEC.md` — Test suite spec
- `GUIDES/ai_developer_guide.md` — AI-assisted script development guide
- `GUIDES/human_developer_guide.md` — Human-friendly YuniScripts creation guide

## Key Paths

| Service | Path |
|---------|------|
| FastMCP Server (standalone) | `/home/deck/AIHandler/SCRIPTS/FastMCPServer/` |
| FastMCP Server (YuniScript) | `SCRIPTS/SERVICES/fastmcp_server/` |
| DeepSky Client (YuniScript) | `SCRIPTS/CLIENTS/deepsky_client/` |
| Game Clients | `SCRIPTS/CLIENTS/` |
| Game Servers | `SCRIPTS/SERVERS/` |
| Services | `SCRIPTS/SERVICES/` |
| Games | `SCRIPTS/GAMES/` |
| Programs | `SCRIPTS/PROGRAMS/` |
| Documentation DB | `/home/deck/AIHandler/SCRIPTS/DatabaseHandler/DATA/Databases/Documentation.db` |
