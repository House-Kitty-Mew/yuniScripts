"""
main.py — MC Server Runner Manager (YuniScripts Entry Point)

This is the entry point managed by the YuniScripts engine's process_wrapper.
It provides:
  - CLI interface for server management
  - Phooks integration for inter-script communication
  - All VFS, networking, mod, and runner subsystems

Usage (from YuniScripts CLI):
  python main.py list                         # List all servers
  python main.py create <name> [--mc 1.20.4]  # Create new server
  python main.py start <name>                 # Start server
  python main.py stop <name>                  # Stop server
  python main.py restart <name>               # Restart server
  python main.py status [name]                # Server status
  python main.py import <host_path> [vfs_path] # Import file to VFS
  python main.py extract <vfs_path> [host_path]# Extract file from VFS
  python main.py mod list [server]            # List mods
  python main.py mod install <slug> <server>  # Install mod
  python main.py mod remove <slug> <server>   # Remove mod
  python main.py mod backup <slug>            # Backup mod
  python main.py mod rollback <slug> [backup_id] # Rollback mod
  python main.py mod deps <slug>              # Check deps
  python main.py network rules <server>        # List network rules
  python main.py network allow <server> <port> # Add allow rule
  python main.py vfs ls [path]                # List VFS
  python main.py vfs tree [path]              # Tree view of VFS
  python main.py conversions                  # Show conversion log
"""

import os
import signal
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

# ── Dynamic Config Loader (Admin GUI API) ─────────────────────────
_DYNAMIC_CONFIG_AVAILABLE = False
try:
    _TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / 'SERVICES' / 'fastmcp_server' / 'tools'
    if str(_TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(_TOOLS_DIR))
    from dynamic_config_loader import (
        register_configs, get_config, update_config,
        get_all_configs, load_source, flush_source, on_config_change,
    )
    _DYNAMIC_CONFIG_AVAILABLE = True
except ImportError:
    pass

# ── Register mc-server-runner configs for Admin GUI ──────────────
if _DYNAMIC_CONFIG_AVAILABLE:
    register_configs("mc-server-runner", [
        {"key": "default_mc_version", "type": "str", "default": "1.20.4",
         "description": "Default Minecraft version for new servers",
         "category": "general"},
        {"key": "default_java", "type": "str", "default": "java",
         "description": "Java executable path",
         "category": "general"},
        {"key": "max_concurrent_servers", "type": "int", "default": 1,
         "description": "Maximum concurrent server instances",
         "valid_range": (1, 10), "category": "performance"},
        {"key": "enable_networking_sandbox", "type": "bool", "default": True,
         "description": "Enable virtual networking sandbox (iptables)",
         "category": "security"},
        {"key": "enable_mod_auto_backup", "type": "bool", "default": True,
         "description": "Auto-backup mods before updates/removals",
         "category": "performance"},
        {"key": "max_backups_per_mod", "type": "int", "default": 10,
         "description": "Maximum backup versions per mod",
         "valid_range": (1, 100), "category": "performance"},
        {"key": "atomic_timeout_seconds", "type": "int", "default": 30,
         "description": "VFS atomic operation timeout",
         "valid_range": (5, 300), "category": "performance"},
        {"key": "log_level", "type": "str", "default": "INFO",
         "description": "Logging level",
         "valid_options": ["DEBUG", "INFO", "WARNING", "ERROR"],
         "category": "debug"},
        {"key": "db_path", "type": "str", "default": "DATA/server_data.db",
         "description": "Server database path (relative to script dir)",
         "category": "general"},
        {"key": "vfs_root", "type": "str", "default": "vfs",
         "description": "VFS root directory (relative to script dir)",
         "category": "general"},
        {"key": "temp_dir", "type": "str", "default": "vfs/temp",
         "description": "VFS temp directory",
         "category": "general"},
        {"key": "mods_dir", "type": "str", "default": "vfs/mods",
         "description": "Mod storage directory",
         "category": "general"},
        {"key": "servers_dir", "type": "str", "default": "vfs/servers",
         "description": "Server files directory",
         "category": "general"},
        {"key": "backup_dir", "type": "str", "default": "vfs/backups",
         "description": "Backup storage directory",
         "category": "general"},
    ])
    # Register change listener for log_level to update live
    def _on_log_level_change(source, key, old_val, new_val):
        if key == "log_level":
            logging.getLogger('mc-server-runner').setLevel(getattr(logging, new_val.upper(), logging.INFO))
    on_config_change("mc-server-runner", "log_level", _on_log_level_change)

# Add engine to path
_ENGINE_DIR = Path(__file__).parent / 'engine'
sys.path.insert(0, str(_ENGINE_DIR.parent))

# Engine imports
from engine.database import get_db, Database
from engine.vfs import VFS
from engine.converter import file_to_db_blob, db_blob_to_file
from engine.atomic import get_journal
from engine.mod_manager import ModManager
from engine.networking import NetworkManager
from engine.runner import ServerRunner
from engine.server_profiles import ServerProfile
from engine.mod_cache import ModCache

# ── Logging Setup ──────────────────────────────────────────────

LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'mcsr.log'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('mc-server-runner')


# ── Configuration ──────────────────────────────────────────────

def load_config() -> dict:
    """Load configuration from DATA/config.json, merging with dynamic config values."""
    config_path = Path(__file__).parent / 'DATA' / 'config.json'
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {
            'db_path': 'DATA/server_data.db',
            'vfs_root': 'vfs',
            'default_mc_version': '1.20.4',
        }

    # Merge in dynamic config values (admin GUI overrides take precedence)
    if _DYNAMIC_CONFIG_AVAILABLE:
        for key in config:
            dcl_val = get_config("mc-server-runner", key)
            if dcl_val is not None:
                config[key] = dcl_val
        # Also attempt to load persisted config from DATA/
        try:
            load_source("mc-server-runner")
        except Exception:
            pass

    return config


# ── Database / VFS Factory ─────────────────────────────────────

def get_subsystems(config: dict = None):
    """Create and return (db, vfs, mod_manager) instances."""
    if config is None:
        config = load_config()
    base = Path(__file__).parent
    db_path = str(base / config.get('db_path', 'DATA/server_data.db'))
    vfs_root = str(base / config.get('vfs_root', 'vfs'))

    db = get_db(db_path)
    vfs = VFS(db, vfs_root=vfs_root)
    mod_manager = ModManager(db, vfs)
    return db, vfs, mod_manager


# ── CLI Commands ───────────────────────────────────────────────

def cmd_list_servers(args, config):
    """List all registered server instances."""
    db, vfs, mm = get_subsystems(config)
    servers = db.list_servers()
    if not servers:
        print("No servers registered.")
        print("Use: python main.py create <name> [--mc 1.20.4]")
        return

    print(f"\n{'ID':>3} {'Name':<20} {'MC Version':<12} {'Type':<10} {'Port':<7} {'Status':<10}")
    print('-' * 65)
    for s in servers:
        status = db.get_config(s['id'], 'status', 'stopped')
        running = ServerRunner.get_running(s['id'])
        if running:
            status = 'RUNNING'
        print(f"{s['id']:>3} {s['name']:<20} {s['mc_version']:<12} {s['server_type']:<10} {s['server_port']:<7} {status:<10}")
    print()
    db.close()


def cmd_create_server(args, config):
    """Create a new server instance."""
    db, vfs, mm = get_subsystems(config)
    name = args.name
    mc_version = args.mc or config.get('default_mc_version', '1.20.4')
    server_type = args.type or 'vanilla'

    sid = db.create_server(
        name=name,
        mc_version=mc_version,
        server_type=server_type,
        server_port=args.port or 25565,
        rcon_port=args.rcon_port or 25575,
        rcon_password=args.rcon_pass or '',
        min_ram=args.min_ram or '2G',
        max_ram=args.max_ram or '4G',
    )

    # Create VFS directory structure
    vfs.mkdir(f'/servers/{name}')
    vfs.mkdir(f'/servers/{name}/mods')
    vfs.mkdir(f'/servers/{name}/plugins')

    print(f"Server '{name}' created (ID={sid}, MC {mc_version}, {server_type})")
    print(f"  Port: {args.port or 25565}")
    print(f"  RCON Port: {args.rcon_port or 25575}")
    db.close()


def cmd_setup_server(args, config):
    """Auto-download and configure a server."""
    db, vfs, mm = get_subsystems(config)
    server = db.get_server(args.name)
    if not server:
        print(f'Server not found: {args.name}')
        db.close()
        return

    try:
        profile = ServerProfile(args.type or server['server_type'],
                                args.name,
                                args.mc or server['mc_version'])
        result = profile.setup(db, vfs, server['id'], str(Path.cwd()))
        print(f'Setup complete for {args.name}:')
        for step in result.get('steps', []):
            print(f'  - {step}')
    except Exception as e:
        print(f'Setup failed: {e}')
    db.close()


def cmd_start_server(args, config):
    """Start a server instance."""
    db, vfs, mm = get_subsystems(config)
    server = db.get_server(args.name)
    if not server:
        print(f"Server not found: {args.name}")
        db.close()
        return

    try:
        runner = ServerRunner(db, vfs, server['id'])
        runner.start()
        print(f"Server '{args.name}' started (PID={runner.get_status().get('pid', '?')})")
    except Exception as e:
        print(f"Failed to start server: {e}")
    finally:
        # Don't close db if running - runner needs it
        pass


def cmd_stop_server(args, config):
    """Stop a server instance.

    Works both in-process (via ServerRunner) and cross-process (via DB PID lookup).
    """
    try:
        db, vfs, mm = get_subsystems(config)

    except Exception as e:
        logger.error(f"cmd_stop_server failed: {e}")
        return None
    running = ServerRunner.get_running()
    target = None
    for r in running:
        if r['name'] == args.name:
            target = r['server_id']
            break

    if target is not None:
        runner = ServerRunner._running_servers.get(target)
        if runner:
            runner.stop(timeout=args.timeout or 30, force=args.force or False)
            print(f"Server '{args.name}' stopped")
            db.close()
            return

    # Cross-process stop: read PID from DB and send SIGTERM directly
    server = db.get_server(args.name)
    if not server:
        print(f"Server '{args.name}' not found")
        db.close()
        return

    server_id = server['id']
    pid_str = db.get_config(server_id, 'pid')
    if not pid_str:
        print(f"Server '{args.name}' has no PID record (may already be stopped)")
        db.close()
        return

    try:
        pid = int(pid_str)
        os.kill(pid, 0)  # Check if process exists (no-op signal)
        print(f"Sending SIGTERM to {args.name} (PID {pid})...")
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for graceful shutdown
        import time
        for _ in range(args.timeout or 30):
            try:
                os.kill(pid, 0)
                time.sleep(1)
            except ProcessLookupError:
                break
        else:
            # Force kill if still running
            try:
                os.kill(pid, 0)
                print(f"{args.name} did not stop gracefully, sending SIGKILL...")
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        db.set_config(server_id, 'status', 'stopped')
        db.set_config(server_id, 'pid', '')
        db.set_config(server_id, 'stopped_at', datetime.now().isoformat())
        print(f"Server '{args.name}' stopped")
    except ProcessLookupError:
        # Process already dead - clean up DB state
        print(f"Server '{args.name}' (PID {pid}) is not running, cleaning up DB state")
        db.set_config(server_id, 'status', 'stopped')
        db.set_config(server_id, 'pid', '')
        db.set_config(server_id, 'stopped_at', datetime.now().isoformat())
    except (ValueError, OSError) as e:
        print(f"Failed to stop server '{args.name}': {e}")
    finally:
        db.close()


def cmd_restart_server(args, config):
    """Restart a server instance."""
    db, vfs, mm = get_subsystems(config)
    server = db.get_server(args.name)
    if not server:
        print(f'Server not found: {args.name}')
        db.close()
        return
    try:
        runner = ServerRunner(db, vfs, server['id'])
        runner.restart()
        print(f'Server {args.name} restarted')
    except Exception as e:
        print(f'Restart failed: {e}')
    db.close()


def cmd_status(args, config):
    """Show server status."""
    try:
        db, vfs, mm = get_subsystems(config)

    except Exception as e:
        logger.error(f"cmd_status failed: {e}")
        return None
    running = {r['server_id']: r for r in ServerRunner.get_running()}

    if args.name:
        server = db.get_server(args.name)
        if not server:
            print(f"Server not found: {args.name}")
            db.close()
            return
        servers = [server]
    else:
        servers = db.list_servers()

    if not servers:
        print("No servers registered.")
        db.close()
        return

    print(f"\n{'Name':<20} {'Status':<10} {'PID':<8} {'Up Since':<22} {'Port':<7}")
    print('-' * 70)
    for s in servers:
        r = running.get(s['id'])
        if r:
            status = 'RUNNING'
            pid = str(r.get('pid', '?'))
            uptime = r.get('started_at', '')[:19]
        else:
            status = db.get_config(s['id'], 'status', 'stopped')
            pid = '-'
            uptime = '-'
        print(f"{s['name']:<20} {status:<10} {pid:<8} {uptime:<22} {s['server_port']:<7}")
    print()
    db.close()


def cmd_import(args, config):
    """Import a file from host to VFS."""
    db, vfs, mm = get_subsystems(config)
    vfs_path = args.vfs_path or f"/{os.path.basename(args.host_path)}"
    try:
        result = vfs.import_file(args.host_path, vfs_path)
        print(f"Imported: {args.host_path} -> {result}")
    except Exception as e:
        print(f"Import failed: {e}")
    db.close()


def cmd_extract(args, config):
    """Extract a file from VFS to host."""
    db, vfs, mm = get_subsystems(config)
    output = args.host_path or None
    try:
        result = vfs.extract(args.vfs_path, output)
        print(f"Extracted: {args.vfs_path} -> {result}")
    except Exception as e:
        print(f"Extract failed: {e}")
    db.close()


def cmd_mod(args, config):
    """Mod management subcommands."""
    db, vfs, mm = get_subsystems(config)

    if args.mod_action == 'list':
        if args.server_name:
            server = db.get_server(args.server_name)
            if not server:
                print(f"Server not found: {args.server_name}")
                db.close()
                return
            mods = mm.list_server_mods(server['id'])
        else:
            mods = mm.list_mods()

        if not mods:
            print("No mods found.")
        else:
            print(f"\n{'Name':<25} {'Slug':<20} {'Version':<16} {'MC':<10} {'Loader':<10}")
            print('-' * 80)
            for m in mods:
                print(f"{m['name']:<25} {m['slug']:<20} {m.get('version','?'):<16} {m.get('mc_version','?'):<10} {m.get('loader','?'):<10}")
            print()

    elif args.mod_action == 'install':
        if not args.mod_slug or not args.server_name:
            print("Usage: mod install <slug> <server>")
        else:
            server = db.get_server(args.server_name)
            if not server:
                print(f"Server not found: {args.server_name}")
            else:
                try:
                    mm.install_mod_to_server(args.mod_slug, server['id'])
                    print(f"Mod '{args.mod_slug}' installed to '{args.server_name}'")
                except Exception as e:
                    print(f"Install failed: {e}")

    elif args.mod_action == 'remove':
        if not args.mod_slug:
            print("Usage: mod remove <slug>")
        else:
            try:
                mm.unregister_mod(args.mod_slug, create_backup=True)
                print(f"Mod '{args.mod_slug}' unregistered (backup created)")
            except Exception as e:
                print(f"Remove failed: {e}")

    elif args.mod_action == 'backup':
        if not args.mod_slug:
            print("Usage: mod backup <slug>")
        else:
            try:
                bid = mm.backup_mod(args.mod_slug, notes="Manual backup")
                print(f"Backup created for '{args.mod_slug}' (ID={bid})")
            except Exception as e:
                print(f"Backup failed: {e}")

    elif args.mod_action == 'rollback':
        if not args.mod_slug:
            print("Usage: mod rollback <slug> [backup_id]")
        else:
            try:
                result = mm.rollback_mod(args.mod_slug, args.backup_id)
                print(f"Rolled back: {result['mod_name']} {result['previous_version']} -> {result['restored_version']}")
            except Exception as e:
                print(f"Rollback failed: {e}")

    elif args.mod_action == 'deps':
        if not args.mod_slug:
            print("Usage: mod deps <slug>")
        else:
            try:
                unmet = mm.check_dependencies(args.mod_slug)
                if unmet:
                    print(f"Unmet dependencies for '{args.mod_slug}':")
                    for d in unmet:
                        print(f"  - {d['dep_slug']} (required={d['required']}, met={d.get('met', False)})")
                else:
                    print(f"All dependencies met for '{args.mod_slug}'")
            except Exception as e:
                print(f"Dependency check failed: {e}")

    db.close()


def cmd_network(args, config):
    """Network management subcommands."""
    try:
        db, vfs, mm = get_subsystems(config)

    except Exception as e:
        logger.error(f"cmd_network failed: {e}")
        return None

    if not args.network_action:
        print("Usage: network <rules|allow|deny> <server> [options]")
        db.close()
        return

    server = db.get_server(args.server_name) if args.server_name else None
    if not server:
        print(f"Server not found: {args.server_name}")
        db.close()
        return

    nm = NetworkManager(db, server['id'])

    if args.network_action == 'rules':
        rules = nm.firewall.list_rules()
        print(f"\nNetwork rules for '{server['name']}':")
        if not rules:
            print("  No rules configured.")
        else:
            print(f"{'ID':>3} {'Type':<8} {'Direction':<10} {'Protocol':<8} {'Port/Ports':<12} {'Target':<20}")
            print('-' * 65)
            for r in rules:
                print(f"{r['id']:>3} {r['rule_type']:<8} {r['direction']:<10} {r['protocol']:<8} {r.get('port_range',''):<12} {r.get('target_host','*'):<20}")
            print()

    elif args.network_action == 'allow':
        # Allow a port
        port = args.port
        if port:
            nm.firewall.add_rule(rule_type='allow', port_range=str(port))
            print(f"Allow rule added for port {port} on '{server['name']}'")
        else:
            print("Usage: network allow <server> --port <port>")

    elif args.network_action == 'deny':
        port = args.port
        if port:
            nm.firewall.add_rule(rule_type='deny', port_range=str(port))
            print(f"Deny rule added for port {port} on '{server['name']}'")
        else:
            print("Usage: network deny <server> --port <port>")

    db.close()


def cmd_vfs(args, config):
    """VFS exploration subcommands."""
    db, vfs, mm = get_subsystems(config)

    if args.vfs_action == 'ls':
        path = args.vfs_path or '/'
        try:
            items = vfs.listdir(path)
            if not items:
                print(f"(empty) {path}")
            else:
                print(f"\nVFS: {path}")
                print(f"{'Type':<6} {'Size':<10} {'Name'}")
                print('-' * 50)
                for item in items:
                    name = os.path.basename(item['path'])
                    sz = item.get('size', 0)
                    if item['type'] == 'dir':
                        print(f"{'[DIR]':<6} {'-':<10} {name}/")
                    else:
                        if sz > 1024 * 1024:
                            size_str = f"{sz / 1024 / 1024:.1f}MB"
                        elif sz > 1024:
                            size_str = f"{sz / 1024:.1f}KB"
                        else:
                            size_str = f"{sz}B"
                        print(f"{'[FILE]':<6} {size_str:<10} {name}")
                print()
        except Exception as e:
            print(f"VFS ls failed: {e}")

    elif args.vfs_action == 'tree':
        path = args.vfs_path or '/'
        try:
            _print_vfs_tree(vfs, path, 0)
        except Exception as e:
            print(f"VFS tree failed: {e}")

    db.close()


def _print_vfs_tree(vfs, path: str, depth: int, prefix: str = ''):
    """Recursive VFS tree printer."""
    items = vfs.listdir(path)
    for i, item in enumerate(items):
        is_last = i == len(items) - 1
        connector = '└── ' if is_last else '├── '
        name = os.path.basename(item['path'])
        if item['type'] == 'dir':
            print(f"{prefix}{connector}{name}/")
            extension = '    ' if is_last else '│   '
            next_path = f"{path.rstrip('/')}/{name}"
            _print_vfs_tree(vfs, next_path, depth + 1, prefix + extension)
        else:
            sz = item.get('size', 0)
            sz_str = f"{sz}B" if sz < 1024 else f"{sz/1024:.1f}KB" if sz < 1024*1024 else f"{sz/1024/1024:.1f}MB"
            print(f"{prefix}{connector}{name} ({sz_str})")


def cmd_conversions(args, config):
    """Show conversion log."""
    db, vfs, mm = get_subsystems(config)
    limit = args.limit or 20
    rows = db.query(
        "SELECT * FROM conversion_log ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    if not rows:
        print("No conversions logged.")
    else:
        print(f"\nRecent conversions (last {limit}):")
        print(f"{'ID':>3} {'Direction':<10} {'Path':<45} {'Size':<10} {'Result':<10}")
        print('-' * 80)
        for r in rows:
            r = dict(r)
            print(f"{r['id']:>3} {r['direction']:<10} {r['vfs_path'][:42]:<45} {r.get('original_size', 0):<10} {r.get('result', ''):<10}")
        print()
    db.close()


# ── Argument Parser ───────────────────────────────────────────


def cmd_admin(args, config):
    """Admin mod management interface."""
    try:
        from admin_mod_manager import ModAdmin, print_dashboard, print_mod_table, print_backup_table
    except ImportError as e:
        print(f"Admin module not available: {e}")
        print("Run: pip install -r requirements.txt")
        return

    base = Path(__file__).parent
    admin = ModAdmin(
        db_path=str(base / config.get('db_path', 'DATA/server_data.db')),
        vfs_root=str(base / config.get('vfs_root', 'vfs')),
    )

    try:
        if args.admin_action == 'dashboard':
            data = admin.dashboard()
            print_dashboard(data)

        elif args.admin_action == 'list':
            server = admin.db.get_server(args.server_name) if args.server_name else None
            sid = server['id'] if server else None
            mods = admin.list_mods(server_id=sid)
            print_mod_table(mods, f"Mods{f' on {args.server_name}' if args.server_name else ''}")

        elif args.admin_action == 'register':
            if not all([args.mod_name, args.mod_slug, args.mod_version, args.mc_version, args.loader]):
                print("Error: --name, --slug, --version, --mc, --loader are all required")
                return
            result = admin.register_mod(
                args.mod_name, args.mod_slug, args.mod_version,
                args.mc_version, args.loader,
                file_path=args.file,
            )
            print(json.dumps(result, indent=2))

        elif args.admin_action == 'remove':
            if not args.mod_slug:
                print("Error: --slug required")
                return
            result = admin.remove_mod(args.mod_slug)
            print(json.dumps(result, indent=2))

        elif args.admin_action == 'backup':
            if not args.mod_slug:
                print("Error: --slug required")
                return
            result = admin.create_backup(args.mod_slug, notes=args.notes)
            print(json.dumps(result, indent=2))

        elif args.admin_action == 'rollback':
            if not args.mod_slug:
                print("Error: --slug required")
                return
            result = admin.rollback_mod(args.mod_slug, args.backup_id)
            print(json.dumps(result, indent=2))

        elif args.admin_action == 'deps':
            if args.mod_slug:
                result = admin.view_dependencies(args.mod_slug)
                if result['success']:
                    print(f"\n  {result['mod']['name']} ({result['mod']['slug']} v{result['mod']['version']})")
                    if result['depends_on']:
                        print(f"  Depends on:")
                        for d in result['depends_on']:
                            req = 'required' if d['required'] else 'optional'
                            print(f"    * {d['name']} ({d['slug']}) {d['version']} - {req}")
                    if result['depended_by']:
                        print(f"  Depended by:")
                        for d in result['depended_by']:
                            print(f"    * {d['name']} ({d['slug']})")
                else:
                    print(f"  Error: {result['error']}")
            else:
                data = admin.dashboard()
                if data['mods_with_issues']:
                    for m in data['mods_with_issues']:
                        print(f"\n  WARNING {m['name']} ({m['slug']}):")
                        for d in m['unmet']:
                            print(f"      - {d['dep_slug']} (required={d['required']})")
                else:
                    print("  All dependencies are met.")

        elif args.admin_action == 'install':
            if not args.mod_slug or not args.server_name:
                print("Error: --slug and --server required")
                return
            result = admin.install_mod_to_server(args.mod_slug, args.server_name)
            print(json.dumps(result, indent=2))

        elif args.admin_action == 'compat':
            if not args.mod_slug:
                print("Error: --slug required")
                return
            result = admin.check_compatibility(args.mod_slug, args.mc_version, args.loader)
            print(json.dumps(result, indent=2))

        elif args.admin_action == 'search':
            result = admin.search_modrinth(
                query=args.query or args.mod_slug or '',
                loader=args.loader, mc_version=args.mc_version, limit=20,
            )
            if result['success'] and result['results']:
                print(f"\n  Search results for '{args.query or args.mod_slug}':")
                for r in result['results'][:10]:
                    print(f"    * {r.get('title', '?')} ({r.get('slug', '?')}) - "
                          f"{r.get('downloads', 0)} downloads")
            else:
                print("  No results or search failed.")

        elif args.admin_action == 'mass-backup':
            slugs = args.mod_slug.split(',') if args.mod_slug else None
            result = admin.mass_backup(slugs)
            print(json.dumps(result, indent=2))

        elif args.admin_action == 'clear-cache':
            result = admin.clear_cache(older_than_days=args.days or 30)
            print(json.dumps(result, indent=2))

        elif args.admin_action == 'server-mods':
            if not args.server_name:
                print("Error: --server required")
                return
            server = admin.db.get_server(args.server_name)
            if not server:
                print(f"Server not found: {args.server_name}")
                return
            smods = admin.mm.list_server_mods(server['id']) or []
            if not smods:
                print(f"  No mods installed on '{args.server_name}'.")
            else:
                print(f"\n  Mods installed on '{args.server_name}':")
                print(f"  {'Name':<22} {'Slug':<18} {'Version':<12}")
                print(f"  {'-'*55}")
                for m in smods:
                    print(f"  {m.get('name', '?'):<22} {m.get('slug', '?'):<18} {m.get('version', '?'):<12}")
                print()

        else:
            print(f"Unknown admin action: {args.admin_action}")

    finally:
        admin.close()


def build_parser():
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog='mc-server-runner',
        description='MC Server Runner Manager — Sandboxed Minecraft server management',
    )
    parser.add_argument('--config', help='Path to config file')

    sub = parser.add_subparsers(dest='command', help='Command')

    # list
    p_list = sub.add_parser('list', help='List all servers')

    # create
    p_create = sub.add_parser('create', help='Create a new server')
    p_create.add_argument('name', help='Server name')
    p_create.add_argument('--mc', help='Minecraft version (e.g. 1.20.4)')
    p_create.add_argument('--type', dest='type', default='vanilla',
                          choices=['vanilla', 'paper', 'spigot', 'fabric', 'forge'])
    p_create.add_argument('--port', type=int, default=25565, help='Server port')
    p_create.add_argument('--rcon-port', type=int, default=25575, help='RCON port')
    p_create.add_argument('--rcon-pass', help='RCON password')
    p_create.add_argument('--min-ram', default='2G', help='Min RAM (e.g. 2G)')
    p_create.add_argument('--max-ram', default='4G', help='Max RAM (e.g. 4G)')

    # setup
    p_setup = sub.add_parser('setup', help='Auto-download and configure a server')
    p_setup.add_argument('name', help='Server name')
    p_setup.add_argument('--type', dest='type', default=None,
                          choices=['vanilla', 'fabric', 'quilt', 'forge', 'neoforge', 'paper', 'purpur'])
    p_setup.add_argument('--mc', help='MC version (e.g. 1.20.4)')

    # start
    p_start = sub.add_parser('start', help='Start a server')
    p_start.add_argument('name', help='Server name')

    # stop
    p_stop = sub.add_parser('stop', help='Stop a server')
    p_stop.add_argument('name', help='Server name')
    p_stop.add_argument('--timeout', type=int, default=30, help='Graceful stop timeout')
    p_stop.add_argument('--force', action='store_true', help='Force kill')

    # restart
    p_restart = sub.add_parser('restart', help='Restart a server')
    p_restart.add_argument('name', help='Server name')

    # status
    p_status = sub.add_parser('status', help='Show server status')
    p_status.add_argument('name', nargs='?', help='Server name (optional)')

    # import
    p_import = sub.add_parser('import', help='Import file to VFS')
    p_import.add_argument('host_path', help='Host file path')
    p_import.add_argument('vfs_path', nargs='?', help='VFS target path')

    # extract
    p_extract = sub.add_parser('extract', help='Extract file from VFS')
    p_extract.add_argument('vfs_path', help='VFS file path')
    p_extract.add_argument('host_path', nargs='?', help='Host output path')

    # mod
    p_mod = sub.add_parser('mod', help='Mod management')
    p_mod.add_argument('mod_action', choices=['list', 'install', 'remove', 'backup', 'rollback', 'deps'])
    p_mod.add_argument('mod_slug', nargs='?', help='Mod slug')
    p_mod.add_argument('server_name', nargs='?', help='Server name')
    p_mod.add_argument('--backup-id', dest='backup_id', type=int, help='Backup ID for rollback')

    # network
    p_net = sub.add_parser('network', help='Network management')
    p_net.add_argument('network_action', choices=['rules', 'allow', 'deny'])
    p_net.add_argument('server_name', nargs='?', help='Server name')
    p_net.add_argument('--port', type=int, help='Port number')

    # admin
    p_admin = sub.add_parser('admin', help='Admin mod management')
    p_admin.add_argument('admin_action', 
        choices=['dashboard', 'list', 'register', 'remove', 'backup',
                 'rollback', 'deps', 'install', 'compat', 'search',
                 'mass-backup', 'clear-cache', 'server-mods'])
    p_admin.add_argument('mod_slug', nargs='?', help='Mod slug')
    p_admin.add_argument('server_name', nargs='?', help='Server name')
    p_admin.add_argument('--mod-name', dest='mod_name', help='Mod name (for register)')
    p_admin.add_argument('--mod-version', dest='mod_version', help='Mod version')
    p_admin.add_argument('--mc-version', dest='mc_version', help='Minecraft version')
    p_admin.add_argument('--loader', help='Mod loader (fabric, forge, etc.)')
    p_admin.add_argument('--file', help='Path to mod file')
    p_admin.add_argument('--backup-id', dest='backup_id', type=int, help='Backup ID')
    p_admin.add_argument('--notes', help='Backup notes')
    p_admin.add_argument('--query', help='Search query')
    p_admin.add_argument('--days', type=int, help='Days threshold for cache clear')

    # vfs
    p_vfs = sub.add_parser('vfs', help='VFS exploration')
    p_vfs.add_argument('vfs_action', choices=['ls', 'tree'])
    p_vfs.add_argument('vfs_path', nargs='?', default='/', help='VFS path')

    # conversions
    p_conv = sub.add_parser('conversions', help='Show conversion log')
    p_conv.add_argument('--limit', type=int, default=20, help='Limit results')

    return parser


# ── Main Entry Point ──────────────────────────────────────────

def main():
    """Main entry point for YuniScripts."""
    config = load_config()

    # Sync dynamic config values from loaded config
    if _DYNAMIC_CONFIG_AVAILABLE:
        for key, val in config.items():
            update_config("mc-server-runner", key, val, changed_by="startup")

    parser = build_parser()
    args = parser.parse_args()

    # Log startup
    logger.info(f"MC Server Runner v1.0.0 starting...")
    logger.info(f"Config: db={config.get('db_path')}, vfs={config.get('vfs_root')}")

    # Dispatch commands
    handlers = {
        'list': cmd_list_servers,
        'create': cmd_create_server,
        'start': cmd_start_server,
        'stop': cmd_stop_server,
        'restart': cmd_restart_server,
        'status': cmd_status,
        'import': cmd_import,
        'extract': cmd_extract,
        'mod': cmd_mod,
        'network': cmd_network,
        'vfs': cmd_vfs,
        'setup': cmd_setup_server,
        'conversions': cmd_conversions,
        'admin': cmd_admin,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args, config)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()

