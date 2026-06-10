# MC Server Runner — Architecture Document

**Version:** 1.0.0  
**Last Updated:** 2025-06-08  
**Author:** YuniScripts Engine Team

---

## Table of Contents

1. [Overview](#1-overview)
2. [Directory Structure](#2-directory-structure)
3. [Entry Point: `main.py`](#3-entry-point-mainpy)
4. [Engine Modules](#4-engine-modules)
5. [CLI Commands Module](#5-cli-commands-module)
6. [Configuration](#6-configuration)
7. [Database Schema](#7-database-schema)
8. [Supported Server Types](#8-supported-server-types)
9. [RCON Command Flow](#9-rcon-command-flow)
10. [VFS System Design](#10-vfs-system-design)
11. [Atomic Operations System](#11-atomic-operations-system)
12. [Converter Logic](#12-converter-logic)
13. [Mod Manager Capabilities](#13-mod-manager-capabilities)
14. [Runner Lifecycle](#14-runner-lifecycle)
15. [Missing Dependency Detection](#15-missing-dependency-detection)
16. [Test Coverage](#16-test-coverage)
17. [Data Flow Diagrams](#17-data-flow-diagrams)

---

## 1. Overview

The **MC Server Runner Manager** is a fully sandboxed Minecraft server management system designed to operate within the YuniScripts engine ecosystem. It provides:

- **Virtual File System (VFS):** All server files stored as compressed, integrity-verified blobs in SQLite, transparently extracted at runtime.
- **Virtual Networking:** Port reservation, firewall ACL management (via iptables), bandwidth/latency/packet-loss simulation.
- **Full Mod System:** Mod registry with dependency graphs, versioned backups with rollback, Modrinth/CurseForge API caching.
- **Multi-Server Lifecycle:** Start, stop, restart, monitor multiple server processes concurrently.
- **Auto-Setup Profiles:** Automatic server JAR download for Vanilla, Fabric, Quilt, Forge, NeoForge, Paper, Purpur, Spigot.

The system is managed by the YuniScripts process wrapper and communicates via file-based hooks (Phooks). All persistent state lives in a single SQLite database (server_data.db).

---

## 2. Directory Structure

```
mc-server-runner/
├── main.py                     # CLI entry point, argument parsing, command dispatch
├── meta.info                   # YuniScripts script metadata (INI format)
├── main.py.bak                 # Backup of main.py (not actively used)
│
├── DATA/
│   └── config.json             # Runtime configuration (JSON)
│
├── engine/
│   ├── __init__.py             # Module exports
│   ├── atomic.py               # Journal-based atomic operation manager
│   ├── converter.py            # File<->DB conversion with SHA-256 validation
│   ├── database.py             # SQLite schema + CRUD for server_data.db
│   ├── mod_cache.py            # Modrinth & CurseForge API caching bridge
│   ├── mod_manager.py          # Mod registry, dependency, backup, rollback
│   ├── networking.py           # Virtual network adapter, firewall, port manager
│   ├── runner.py               # MC server process lifecycle management
│   ├── server_profiles.py      # Server type profiles + auto-download setup
│   └── vfs.py                  # Virtual File System (simulated root /)
│
├── FUNCTIONS/
│   └── server_cmds.py          # CLI command implementations (legacy/direct-use)
│
├── logs/                       # Runtime logs (mcsr.log)
│
└── tests/
    └── test_vfs_integration.py # Integration tests for VFS pipeline
```


## 3. Entry Point: main.py

**File:** main.py (21,355 bytes)  
**Purpose:** CLI entry point, argument parsing, command dispatch, subsystem factory.

### Responsibilities

1. **Configuration Loading:** Reads DATA/config.json via load_config().
2. **Logging Setup:** Configures file+stdout logging to logs/mcsr.log at INFO level.
3. **Subsystem Factory:** get_subsystems(config) creates and returns (db, vfs, mod_manager) triple.
4. **Argument Parsing:** Uses argparse with subparsers for each command.
5. **Command Dispatch:** Maps parsed command strings to handler functions.
6. **Error Handling:** Each command handler wraps operations in try/except.

### CLI Commands

| Command | Description |
|---------|-------------|
| list | List all registered server instances |
| create <name> | Create a new server instance |
| setup <name> | Auto-download and configure server JAR |
| start <name> | Start a server |
| stop <name> | Stop a server (with graceful timeout/force) |
| restart <name> | Restart a server |
| status [name] | Show server status |
| import <host> [vfs] | Import host file into VFS |
| extract <vfs> [host] | Extract file from VFS |
| mod list/install/remove/backup/rollback/deps | Mod management |
| network rules/allow/deny <server> | Network rule management |
| vfs ls/tree [path] | VFS exploration |
| conversions | Show conversion log |

### Notable Design Decisions

- **NOTE:** cmd_restart actually calls cmd_stop_server (dispatch logic maps restart to cmd_stop_server). The restart logic in ServerRunner.restart() is never actually called from the CLI.
- The database connection is opened per-command and closed explicitly in each handler.
- ServerRunner holds a persistent DB reference; cmd_start_server intentionally does NOT close the DB.
- Import path mangling: sys.path.insert(0, ...) is called at module level.

---

## 4. Engine Modules

### 4.1 database.py -- SQLite Database Layer

**File:** engine/database.py (27,558 bytes)  
**Purpose:** Manages all persistent state in server_data.db.

#### Architecture

- **Singleton pattern:** get_db(db_path) returns a cached Database instance per path.
- **Schema version auto-creation:** Database.__init__() calls _create_schema() which runs all CREATE TABLE IF NOT EXISTS statements.
- **Thread-safe:** WAL journal mode, 5-second busy timeout, foreign keys enabled.

#### Key Classes

| Class | Responsibility |
|-------|---------------|
| Database | Full CRUD for VFS files, server instances, mods, backups, network rules, virtual adapters, atomic operations, conversion log |

#### Database Schema (11 tables)

See Section 7.

#### VFS Operations

- store_file(vfs_path, blob_data, file_mode) -- Insert or UPDATE (via ON CONFLICT ... DO UPDATE SET). Compresses data with zlib level 6.
- get_file(vfs_path) -- Retrieve and decompress a VFS file blob. Returns dict or None.
- delete_file(vfs_path) -- Delete from DB. Returns True if row removed.
- list_files(prefix) -- List files under prefix path, with SQL LIKE pattern matching.

#### Server Operations

- create_server(**kwargs) -- Inserts with safe-key filtering. Requires name and mc_version.
- get_server(server_id_or_name) -- Accepts int or string (name).
- update_server(server_id, **kwargs) -- Safe-key filtered UPDATE.
- delete_server(server_id) -- Cascading delete: removes flags, network rules, virtual adapters.
- list_servers() -- Returns all server instances.

#### Mod Operations

- create_mod(**kwargs) -- Requires name, slug, version, mc_version, loader.
- get_mod(mod_id_or_slug) -- Lookup by int ID or string slug.
- list_mods(server_id) -- All mods, optionally filtered by server.
- check_dependencies(mod_slug) -- Queries mod_dependencies table, returns unmet deps.
- add_backup(mod_id, version, backup_blob) -- Stores compressed backup blob.
- list_backups(mod_id) -- Returns backups ordered by creation DESC.

#### Atomic Operations

- begin_atomic(op_type, op_key, before_state) -- Creates journal entry.
- commit_atomic(op_id) -- Marks committed.
- rollback_atomic(op_id) -- Restores before-state. Handles file_write, file_delete, mod_install, mod_update types.

#### Network Operations

- add_network_rule(**kwargs) -- Inserts ACL rule. Requires server_id and rule_type.
- list_network_rules(server_id) -- Ordered by priority ASC.
- get_virtual_adapter(server_id) -- Returns virtual NIC config.
- create_virtual_adapter(**kwargs) -- Creates virtual NIC. Requires server_id.
- get_config(server_id, key, default) / set_config(server_id, key, value) -- Runtime flags via server_flags table.

#### Conversion Logging

- log_conversion(vfs_path, direction, file_hash, size, result, error) -- Audit trail entry.

**NOTE:** There is a mismatch between the Database class and what VirtualAdapter expects: Database.create_virtual_adapter() returns an int (row ID) but VirtualAdapter._load() expects a dict. Tests work around this by inserting raw SQL.

---

### 4.2 atomic.py -- Journal-Based Atomic Operations

**File:** engine/atomic.py (18,614 bytes)  
**Purpose:** Provides transaction-like guarantees for VFS mutations.

#### Key Classes

| Class | Responsibility |
|-------|---------------|
| AtomicOperation | Single operation with op_id, type, key, before/after state, status, timestamp. Uses __slots__ for memory efficiency. |
| AtomicJournal | Thread-safe stack-based journal. Supports begin/commit/rollback lifecycle. |
| AtomicWriteContext | Context object for safe file writes (captures before-state, supplies after-state). |

#### Journal Lifecycle

begin(op_type, op_key, before_state) -> op_id
    |
[perform mutation]
    |
commit(op_id, after_state)  OR  rollback(op_id)

#### Features

- **Thread-safe:** All public methods acquire self._lock (threading.Lock()).
- **Double-checked locking singleton:** get_journal(db) creates module-level AtomicJournal on first call.
- **Context manager:** with AtomicJournal() as j: commits on success, rolls back all on exception.
- **Optional DB persistence:** If a DB instance is provided, operations are persisted to atomic_operations table.
- **LIFO rollback:** rollback_all() rolls back pending operations in reverse order.
- **Testing support:** reset_journal() clears the singleton for test isolation.

#### atomic_write Helper

The atomic_write(file_path, db, vfs) function creates an AtomicWriteContext:

1. Captures before-state (reads existing file or marks as non-existent).
2. Returns context with set_new_content(), commit(), rollback() methods.
3. Caller writes data, then commits or rolls back.

---

### 4.3 converter.py -- File<->DB Conversion with Integrity

**File:** engine/converter.py (12,645 bytes)  
**Purpose:** Single point of truth for all file<->DB conversion with SHA-256 integrity validation.

#### Core Functions

| Function | Description |
|----------|-------------|
| compress_data(data) | zlib compress at level 6 |
| decompress_data(compressed) | zlib decompress |
| file_to_db_blob(file_path) | Reads file -> compresses -> dual SHA-256 hashes -> returns dict |
| bytes_to_db_blob(raw_bytes) | Same flow from in-memory bytes |
| db_blob_to_file(blob_data, validation_hash, output_path) | Verifies hash -> decompresses -> writes file |
| validate_file_integrity(file_path, expected_hash) | Pure check (no modification) |

#### Dual Hash Design

Raw File Bytes -> import_hash = SHA-256(raw_bytes) [import integrity check]
                 -> zlib.compress(level=6) -> validation_hash = SHA-256(compressed_blob) [extract verification]

This design means:
- **Import hash** verifies the round-trip: original file <-> decompressed file.
- **Validation hash** verifies the stored blob was not corrupted in the DB.

#### ConversionRecord

Lightweight audit trail class with __slots__: vfs_path, direction (import/export), file_hash, original_size, result, error_msg, timestamp.

---

### 4.4 vfs.py -- Virtual File System

**File:** engine/vfs.py (19,274 bytes)  
**Purpose:** Implements a simulated root / filesystem backed by SQLite.

#### Architecture

User/CLI Code -> VFS (simulated paths like /servers/my-server/server.properties) 
                 -> Database (server_data.db -- compressed blobs) 
                 -> Physical filesystem (vfs_root/ -- extracted on demand)

#### Key Methods

| Method | Description |
|--------|-------------|
| read(vfs_path) | Decompress and return bytes from DB |
| write(vfs_path, data, ...) | Compress, hash, store in DB via atomic journal |
| delete(vfs_path) | Remove from DB + physical, journaled |
| exists(vfs_path) | Check DB for path |
| listdir(vfs_path) | List files/dirs under prefix (DB query) |
| mkdir(vfs_path) | Create physical directory |
| extract(vfs_path, output_path) | Decompress blob -> write physical file with hash validation |
| extract_all(vfs_prefix) | Extract all files under prefix |
| import_file(host_path, vfs_path) | Read host file -> compress -> store in DB |
| import_directory(host_dir, vfs_prefix) | Import entire directory tree |
| get_info(vfs_path) | Metadata (size, mode, timestamps) |
| commit_all() / rollback_all() | Batch journal operations |

#### Path Mapping

- VFS path /servers/my-server/server.properties -> physical vfs_root/servers/my-server/server.properties
- All VFS paths must start with /
- _normalize_path() ensures leading / and no trailing slash

#### Atomic Integration

- write() captures before-state and creates journal entry if atomic=True
- delete() similarly journaled
- The VFS constructor accepts an optional AtomicJournal instance

#### Known Issues

- write() and delete() have structural issues with try/except blocks containing normalization calls.
- listdir() returns None on exception instead of empty list -- callers could crash.

---

### 4.5 mod_manager.py -- Mod Lifecycle Management

**File:** engine/mod_manager.py (25,133 bytes)  
**Purpose:** Full mod lifecycle: registry, dependency graphs, versioned backups, rollback, compatibility validation.

#### Exceptions

| Exception | Description |
|-----------|-------------|
| ModError | Raised on any mod operation failure |

#### ModLoaders Constants

FABRIC, FORGE, QUILT, NEOFORGE, VANILLA, BUKKIT, PAPER, PURPUR

#### Key Methods

| Method | Description |
|--------|-------------|
| register_mod(name, slug, version, mc_version, loader, ...) | Create mod entry + import file to VFS |
| unregister_mod(slug, create_backup) | Remove mod, check reverse deps, backup, clean VFS |
| get_mod(slug) / list_mods(...) | Read queries with filters |
| add_dependency(mod_slug, depends_on_slug, required) | Add edge to dependency graph with circular check |
| check_dependencies(slug) | Return list of unmet deps |
| resolve_dependencies(slug) | Recursive topological sort of dep graph (max depth 50) |
| check_mc_version_compatibility(slug, mc_version) | Exact + partial MC version matching |
| check_loader_compatibility(mod_loader, server_loader) | Cross-loader compatibility groups |
| backup_mod(slug, notes) | Create versioned backup (file stored as compressed blob) |
| list_backups(slug) | List all backups for mod |
| rollback_mod(slug, backup_id) | Restore mod from backup blob + hash verification |
| auto_backup_before_update(slug) | Pre-update backup hook |
| install_mod_to_server(slug, server_id) | Copy mod from VFS mods storage to server's mods dir |
| remove_mod_from_server(slug, server_id) | Remove mod from server |
| list_server_mods(server_id) | List mods on a server |
| install_server_modpack(server_id, mod_slugs) | Bulk install with dependency resolution |
| upgrade_mod(slug, new_version, new_file_data) | Upgrade with automatic backup |

#### Compatibility Groups

| Loader | Compatible With |
|--------|----------------|
| Fabric | Quilt |
| Quilt | Fabric |
| Forge | NeoForge |
| NeoForge | Forge |
| Bukkit | Paper, Purpur |
| Paper | Bukkit, Purpur |
| Purpur | Bukkit, Paper |

#### Backup System

- Backups stored as zlib-compressed blobs in mod_backups table
- SHA-256 hash stored alongside for verification on restore
- rollback_mod() verifies hash before restoring
- auto_backup_before_update() called by upgrade workflow

---

### 4.6 mod_cache.py -- Modrinth + CurseForge Caching Bridge

**File:** engine/mod_cache.py (14,984 bytes)  
**Purpose:** Downloads and caches mod metadata + binaries from Modrinth and CurseForge APIs.

#### Key Methods

| Method | Description |
|--------|-------------|
| modrinth_search(query, loaders, mc_version, limit) | Search Modrinth with facet filtering |
| modrinth_get_project(slug) | Get full project metadata |
| modrinth_get_versions(slug, mc_version, loaders) | Get versions with MC+loader filter, sorted by date |
| modrinth_get_latest(slug, mc_version, loader, version_type) | Get latest matching version |
| modrinth_download(slug, mc_version, loader, version_type) | Download with VFS caching + SHA-512/SHA-1 verification |
| curseforge_search(query, game_version, mod_loader_type, limit) | CurseForge search (requires API key) |
| install_mod_from_cache(slug, mc_version, loader, server_id) | Download + install in one operation |
| install_bulk(slugs, mc_version, loader, server_id) | Multiple mod install with {installed, failed} result |
| clear_cache(older_than_days) | Cache cleanup (stub -- logs only) |

#### Caching Strategy

1. Check VFS cache -> if cached, return immediately
2. Fetch metadata from Modrinth API
3. Download binary from CDN (60s timeout)
4. Verify SHA-512 hash and file size
5. Store in VFS at /mod-cache/ with metadata in server_flags table
6. Return (filename, file_data) tuple

#### API Endpoints

- Modrinth: https://api.modrinth.com/v2/
- CurseForge: https://api.curseforge.com/v1/
- Fabric Meta: https://meta.fabricmc.net/v2/
- Paper API: https://api.papermc.io/v2/
- Purpur API: https://api.purpurmc.org/v2/
- Mojang Meta: https://piston-meta.mojang.com/mc/game/version_manifest.json

---

### 4.7 networking.py -- Virtual Network Sandbox

**File:** engine/networking.py (35,448 bytes)  
**Purpose:** Provides virtual network sandbox configuration for server instances.

#### Architecture

The networking module does NOT intercept actual system sockets. Instead it:

1. Manages virtual IP assignments (auto-generated from 10.0.0.0/24).
2. Maintains firewall ACL rules (stored in DB, enforced via iptables at server start).
3. Allocates ports to prevent conflicts between collocated server instances.

#### Key Classes

| Class | Responsibility |
|-------|---------------|
| VirtualAdapter | Virtual NIC configuration (IP, MAC, bandwidth, latency, packet loss) |
| Firewall | ACL rule management + iptables script generation + connection checking |
| PortManager | Thread-safe port reservation with DB persistence |
| NetworkManager | Top-level coordinator, combines adapter + firewall + ports |

#### VirtualAdapter

- Auto-creates on first access (lazy init in _load()).
- Generates random IP from 10.0.0.0/24 and MAC with locally-administered bit (0x02).
- Supports bandwidth limit (kbps), latency simulation (ms), packet loss (%).
- Validation on update: IP format, subnet format, packet loss range (0.0-100.0).

#### Firewall (Default-Deny Model)

- Rules evaluated in priority order (lower number = checked first).
- Supports: rule_type (allow/deny), direction (inbound/outbound), protocol (tcp/udp/any), port_range, target_host (with wildcard support).
- check_connection(host, port, protocol) -- Evaluate rules against connection parameters.
- generate_firewall_script() -- Produces iptables script creating MC-SERVER-{id} chain.
- get_allowed_ports() -- Returns sorted, deduplicated list of allowed ports.

#### PortManager (Thread-Safe)

- Class-level threading.Lock and _reserved dict (port -> server_id).
- reserve(port, server_id, db) / release(port, server_id) / check_available(port)
- find_free_port(preferred, db) -- Search upward from preferred, wrap around.
- get_server_for_port(port) -- Lookup owner.

#### NetworkManager

- get_sandbox_config() -- Combined adapter + firewall + port config.
- get_server_properties_override() -- server-port and server-ip overrides.
- initialize_default_rules() -- Default allow rules for MC port (25565) and RCON port (25575).
- generate_network_config() -- Primary method called by runner before launch.
- cleanup() -- Release ports.

#### Driver Compatibility Note

Firewall expects db.add_firewall_rule() / db.remove_firewall_rule() / db.list_firewall_rules(), but the actual Database class has add_network_rule() / (delete_server cleans up network rules) / list_network_rules(). The Firewall class would fail if used directly through the Database API.

---

### 4.8 runner.py -- Server Process Lifecycle

**File:** engine/runner.py (20,006 bytes)  
**Purpose:** Manages Minecraft server process lifecycle within the VFS sandbox.

#### Key Classes

| Class | Responsibility |
|-------|---------------|
| ServerRunnerError | Exception for runner failures |
| ServerRunner | Full lifecycle management with class-level running registry |

#### ServerRunner Architecture

ServerRunner (per server instance):
  - Database (config, status flags)
  - VFS (file extraction/saving)
  - NetworkManager (port reservation, firewall)
  - ModManager (mod installation)
  - subprocess.Popen (Java process)
  - Monitor thread (health check loop)
  - Work directory (vfs/<name>/)

#### Lifecycle

start():
  1. _prepare_workdir() -- Extract VFS to physical directory
  2. _generate_server_properties() -- Write server.properties
  3. _install_mods_to_workdir() -- Copy mod files
  4. _setup_network() -- Reserve ports, init firewall rules
  5. _launch_java_process() -- Popen Java with Aikar's flags
  6. _start_monitor() -- Background health check thread

stop(timeout, force):
  1. _rcon_stop(timeout) -- RCON stop command (try)
  2. _signal_stop(timeout) -- SIGTERM -> wait -> SIGKILL fallback
  3. _cleanup() -- Release ports, update status, unregister

restart(timeout): stop() -> clear stop event -> start(extract_files=False)

#### Java Default Arguments

Uses Aikar's Flags for G1GC optimization: +UseG1GC, +ParallelRefProcEnabled, MaxGCPauseMillis=200, etc.

#### RCON Implementation (Partially Broken)

The _rcon_stop() method connects to 127.0.0.1:{rcon_port} via TCP socket and sends an auth packet, but **never actually sends the stop command** -- the code returns True immediately after the comment "# Send stop command".

#### Known Issues

1. RCON stop doesn't send command -- returns True without sending "stop".
2. Monitor thread only logs exit -- no automatic restart logic.
3. _cleanup() clears server_port and rcon_port flags -- should retain configured ports.
4. cmd_restart in main.py dispatches to cmd_stop_server instead of using ServerRunner.restart().

---

### 4.9 server_profiles.py -- Server Type Profiles & Setup

**File:** engine/server_profiles.py (19,764 bytes)  
**Purpose:** Defines setup procedures for each Minecraft server type/loader.

#### Supported Types

| Type | Setup Method | Downloads From |
|------|-------------|----------------|
| vanilla | Mojang manifest | piston-meta.mojang.com |
| fabric | Fabric Maven + Meta API | maven.fabricmc.net |
| quilt | Quilt Meta API | meta.quiltmc.org |
| forge | Forge Maven (hints only) | maven.minecraftforge.net |
| neoforge | NeoForge Maven (hints only) | maven.neoforged.net |
| paper | PaperMC API | api.papermc.io |
| purpur | Purpur API | api.purpurmc.org |
| spigot | BuildTools (manual) | N/A (must compile) |
| bukkit | Legacy (manual) | N/A |

#### Setup Patterns

- **Vanilla, Paper, Purpur:** Direct JAR download -> store in VFS.
- **Fabric:** Downloads launcher JAR from Maven, falls back to Meta API for loader info.
- **Quilt, Forge, NeoForge, Spigot:** Write setup hints (.json files) -- some require manual installer steps.
- **All types** write eula=true.

---

## 5. CLI Commands Module: FUNCTIONS/server_cmds.py

**File:** FUNCTIONS/server_cmds.py (21,899 bytes)  
**Purpose:** Self-contained command implementations for direct use from YuniScripts shell or programmatic import.

This module duplicates many of the command handlers found in main.py but is designed for standalone use. Each function opens its own database connection, prints results, and closes the connection.

### Command Functions

cmd_list_servers, cmd_create_server, cmd_start_server, cmd_stop_server, cmd_restart_server, cmd_server_status,
cmd_install_mod, cmd_list_mods, cmd_remove_mod, cmd_backup_mod, cmd_rollback_mod, cmd_check_dependencies,
cmd_vfs_list, cmd_vfs_import, cmd_vfs_extract, cmd_network_rules, cmd_add_network_rule, cmd_list_conversions

### Bug: Inconsistent ServerRunner Constructor

- main.py calls: ServerRunner(db, vfs, server['id']) -- 3 args.
- server_cmds.py calls: ServerRunner(server, db) -- 2 args (will fail).

---

## 6. Configuration

### 6.1 DATA/config.json

- db_path: DATA/server_data.db (default)
- vfs_root: vfs (default)
- default_mc_version: **26.1.2** (NOT a real MC version -- potential bug)
- max_concurrent_servers: 1
- enable_networking_sandbox: true
- atomic_timeout_seconds: 30
- log_level: INFO

### 6.2 meta.info

YuniScripts metadata: name, version (1.0.0), category (games), entry_point (main.py).
Virtual port map: 25565 (MC), 25575 (RCON), 8000 (Admin API).

---

## 7. Database Schema

Database file: DATA/server_data.db (auto-created, WAL journal mode).

### Tables (11 total)

1. **vfs_nodes** -- Compressed file blobs with path-based lookup
2. **vfs_metadata** -- Extended attributes per VFS node
3. **server_instances** -- Registered MC server configurations
4. **server_flags** -- Runtime flags per server instance
5. **mods** -- Mod registry (slug unique, versioned)
6. **mod_dependencies** -- Dependency graph edges
7. **mod_backups** -- Versioned backups for rollback
8. **network_rules** -- Virtual network ACLs
9. **virtual_adapters** -- Virtual NIC configurations
10. **conversion_log** -- Audit trail for file conversions
11. **atomic_operations** -- Journal for rollback safety

---

## 8. Supported Server Types

| Type | Installation | Mods | Plugins | Java Min | Notes |
|------|-------------|------|---------|----------|-------|
| vanilla | Auto-download | No | No | 17 | Official server.jar |
| fabric | Auto-download | Yes (Fabric) | No | 17 | Bootstrap launcher |
| quilt | Manual hint only | Yes (Quilt) | No | 17 | Fabric-compatible |
| forge | Manual hint only | Yes (Forge) | No | 17 | Requires installer |
| neoforge | Manual hint only | Yes (NeoForge) | No | 17 | Forge fork |
| paper | Auto-download | No | Yes (Bukkit) | 17 | High-performance |
| purpur | Auto-download | No | Yes (Bukkit) | 17 | Paper fork |
| spigot | Manual (BuildTools) | No | Yes (Bukkit) | 17 | Needs compilation |
| bukkit | Manual | No | Yes (Bukkit) | 8 | Legacy |

---

## 9. RCON Command Flow

The RCON system uses the RCON protocol over TCP.

### Flow

ServerRunner.stop():
  1. Check if rcon_password is set (skip if empty)
  2. Create TCP socket to 127.0.0.1:{rcon_port}
  3. Send RCON authentication packet (type=3)
  4. [BUG] Never sends the stop command -- returns True immediately

### Packet Structure (per wiki.vg/Rcon)

Length: int32 LE -- length of rest of packet
ID:     int32 LE -- request ID
Type:   int32 LE -- 3=auth, 2=command
Payload: UTF-8 string + NUL + NUL padding

---

## 10. VFS System Design

### Layer Architecture

User/CLI -> VFS Class (virtual paths) 
            -> Database Layer (server_data.db -- vfs_nodes table) 
               -> Compressed blob storage (zlib level 6) 
               -> Dual SHA-256 hashing + audit log
            -> Physical Layer (vfs/ directory) 
               -> On-demand extraction with integrity verification

### Data Flow

**Import (host -> VFS):**
host_file -> file_to_db_blob() -> SHA-256(raw)=import_hash -> zlib.compress -> SHA-256(compressed)=validation_hash -> db.store_file() -> vfs_nodes

**Extract (VFS -> host):**
db.get_file() -> SHA-256(blob)==validation_hash? -> zlib.decompress -> write file

**Write (via CLI/runtime):**
user_data -> vfs.write() -> bytes_to_db_blob() -> capture before_state -> db.store_file() -> journal.begin/commit

---

## 11. Atomic Operations System

### Journal Types

| Type | Rollback Action |
|------|-----------------|
| file_write | Restore old blob or delete if new file |
| file_delete | Re-insert deleted blob |
| mod_install | Disable/delete mod |
| mod_update | Restore previous version |

### Lifecycle

get_journal(db) -> module singleton AtomicJournal
  -> begin(op_type, op_key, before_state) -> op_id
  -> [mutation happens]
  -> commit(op_id, after_state) OR rollback(op_id)

Context manager: with AtomicJournal(db) as j: auto-commits on success, auto-rollbacks on exception.

---

## 12. Converter Logic

### Dual Hash Design

Raw bytes -> import_hash = SHA-256(raw) [for round-trip verification]
          -> zlib.compress(level=6)
          -> validation_hash = SHA-256(compressed) [for stored blob verification]

### Functions

file_to_db_blob(path) -- Read host file -> compress + hash -> DB-ready dict
bytes_to_db_blob(data) -- Same from in-memory bytes
db_blob_to_file(blob, hash, path) -- Verify hash -> decompress -> write file
validate_file_integrity(path, hash) -- Pure SHA-256 check

---

## 13. Mod Manager Capabilities

### Feature Status

| Feature | Status |
|---------|--------|
| Registry CRUD | Done |
| File Import to VFS | Done |
| Dependency Graph | Done |
| Circular Detection (DFS) | Done |
| Topological Sort (depth 50) | Done |
| Reverse Dep Check | Done |
| MC Version Compatibility | Done |
| Loader Compatibility Groups | Done |
| Versioned Backups (compressed) | Done |
| Mod Rollback with hash verify | Done |
| Auto Backup Before Update | Done |
| Server Installation | Done |
| Bulk Modpack Install | Done |
| Upgrades with backup | Done |
| Modrinth API Integration | Done |
| CurseForge API (requires key) | Done |
| VFS Caching | Done |

---

## 14. Runner Lifecycle

### Start

1. Validate not already running
2. _prepare_workdir() -- VFS.extract_all to physical
3. _generate_server_properties() -- Write config
4. _install_mods_to_workdir() -- Copy mods
5. _setup_network() -- Reserve ports, firewall rules
6. _launch_java_process() -- Popen Java
7. _start_monitor() -- Daemon thread (5s check)

### Stop

1. Set _stop_event
2. If force -> _force_kill()
3. Try _rcon_stop(timeout) -- [PARTIALLY BROKEN]
4. If RCON fails -> _signal_stop(timeout) -- SIGTERM -> SIGKILL
5. _cleanup() -- Release ports, update DB status

### Monitor

Daemon thread: every 5 seconds check process health. On unexpected exit: set status='crashed', record exit code, run cleanup.

---

## 15. Missing Dependency Detection

### What IS Detected

- Missing mod dependencies (mod_dependencies table query)
- Circular dependencies (DFS-based detection)
- Reverse dependencies (mods that depend on mod being removed)
- MC version incompatibility (exact + partial match)
- Loader incompatibility (direct match + compatibility groups)
- Backup hash mismatch (SHA-256 verification on restore)

### What is NOT Detected

- Missing Java installation (caught at process launch time)
- Missing Modrinth/CurseForge API dependencies
- OS-level port conflicts
- Disk space availability
- Mod file corruption before extraction

---

## 16. Test Coverage

### Test File

tests/test_vfs_integration.py (23,977 bytes) -- 15 test cases using unittest.

### Test Cases

1. test_create_schema -- All 11 tables created
2. test_store_and_retrieve_file -- Store/retrieve content verification
3. test_file_integrity -- Full round-trip hash verification
4. test_atomic_commit -- Begin/commit lifecycle
5. test_atomic_rollback -- Begin/rollback lifecycle
6. test_converter_roundtrip -- Bytes->blob->file hash chain
7. test_bad_hash_rejected -- Tampered data fails validation
8. test_virtual_adapter_create -- MAC/IP format verification
9. test_firewall_allow_rule -- Allow rule storage
10. test_firewall_deny_rule -- Deny rule storage
11. test_server_crud -- Full CRUD cycle
12. test_mod_crud -- Mod lifecycle with deps
13. test_mod_backup_rollback -- Backup storage
14. test_conversion_log -- Log entry verification
15. test_cleanup -- DB close/reopen

### Test Gaps

- VFS class (read, write, delete, listdir, extract, import_file, import_directory)
- ModManager (register, unregister, dependency resolution, backup, rollback)
- ServerRunner lifecycle (start/stop/monitor)
- ServerProfiles (setup methods)
- ModCache (Modrinth/CurseForge integration)
- CLI (main.py argument parsing, command dispatch)
- server_cmds.py functions
- Integration between runner + networking + VFS

---

## 17. Data Flow Diagrams

### Server Creation Flow

CLI: main.py create my-server --mc 1.20.4 --type fabric
  -> cmd_create_server()
    -> db.create_server() -> INSERT INTO server_instances
    -> vfs.mkdir("/servers/my-server")
    -> vfs.mkdir("/servers/my-server/mods")
    -> vfs.mkdir("/servers/my-server/plugins")

### Server Start Flow

CLI: main.py start my-server
  -> cmd_start_server()
    -> db.get_server("my-server")
    -> ServerRunner(db, vfs, server_id).start()
      -> VFS.extract_all("/servers/my-server") -- db.get_file -> decompress -> write physical
      -> _generate_server_properties() -- write server.properties
      -> _install_mods_to_workdir() -- copy mods from VFS
      -> _setup_network() -- PortManager.reserve + init firewall
      -> _launch_java_process() -- subprocess.Popen(["java", ...])
      -> _start_monitor() -- health check thread

### Mod Installation Flow

CLI: main.py mod install fabric-api my-server
  -> cmd_mod()
    -> db.get_server("my-server")
    -> mod_cache.install_mod_from_cache("fabric-api", "1.20.4", "fabric", server_id)
      -> modrinth_download() -- check VFS cache -> API query -> CDN download -> verify SHA-512 -> store VFS cache
      -> store in VFS at /mods/fabric-api.jar
      -> register in DB via create_mod()
      -> ModManager.install_mod_to_server("fabric-api", server_id)
        -> check MC version compatibility
        -> check loader compatibility
        -> check dependencies
        -> copy mod JAR to /servers/my-server/mods/

### Dependency Check Flow

ModManager.check_dependencies("jei")
  -> db.check_dependencies("jei")
    -> db.get_mod("jei") -> {id: 1, ...}
    -> SELECT md.required, m.slug, m.name, m.id
       FROM mod_dependencies md
       LEFT JOIN mods m ON m.id = md.depends_on_mod_id
       WHERE md.mod_id = 1
    -> For each: if dep_id IS NULL -> installed=False; else installed=True
  -> Return list of unmet dependencies

---

## Appendix: Known Issues & Technical Debt

### Bugs
1. **RCON stop doesn't send command** (runner.py:244): Returns True without sending "stop".
2. **ServerRunner constructor mismatch** (server_cmds.py vs main.py): Different arg signatures.
3. **cmd_restart dispatches to cmd_stop_server** (main.py:370): Restart only stops, never starts again.
4. **VFS write() normalization bug**: _normalize_path() called inside try/except but before the except.
5. **default_mc_version is "26.1.2"** in config.json -- not a real Minecraft version.

### Missing Features
1. No automatic restart on crash (monitor only logs).
2. No disk space checks before VFS extraction.
3. No OS-level port conflict detection.
4. Spigot setup requires manual BuildTools compilation.
5. Cache clear (mod_cache.clear_cache()) is a stub.
6. No admin API endpoint implemented (port 8000 reserved in meta.info).
7. CurseForge support requires API key (no auth token management).

### API Inconsistencies
1. Firewall expects db.add_firewall_rule() -- Database has add_network_rule().
2. Database.create_virtual_adapter() returns int -- VirtualAdapter._load() expects dict.
3. Database.log_conversion() signature differs between database.py and callers.
4. ModManager uses db.query() in some places and db.conn.execute() in others.

### Test Gaps
- No tests for VFS class, ModManager, ServerRunner, server profiles, mod cache, or networking integration.
- The 15 existing tests cover database schema, converter logic, atomic operations, and basic CRUD.
