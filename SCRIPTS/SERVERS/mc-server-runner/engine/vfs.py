"""
vfs.py — Virtual File System for MC Server Runner

Implements a simulated root / filesystem backed by SQLite (server_data.db).
All files are stored as compressed blobs in the DB and transparently
extracted to the VFS working directory when needed.

Key design:
  - Simulated root /: All VFS paths start with / (e.g. /servers/my-server/server.properties)
  - Physical backing: Files live as blobs in server_data.db vfs_nodes table
  - On-demand extraction: When a file is needed, it's pulled from DB to the physical VFS dir
  - Atomic writes: All modifications go through AtomicJournal for rollback safety
  - Integrity: Every extract verifies SHA-256 hash before writing
"""

import os
import json
import shutil
import logging
import tempfile
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any, BinaryIO
from datetime import datetime

from engine.database import get_db, Database
from engine.converter import file_to_db_blob, db_blob_to_file, bytes_to_db_blob, compress_data, decompress_data
from engine.atomic import get_journal, AtomicJournal

logger = logging.getLogger('mc-server-runner.vfs')


class VFSPathError(Exception):
    """Raised when a VFS path operation fails."""
    pass


class VFS:
    """
    Virtual File System with simulated root /.

    Maps simulated VFS paths (e.g. /servers/my-server/server.properties)
    to the physical filesystem under a configurable root directory.

    Operations:
      - read(path)        : Get file contents as bytes from DB
      - write(path, data) : Write bytes to DB via atomic journal
      - delete(path)      : Remove file from DB
      - exists(path)      : Check if path exists in DB
      - listdir(path)     : List files/dirs under a VFS path
      - mkdir(path)       : Create a directory node in VFS
      - extract(path)     : Pull file from DB to physical VFS location
      - import_file(path) : Push host file into VFS DB with conversion
      - mount(src, dest)  : Mount a host directory at a VFS path
    """

    def __init__(self, db: Database, vfs_root: str = "vfs", journal: AtomicJournal = None):
        """
        Initialize the VFS.

        Args:
            db: Database instance (server_data.db connection)
            vfs_root: Physical directory path for VFS file extraction
            journal: AtomicJournal instance (or None to auto-create)
        """
        self.db = db
        self.vfs_root = Path(vfs_root).resolve()
        self.journal = journal or AtomicJournal(db)
        self._ensure_vfs_root()

    def _ensure_vfs_root(self):
        """Create the physical VFS root directory if it doesn't exist."""
        self.vfs_root.mkdir(parents=True, exist_ok=True)
        logger.info(f"VFS root: {self.vfs_root}")

    def _vfs_to_physical(self, vfs_path: str) -> Path:
        """
        Convert a simulated VFS path (/path/to/file) to physical path.

        Args:
            vfs_path: Simulated path starting with / (e.g. /servers/my-server/config.yml)

        Returns:
            Physical Path object under vfs_root

        Raises:
            VFSPathError: If path doesn't start with /
        """
        if not vfs_path.startswith('/'):
            raise VFSPathError(f"VFS path must start with '/': {vfs_path}")
        # Strip leading / and join with physical root
        relative = vfs_path.lstrip('/')
        return self.vfs_root / relative

    def _normalize_path(self, vfs_path: str) -> str:
        """Normalize a VFS path, ensuring it starts with / and has no trailing slash."""
        vfs_path = '/' + vfs_path.lstrip('/')
        # Remove trailing slash except for root
        if len(vfs_path) > 1 and vfs_path.endswith('/'):
            vfs_path = vfs_path.rstrip('/')
        return vfs_path

    # ── Core DB Operations ──────────────────────────────────────────

    def read(self, vfs_path: str) -> Optional[bytes]:
        """
        Read a file from the VFS database.

        Args:
            vfs_path: VFS path (e.g. /servers/my-server/server.properties)

        Returns:
            File contents as bytes, or None if not found
        """
        vfs_path = self._normalize_path(vfs_path)
        record = self.db.get_raw_file(vfs_path)
        if record is None:
            return None
        # Decompress the blob data
        return decompress_data(record['blob_data'])

    def write(self, vfs_path: str, data: bytes, file_mode: str = '644',
              content_type: str = 'application/octet-stream',
              original_name: str = None, atomic: bool = True) -> bool:
        """
        Write data to the VFS database.

        If atomic=True, the operation goes through AtomicJournal for
        rollback safety. A before-state snapshot is captured.

        Args:
            vfs_path: VFS target path
            data: File content as bytes
            file_mode: Unix permission string (e.g. '644', '755')
            content_type: MIME type
            original_name: Original filename (for tracking)
            atomic: If True, use journal-based atomic write

        Returns:
            True if successful
        """
        try:
            vfs_path = self._normalize_path(vfs_path)

        except Exception as e:
            logger.error(f"write failed: {e}")
            return False
        before_state = None

        # Capture before state for atomic rollback
        if atomic:
            existing = self.db.get_file(vfs_path)
            if existing:
                before_state = {
                    'vfs_path': vfs_path,
                    'blob_data': existing['blob_data'].hex(),
                    'file_mode': existing['file_mode'],
                }

        #         # Store raw bytes in database (store_file handles compression internally)
        original_name_used = original_name or os.path.basename(vfs_path)
        self.db.store_file(
            vfs_path=vfs_path,
            blob_data=data,
            file_mode=file_mode,
            content_type=content_type,
            original_name=original_name_used,
        )
        
        # Log the conversion
        file_hash_val = hashlib.sha256(data).hexdigest()
        self.db.log_conversion(
            vfs_path=vfs_path,
            direction='import',
            file_hash=file_hash_val,
            size=len(data),
            result='success'
        )
        
        # Journal the atomic operation
        if atomic and before_state is not None:
            op_id = self.journal.begin('file_write', vfs_path, before_state)
            self.journal.commit(op_id)
        
        logger.debug(f"VFS write: {vfs_path} ({len(data)} bytes)")
        return True
    def delete(self, vfs_path: str, atomic: bool = True) -> bool:
        """
        Delete a file from the VFS database.

        Args:
            vfs_path: VFS path to delete
            atomic: If True, journal the operation for rollback

        Returns:
            True if deleted, False if not found
        """
        try:
            vfs_path = self._normalize_path(vfs_path)


            # Capture before state

        except Exception as e:
            logger.error(f"delete failed: {e}")
            return False
        before_state = None
        if atomic:
            existing = self.db.get_file(vfs_path)
            if existing:
                before_state = {
                    'vfs_path': vfs_path,
                    'blob_data': existing['blob_data'].hex(),
                    'file_mode': existing['file_mode'],
                }

        result = self.db.delete_file(vfs_path)
        if result and atomic and before_state:
            op_id = self.journal.begin('file_delete', vfs_path, before_state)
            self.journal.commit(op_id)

        # Also remove physical file if it exists
        physical = self._vfs_to_physical(vfs_path)
        if physical.exists():
            physical.unlink()

        return result

    def exists(self, vfs_path: str) -> bool:
        """Check if a path exists in the VFS database (file or directory)."""
        vfs_path = self._normalize_path(vfs_path)
        # Check file
        if self.db.get_file(vfs_path) is not None:
            return True
        # Check directory marker
        if self.db.get_file(vfs_path + '/.vfs_dir') is not None:
            return True
        return False

    def listdir(self, vfs_path: str = '/') -> List[Dict[str, Any]]:
        """
        List files and directories under a VFS path.

        Args:
            vfs_path: VFS directory path

        Returns:
            List of dicts with keys: path, type (file/dir), size, modified
        """
        try:
            vfs_path = self._normalize_path(vfs_path)

        except Exception as e:
            logger.error(f"listdir failed: {e}")
            return None
        prefix = vfs_path if vfs_path.endswith('/') else vfs_path + '/'
        if prefix == '//':
            prefix = '/'

        files = self.db.list_files(prefix)
        result = []
        seen_names = set()

        for f in files:
            rel_path = f['vfs_path'][len(prefix):] if f['vfs_path'].startswith(prefix) else f['vfs_path']
            if '/' in rel_path:
                # This is a nested file — add the directory entry
                dir_name = rel_path.split('/')[0]
                if dir_name not in seen_names:
                    result.append({
                        'path': prefix + dir_name,
                        'type': 'dir',
                        'size': 0,
                        'modified': f.get('updated_at', '')
                    })
                    seen_names.add(dir_name)
            else:
                result.append({
                    'path': f['vfs_path'],
                    'type': 'file',
                    'size': f.get('original_size', 0),
                    'modified': f.get('updated_at', '')
                })

        return result

    def mkdir(self, vfs_path: str) -> bool:
        """
        Create a directory node in the VFS.

        In the VFS, directories are implied by file paths. This method
        creates the physical directory under vfs_root for extraction
        and stores a directory marker in the DB so that exists() can
        find it.

        Args:
            vfs_path: VFS directory path

        Returns:
            True
        """
        vfs_path = self._normalize_path(vfs_path)
        physical = self._vfs_to_physical(vfs_path)
        physical.mkdir(parents=True, exist_ok=True)
        # Store a directory marker in DB so exists() can find directories
        self.db.store_file(
            vfs_path=vfs_path + '/.vfs_dir',
            blob_data=b'VFS_DIRECTORY',
            file_mode='755',
            content_type='inode/directory',
        )
        logger.debug(f"VFS mkdir: {vfs_path} -> {physical}")
        return True

    # ── Extraction / Import ────────────────────────────────────────

    def extract(self, vfs_path: str, output_path: str = None, validate: bool = True) -> Optional[str]:
        """
        Extract a file from the DB to the physical filesystem with integrity validation.

        Args:
            vfs_path: VFS path to extract
            output_path: Physical output path (default: VFS root + VFS path)
            validate: If True, verify SHA-256 hash after extraction

        Returns:
            Physical path of the extracted file, or None on failure

        Raises:
            VFSPathError: If file not found or hash validation fails
        """
        vfs_path = self._normalize_path(vfs_path)
        record = self.db.get_raw_file(vfs_path)
        if record is None:
            raise VFSPathError(f"File not found in VFS: {vfs_path}")

        # Determine output path
        if output_path is None:
            output_path = str(self._vfs_to_physical(vfs_path))

        # Ensure parent directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Get validation hash from vfs_metadata
        validation_hash = ''
        try:
            meta_row = self.db.conn.execute(
                "SELECT validation_hash FROM vfs_metadata WHERE node_id = ?",
                (record['id'],)
            ).fetchone()
            if meta_row:
                validation_hash = meta_row['validation_hash']
        except Exception:
            pass

        # Decompress and write
        try:
            db_blob_to_file(
                blob_data=record['blob_data'],
                validation_hash=validation_hash,
                output_path=output_path
            )

            # Apply file mode permission
            file_mode = record.get('file_mode', '644')
            try:
                os.chmod(output_path, int(file_mode, 8))
            except (ValueError, PermissionError):
                pass  # Best-effort permission setting

            # Log the extraction
            self.db.log_conversion(
                vfs_path=vfs_path,
                direction='extract',
                file_hash=validation_hash or record.get('import_hash', ''),
                size=record.get('original_size', 0),
                result='success'
            )

            # Post-extract validation (check original data hash)
            import_hash_check = ''
            try:
                meta_row2 = self.db.conn.execute(
                    "SELECT import_hash FROM vfs_metadata WHERE node_id = ?",
                    (record['id'],)
                ).fetchone()
                if meta_row2:
                    import_hash_check = meta_row2['import_hash']
            except Exception:
                pass
            if validate:
                if import_hash_check:
                    from engine.converter import validate_file_integrity
                    if not validate_file_integrity(output_path, import_hash_check):
                        logger.error(f"Integrity check FAILED for {vfs_path}")
                        # Remove the invalid file
                        os.unlink(output_path)
                        raise VFSPathError(f"Integrity validation failed: {vfs_path}")

            logger.info(f"VFS extract: {vfs_path} -> {output_path}")
            return output_path

        except (ValueError, IOError, OSError) as e:
            logger.error(f"VFS extract failed for {vfs_path}: {e}")
            raise VFSPathError(f"Extract failed: {e}")

    def extract_all(self, vfs_prefix: str = '/') -> int:
        """
        Extract ALL files under a VFS prefix to the physical filesystem.

        Args:
            vfs_prefix: VFS path prefix (e.g. /servers/my-server)

        Returns:
            Number of files extracted
        """
        vfs_prefix = self._normalize_path(vfs_prefix)
        files = self.db.list_files(vfs_prefix)
        count = 0

        for f in files:
            try:
                self.extract(f['vfs_path'])
                count += 1
            except VFSPathError as e:
                logger.warning(f"Skipping {f['vfs_path']}: {e}")

        logger.info(f"Extracted {count}/{len(files)} files from {vfs_prefix}")
        return count

    def import_file(self, host_path: str, vfs_path: str = None) -> str:
        """Import a file from the host filesystem into the VFS.

        The file is read, hashed, and stored in the database.
        store_file handles compression internally.

        Args:
            host_path: Absolute path to the file on the host
            vfs_path: Target VFS path (default: /<basename>)

        Returns:
            VFS path of the imported file

        Raises:
            VFSPathError: If host file doesn't exist or conversion fails
        """
        try:
            host = Path(host_path)
        except Exception as e:
            logger.error(f"import_file failed: {e}")
            return ""
        if not host.exists():
            raise VFSPathError(f"Host file not found: {host_path}")
        if not host.is_file():
            raise VFSPathError(f"Not a file: {host_path}")

        if vfs_path is None:
            vfs_path = '/' + host.name
        vfs_path = self._normalize_path(vfs_path)

        # Read raw bytes (store_file handles compression internally)
        raw_data = host.read_bytes()
        import_hash = hashlib.sha256(raw_data).hexdigest()

        # Store in database
        self.db.store_file(
            vfs_path=vfs_path,
            blob_data=raw_data,
            file_mode=oct(host.stat().st_mode)[-3:] if host.stat().st_mode else '644',
            content_type=self._guess_content_type(host.name),
            original_name=host.name,
            import_hash=import_hash,
        )

        # Log conversion
        self.db.log_conversion(
            vfs_path=vfs_path,
            direction='import',
            file_hash=import_hash,
            size=len(raw_data),
            result='success'
        )

        logger.info(f"VFS import: {host_path} -> {vfs_path} ({len(raw_data)} bytes)")
        return vfs_path

    def import_directory(self, host_dir: str, vfs_prefix: str = '/') -> int:
        """
        Import an entire directory tree into the VFS.

        Args:
            host_dir: Host directory path
            vfs_prefix: VFS target prefix

        Returns:
            Number of files imported
        """
        host = Path(host_dir)
        if not host.is_dir():
            raise VFSPathError(f"Not a directory: {host_dir}")

        vfs_prefix = self._normalize_path(vfs_prefix)
        count = 0

        for file_path in host.rglob('*'):
            if file_path.is_file():
                # Skip hidden files and __pycache__
                if any(p.startswith('.') for p in file_path.parts):
                    if not any(p in file_path.parts for p in ['.git', '__pycache__', 'node_modules']):
                        pass  # Only skip git/cache/node_modules
                rel_path = file_path.relative_to(host)
                vfs_target = f"{vfs_prefix}/{rel_path}".replace('\\', '/')
                try:
                    self.import_file(str(file_path), vfs_target)
                    count += 1
                except VFSPathError as e:
                    logger.warning(f"Skipping {file_path}: {e}")

        logger.info(f"Imported {count} files from {host_dir}")
        return count

    # ── Utility ────────────────────────────────────────────────────

    def _guess_content_type(self, filename: str) -> str:
        """Guess MIME type from filename extension."""
        ext = os.path.splitext(filename)[1].lower()
        types = {
            '.py': 'text/x-python',
            '.json': 'application/json',
            '.yml': 'text/yaml',
            '.yaml': 'text/yaml',
            '.toml': 'text/toml',
            '.xml': 'text/xml',
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.properties': 'text/x-java-properties',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jar': 'application/java-archive',
            '.zip': 'application/zip',
            '.gz': 'application/gzip',
            '.sh': 'text/x-shellscript',
            '.bat': 'application/x-bat',
            '.ps1': 'text/x-powershell',
        }
        return types.get(ext, 'application/octet-stream')

    def get_info(self, vfs_path: str) -> Optional[Dict[str, Any]]:
        """Get metadata info about a VFS node."""
        vfs_path = self._normalize_path(vfs_path)
        record = self.db.get_raw_file(vfs_path)
        if record is None:
            return None
        return {
            'vfs_path': record['vfs_path'],
            'size': record.get('original_size', 0),
            'mode': record.get('file_mode', '644'),
            'created': record.get('created_at', ''),
            'updated': record.get('updated_at', ''),
        }

    def commit_all(self) -> int:
        """Commit all pending atomic operations. Returns count committed."""
        pending = self.journal.get_pending()
        for op in pending:
            self.journal.commit(op['id'])
        return len(pending)

    def rollback_all(self) -> int:
        """Roll back all pending atomic operations. Returns count rolled back."""
        results = self.journal.rollback_all()
        return len(results)

    def cleanup(self):
        """Clean up VFS state (commit pending operations)."""
        self.commit_all()
        logger.info("VFS cleanup complete")

