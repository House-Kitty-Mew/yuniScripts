"""Mod manager for MC Server Runner."""
from __future__ import annotations

import os
import json
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from engine.database import Database
from engine.vfs import VFS

logger = logging.getLogger("mc-server-runner.mod_manager")


class ModError(Exception):
    """Raised when a mod operation fails."""
    pass


class ModLoaders:
    """Supported mod loader types."""
    FABRIC = 'fabric'
    FORGE = 'forge'
    QUILT = 'quilt'
    NEOFORGE = 'neoforge'
    VANILLA = 'vanilla'
    BUKKIT = 'bukkit'
    PAPER = 'paper'
    PURPUR = 'purpur'

    ALL = [FABRIC, FORGE, QUILT, NEOFORGE, VANILLA, BUKKIT, PAPER, PURPUR]


class ModManager:
    """
    Full mod lifecycle manager for MC Server Runner.

    Manages mod registry, dependency graphs, versioned backups,
    and compatibility validation.
    """

    def __init__(self, db: Database, vfs: VFS):
        """
        Initialize the mod manager.

        Args:
            db: Database instance
            vfs: VFS instance (mod files are stored in the VFS)
        """
        self.db = db
        self.vfs = vfs
        self._mods_dir = '/mods'  # VFS path for mod files

    # ── Registry Operations ────────────────────────────────────────

    def register_mod(self, name: str, slug: str, version: str,
                     mc_version: str, loader: str,
                     download_url: str = None, file_data: bytes = None,
                     file_path: str = None) -> int:
        """
        Register a new mod in the database.

        If file_data or file_path is provided, the mod file is also
        imported into the VFS.

        Args:
            name: Human-readable mod name
            slug: Unique identifier (e.g. 'fabric-api')
            version: Mod version string (e.g. '0.92.0+1.20.4')
            mc_version: Target Minecraft version (e.g. '1.20.4')
            loader: Mod loader type ('fabric', 'forge', etc.)
            download_url: Optional download URL
            file_data: Optional mod file content as bytes
            file_path: Optional path to mod file on host

        Returns:
            Mod ID in the database

        Raises:
            ModError: If slug already exists or loader is invalid
        """
        if loader not in ModLoaders.ALL:
            raise ModError(f"Invalid mod loader: {loader}. Must be one of {ModLoaders.ALL}")

        # Check for existing mod with same slug
        existing = self.db.get_mod(slug)
        if existing:
            raise ModError(f"Mod with slug '{slug}' already exists (ID={existing['id']})")

        # Create mod record
        mod_id = self.db.create_mod(
            name=name,
            slug=slug,
            version=version,
            mc_version=mc_version,
            loader=loader,
            download_url=download_url
        )

        # Import mod file if provided
        file_hash = None
        if file_data:
            file_hash = self._import_mod_file(mod_id, slug, file_data)
        elif file_path:
            with open(file_path, 'rb') as f:
                file_data = f.read()
                file_hash = self._import_mod_file(mod_id, slug, file_data)

        logger.info(f"Registered mod: {name} v{version} (MC {mc_version}, {loader}) [ID={mod_id}]")
        return mod_id

    def unregister_mod(self, slug: str, create_backup: bool = True) -> bool:
        """
        Remove a mod from the registry.

        Args:
            slug: Mod slug to remove
            create_backup: If True, create a final backup before removal

        Returns:
            True if removed

        Raises:
            ModError: If mod not found or has unresolved dependencies
        """
        mod = self.db.get_mod(slug)

        if not mod:
            raise ModError(f"Mod not found: {slug}")

        # Check for reverse dependencies (mods that depend on this one)
        dependents = self._get_dependents(mod['id'])
        if dependents:
            names = [d['name'] for d in dependents]
            raise ModError(
                f"Cannot remove '{slug}': {len(dependents)} mod(s) depend on it: {', '.join(names)}"
            )

        # Create final backup
        if create_backup:
            self.backup_mod(slug, notes=f"Pre-removal backup of {mod['version']}")

        # Remove mod file from VFS
        vfs_path = f"{self._mods_dir}/{slug}.jar"
        if self.vfs.exists(vfs_path):
            self.vfs.delete(vfs_path)

        # Delete from database
        return self.db.delete('mods', where={'id': mod['id']})

    def get_mod(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get mod details by slug or ID."""
        return self.db.get_mod(slug)

    def list_mods(self, server_id: int = None, mc_version: str = None,
                  loader: str = None, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """
        List all registered mods with optional filters.

        Args:
            server_id: Filter by server association
            mc_version: Filter by Minecraft version compatibility
            loader: Filter by mod loader
            enabled_only: Only show enabled mods

        Returns:
            List of mod dicts
        """
        return self.db.list_mods(
            server_id=server_id,
            mc_version=mc_version,
            loader=loader,
            enabled_only=enabled_only
        )

    # ── Dependency Management ──────────────────────────────────────

    def add_dependency(self, mod_slug: str, depends_on_slug: str,
                       required: bool = True) -> bool:
        """
        Add a dependency relationship between two mods.

        Args:
            mod_slug: The mod that has a dependency
            depends_on_slug: The mod that is depended upon
            required: True if required, False if optional

        Returns:
            True if added
        """
        mod = self.db.get_mod(mod_slug)
        dep = self.db.get_mod(depends_on_slug)

        if not mod:
            raise ModError(f"Mod not found: {mod_slug}")
        if not dep:
            raise ModError(f"Dependency mod not found: {depends_on_slug}")

        # Check for circular dependency
        if self._would_create_circle(mod_slug, depends_on_slug):
            raise ModError(f"Cannot add dependency: would create circular reference")

        self.db.execute(
            """INSERT OR IGNORE INTO mod_dependencies (mod_id, depends_on_mod_id, required)
            VALUES (?, ?, ?)""",
            (mod['id'], dep['id'], 1 if required else 0)
        )
        logger.info(f"Dependency: {mod_slug} -> {depends_on_slug} (required={required})")
        return True

    def check_dependencies(self, slug: str) -> List[Dict[str, Any]]:
        """
        Check all dependencies for a mod. Returns list of unmet dependencies.

        Args:
            slug: Mod slug to check

        Returns:
            List of dicts: {dep_slug, dep_name, required, met, version_mismatch}
        """
        mod = self.db.get_mod(slug)
        if not mod:
            raise ModError(f"Mod not found: {slug}")

        return self.db.check_dependencies(slug)

    def resolve_dependencies(self, slug: str) -> List[Dict[str, Any]]:
        """
        Recursively resolve all dependencies for a mod in topological order.

        Args:
            slug: Root mod slug

        Returns:
            List of mod dicts in dependency order (dependencies first)
        """
        visited = set()
        resolved = []

        def _resolve(current_slug: str, depth: int = 0):
            if depth > 50:  # Circular dependency protection
                raise ModError(f"Circular dependency detected at depth {depth}")

            if current_slug in visited:
                return
            visited.add(current_slug)

            mod = self.db.get_mod(current_slug)
            if not mod:
                return

            # Get direct dependencies
            deps = self.db.query(
                """SELECT m.slug FROM mod_dependencies d
                JOIN mods m ON m.id = d.depends_on_mod_id
                WHERE d.mod_id = ? AND d.required = 1""",
                (mod['id'],)
            )
            for (dep_slug,) in deps:
                _resolve(dep_slug, depth + 1)

            resolved.append(mod)

        _resolve(slug)
        return resolved

    def _would_create_circle(self, mod_slug: str, dep_slug: str) -> bool:
        """Check if adding dep_slug as a dependency of mod_slug would create a cycle."""
        mod = self.db.get_mod(dep_slug)

        if not mod:
            return False

        # Check if dep_slug already depends on mod_slug
        deps = self.db.query(
            """SELECT m.slug FROM mod_dependencies d
            JOIN mods m ON m.id = d.depends_on_mod_id
            WHERE d.mod_id = ?""",
            (mod['id'],)
        )
        for dep in deps:
            slug = dep['slug']
            if slug == mod_slug:
                return True
            if self._would_create_circle(mod_slug, slug):
                return True

        return False

    def _get_dependents(self, mod_id: int) -> List[Dict[str, Any]]:
        """Get mods that depend on the given mod."""
        rows = self.db.query(
            """SELECT m.* FROM mods m
            JOIN mod_dependencies d ON d.mod_id = m.id
            WHERE d.depends_on_mod_id = ?""",
            (mod_id,)
        )
        return [dict(r) for r in rows]

    # ── Version Compatibility ──────────────────────────────────────

    def check_mc_version_compatibility(self, mod_slug: str, mc_version: str) -> Tuple[bool, str]:
        """
        Check if a mod is compatible with a Minecraft version.

        Args:
            mod_slug: Mod slug
            mc_version: Target MC version (e.g. '1.20.4')

        Returns:
            (compatible: bool, message: str)
        """
        mod = self.db.get_mod(mod_slug)

        if not mod:
            return False, f"Mod not found: {mod_slug}"

        mod_mc = mod['mc_version']

        # Exact match
        if mod_mc == mc_version:
            return True, f"Exact match: {mod_mc}"

        # Check for partial match (e.g. 1.20 matches 1.20.4)
        mod_mc_base = '.'.join(mod_mc.split('.')[:2])
        mc_base = '.'.join(mc_version.split('.')[:2])
        if mod_mc_base == mc_base:
            return True, f"Partial match: {mod_mc} for {mc_version}"

        return False, f"Incompatible: {mod_mc} != {mc_version}"

    def check_loader_compatibility(self, mod_loader: str, server_loader: str) -> Tuple[bool, str]:
        """
        Check if a mod's loader is compatible with the server's loader.

        Args:
            mod_loader: Mod loader type
            server_loader: Server loader type

        Returns:
            (compatible: bool, message: str)
        """
        # Direct match
        if mod_loader == server_loader:
            return True, f"Same loader: {mod_loader}"

        # Loader compatibility groups
        compatible_groups = {
            ModLoaders.FABRIC: [ModLoaders.QUILT],
            ModLoaders.QUILT: [ModLoaders.FABRIC],
            ModLoaders.FORGE: [ModLoaders.NEOFORGE],
            ModLoaders.NEOFORGE: [ModLoaders.FORGE],
            ModLoaders.BUKKIT: [ModLoaders.PAPER, ModLoaders.PURPUR],
            ModLoaders.PAPER: [ModLoaders.BUKKIT, ModLoaders.PURPUR],
            ModLoaders.PURPUR: [ModLoaders.BUKKIT, ModLoaders.PAPER],
        }

        compat_list = compatible_groups.get(mod_loader, [])
        if server_loader in compat_list:
            return True, f"Compatible: {mod_loader} -> {server_loader}"

        return False, f"Incompatible loaders: {mod_loader} != {server_loader}"

    # ── Backup & Rollback ──────────────────────────────────────────

    def backup_mod(self, slug: str, notes: str = None) -> int:
        """
        Create a versioned backup of a mod.

        The backup stores the entire mod file as a blob in mod_backups table,
        allowing full restoration of any backed-up version.

        Args:
            slug: Mod slug
            notes: Optional notes about this backup

        Returns:
            Backup ID
        """
        mod = self.db.get_mod(slug)
        if not mod:
            raise ModError(f"Mod not found: {slug}")

        # Get mod file from VFS
        vfs_path = f"{self._mods_dir}/{slug}.jar"
        file_data = self.vfs.read(vfs_path)
        if file_data is None:
            # Mod exists in registry but no file — create backup metadata only
            backup_id = self.db.add_backup(
                mod_id=mod['id'],
                version=mod['version'],
                backup_blob=None,
                file_hash=None,
                notes=notes or f"Backup of {mod['version']} (no file)"
            )
        else:
            file_hash = hashlib.sha256(file_data).hexdigest()
            compressed = __import__('zlib').compress(file_data, level=6)
            backup_id = self.db.add_backup(
                mod_id=mod['id'],
                version=mod['version'],
                backup_blob=compressed,
                file_hash=file_hash,
                notes=notes or f"Backup of {mod['version']}"
            )

        logger.info(f"Backup created: {slug} v{mod['version']} [Backup ID={backup_id}]")
        return backup_id

    def list_backups(self, slug: str) -> List[Dict[str, Any]]:
        """List all backups for a mod."""
        mod = self.db.get_mod(slug)
        if not mod:
            raise ModError(f"Mod not found: {slug}")
        return self.db.list_backups(mod['id'])

    def rollback_mod(self, slug: str, backup_id: int = None) -> Dict[str, Any]:
        """
        Rollback a mod to a previous version using a stored backup.

        Args:
            slug: Mod slug
            backup_id: Specific backup ID to restore (default: most recent)

        Returns:
            Dict with rollback details (mod_slug, previous_version, restored_version, backup_id)

        Raises:
            ModError: If backup not found or restoration fails
        """
        mod = self.db.get_mod(slug)
        if not mod:
            raise ModError(f"Mod not found: {slug}")

        # Get the backup
        if backup_id:
            backups = self.db.query(
                "SELECT * FROM mod_backups WHERE id = ? AND mod_id = ?",
                (backup_id, mod['id'])
            )
        else:
            # Most recent backup
            backups = self.db.query(
                "SELECT * FROM mod_backups WHERE mod_id = ? ORDER BY created_at DESC LIMIT 1",
                (mod['id'],)
            )

        if not backups:
            raise ModError(f"No backup found for mod '{slug}'" +
                           (f" with ID {backup_id}" if backup_id else ""))

        backup = dict(backups[0])
        previous_version = mod['version']
        restored_version = backup['version']

        # Restore file data from backup blob
        if backup.get('backup_blob'):
            decompressed = __import__('zlib').decompress(backup['backup_blob'])
            # Verify hash
            if backup.get('file_hash'):
                actual_hash = hashlib.sha256(decompressed).hexdigest()
                if actual_hash != backup['file_hash']:
                    raise ModError(f"Backup hash mismatch: expected {backup['file_hash']}, got {actual_hash}")

            # Write to VFS
            vfs_path = f"{self._mods_dir}/{slug}.jar"
            self.vfs.write(vfs_path, decompressed, atomic=True)

        # Update mod version in registry
        self.db.execute(
            "UPDATE mods SET version = ?, updated_at = datetime('now') WHERE id = ?",
            (restored_version, mod['id'])
        )

        logger.info(
            f"Rollback complete: {slug} {previous_version} -> {restored_version} "
            f"[Backup ID={backup['id']}]"
        )

        return {
            'mod_slug': slug,
            'mod_name': mod['name'],
            'previous_version': previous_version,
            'restored_version': restored_version,
            'backup_id': backup['id'],
        }

    def auto_backup_before_update(self, slug: str) -> Optional[int]:
        """
        Automatically create a backup before updating a mod.
        Called by the update workflow.

        Args:
            slug: Mod slug to backup

        Returns:
            Backup ID or None if mod doesn't exist
        """
        mod = self.db.get_mod(slug)
        if not mod:
            return None
        return self.backup_mod(slug, notes=f"Auto-backup before update of {mod['version']}")

    # ── Mod Files ──────────────────────────────────────────────────

    def _import_mod_file(self, mod_id: int, slug: str, file_data: bytes) -> str:
        """Import mod file data into the VFS."""
        vfs_path = f"{self._mods_dir}/{slug}.jar"
        self.vfs.write(vfs_path, file_data, file_mode='644',
                       content_type='application/java-archive',
                       original_name=f"{slug}.jar")
        return hashlib.sha256(file_data).hexdigest()

    def install_mod_to_server(self, slug: str, server_id: int) -> bool:
        """
        Install a registered mod into a server's mods directory.

        The mod file is extracted from the VFS mods storage to the
        server's mods folder.

        Args:
            slug: Mod slug
            server_id: Server instance ID

        Returns:
            True if installed

        Raises:
            ModError: If mod not found or server not found
        """
        mod = self.db.get_mod(slug)

        if not mod:
            raise ModError(f"Mod not found: {slug}")

        server = self.db.get_server(server_id)
        if not server:
            raise ModError(f"Server not found: ID={server_id}")

        # Check compatibility
        compat, msg = self.check_mc_version_compatibility(slug, server['mc_version'])
        if not compat:
            raise ModError(f"Version incompatibility: {msg}")

        compat2, msg2 = self.check_loader_compatibility(mod['loader'], server['server_type'])
        if not compat2:
            logger.warning(f"Loader warning for {slug}: {msg2}")

        # Check dependencies first
        unmet = self.db.check_dependencies(slug)
        if unmet:
            raise ModError(
                f"Unmet dependencies for {slug}: " +
                ", ".join(f"{d['dep_slug']} (required={d['required']})" for d in unmet)
            )

        # Extract mod file to server's mods VFS directory
        mod_vfs_path = f"{self._mods_dir}/{slug}.jar"
        server_mods_dir = f"/servers/{server['name']}/mods"

        # Ensure server mods directory exists in VFS
        self.vfs.mkdir(server_mods_dir)

        # Copy mod file to server mods directory
        mod_data = self.vfs.read(mod_vfs_path)
        if mod_data:
            self.vfs.write(f"{server_mods_dir}/{slug}.jar", mod_data, atomic=True)

            logger.info(f"Installed mod {slug} v{mod['version']} to server '{server['name']}'")
            return True

        logger.warning(f"Failed to install mod {slug}: mod file not found in VFS")
        return False

    def remove_mod_from_server(self, slug: str, server_id: int) -> bool:
        """Remove a mod from a server's mods directory."""
        server = self.db.get_server(server_id)
        if not server:
            raise ModError(f"Server not found: ID={server_id}")

        vfs_path = f"/servers/{server['name']}/mods/{slug}.jar"
        if self.vfs.exists(vfs_path):
            self.vfs.delete(vfs_path)
            logger.info(f"Removed mod {slug} from server '{server['name']}'")
            return True
        return False

    def list_server_mods(self, server_id: int) -> List[Dict[str, Any]]:
        """List all mods installed on a server."""
        server = self.db.get_server(server_id)

        if not server:
            raise ModError(f"Server not found: ID={server_id}")

        mods_dir = f"/servers/{server['name']}/mods"
        files = self.vfs.listdir(mods_dir)
        mods = []
        for f in files:
            if f['type'] == 'file' and f['path'].endswith('.jar'):
                slug = os.path.splitext(os.path.basename(f['path']))[0]
                mod_info = self.db.get_mod(slug)
                if mod_info:
                    mods.append(mod_info)
                else:
                    mods.append({'slug': slug, 'name': slug, 'version': 'unknown'})

        return mods

    # ── Bulk Operations ────────────────────────────────────────────

    def install_server_modpack(self, server_id: int, mod_slugs: List[str]) -> Dict[str, Any]:
        """
        Install a set of mods (modpack) to a server with dependency resolution.

        Args:
            server_id: Server instance ID
            mod_slugs: List of mod slugs to install

        Returns:
            Dict with results: {installed: [...], failed: [...], skipped: [...]}
        """
        results = {'installed': [], 'failed': [], 'skipped': []}

        # Resolve all dependencies
        all_slugs = list(mod_slugs)
        for slug in mod_slugs:
            try:
                resolved = self.resolve_dependencies(slug)
                for m in resolved:
                    if m['slug'] not in all_slugs:
                        all_slugs.append(m['slug'])
            except ModError as e:
                results['failed'].append({'slug': slug, 'reason': str(e)})

        # Install each mod
        for slug in all_slugs:
            try:
                self.install_mod_to_server(slug, server_id)
                results['installed'].append(slug)
            except ModError as e:
                results['failed'].append({'slug': slug, 'reason': str(e)})

        logger.info(
            f"Modpack install: {len(results['installed'])} installed, "
            f"{len(results['failed'])} failed, {len(results['skipped'])} skipped"
        )
        return results

    def upgrade_mod(self, slug: str, new_version: str, new_file_data: bytes = None) -> Dict[str, Any]:
        """
        Upgrade a mod to a new version with automatic backup.

        Args:
            slug: Mod slug
            new_version: New version string
            new_file_data: New mod file data (or None to keep existing)

        Returns:
            Dict with upgrade details
        """
        mod = self.db.get_mod(slug)
        if not mod:
            raise ModError(f"Mod not found: {slug}")

        old_version = mod['version']

        # Auto-backup before upgrade
        backup_id = self.auto_backup_before_update(slug)

        # Update version in registry
        self.db.execute(
            "UPDATE mods SET version = ?, updated_at = datetime('now') WHERE id = ?",
            (new_version, mod['id'])
        )

        # Update file if provided
        if new_file_data:
            vfs_path = f"{self._mods_dir}/{slug}.jar"
            self.vfs.write(vfs_path, new_file_data, atomic=True)

        logger.info(f"Upgraded {slug}: {old_version} -> {new_version}")
        return {
            'slug': slug,
            'name': mod['name'],
            'old_version': old_version,
            'new_version': new_version,
            'backup_id': backup_id,
        }
