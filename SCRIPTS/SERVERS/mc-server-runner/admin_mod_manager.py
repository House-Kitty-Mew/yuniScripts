"""
admin_mod_manager.py — Dedicated Mod Management Admin Section

Integrates with the yuniscripts admin GUI to provide a complete
mod management interface for mc-server-runner.

Provides:
  - Dashboard: overview of all mods, servers, and their relationships
  - Mod lifecycle: install, update, backup, rollback, uninstall
  - Dependency viewer: visual dependency graph
  - Compatibility checker: validates MC version + loader compatibility
  - Bulk operations: modpack install, mass backup
  - Cache management: Modrinth/CurseForge cache operations
"""

import os
import sys
import json
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

logger = logging.getLogger('mc-server-runner.admin_mod_manager')

# Ensure engine is importable
_ENGINE_DIR = os.path.join(os.path.dirname(__file__), 'engine')
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))


class ModAdmin:
    """Mod management admin interface for yuniscripts GUI."""

    def __init__(self, db_path: str = None, vfs_root: str = None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), 'DATA', 'server_data.db')
        if vfs_root is None:
            vfs_root = os.path.join(os.path.dirname(__file__), 'vfs')

        from engine.database import get_db
        from engine.vfs import VFS
        from engine.mod_manager import ModManager
        from engine.mod_cache import ModCache

        self.db = get_db(db_path)
        self.vfs = VFS(self.db, vfs_root=vfs_root)
        self.mm = ModManager(self.db, self.vfs)
        self.cache = ModCache(self.db, self.vfs)

    def close(self):
        """Close database connection."""
        try:
            self.db.close()
        except Exception:
            pass

    # ═══════════════════════════════════════════════
    # DASHBOARD
    # ═══════════════════════════════════════════════

    def dashboard(self) -> dict:
        """Full admin dashboard with summary stats."""
        servers = self.db.list_servers()
        mods = self.mm.list_mods()
        backups = self.db.query("SELECT COUNT(*) as count FROM mod_backups")[0]['count']
        deps = self.db.query("SELECT COUNT(*) as count FROM mod_dependencies")[0]['count']

        per_server = []
        for s in servers:
            smods = self.mm.list_server_mods(s['id']) or []
            status = self.db.get_config(s['id'], 'status', 'stopped')
            per_server.append({
                'id': s['id'], 'name': s['name'],
                'type': s['server_type'], 'mc': s['mc_version'],
                'mod_count': len(smods), 'status': status,
            })

        # Mods with unmet dependencies
        mods_with_issues = []
        for m in mods:
            try:
                unmet = self.mm.check_dependencies(m['slug'])
                if unmet:
                    mods_with_issues.append({
                        'slug': m['slug'], 'name': m['name'],
                        'unmet': unmet,
                    })
            except Exception:
                pass

        return {
            'total_servers': len(servers),
            'total_mods': len(mods),
            'total_backups': backups,
            'total_dependencies': deps,
            'servers': per_server,
            'mods_with_issues': mods_with_issues,
            'timestamp': datetime.now().isoformat(),
        }

    # ═══════════════════════════════════════════════
    # MOD LIFECYCLE
    # ═══════════════════════════════════════════════

    def list_mods(self, server_id: int = None) -> List[dict]:
        """List all mods, optionally filtered by server."""
        return self.mm.list_mods(server_id=server_id)

    def register_mod(self, name: str, slug: str, version: str,
                     mc_version: str, loader: str,
                     file_path: str = None, file_data: bytes = None,
                     download_from: str = None) -> dict:
        """Register a new mod with optional download."""
        try:
            if download_from == 'modrinth':
                from engine.mod_cache import ModCache
                result = self.cache.install_mod_from_cache(
                    slug, mc_version, loader, server_id=None)
                return {'success': True, 'mod': result, 'method': 'modrinth'}

            mod_id = self.mm.register_mod(
                name, slug, version, mc_version, loader,
                file_data=file_data, file_path=file_path,
            )
            return {'success': True, 'mod_id': mod_id}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def install_mod_to_server(self, slug: str, server_name: str,
                               resolve_remote_deps: bool = True) -> dict:
        """
        Install a mod to a server, optionally resolving remote dependencies.

        When resolve_remote_deps is True, the method:
          1. Gets server info (MC version, loader type)
          2. Resolves the mod's remote dependencies from Modrinth
          3. Downloads and installs all required dependencies
          4. Installs the requested mod itself

        Args:
            slug: Mod slug to install
            server_name: Server name to install to
            resolve_remote_deps: If True, auto-resolve + install dependencies

        Returns:
            Dict with install results including dep tree if resolved.
        """
        try:
            server = self.db.get_server(server_name)
            if not server:
                return {'success': False, 'error': f"Server '{server_name}' not found"}

            if resolve_remote_deps:
                # Resolve and install mod + all remote dependencies
                dep_result = self.cache.install_with_dependencies(
                    slug,
                    server['mc_version'],
                    server['server_type'],
                    server['id'],
                )
                return {
                    'success': True,
                    'slug': slug,
                    'server': server_name,
                    'dep_result': dep_result,
                }
            else:
                # Legacy: install registered mod only (local deps)
                self.mm.install_mod_to_server(slug, server['id'])
                return {'success': True, 'slug': slug, 'server': server_name}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def remove_mod(self, slug: str, keep_backup: bool = True) -> dict:
        """Unregister a mod with optional backup."""
        try:
            self.mm.unregister_mod(slug, create_backup=keep_backup)
            return {'success': True, 'slug': slug, 'backup_created': keep_backup}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def upgrade_mod(self, slug: str, new_version: str,
                    file_data: bytes = None) -> dict:
        """Upgrade a mod with automatic backup."""
        try:
            result = self.mm.upgrade_mod(slug, new_version, file_data)
            return {
                'success': True,
                'slug': slug,
                'old_version': result.get('old_version'),
                'new_version': result.get('new_version'),
                'backup_id': result.get('backup_id'),
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════
    # BACKUP & ROLLBACK
    # ═══════════════════════════════════════════════

    def create_backup(self, slug: str, notes: str = None) -> dict:
        """Create a manual backup of a mod."""
        try:
            bid = self.mm.backup_mod(slug, notes=notes)
            return {'success': True, 'backup_id': bid, 'slug': slug}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def list_backups(self, slug: str) -> dict:
        """List all backups for a mod."""
        try:
            backups = self.mm.list_backups(slug)
            return {'success': True, 'backups': backups}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def rollback_mod(self, slug: str, backup_id: int = None) -> dict:
        """Rollback a mod to a previous version."""
        try:
            result = self.mm.rollback_mod(slug, backup_id)
            return {
                'success': True,
                'slug': slug,
                'previous': result.get('previous_version'),
                'restored': result.get('restored_version'),
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════
    # DEPENDENCY MANAGEMENT
    # ═══════════════════════════════════════════════

    def view_dependencies(self, slug: str) -> dict:
        """View full dependency tree for a mod."""
        try:
            mod = self.mm.get_mod(slug)
            if not mod:
                return {'success': False, 'error': f"Mod not found: {slug}"}

            # Direct deps
            deps = self.db.query(
                """SELECT md.required, m.slug, m.name, m.version, m.mc_version, m.loader
                   FROM mod_dependencies md
                   JOIN mods m ON m.id = md.depends_on_mod_id
                   WHERE md.mod_id = ?""",
                (mod['id'],)
            )

            # Dependents (mods that depend on this)
            dependents = self.db.query(
                """SELECT m.slug, m.name, m.version
                   FROM mod_dependencies md
                   JOIN mods m ON m.id = md.mod_id
                   WHERE md.depends_on_mod_id = ?""",
                (mod['id'],)
            )

            return {
                'success': True,
                'mod': {'slug': mod['slug'], 'name': mod['name'],
                        'version': mod['version']},
                'depends_on': deps,
                'depended_by': dependents,
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def add_dependency(self, mod_slug: str, dep_slug: str,
                       required: bool = True) -> dict:
        """Add a dependency link between two mods."""
        try:
            self.mm.add_dependency(mod_slug, dep_slug, required)
            return {'success': True, 'mod': mod_slug, 'dep': dep_slug}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def remove_dependency(self, mod_slug: str, dep_slug: str) -> dict:
        """Remove a dependency link."""
        try:
            mod = self.mm.get_mod(mod_slug)
            dep = self.mm.get_mod(dep_slug)
            if not mod or not dep:
                return {'success': False, 'error': 'Mod not found'}
            self.db.execute(
                "DELETE FROM mod_dependencies WHERE mod_id=? AND depends_on_mod_id=?",
                (mod['id'], dep['id']),
            )
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════
    # COMPATIBILITY CHECKER
    # ═══════════════════════════════════════════════

    def check_compatibility(self, slug: str, mc_version: str = None,
                            server_loader: str = None) -> dict:
        """Check MC version + loader compatibility for a mod."""
        try:
            results = {}
            if mc_version:
                compat, msg = self.mm.check_mc_version_compatibility(slug, mc_version)
                results['mc_version'] = {'compatible': compat, 'message': msg}
            if server_loader:
                mod = self.mm.get_mod(slug)
                if mod:
                    compat, msg = self.mm.check_loader_compatibility(
                        mod['loader'], server_loader)
                    results['loader'] = {'compatible': compat, 'message': msg}
            return {'success': True, 'slug': slug, 'checks': results}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════
    # BULK OPERATIONS
    # ═══════════════════════════════════════════════

    def install_modpack(self, server_name: str, mod_slugs: List[str]) -> dict:
        """Install multiple mods at once with dependency resolution."""
        try:
            server = self.db.get_server(server_name)
            if not server:
                return {'success': False, 'error': f"Server not found: {server_name}"}
            result = self.mm.install_server_modpack(server['id'], mod_slugs)
            return {
                'success': True,
                'server': server_name,
                'installed': result.get('installed', []),
                'failed': result.get('failed', []),
                'skipped': result.get('skipped', []),
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def mass_backup(self, slugs: List[str] = None) -> dict:
        """Backup all mods, or a specified subset."""
        try:
            if slugs:
                mods = [{'slug': s} for s in slugs]
            else:
                mods = self.mm.list_mods()

            results = []
            for m in mods:
                try:
                    bid = self.mm.backup_mod(m['slug'], notes='Mass backup')
                    results.append({'slug': m['slug'], 'backup_id': bid, 'success': True})
                except Exception as e:
                    results.append({'slug': m['slug'], 'success': False, 'error': str(e)})

            return {'success': True, 'results': results,
                    'total': len(results), 'ok': sum(1 for r in results if r['success'])}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════
    # CACHE MANAGEMENT
    # ═══════════════════════════════════════════════

    def clear_cache(self, older_than_days: int = 30) -> dict:
        """Clear old cached mod downloads."""
        try:
            self.cache.clear_cache(older_than_days=older_than_days)
            return {'success': True, 'message': f'Cache cleared (>{older_than_days} days)'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def search_modrinth(self, query: str, loader: str = None,
                        mc_version: str = None, limit: int = 20) -> dict:
        """Search Modrinth for mods."""
        try:
            results = self.cache.modrinth_search(query, loaders=[loader] if loader else None,
                                                  mc_version=mc_version, limit=limit)
            return {'success': True, 'results': results}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def download_from_modrinth(self, slug: str, mc_version: str,
                                loader: str,
                                resolve_deps: bool = True) -> dict:
        """
        Download a mod directly from Modrinth.

        If resolve_deps is True (default), also downloads and installs
        any required dependencies the mod declares on Modrinth.

        Args:
            slug: Mod slug on Modrinth
            mc_version: Target Minecraft version
            loader: Mod loader (e.g. 'fabric', 'forge')
            resolve_deps: If True, recursively install remote dependencies

        Returns:
            Dict with 'success', and either 'result' or 'error'.
            When resolve_deps=True, 'result' includes the dependency tree.
        """
        try:
            if resolve_deps:
                dep_result = self.cache.install_with_dependencies(
                    slug, mc_version, loader, server_id=None)
                return {
                    'success': True,
                    'result': {
                        'slug': slug,
                        'installed': dep_result.get('installed', []),
                        'optional': dep_result.get('optional', []),
                        'incompatible': dep_result.get('incompatible', []),
                        'failed': dep_result.get('failed', []),
                        'depth': dep_result.get('depth', 0),
                    }
                }
            else:
                result = self.cache.modrinth_download(slug, mc_version, loader)
                return {'success': True, 'result': result}
        except Exception as e:
            return {'success': False, 'error': str(e)}


# ═══════════════════════════════════════════════
# CLI INTERFACE (for yuniscripts admin shell)
# ═══════════════════════════════════════════════

def print_dashboard(data: dict):
    """Print formatted dashboard to console."""
    print(f"\n{'='*60}")
    print(f"  MC SERVER RUNNER — MOD ADMIN DASHBOARD")
    print(f"  {data['timestamp']}")
    print(f"{'='*60}")
    print(f"\n  Summary:")
    print(f"    • Servers:      {data['total_servers']}")
    print(f"    • Mods:         {data['total_mods']}")
    print(f"    • Backups:      {data['total_backups']}")
    print(f"    • Dependencies: {data['total_dependencies']}")
    if data.get('servers'):
        print(f"\n  Servers:")
        for s in data['servers']:
            icon = '🔴' if s['status'] == 'running' else '⚫'
            print(f"    {icon} [{s['id']}] {s['name']} — {s['type']} {s['mc']} ({s['mod_count']} mods)")
    if data.get('mods_with_issues'):
        print(f"\n  ⚠ Mods with issues:")
        for m in data['mods_with_issues']:
            print(f"    • {m['name']} ({m['slug']}): {len(m['unmet'])} unmet deps")
    print()


def print_mod_table(mods: List[dict], title: str = "Mods"):
    """Print formatted mod list table."""
    if not mods:
        print(f"  No {title.lower()} found.")
        return
    print(f"\n  {title}:")
    print(f"  {'ID':>3} {'Name':<22} {'Slug':<18} {'Version':<12} {'MC':<8} {'Loader':<8}")
    print(f"  {'-'*75}")
    for m in mods:
        print(f"  {m.get('id', '?'):>3} {m.get('name', '?'):<22} "
              f"{m.get('slug', '?'):<18} {m.get('version', '?'):<12} "
              f"{m.get('mc_version', '?'):<8} {m.get('loader', '?'):<8}")
    print()


def print_backup_table(backups: List[dict]):
    """Print formatted backup list."""
    if not backups:
        print("  No backups found.")
        return
    print(f"\n  Backups:")
    print(f"  {'ID':>3} {'Version':<14} {'Created':<22} {'Notes'}")
    print(f"  {'-'*60}")
    for b in backups:
        print(f"  {b.get('id', '?'):>3} {b.get('version', '?'):<14} "
              f"{b.get('created_at', '?'):<22} {b.get('notes', '')}")
    print()


if __name__ == '__main__':
    """CLI entry point for admin mod management."""
    import argparse
    parser = argparse.ArgumentParser(prog='mod-admin',
        description='MC Server Runner — Mod Management Admin')
    parser.add_argument('action', nargs='?', default='dashboard',
        choices=['dashboard', 'list', 'register', 'remove', 'backup',
                 'rollback', 'deps', 'install', 'compat', 'search'])
    parser.add_argument('--slug', help='Mod slug')
    parser.add_argument('--server', help='Server name')
    parser.add_argument('--name', help='Mod name')
    parser.add_argument('--version', help='Mod version')
    parser.add_argument('--mc', help='Minecraft version')
    parser.add_argument('--loader', help='Mod loader')
    parser.add_argument('--db', help='Database path')
    parser.add_argument('--backup-id', type=int, help='Backup ID for rollback')

    args = parser.parse_args()
    admin = ModAdmin(db_path=args.db)

    try:
        if args.action == 'dashboard':
            data = admin.dashboard()
            print_dashboard(data)

        elif args.action == 'list':
            server = admin.db.get_server(args.server) if args.server else None
            sid = server['id'] if server else None
            mods = admin.list_mods(server_id=sid)
            print_mod_table(mods, f"Mods{f' on {args.server}' if args.server else ''}")

        elif args.action == 'register':
            if not all([args.name, args.slug, args.version, args.mc, args.loader]):
                print("Error: --name, --slug, --version, --mc, --loader required")
            else:
                result = admin.register_mod(args.name, args.slug, args.version,
                                           args.mc, args.loader)
                print(json.dumps(result, indent=2))

        elif args.action == 'remove':
            if not args.slug:
                print("Error: --slug required")
            else:
                result = admin.remove_mod(args.slug)
                print(json.dumps(result, indent=2))

        elif args.action == 'backup':
            if not args.slug:
                print("Error: --slug required")
            else:
                result = admin.create_backup(args.slug)
                print(json.dumps(result, indent=2))

        elif args.action == 'rollback':
            if not args.slug:
                print("Error: --slug required")
            else:
                result = admin.rollback_mod(args.slug, args.backup_id)
                print(json.dumps(result, indent=2))

        elif args.action == 'deps':
            if not args.slug:
                # List all mods with issues
                data = admin.dashboard()
                if data['mods_with_issues']:
                    for m in data['mods_with_issues']:
                        print(f"\n  ⚠ {m['name']} ({m['slug']}):")
                        for d in m['unmet']:
                            print(f"      - {d['dep_slug']} (required={d['required']})")
                else:
                    print("  All dependencies are met.")
            else:
                result = admin.view_dependencies(args.slug)
                if result['success']:
                    print(f"\n  {result['mod']['name']} ({result['mod']['slug']} v{result['mod']['version']})")
                    if result['depends_on']:
                        print(f"  Depends on:")
                        for d in result['depends_on']:
                            req = 'required' if d['required'] else 'optional'
                            print(f"    • {d['name']} ({d['slug']}) {d['version']} — {req}")
                    if result['depended_by']:
                        print(f"  Depended by:")
                        for d in result['depended_by']:
                            print(f"    • {d['name']} ({d['slug']})")
                else:
                    print(f"  Error: {result['error']}")

        elif args.action == 'install':
            if not args.slug or not args.server:
                print("Error: --slug and --server required")
            else:
                result = admin.install_mod_to_server(args.slug, args.server)
                print(json.dumps(result, indent=2))

        elif args.action == 'compat':
            if not args.slug:
                print("Error: --slug required")
            else:
                result = admin.check_compatibility(args.slug, args.mc, args.loader)
                print(json.dumps(result, indent=2))

        elif args.action == 'search':
            from engine.mod_cache import ModCache
            cache = ModCache(admin.db, admin.vfs)
            results = cache.modrinth_search(args.slug or '', mc_version=args.mc, limit=10)
            print(json.dumps(results, indent=2) if results else "No results")

    finally:
        admin.close()
