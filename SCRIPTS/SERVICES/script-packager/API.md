# Script Packager — API Documentation

## Overview

The Script Packager creates portable **Script Datagrams** — complete snapshots of a yuniScripts script's entire state. A Script Datagram captures:

- All script source files (main.py, Phooks.py, hooks.py, API.md, etc.)
- Script metadata (meta.info/meta.json)
- Runtime configuration files
- Database files (SQLite databases used by the script)
- Per-script compile/decompile instructions
- Integrity hashing for verification

A Script Datagram can be moved to another yuniScripts engine and **perfectly reproduced** — the destination engine unpacks it into a fully functional script that runs exactly as it did at the source.

## Quick Start

### From GUI Dashboard
1. Open the **Script Packager** tab in the GUI Dashboard
2. Select a script from the dropdown
3. Click **📸 Create Snapshot** to package it
4. The datagram path is shown — copy it to another engine
5. On the destination engine, click **📦 Load Datagram** and provide the path

### Via Phooks Events

```python
from engine.phooks_client import PhooksClient

client = PhooksClient("my-admin-tool",
    listen_events=["packager.response.*"],
    emit_events=["packager.snapshot.create", "packager.deploy.execute"])
client.register()

# Create a snapshot of the FastMCP server script
client.emit("packager.snapshot.create", {
    "script_id": "SERVICES/fastmcp_server",
    "output_path": "/home/deck/yuni_datagrams/fastmcp_snapshot",
    "name": "FastMCP v1.0 Snapshot",
    "author": "Admin",
    "include_databases": True,
    "compress": True,
})

# Later, on a different engine:
client.emit("packager.deploy.execute", {
    "datagram_path": "/home/deck/yuni_datagrams/fastmcp_snapshot",
    "target_script_id": "SERVICES/fastmcp_server",
    "auto_start": True,
})
```

## Script Datagram Structure

```
<output_path>/
├── Meta/
│   ├── Base.ini                  # Standard datagram metadata
│   └── ScriptMeta.ini            # Script-specific metadata
├── Script/
│   ├── main.py                   # All script source files
│   ├── Phooks.py
│   ├── hooks.py
│   ├── API.md
│   ├── meta.info
│   ├── requirements.txt
│   ├── compile_instructions.json  # How this script was packaged
│   └── decompile_instructions.json # How to unpack on target engine
├── Databases/                    # Script's SQLite databases
│   └── Default/
│       └── Data/
├── Configs/                      # Runtime config snapshots
├── Functions/                    # Embedded self-extraction functions
├── LargeAssets/
├── PreLoad/
└── Manifest.json                 # Complete file listing with SHA256 hashes
```

## Per-Script Compile/Decompile Configs

Each script can have its own compile and decompile configuration stored in:
`DATA/script_packager_configs/<script_id>.compile.json`
`DATA/script_packager_configs/<script_id>.decompile.json`

### Compile Config

```json
{
  "script_id": "SERVICES/fastmcp_server",
  "version": "1.0.0",
  "include_patterns": ["*.py", "*.json", "*.md", "*.info", "*.txt"],
  "exclude_patterns": ["__pycache__", "*.pyc", ".git", "venv", "tests"],
  "include_databases": true,
  "include_venv": false,
  "include_configs": true,
  "config_sources": [],
  "packaging": {
    "compress": false,
    "hash_algorithm": "SHA256",
    "datagram_version": "1.0.0"
  }
}
```

### Decompile Config

```json
{
  "script_id": "SERVICES/fastmcp_server",
  "target_path": "SCRIPTS/SERVICES/fastmcp_server",
  "post_unpack_actions": [
    {"type": "register_script", "enabled": true},
    {"type": "install_dependencies"},
    {"type": "restore_configs"},
    {"type": "start_script", "delay_seconds": 2}
  ],
  "compatibility": {
    "min_engine_version": "1.0.0",
    "required_ports": []
  },
  "unpackaging": {
    "extract_method": "directory_copy",
    "overwrite_existing": false,
    "create_backup": true,
    "on_conflict": "skip"
  }
}
```

## Phooks Events Reference

| Event | Direction | Description |
|-------|-----------|-------------|
| `packager.snapshot.create` | Emit | Create a script snapshot datagram |
| `packager.snapshot.load` | Emit | Load/examine a snapshot datagram |
| `packager.snapshot.list` | Emit | List all stored snapshots |
| `packager.snapshot.delete` | Emit | Delete a snapshot |
| `packager.snapshot.info` | Emit | Get snapshot metadata |
| `packager.deploy.preview` | Emit | Preview deployment effects |
| `packager.deploy.execute` | Emit | Deploy a snapshot into engine |
| `packager.script.list` | Emit | List all scripts available for packaging |
| `packager.script.config.get` | Emit | Get compile/decompile config for a script |
| `packager.script.config.set` | Emit | Set compile/decompile config |