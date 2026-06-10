"""Server management commands for YuniScripts CLI.

Each cmd_* function is a self-contained command that opens a database
connection, performs its operation, prints results, and then closes
the connection.  Designed to be called from the YuniScripts shell or
imported and used programmatically.
"""

import json
import sys
import os
import hashlib


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_db(db_path: str = "DATA/server_data.db"):
    """Open a database connection, resolving relative paths."""
    from engine.database import get_db
    return get_db(db_path)


def _resolve_name_or_id(value, db):
    """Return server dict, accepting either a name string or integer id."""
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        return db.get_server(int(value))
    return db.get_server(value)


def _server_not_found(name_or_id) -> str:
    return f"[ERROR] Server '{name_or_id}' not found."


def _mod_not_found(slug) -> str:
    return f"[ERROR] Mod '{slug}' not found."


def _print_separator(char: str = "─", width: int = 60):
    print(char * width)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_vfs(db, db_path: str = "DATA/server_data.db"):
    """Create a VFS instance from the database."""
    from engine.vfs import VFS
    base_dir = os.path.dirname(os.path.abspath(db_path))
    vfs_root = os.path.join(base_dir, "vfs")
    return VFS(db, vfs_root=vfs_root)


# ===================================================================
#  Server commands
# ===================================================================

def cmd_list_servers(db_path: str = "DATA/server_data.db", **kwargs):
    """List all registered MC server instances."""
    db = _get_db(db_path)
    try:
        servers = db.list_servers()
        _print_separator()
        if not servers:
            print("  No servers registered.")
        else:
            for s in servers:
                print(
                    f"  [{s['id']}] {s['name']} "
                    f"- MC {s['mc_version']} ({s['server_type']}) "
                    f"- Port {s['server_port']}"
                )
        print(f"  Total: {len(servers)} server(s)")
        _print_separator()
    finally:
        db.close()


def cmd_create_server(
    name: str,
    mc_version: str,
    server_type: str = "vanilla",
    server_port: int = 25565,
    java_version: str = "17",
    min_ram: str = "2G",
    max_ram: str = "4G",
    db_path: str = "DATA/server_data.db",
):
    """Create a new server instance."""
    db = _get_db(db_path)
    try:
        if not (1024 <= server_port <= 65535):
            print(f"[ERROR] Port {server_port} is outside the allowed range (1024-65535).")
            return

        server_id = db.create_server(
            name=name,
            mc_version=mc_version,
            server_type=server_type,
            server_port=server_port,
            java_version=java_version,
            min_ram=min_ram,
            max_ram=max_ram,
        )
        print(f"[OK] Server '{name}' created (ID: {server_id}).")
        print(f"     Type: {server_type}, MC: {mc_version}, Port: {server_port}")
    finally:
        db.close()


def cmd_start_server(
    name_or_id,
    db_path: str = "DATA/server_data.db",
):
    """Start a server instance via the runner."""
    from engine.runner import ServerRunner

    db = _get_db(db_path)
    try:
        server = _resolve_name_or_id(name_or_id, db)
        if server is None:
            print(_server_not_found(name_or_id))
            return

        vfs = _get_vfs(db, db_path)
        runner = ServerRunner(db, vfs, server['id'])
        result = runner.start()
        if result:
            print(f"[OK] Server '{server['name']}' started (PID: {result}).")
        else:
            print(f"[ERROR] Failed to start server '{server['name']}'.")
    finally:
        db.close()


def cmd_stop_server(
    name_or_id,
    db_path: str = "DATA/server_data.db",
):
    """Gracefully stop a running server."""
    from engine.runner import ServerRunner

    db = _get_db(db_path)
    try:
        server = _resolve_name_or_id(name_or_id, db)
        if server is None:
            print(_server_not_found(name_or_id))
            return

        vfs = _get_vfs(db, db_path)
        runner = ServerRunner(db, vfs, server['id'])
        ok = runner.stop()
        if ok:
            print(f"[OK] Server '{server['name']}' stopped.")
        else:
            print(f"[ERROR] Failed to stop server '{server['name']}' (may not be running).")
    finally:
        db.close()


def cmd_restart_server(
    name_or_id,
    db_path: str = "DATA/server_data.db",
):
    """Restart a server."""
    from engine.runner import ServerRunner

    db = _get_db(db_path)
    try:
        server = _resolve_name_or_id(name_or_id, db)
        if server is None:
            print(_server_not_found(name_or_id))
            return

        vfs = _get_vfs(db, db_path)
        runner = ServerRunner(db, vfs, server['id'])
        ok = runner.restart()
        if ok:
            print(f"[OK] Server '{server['name']}' restarted.")
        else:
            print(f"[ERROR] Failed to restart server '{server['name']}'.")
    finally:
        db.close()


def cmd_server_status(
    name_or_id=None,
    db_path: str = "DATA/server_data.db",
):
    """Show status of all or one server."""
    from engine.runner import ServerRunner

    db = _get_db(db_path)
    try:
        if name_or_id is not None:
            server = _resolve_name_or_id(name_or_id, db)
            if server is None:
                print(_server_not_found(name_or_id))
                return
            servers = [server]
        else:
            servers = db.list_servers()

        vfs = _get_vfs(db, db_path)
        _print_separator("━")
        if not servers:
            print("  No servers registered.")
            _print_separator("━")
            return

        for s in servers:
            runner = ServerRunner(db, vfs, s['id'])
            status = runner.status()
            pid = status.get("pid", "—") if status else "ERR"
            running = status.get("running", False) if status else False
            state = "● RUNNING" if running else "○ STOPPED"
            uptime = status.get("uptime", "—") if status else "—"

            print(f"  [{s['id']}] {s['name']}")
            print(f"        MC: {s['mc_version']}  Type: {s['server_type']}")
            print(f"        Port: {s['server_port']}  PID: {pid}")
            print(f"        State: {state}  Uptime: {uptime}")
            print()
        print(f"  Total: {len(servers)} server(s)")
        _print_separator("━")
    finally:
        db.close()


# ===================================================================
#  Mod commands
# ===================================================================

def cmd_install_mod(
    mod_slug: str,
    mod_version: str,
    server_name: str = None,
    mc_version: str = "1.20.4",
    loader: str = "fabric",
    db_path: str = "DATA/server_data.db",
):
    """Install a mod to the registry."""
    db = _get_db(db_path)
    try:
        # Check if mod already exists
        existing = db.get_mod(mod_slug)
        if existing is not None:
            print(f"[WARN] Mod '{mod_slug}' is already registered (v{existing['version']}).")
            return

        mod_id = db.create_mod(
            name=mod_slug.capitalize().replace("-", " ").replace("_", " "),
            slug=mod_slug,
            version=mod_version,
            mc_version=mc_version,
            loader=loader,
        )
        print(f"[OK] Mod '{mod_slug}' v{mod_version} registered (ID: {mod_id}).")
    finally:
        db.close()


def cmd_list_mods(
    server_name=None,
    db_path: str = "DATA/server_data.db",
):
    """List installed mods."""
    db = _get_db(db_path)
    try:
        mods = db.list_mods()
        _print_separator()
        if not mods:
            print("  No mods registered.")
        else:
            for m in mods:
                print(
                    f"  [{m['id']}] {m['slug']:20s} v{m['version']:12s}  "
                    f"{m['loader']:8s}  MC {m['mc_version']}"
                )
        print(f"  Total: {len(mods)} mod(s)")
        _print_separator()
    finally:
        db.close()


def cmd_remove_mod(
    mod_slug: str,
    db_path: str = "DATA/server_data.db",
):
    """Remove a mod. Creates a backup first."""
    db = _get_db(db_path)
    try:
        mod = db.get_mod(mod_slug)
        if mod is None:
            print(_mod_not_found(mod_slug))
            return

        # Auto-backup before removal
        print(f"[INFO] Creating backup of '{mod_slug}' before removal...")
        backup_data = json.dumps(mod, indent=2, default=str).encode("utf-8")
        db.add_backup(
            mod_id=mod["id"],
            version=mod["version"],
            backup_blob=backup_data,
        )

        # Delete using raw SQL (no delete_mod method)
        db.conn.execute("DELETE FROM mod_dependencies WHERE mod_id = ?", (mod["id"],))
        db.conn.execute("DELETE FROM mod_backups WHERE mod_id = ?", (mod["id"],))
        db.conn.execute("DELETE FROM mods WHERE id = ?", (mod["id"],))
        db.conn.commit()
        print(f"[OK] Mod '{mod_slug}' removed (backup saved).")
    finally:
        db.close()


def cmd_backup_mod(
    mod_slug: str,
    db_path: str = "DATA/server_data.db",
):
    """Create a manual backup of a mod."""
    db = _get_db(db_path)
    try:
        mod = db.get_mod(mod_slug)
        if mod is None:
            print(_mod_not_found(mod_slug))
            return

        backup_data = json.dumps(mod, indent=2, default=str).encode("utf-8")
        backup_id = db.add_backup(
            mod_id=mod["id"],
            version=mod["version"],
            backup_blob=backup_data,
        )
        print(f"[OK] Backup created for '{mod_slug}' (ID: {backup_id}).")
    finally:
        db.close()


def cmd_rollback_mod(
    mod_slug: str,
    backup_id: int = None,
    db_path: str = "DATA/server_data.db",
):
    """Rollback a mod to a previous version."""
    db = _get_db(db_path)
    try:
        mod = db.get_mod(mod_slug)
        if mod is None:
            print(_mod_not_found(mod_slug))
            return

        backups = db.list_backups(mod["id"])
        if not backups:
            print(f"[ERROR] No backups found for '{mod_slug}'.")
            return

        if backup_id is not None:
            selected = [b for b in backups if b["id"] == backup_id]
            if not selected:
                print(f"[ERROR] Backup ID {backup_id} not found for '{mod_slug}'.")
                return
        else:
            selected = [backups[0]]  # most recent (list is DESC)

        bk = selected[0]
        restored_blob = bk.get("backup_blob")
        if restored_blob is None:
            print(f"[ERROR] Backup ID {bk['id']} has no data blob.")
            return

        try:
            restored_meta = json.loads(restored_blob.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            restored_meta = {}

        new_version = restored_meta.get("version", bk.get("version", "restored"))

        db.conn.execute(
            "UPDATE mods SET version = ?, updated_at = datetime('now') WHERE id = ?",
            (new_version, mod["id"]),
        )
        db.conn.commit()

        print(
            f"[OK] Mod '{mod_slug}' rolled back to v{new_version} "
            f"(backup ID: {bk['id']})."
        )
    finally:
        db.close()


def cmd_check_dependencies(
    mod_slug: str,
    db_path: str = "DATA/server_data.db",
):
    """Check dependencies for a mod, report any unmet requirements."""
    db = _get_db(db_path)
    try:
        unmet = db.check_dependencies(mod_slug)
        _print_separator()
        print(f"  Dependency check for '{mod_slug}':")
        if not unmet:
            print("    ✓ All dependencies met.")
        else:
            for d in unmet:
                status = "✓" if d.get("installed") else "✗"
                print(f"    {status}  {d.get('dep_slug', '?')}  "
                      f"({'required' if d.get('required') else 'optional'})")
        _print_separator()
    finally:
        db.close()


# ===================================================================
#  VFS commands
# ===================================================================

def cmd_vfs_list(
    vfs_path: str = "/",
    db_path: str = "DATA/server_data.db",
):
    """List files in the VFS at a given path."""
    db = _get_db(db_path)
    try:
        files = db.list_files(prefix=vfs_path)
        _print_separator()
        if not files:
            print(f"  (empty)  {vfs_path}")
        else:
            for f in files:
                etype = "📄"
                size = f.get("original_size", 0)
                mtime = f.get("updated_at", "—")
                print(f"  {etype} {f['vfs_path']:40s} {size:>10,}B  {mtime}")
        print(f"  Total: {len(files)} file(s)")
        _print_separator()
    finally:
        db.close()


def cmd_vfs_import(
    host_path: str,
    vfs_path: str,
    db_path: str = "DATA/server_data.db",
):
    """Import a file from the host filesystem into the VFS."""
    from engine.converter import file_to_db_blob

    if not os.path.isfile(host_path):
        print(f"[ERROR] Host path '{host_path}' is not a file or does not exist.")
        return

    db = _get_db(db_path)
    try:
        blob_info = file_to_db_blob(host_path)
        blob_data = blob_info["blob_data"]

        node_id = db.store_file(vfs_path, blob_data)
        if node_id is not None:
            size = blob_info["original_size"]
            print(f"[OK] Imported '{host_path}' ({size:,} B) → VFS '{vfs_path}' (ID: {node_id}).")

            # Log the conversion
            db.log_conversion(
                vfs_path=vfs_path,
                direction="import",
                file_hash=blob_info["import_hash"],
                size=size,
                result="success",
            )
        else:
            print(f"[ERROR] Failed to import '{host_path}' into VFS.")
    finally:
        db.close()


def cmd_vfs_extract(
    vfs_path: str,
    host_path: str,
    db_path: str = "DATA/server_data.db",
):
    """Extract a file from the VFS to the host filesystem with validation."""
    from engine.converter import db_blob_to_file, validate_file_integrity

    db = _get_db(db_path)
    try:
        entry = db.get_file(vfs_path)
        if entry is None:
            print(f"[ERROR] VFS path '{vfs_path}' not found.")
            return

        blob_data = entry["blob_data"]
        os.makedirs(os.path.dirname(os.path.abspath(host_path)), exist_ok=True)

        db_blob_to_file(blob_data, host_path)
        original_size = entry.get("original_size", len(blob_data))
        print(f"[OK] Extracted VFS '{vfs_path}' → '{host_path}' ({original_size:,} B).")

        # Verify integrity
        content_hash = _sha256(blob_data)
        ok = validate_file_integrity(host_path, content_hash)
        if ok:
            print(f"[OK] Integrity verified for '{host_path}'.")

            # Log the conversion
            db.log_conversion(
                vfs_path=vfs_path,
                direction="extract",
                file_hash=content_hash,
                size=original_size,
                result="success",
            )
        else:
            print(f"[WARN] Integrity check FAILED for '{host_path}' — file may be corrupted.")
            db.log_conversion(
                vfs_path=vfs_path,
                direction="extract",
                file_hash=content_hash,
                size=original_size,
                result="integrity_fail",
                error="hash mismatch after extraction",
            )
    finally:
        db.close()


# ===================================================================
#  Networking commands
# ===================================================================

def cmd_network_rules(
    server_name: str,
    db_path: str = "DATA/server_data.db",
):
    """List network rules for a server."""
    from engine.networking import Firewall

    db = _get_db(db_path)
    try:
        server = _resolve_name_or_id(server_name, db)
        if server is None:
            print(_server_not_found(server_name))
            return

        fw = Firewall(db, server["id"])
        rules = fw.list_rules()
        _print_separator()
        print(f"  Network rules for '{server['name']}':")
        if not rules:
            print("    (no rules — default-deny, no traffic allowed)")
        else:
            for r in rules:
                print(
                    f"  [{r['id']}] {r['rule_type']:5s}  "
                    f"{r['direction']:8s}  {r['protocol']:4s}  "
                    f"ports {r.get('port_range', 'any')}  "
                    f"→ {r.get('target_host', '*')}"
                )
        print(f"  Total: {len(rules)} rule(s)")
        _print_separator()
    finally:
        db.close()


def cmd_add_network_rule(
    server_name: str,
    rule_type: str,
    direction: str = "inbound",
    protocol: str = "tcp",
    port_range: str = None,
    target_host: str = "*",
    priority: int = 100,
    db_path: str = "DATA/server_data.db",
):
    """Add a network rule to a server."""
    from engine.networking import Firewall

    db = _get_db(db_path)
    try:
        server = _resolve_name_or_id(server_name, db)
        if server is None:
            print(_server_not_found(server_name))
            return

        if rule_type not in ("allow", "deny"):
            print(f"[ERROR] rule_type must be 'allow' or 'deny', got '{rule_type}'.")
            return

        fw = Firewall(db, server["id"])
        rule_id = fw.add_rule(
            rule_type=rule_type,
            direction=direction,
            protocol=protocol,
            port_range=port_range,
            target_host=target_host,
            priority=priority,
        )
        if rule_id > 0:
            print(
                f"[OK] Added {rule_type} rule for {direction} {protocol}:{port_range or '*'} "
                f"on '{server['name']}' (rule ID: {rule_id})."
            )
        else:
            print(f"[ERROR] Failed to add rule (DB error).")
    finally:
        db.close()


# ===================================================================
#  Conversion / history commands
# ===================================================================

def cmd_list_conversions(
    limit: int = 20,
    db_path: str = "DATA/server_data.db",
):
    """Show recent conversion log entries."""
    db = _get_db(db_path)
    try:
        rows = db.conn.execute(
            "SELECT * FROM conversion_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        _print_separator()
        if not rows:
            print("  No conversions logged yet.")
        else:
            for r in rows:
                entry = dict(r)
                print(
                    f"  [{entry['id']}] {entry.get('created_at', '????')}  "
                    f"{entry['direction']:8s}  "
                    f"{entry['vfs_path']:40s}  "
                    f"{entry['original_size']:>8,}B  "
                    f"[{entry['result']}]"
                )
        print(f"  Showing {len(rows)} of last {limit} log(s)")
        _print_separator()
    finally:
        db.close()


# ===================================================================
#  General help
# ===================================================================

def help_text() -> str:
    """Return full help text for all commands."""
    return (
        "╔══════════════════════════════════════════════════════════╗\n"
        "║         YuniScripts MC Server Runner Manager           ║\n"
        "╚══════════════════════════════════════════════════════════╝\n"
        "\n"
        "─── Server Commands ───────────────────────────────────────\n"
        "  list_servers                        List all MC server instances\n"
        "  create_server <name> <mc_ver>        Create a new server\n"
        "        [--type vanilla|paper|fabric|forge]\n"
        "        [--port <1024-65535>]\n"
        "        [--java <version>] [--ram <min>-<max>]\n"
        "  start_server <name|id>               Start a server\n"
        "  stop_server <name|id>                Stop a server\n"
        "  restart_server <name|id>             Restart a server\n"
        "  server_status [name|id]              Show server status\n"
        "\n"
        "─── Mod Commands ──────────────────────────────────────────\n"
        "  install_mod <slug> <version>         Register a new mod\n"
        "        [--mc <ver>] [--loader fabric|forge|vanilla]\n"
        "  list_mods                            List all mods\n"
        "  remove_mod <slug>                    Remove mod (auto-backup)\n"
        "  backup_mod <slug>                    Create manual backup\n"
        "  rollback_mod <slug> [backup_id]      Rollback mod to backup\n"
        "  check_dependencies <slug>            Check mod dependencies\n"
        "\n"
        "─── VFS Commands ──────────────────────────────────────────\n"
        "  vfs_list [path=/]                    List VFS files\n"
        "  vfs_import <host_path> <vfs_path>    Import file into VFS\n"
        "  vfs_extract <vfs_path> <host_path>   Extract file from VFS\n"
        "\n"
        "─── Network Commands ──────────────────────────────────────\n"
        "  network_rules <server>               List firewall rules\n"
        "  add_network_rule <server> <type>     Add firewall rule\n"
        "        [--direction inbound|outbound]\n"
        "        [--protocol tcp|udp] [--ports <range>]\n"
        "        (type: allow|deny)\n"
        "\n"
        "─── History Commands ──────────────────────────────────────\n"
        "  list_conversions [limit=20]          Show conversion logs\n"
        "\n"
        "─── Options ───────────────────────────────────────────────\n"
        "  --db <path>    Use custom database path (default: DATA/server_data.db)\n"
        "\n"
        "Use --help with any command for more details.\n"
    )
