# Server Stats Collector API

## Overview
Polls a remote server stats daemon over UDP, stores results in local SQLite,
and exposes a UDP command interface for querying data.

The remote daemon (separate script, not included here) requires `psutil` and
`xxhash`. The collector itself uses only Python stdlib.

## Local Callbacks (not engine hooks)
This script defines `on_stats_received_callback` in its `hooks.py`. However,
this is a **local callback** called directly by main.py, not registered with
the engine's hook system. It fires after stats are saved to the database.

## Commands (UDP on `command_port` = 25570)
- `latest`             → JSON object with most recent stats row
- `history <minutes>`  → JSON array of stats from the last N minutes
- `config`             → Show current collector configuration
- `reload-config`      → Hot-reload DATA/config.json without restarting

## Ports
- 25570 (UDP) – command/query interface

## Configuration
All settings stored in `DATA/server_stats_collector_config.json` (centralized).
The legacy path `DATA/config.json` (local to the script directory) is auto-migrated.
