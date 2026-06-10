# Datagram Engine Module — API Documentation

## Overview

The Datagram Engine Module provides a forward/backward compatible data archival system as a YuniScripts service. Other YuniScripts scripts can interact with it via Phooks events to create, load, and manipulate datagram archives.

## Quick Start

```python
# From any YuniScript script:
from engine.phooks_client import PhooksClient

client = PhooksClient("my-script", 
    listen_events=["datagram.response.*"],
    emit_events=["datagram.create", "datagram.load", "datagram.db.*"])
client.register()

# Create a datagram
client.emit("datagram.create", {
    "path": "/path/to/my_datagram",
    "name": "My Data Archive"
})

# Load an existing datagram
client.emit("datagram.load", {
    "path": "/path/to/existing_datagram"
})
```

## Phooks Events

### Lifecycle Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `datagram.create` | Emit | Create a new datagram at the given path |
| `datagram.load` | Emit | Load an existing datagram from a path |
| `datagram.validate` | Emit | Validate datagram directory structure |

### Metadata Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `datagram.meta.get` | Emit | Get datagram metadata |
| `datagram.meta.update` | Emit | Update datagram metadata |

### Hash/Integrity Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `datagram.hash.compute` | Emit | Compute content hash |
| `datagram.hash.verify` | Emit | Verify stored hash against content |
| `datagram.hash.update` | Emit | Recompute and update stored hash |

### Database Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `datagram.db.insert` | Emit | Insert a record into a datagram database table |
| `datagram.db.select` | Emit | Query records from a datagram database table |
| `datagram.db.update` | Emit | Update records in a datagram database table |
| `datagram.db.delete` | Emit | Delete records from a datagram database table |

### Function Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `datagram.func.load` | Emit | Load an embedded function |
| `datagram.func.execute` | Emit | Execute a loaded function |
| `datagram.func.list` | Emit | List available functions |

### Compatibility Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `datagram.compat.check` | Emit | Check version compatibility |
| `datagram.compat.register` | Emit | Register a component version |

## Direct Python API

For scripts that import the engine directly:

```python
from engine import (
    Datagram, DatagramMeta, DatagramVersion, DatagramHash,
    HashAlgorithm, DatabaseType,
    load_datagram, create_datagram, compute_datagram_hash,
    verify_datagram_hash, update_datagram_hash,
    validate_datagram_structure, parse_ini_content,
    CompatibilityChecker, DatabaseRecord,
    SQLiteDatabase, JSONDatabase, create_database,
    FunctionRegistry, DatagramFunction,
)

# Create a datagram
dg = create_datagram("/path/to/datagram", name="My Archive", author="Me")

# Load a datagram
dg = load_datagram("/path/to/datagram")

# Compute and verify hash
hash_value = compute_datagram_hash(dg)
is_valid, computed = verify_datagram_hash(dg)

# Insert data
db = create_database(DatabaseType.SQLITE, "/path/to/data.db")
try:
    row_id = db.insert("items", {"name": "Test Item", "value": 42})
    results = db.select("items", where={"value": 42})
finally:
    db.close()

# Check compatibility
checker = CompatibilityChecker()
result = checker.check_datagram_compatibility(DatagramVersion(1, 0, 0))
print(f"Compatible: {result.compatible}")
```

## Data Types

The engine supports these data types for storage:

- `STRING` — Text values
- `INTEGER` — Integer numbers
- `FLOAT` — Floating point numbers
- `BOOLEAN` — True/False values
- `BINARY` — Binary data (stored as hex in JSON)
- `JSON` — Structured JSON objects
- `DATETIME` — Date/time strings
- `UUID` — UUID identifiers
- `NULL` — Null values

## API.md — Required by YuniScripts Engine
This file is required by the YuniScripts engine for all managed scripts.
