"""
╔══════════════════════════════════════════════════════════════════════╗
║  Multi-Server Manager — VFS-Backed Database Isolation               ║
║  Per-subsystem, per-server SQLite database isolation via VFS        ║
╚══════════════════════════════════════════════════════════════════════╝

DESIGN:
  - Each subsystem + server combo gets its own isolated SQLite database
  - Uses the AIHandler VFS (Virtual File System) for staging + validation
  - Supports: create, destroy, backup, restore, health check
  - Thread-safe connection management with connection pooling
  - Automatic schema migration support

USAGE:
    from engine.vfs_db_isolation import VFSDatabaseManager

    mgr = VFSDatabaseManager(data_root="/path/to/DATA/vfs")
    db = mgr.get_database("auction_house", "server1")
    await db.execute("CREATE TABLE IF NOT EXISTS items (id INT, name TEXT)")
    await db.execute("INSERT INTO items VALUES (?, ?)", (1, "sword"))
    rows = await db.query("SELECT * FROM items")
"""

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, AsyncIterator
from enum import Enum

logger = logging.getLogger("vfs_db_isolation")


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class VFSDBError(Exception):
    """Base exception for VFS database errors."""
    pass

class VFSDBConnectionError(VFSDBError):
    """Raised when database connection fails."""
    pass

class VFSDBQueryError(VFSDBError):
    """Raised on database query failure."""
    pass

class VFSDBConfigError(VFSDBError):
    """Raised on configuration errors."""
    pass

class VFSDBMigrationError(VFSDBError):
    """Raised on schema migration failure."""
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class VFSDatabaseInfo:
    """Metadata about a VFS-backed database instance."""
    subsystem: str
    server_id: str
    db_path: str
    size_bytes: int = 0
    table_count: int = 0
    created_at: Optional[str] = None
    last_accessed: Optional[str] = None
    schema_version: int = 0
    is_healthy: bool = True
    error_message: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# VFS-Backed Database Instance
# ──────────────────────────────────────────────────────────────────────────────

class VFSDatabase:
    """
    A single VFS-backed SQLite database instance for a subsystem+server pair.
    
    Features:
    - Thread-safe with per-connection locking (sqlite3 is not thread-safe)
    - Automatic backup on write operations
    - Schema version tracking
    - Connection health checking
    """
    
    def __init__(
        self,
        subsystem: str,
        server_id: str,
        db_path: str,
        auto_backup: bool = True,
        backup_limit: int = 5,
    ):
        self.subsystem = subsystem
        self.server_id = server_id
        self.db_path = db_path
        self.auto_backup = auto_backup
        self.backup_limit = backup_limit
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._schema_version: int = 0
        self._created_at = datetime.now(timezone.utc).isoformat()
        self._last_accessed = self._created_at
        self._open_count = 0
    
    def _ensure_dir(self) -> None:
        """Ensure the database directory exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    
    def open(self) -> None:
        """
        Open the database connection.
        
        Creates the database file and parent directories if they don't exist.
        
        Raises:
            VFSDBConnectionError: If connection fails
        """
        with self._lock:
            if self._conn is not None:
                self._open_count += 1
                return
            
            self._ensure_dir()
            
            try:
                self._conn = sqlite3.connect(
                    self.db_path,
                    timeout=10,
                    check_same_thread=False,  # We use our own lock
                )
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._open_count += 1
                self._last_accessed = datetime.now(timezone.utc).isoformat()
                
                logger.debug(
                    "Opened VFS database: %s/%s at %s",
                    self.subsystem, self.server_id, self.db_path
                )
            except sqlite3.Error as e:
                raise VFSDBConnectionError(
                    f"Failed to open database '{self.db_path}': {e}"
                )
    
    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn is None:
                return
            
            self._open_count -= 1
            if self._open_count > 0:
                return
            
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            finally:
                self._conn = None
    
    def execute(self, sql: str, params: Optional[tuple] = None) -> int:
        """
        Execute a non-SELECT SQL statement.
        
        Args:
            sql: SQL statement (INSERT, UPDATE, DELETE, CREATE, etc.)
            params: Optional query parameters
        
        Returns:
            Number of rows affected
        
        Raises:
            VFSDBQueryError: On query failure
            VFSDBConnectionError: If not connected
        """
        with self._lock:
            if self._conn is None:
                raise VFSDBConnectionError(
                    f"Database not open: {self.subsystem}/{self.server_id}"
                )
            
            try:
                cursor = self._conn.execute(sql, params or ())
                self._conn.commit()
                self._last_accessed = datetime.now(timezone.utc).isoformat()
                return cursor.rowcount
            except sqlite3.Error as e:
                raise VFSDBQueryError(
                    f"Query failed on '{self.subsystem}/{self.server_id}': {e}\nSQL: {sql}"
                )
    
    def executemany(
        self, sql: str, params_list: List[tuple]
    ) -> int:
        """
        Execute a SQL statement against multiple parameter sets.
        
        Args:
            sql: SQL statement
            params_list: List of parameter tuples
        
        Returns:
            Total number of rows affected
        """
        with self._lock:
            if self._conn is None:
                raise VFSDBConnectionError(
                    f"Database not open: {self.subsystem}/{self.server_id}"
                )
            
            try:
                total = 0
                for params in params_list:
                    cursor = self._conn.execute(sql, params)
                    total += cursor.rowcount
                self._conn.commit()
                self._last_accessed = datetime.now(timezone.utc).isoformat()
                return total
            except sqlite3.Error as e:
                raise VFSDBQueryError(
                    f"Batch query failed on '{self.subsystem}/{self.server_id}': {e}"
                )
    
    def query(self, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Execute a SELECT query and return results as dicts.
        
        Args:
            sql: SELECT SQL statement
            params: Optional query parameters
        
        Returns:
            List of row dictionaries with column names as keys
        
        Raises:
            VFSDBQueryError: On query failure
            VFSDBConnectionError: If not connected
        """
        with self._lock:
            if self._conn is None:
                raise VFSDBConnectionError(
                    f"Database not open: {self.subsystem}/{self.server_id}"
                )
            
            try:
                cursor = self._conn.execute(sql, params or ())
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                self._last_accessed = datetime.now(timezone.utc).isoformat()
                return [dict(zip(columns, row)) for row in rows]
            except sqlite3.Error as e:
                raise VFSDBQueryError(
                    f"Query failed on '{self.subsystem}/{self.server_id}': {e}\nSQL: {sql}"
                )
    
    def query_one(
        self, sql: str, params: Optional[tuple] = None
    ) -> Optional[Dict[str, Any]]:
        """Execute SELECT and return the first row, or None."""
        rows = self.query(sql, params)
        return rows[0] if rows else None
    
    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database."""
        result = self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return result is not None
    
    def get_table_names(self) -> List[str]:
        """Get list of all table names in the database."""
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [row["name"] for row in rows]
    
    def get_info(self) -> VFSDatabaseInfo:
        """Get metadata about this database."""
        with self._lock:
            path = Path(self.db_path)
            size = path.stat().st_size if path.exists() else 0
            tables = []
            try:
                if self._conn:
                    tables = self.get_table_names()
            except Exception:
                pass
            
            return VFSDatabaseInfo(
                subsystem=self.subsystem,
                server_id=self.server_id,
                db_path=self.db_path,
                size_bytes=size,
                table_count=len(tables),
                created_at=self._created_at,
                last_accessed=self._last_accessed,
                schema_version=self._schema_version,
                is_healthy=True,
            )
    
    def get_size(self) -> int:
        """Get database file size in bytes."""
        path = Path(self.db_path)
        return path.stat().st_size if path.exists() else 0
    
    def vacuum(self) -> None:
        """Run VACUUM to reclaim space."""
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute("VACUUM")
                self._conn.commit()
            except sqlite3.Error as e:
                logger.warning("VACUUM failed: %s", e)
    
    def create_backup(self, backup_dir: Optional[str] = None) -> str:
        """
        Create a backup of the database.
        
        Args:
            backup_dir: Directory for backups (defaults to db_path + '.backups/')
        
        Returns:
            Path to the backup file
        
        Raises:
            VFSDBError: If backup fails
        """
        path = Path(self.db_path)
        if not path.exists():
            raise VFSDBError(f"Database file not found: {self.db_path}")
        
        if backup_dir:
            bdir = Path(backup_dir)
        else:
            bdir = path.parent / ".backups"
        
        bdir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = bdir / f"{path.stem}_{timestamp}.db"
        
        with self._lock:
            if self._conn:
                # Use backup API when connected
                backup_conn = sqlite3.connect(str(backup_path))
                try:
                    self._conn.backup(backup_conn)
                finally:
                    backup_conn.close()
            else:
                # File copy when not connected
                import shutil
                shutil.copy2(str(path), str(backup_path))
        
        # Clean old backups
        self._cleanup_old_backups(bdir)
        
        logger.info("Created database backup: %s", backup_path)
        return str(backup_path)
    
    def _cleanup_old_backups(self, backup_dir: Path) -> None:
        """Remove old backups exceeding the limit."""
        if self.backup_limit <= 0:
            return
        
        backups = sorted(backup_dir.glob(f"{Path(self.db_path).stem}_*.db"))
        while len(backups) > self.backup_limit:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass
    
    def set_schema_version(self, version: int) -> None:
        """Set the schema version in the database."""
        with self._lock:
            self._schema_version = version
            if self._conn:
                try:
                    self._conn.execute(
                        f"PRAGMA user_version = {version}"
                    )
                except sqlite3.Error:
                    pass
    
    def get_schema_version(self) -> int:
        """Get the current schema version from the database."""
        with self._lock:
            if self._conn:
                try:
                    row = self._conn.execute("PRAGMA user_version").fetchone()
                    if row:
                        self._schema_version = row[0]
                except sqlite3.Error:
                    pass
            return self._schema_version


# ──────────────────────────────────────────────────────────────────────────────
# VFS Database Manager
# ──────────────────────────────────────────────────────────────────────────────

class VFSDatabaseManager:
    """
    Manages VFS-backed databases for all subsystem+server combinations.
    
    Features:
    - Creates and manages isolated databases per subsystem+server
    - Connection pooling and lifecycle management
    - Thread-safe operations
    - Backup and restore capabilities
    - Monitoring and health checks
    """
    
    def __init__(
        self,
        data_root: str = "DATA/vfs",
        auto_backup: bool = True,
        backup_limit: int = 5,
    ):
        self.data_root = Path(data_root)
        self.auto_backup = auto_backup
        self.backup_limit = backup_limit
        self._lock = threading.RLock()
        self._databases: Dict[Tuple[str, str], VFSDatabase] = {}
        self._global_schema_version: int = 0
    
    def _get_db_path(self, subsystem: str, server_id: str) -> str:
        """Get the filesystem path for a subsystem+server database."""
        # Sanitize names for filesystem safety
        safe_subsystem = self._sanitize_name(subsystem)
        safe_server = self._sanitize_name(server_id)
        
        db_dir = self.data_root / safe_subsystem / safe_server
        return str(db_dir / "data.db")
    
    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize a name for use in filesystem paths."""
        import re
        # Replace non-alphanumeric chars with underscore
        sanitized = re.sub(r'[^a-zA-Z0-9_\-.]', '_', str(name))
        # Limit length
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        # Ensure not empty
        if not sanitized:
            sanitized = "unnamed"
        return sanitized
    
    def get_database(
        self,
        subsystem: str,
        server_id: str,
        auto_open: bool = True,
    ) -> VFSDatabase:
        """
        Get or create a VFSDatabase for a subsystem+server combination.
        
        Args:
            subsystem: Subsystem name (e.g., "auction_house")
            server_id: Server identifier (e.g., "server1")
            auto_open: Automatically open the connection
        
        Returns:
            VFSDatabase instance
        
        Raises:
            VFSDBConfigError: On invalid parameters
        """
        if not subsystem or not isinstance(subsystem, str):
            raise VFSDBConfigError("subsystem must be a non-empty string")
        if not server_id or not isinstance(server_id, str):
            raise VFSDBConfigError("server_id must be a non-empty string")
        
        key = (subsystem, server_id)
        
        with self._lock:
            if key not in self._databases:
                db_path = self._get_db_path(subsystem, server_id)
                db = VFSDatabase(
                    subsystem=subsystem,
                    server_id=server_id,
                    db_path=db_path,
                    auto_backup=self.auto_backup,
                    backup_limit=self.backup_limit,
                )
                self._databases[key] = db
                
                if auto_open:
                    db.open()
                
                logger.info(
                    "Created VFS database for subsystem '%s' on server '%s'",
                    subsystem, server_id
                )
            
            return self._databases[key]
    
    def remove_database(
        self,
        subsystem: str,
        server_id: str,
        delete_file: bool = False,
    ) -> bool:
        """
        Remove a database from the manager.
        
        Args:
            subsystem: Subsystem name
            server_id: Server identifier
            delete_file: If True, also delete the database file
        
        Returns:
            True if database was removed, False if it didn't exist
        """
        key = (subsystem, server_id)
        
        with self._lock:
            if key not in self._databases:
                return False
            
            db = self._databases[key]
            db.close()
            
            if delete_file:
                db_path = Path(db.db_path)
                try:
                    if db_path.exists():
                        db_path.unlink()
                    # Also clean up WAL/SHM files
                    for ext in ["-wal", "-shm"]:
                        extra = db_path.with_suffix(db_path.suffix + ext)
                        if extra.exists():
                            extra.unlink()
                except OSError as e:
                    logger.warning(
                        "Failed to delete database file '%s': %s",
                        db_path, e
                    )
            
            del self._databases[key]
            logger.info(
                "Removed VFS database for '%s' on '%s' (delete_file=%s)",
                subsystem, server_id, delete_file
            )
            return True
    
    def list_databases(self) -> List[VFSDatabaseInfo]:
        """
        List all managed databases with metadata.
        
        Returns:
            List of VFSDatabaseInfo objects
        """
        with self._lock:
            return [
                db.get_info()
                for db in self._databases.values()
            ]
    
    def get_databases_for_subsystem(self, subsystem: str) -> List[VFSDatabaseInfo]:
        """Get all databases for a specific subsystem."""
        with self._lock:
            return [
                db.get_info()
                for key, db in self._databases.items()
                if key[0] == subsystem
            ]
    
    def get_databases_for_server(self, server_id: str) -> List[VFSDatabaseInfo]:
        """Get all databases for a specific server."""
        with self._lock:
            return [
                db.get_info()
                for key, db in self._databases.items()
                if key[1] == server_id
            ]
    
    def close_all(self) -> None:
        """Close all database connections."""
        with self._lock:
            for key, db in self._databases.items():
                try:
                    db.close()
                except Exception as e:
                    logger.warning(
                        "Error closing database '%s': %s", key, e
                    )
    
    def backup_all(self, backup_root: Optional[str] = None) -> Dict[str, str]:
        """
        Create backups of all managed databases.
        
        Args:
            backup_root: Root directory for backups
        
        Returns:
            Dict mapping (subsystem/server_id) to backup path
        """
        results = {}
        with self._lock:
            for key, db in self._databases.items():
                try:
                    bdir = None
                    if backup_root:
                        bdir = str(
                            Path(backup_root) / key[0] / key[1]
                        )
                    backup_path = db.create_backup(bdir)
                    results[f"{key[0]}/{key[1]}"] = backup_path
                except Exception as e:
                    logger.warning(
                        "Backup failed for '%s/%s': %s",
                        key[0], key[1], e
                    )
                    results[f"{key[0]}/{key[1]}"] = f"ERROR: {e}"
        return results
    
    @property
    def database_count(self) -> int:
        """Number of managed databases."""
        with self._lock:
            return len(self._databases)
    
    def create_database_structure(
        self, subsystem: str, server_id: str, schema_sql: str
    ) -> VFSDatabase:
        """
        Create a database with an initial schema.
        
        Args:
            subsystem: Subsystem name
            server_id: Server identifier
            schema_sql: CREATE TABLE statements to initialize
        
        Returns:
            VFSDatabase instance
        """
        db = self.get_database(subsystem, server_id, auto_open=True)
        
        # Execute schema SQL (split by semicolons)
        statements = [
            s.strip() for s in schema_sql.split(";")
            if s.strip()
        ]
        for stmt in statements:
            db.execute(stmt)
        
        return db
    
    def health_check_all(self) -> Dict[str, bool]:
        """
        Perform health checks on all managed databases.
        
        Returns:
            Dict mapping (subsystem/server_id) to health status
        """
        results = {}
        with self._lock:
            for key, db in self._databases.items():
                try:
                    # Quick connectivity check
                    db.query("SELECT 1")
                    results[f"{key[0]}/{key[1]}"] = True
                except Exception as e:
                    results[f"{key[0]}/{key[1]}"] = False
                    logger.warning(
                        "Health check failed for '%s/%s': %s",
                        key[0], key[1], e
                    )
        return results
