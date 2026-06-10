"""
database.py — SQLite Database Module for MC Server Runner Manager

Manages server_data.db with full VFS, server instances, mod registry,
virtual networking, atomic journaling, and compressed blob storage.
"""

import sqlite3
import os
import json
import zlib
import hashlib
import threading
from datetime import datetime

# ---------------------------------------------------------------------------
# Singleton cache
# ---------------------------------------------------------------------------
_db_instances = {}
_db_lock = threading.Lock()


def get_db(db_path=None):
    """Return a cached Database singleton for the given path."""
    if db_path is None:
        db_dir = os.path.join(os.path.dirname(__file__), '..', 'DATA')
        db_path = os.path.join(db_dir, 'server_data.db')
    db_path = os.path.abspath(db_path)

    with _db_lock:
        if db_path not in _db_instances:
            _db_instances[db_path] = Database(db_path)
        return _db_instances[db_path]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
-- vfs_nodes: All files stored as blobs with path-based lookup
CREATE TABLE IF NOT EXISTS vfs_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vfs_path TEXT UNIQUE NOT NULL,
    blob_data BLOB NOT NULL,
    original_size INTEGER NOT NULL,
    compressed_size INTEGER NOT NULL,
    file_mode TEXT DEFAULT '644',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- vfs_metadata: Extended attributes per VFS node
CREATE TABLE IF NOT EXISTS vfs_metadata (
    node_id INTEGER PRIMARY KEY,
    content_type TEXT DEFAULT 'application/octet-stream',
    original_name TEXT,
    import_hash TEXT NOT NULL,
    validation_hash TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    FOREIGN KEY (node_id) REFERENCES vfs_nodes(id)
);

-- server_instances: Registered MC server configurations
CREATE TABLE IF NOT EXISTS server_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    mc_version TEXT NOT NULL,
    server_type TEXT DEFAULT 'vanilla',
    java_version TEXT DEFAULT '17',
    min_ram TEXT DEFAULT '2G',
    max_ram TEXT DEFAULT '4G',
    server_port INTEGER DEFAULT 25565,
    rcon_port INTEGER DEFAULT 25575,
    rcon_password TEXT,
    auto_start INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- server_flags: Runtime flags per server instance
CREATE TABLE IF NOT EXISTS server_flags (
    server_id INTEGER NOT NULL,
    flag_key TEXT NOT NULL,
    flag_value TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (server_id, flag_key),
    FOREIGN KEY (server_id) REFERENCES server_instances(id)
);

-- mods: Mod registry
CREATE TABLE IF NOT EXISTS mods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    version TEXT NOT NULL,
    mc_version TEXT NOT NULL,
    loader TEXT NOT NULL,
    download_url TEXT,
    file_hash TEXT,
    file_size INTEGER,
    enabled INTEGER DEFAULT 1,
    installed_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- mod_dependencies: Mod dependency graph
CREATE TABLE IF NOT EXISTS mod_dependencies (
    mod_id INTEGER NOT NULL,
    depends_on_mod_id INTEGER NOT NULL,
    required INTEGER DEFAULT 1,
    PRIMARY KEY (mod_id, depends_on_mod_id),
    FOREIGN KEY (mod_id) REFERENCES mods(id),
    FOREIGN KEY (depends_on_mod_id) REFERENCES mods(id)
);

-- mod_backups: Versioned backups for rollback
CREATE TABLE IF NOT EXISTS mod_backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mod_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    backup_blob BLOB,
    file_hash TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    notes TEXT,
    FOREIGN KEY (mod_id) REFERENCES mods(id)
);

-- network_rules: Virtual network ACLs
CREATE TABLE IF NOT EXISTS network_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    rule_type TEXT NOT NULL,
    direction TEXT DEFAULT 'outbound',
    protocol TEXT DEFAULT 'tcp',
    port_range TEXT,
    target_host TEXT DEFAULT '*',
    target_port INTEGER,
    priority INTEGER DEFAULT 100,
    enabled INTEGER DEFAULT 1,
    FOREIGN KEY (server_id) REFERENCES server_instances(id)
);

-- virtual_adapters: Virtual NIC configurations
CREATE TABLE IF NOT EXISTS virtual_adapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL UNIQUE,
    virtual_ip TEXT,
    subnet TEXT DEFAULT '10.0.0.0/24',
    mac_address TEXT,
    upstream_dns TEXT DEFAULT '1.1.1.1',
    bandwidth_limit INTEGER DEFAULT 0,
    packet_loss REAL DEFAULT 0.0,
    latency_ms INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    FOREIGN KEY (server_id) REFERENCES server_instances(id)
);

-- conversion_log: Audit trail for file conversions
CREATE TABLE IF NOT EXISTS conversion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vfs_path TEXT NOT NULL,
    direction TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    original_size INTEGER NOT NULL,
    result TEXT DEFAULT 'success',
    error_msg TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- atomic_operations: Journal for rollback safety
CREATE TABLE IF NOT EXISTS atomic_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type TEXT NOT NULL,
    op_key TEXT NOT NULL,
    before_state TEXT,
    after_state TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now')),
    committed_at TEXT
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _compress(data: bytes) -> bytes:
    """Compress blob data with zlib (level 6)."""
    return zlib.compress(data, level=6)


def _decompress(data: bytes) -> bytes:
    """Decompress zlib-compressed data."""
    return zlib.decompress(data)


def _sha256(data: bytes) -> str:
    """Return hex SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    """ISO-8601 timestamp string."""
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


def _row_to_dict(columns, row) -> dict:
    """Convert a sqlite3.Row or tuple to a dict using column names."""
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return dict(zip(columns, row))


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------
class Database:
    """Manages server_data.db with full CRUD, VFS, and journal support."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self._conn = None
        self._connect()
        self._create_schema()

    # -- connection management ------------------------------------------------

    def _connect(self):
        """Open (or reopen) the SQLite connection."""
        db_dir = os.path.dirname(self.db_path)
        os.makedirs(db_dir, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the connection, reconnecting if closed."""
        if self._conn is None:
            self._connect()
        return self._conn

    def _create_schema(self):
        """Execute all CREATE TABLE IF NOT EXISTS statements."""
        for statement in SCHEMA_SQL.split(';'):
            stmt = statement.strip()
            if stmt:
                self.conn.execute(stmt)
        self.conn.commit()

    # -- VFS operations ------------------------------------------------------

    def store_file(self, vfs_path: str, blob_data: bytes,
                   file_mode: str = '644',
                   content_type: str = 'application/octet-stream',
                   original_name: str = None,
                   import_hash: str = None,
                   validation_hash: str = None) -> int:
        """
        Insert or update a file in the VFS, compressing the blob.

        Returns the node_id of the stored file.
        """
        original_size = len(blob_data)
        compressed = _compress(blob_data)
        compressed_size = len(compressed)
        now = _now()

        self.conn.execute(
            """INSERT INTO vfs_nodes
               (vfs_path, blob_data, original_size, compressed_size, file_mode,
                updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(vfs_path) DO UPDATE SET
                   blob_data         = excluded.blob_data,
                   original_size     = excluded.original_size,
                   compressed_size   = excluded.compressed_size,
                   file_mode         = excluded.file_mode,
                   updated_at        = excluded.updated_at""",
            (vfs_path, compressed, original_size, compressed_size,
             file_mode, now),
        )
        self.conn.commit()

        row = self.conn.execute(
            "SELECT id FROM vfs_nodes WHERE vfs_path = ?",
            (vfs_path,)
        ).fetchone()
        node_id = row['id']
        
        # Store metadata in vfs_metadata table
        orig_name = original_name or os.path.basename(vfs_path.rstrip('/'))
        imp_hash = import_hash or _sha256(blob_data)
        val_hash = validation_hash or _sha256(compressed)
        self.conn.execute(
            """INSERT INTO vfs_metadata (node_id, content_type, original_name, import_hash, validation_hash)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET
                   content_type = excluded.content_type,
                   original_name = excluded.original_name,
                   import_hash = excluded.import_hash,
                   validation_hash = excluded.validation_hash""",
            (node_id, content_type, orig_name, imp_hash, val_hash),
        )
        self.conn.commit()
        return row['id']

    def get_raw_file(self, vfs_path: str) -> dict:
        """Retrieve a file by VFS path WITHOUT decompressing blob_data."""
        row = self.conn.execute(
            """SELECT id, vfs_path, blob_data, original_size,
                      compressed_size, file_mode, created_at, updated_at
               FROM vfs_nodes WHERE vfs_path = ?""",
            (vfs_path,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_file(self, vfs_path: str) -> dict:
        """Retrieve a file by VFS path. Returns dict or None."""
        row = self.conn.execute(
            """SELECT id, vfs_path, blob_data, original_size,
                      compressed_size, file_mode, created_at, updated_at
               FROM vfs_nodes WHERE vfs_path = ?""",
            (vfs_path,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['blob_data'] = _decompress(result['blob_data'])
        return result

    def delete_file(self, vfs_path: str) -> bool:
        """Delete a file from the VFS. Returns True if a row was removed."""
        # Delete from vfs_metadata first to avoid FK constraint failure
        # (vfs_metadata.node_id has FK -> vfs_nodes(id) without CASCADE)
        self.conn.execute(
            "DELETE FROM vfs_metadata WHERE node_id IN "
            "(SELECT id FROM vfs_nodes WHERE vfs_path = ?)",
            (vfs_path,)
        )
        cur = self.conn.execute(
            "DELETE FROM vfs_nodes WHERE vfs_path = ?",
            (vfs_path,)
        )
        self.conn.commit()
        # Also clean up metadata
        # (metadata is linked by node_id, but we need node_id — easiest:
        #  fetch node_id first. For simplicity, let ON DELETE CASCADE be
        #  handled via application-level cleanup.)
        return cur.rowcount > 0

    def list_files(self, prefix: str = '') -> list:
        """List VFS files under the given path prefix."""
        if prefix:
            pattern = prefix.rstrip('/') + '/%'
            rows = self.conn.execute(
                """SELECT id, vfs_path, original_size, compressed_size,
                          file_mode, created_at, updated_at
                   FROM vfs_nodes
                   WHERE vfs_path = ? OR vfs_path LIKE ?
                   ORDER BY vfs_path""",
                (prefix, pattern)
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT id, vfs_path, original_size, compressed_size,
                          file_mode, created_at, updated_at
                   FROM vfs_nodes ORDER BY vfs_path"""
            ).fetchall()
        return [dict(r) for r in rows]
    def execute(self, sql: str, params: tuple = ()):
        """Execute a raw SQL statement and return the cursor."""
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.lastrowid if cur.lastrowid else cur.rowcount



    # -- Server instance operations ------------------------------------------

    def get_server(self, server_id_or_name) -> dict:
        """Look up a server by integer ID or string name."""
        if isinstance(server_id_or_name, int):
            row = self.conn.execute(
                "SELECT * FROM server_instances WHERE id = ?",
                (server_id_or_name,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM server_instances WHERE name = ?",
                (server_id_or_name,)
            ).fetchone()
        return dict(row) if row else None

    def list_servers(self) -> list:
        """Return all server instances."""
        rows = self.conn.execute(
            "SELECT * FROM server_instances ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    def create_server(self, **kwargs) -> int:
        """
        Create a new server instance. Required kwargs:
        name, mc_version. Returns the new server ID.
        """
        safe_keys = [
            'name', 'mc_version', 'server_type', 'java_version',
            'min_ram', 'max_ram', 'server_port', 'rcon_port',
            'rcon_password', 'auto_start', 'enabled',
        ]
        data = {k: v for k, v in kwargs.items() if k in safe_keys and v is not None}

        if 'name' not in data or 'mc_version' not in data:
            raise ValueError("'name' and 'mc_version' are required")

        columns = ', '.join(data.keys())
        placeholders = ', '.join('?' for _ in data)
        values = list(data.values())

        cur = self.conn.execute(
            f"INSERT INTO server_instances ({columns}) VALUES ({placeholders})",
            values,
        )
        self.conn.commit()
        return cur.lastrowid

    def update_server(self, server_id: int, **kwargs) -> bool:
        """Update fields on an existing server instance."""
        safe_keys = [
            'name', 'mc_version', 'server_type', 'java_version',
            'min_ram', 'max_ram', 'server_port', 'rcon_port',
            'rcon_password', 'auto_start', 'enabled',
        ]
        updates = {k: v for k, v in kwargs.items() if k in safe_keys and v is not None}
        if not updates:
            return False

        updates['updated_at'] = _now()
        set_clause = ', '.join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [server_id]

        cur = self.conn.execute(
            f"UPDATE server_instances SET {set_clause} WHERE id = ?",
            values,
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_server(self, server_id: int) -> bool:
        """Delete a server instance and its related rows."""
        # Cleanup related data
        self.conn.execute("DELETE FROM server_flags WHERE server_id = ?", (server_id,))
        self.conn.execute("DELETE FROM network_rules WHERE server_id = ?", (server_id,))
        self.conn.execute("DELETE FROM virtual_adapters WHERE server_id = ?", (server_id,))
        cur = self.conn.execute(
            "DELETE FROM server_instances WHERE id = ?", (server_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # -- Mod operations ------------------------------------------------------

    def get_mod(self, mod_id_or_slug) -> dict:
        """Look up a mod by integer ID or string slug."""
        if isinstance(mod_id_or_slug, int):
            row = self.conn.execute(
                "SELECT * FROM mods WHERE id = ?", (mod_id_or_slug,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM mods WHERE slug = ?", (mod_id_or_slug,)
            ).fetchone()
        return dict(row) if row else None

    def list_mods(self, server_id: int = None, mc_version: str = None,
                      loader: str = None, enabled_only: bool = False) -> list:
        """Return all mods with optional filters."""
        if server_id is not None:
            rows = self.conn.execute(
                """SELECT m.* FROM mods m
                   INNER JOIN server_mods sm ON sm.mod_id = m.id
                   WHERE sm.server_id = ?
                   ORDER BY m.name""",
                (server_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM mods ORDER BY name"
            ).fetchall()
        result = [dict(r) for r in rows]
        if mc_version:
            result = [m for m in result if m.get('mc_version') == mc_version]
        if loader:
            result = [m for m in result if m.get('loader') == loader]
        if enabled_only:
            result = [m for m in result if m.get('enabled')]
        return result

    def create_mod(self, **kwargs) -> int:
        """
        Register a new mod. Required kwargs:
        name, slug, version, mc_version, loader.
        Returns the new mod ID.
        """
        safe_keys = [
            'name', 'slug', 'version', 'mc_version', 'loader',
            'download_url', 'file_hash', 'file_size', 'enabled',
        ]
        data = {k: v for k, v in kwargs.items() if k in safe_keys and v is not None}

        required = ['name', 'slug', 'version', 'mc_version', 'loader']
        for r in required:
            if r not in data:
                raise ValueError(f"'{r}' is required")

        columns = ', '.join(data.keys())
        placeholders = ', '.join('?' for _ in data)
        values = list(data.values())

        cur = self.conn.execute(
            f"INSERT INTO mods ({columns}) VALUES ({placeholders})",
            values,
        )
        self.conn.commit()
        return cur.lastrowid

    def check_dependencies(self, mod_slug: str) -> list:
        """
        Check all dependencies for a mod (identified by slug).
        Returns a list of dicts for *unmet* dependencies:
            { 'dep_slug': str, 'dep_name': str, 'required': bool, 'installed': bool }
        """
        mod = self.get_mod(mod_slug)
        if mod is None:
            return [{'dep_slug': mod_slug, 'dep_name': mod_slug,
                      'required': True, 'installed': False, 'error': 'mod_not_found'}]

        deps = self.conn.execute(
            """SELECT md.required,
                      m.slug AS dep_slug, m.name AS dep_name,
                      m.id AS dep_id
               FROM mod_dependencies md
               LEFT JOIN mods m ON m.id = md.depends_on_mod_id
               WHERE md.mod_id = ?""",
            (mod['id'],)
        ).fetchall()

        unmet = []
        for d in deps:
            dep = dict(d)
            installed = dep['dep_id'] is not None
            if not installed:
                unmet.append({
                    'dep_slug': dep['dep_slug'],
                    'dep_name': dep['dep_name'],
                    'required': bool(dep['required']),
                    'installed': installed,
                })
        return unmet

    def add_backup(self, mod_id: int, version: str,
                   backup_blob: bytes = None,
                   file_hash: str = None,
                   notes: str = None) -> int:
        """Store a backup of a mod file. Returns the backup ID."""
        if file_hash is None and backup_blob is not None:
            file_hash = _sha256(backup_blob)
        cur = self.conn.execute(
            """INSERT INTO mod_backups
               (mod_id, version, backup_blob, file_hash, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (mod_id, version, backup_blob, file_hash, notes),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_backups(self, mod_id: int) -> list:
        """Return all backups for a mod, ordered by creation time desc."""
        rows = self.conn.execute(
            """SELECT id, mod_id, version, file_hash,
                      created_at, notes
               FROM mod_backups
               WHERE mod_id = ?
               ORDER BY created_at DESC""",
            (mod_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Conversion log ------------------------------------------------------

    def log_conversion(self, vfs_path: str, direction: str,
                       file_hash: str, size: int,
                       result: str = 'success', error: str = None):
        """Record an import/extract audit entry."""
        self.conn.execute(
            """INSERT INTO conversion_log
               (vfs_path, direction, file_hash, original_size, result, error_msg)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (vfs_path, direction, file_hash, size, result, error),
        )
        self.conn.commit()

    # -- Atomic operations journal -------------------------------------------

    def insert_operation(self, op_dict: dict) -> None:
        """Store an atomic operation record (used by AtomicJournal)."""
        self.conn.execute(
            """INSERT INTO atomic_operations (op_type, op_key, before_state, status)
               VALUES (?, ?, ?, ?)""",
            (op_dict.get('op_type', ''), op_dict.get('op_key', ''),
             json.dumps(op_dict.get('before_state', {})), 'pending'),
        )
        self.conn.commit()

    def update_operation(self, op_dict: dict) -> None:
        """Update an atomic operation record (used by AtomicJournal)."""
        status = op_dict.get('status', 'committed')
        now = _now()
        self.conn.execute(
            """UPDATE atomic_operations SET status = ?, after_state = ?, committed_at = ?
               WHERE id = ?""",
            (status, json.dumps(op_dict.get('after_state', {})), now, op_dict.get('op_id')),
        )
        self.conn.commit()

    def begin_atomic(self, op_type: str, op_key: str,
                     before_state: dict) -> int:
        """
        Start a journal entry for rollback safety.

        Args:
            op_type: One of 'file_write', 'file_delete', 'mod_install', 'mod_update'
            op_key: The affected vfs_path or mod slug
            before_state: JSON-serializable snapshot of state before the op

        Returns:
            op_id (int)
        """
        cur = self.conn.execute(
            """INSERT INTO atomic_operations
               (op_type, op_key, before_state, status)
               VALUES (?, ?, ?, 'pending')""",
            (op_type, op_key, json.dumps(before_state)),
        )
        self.conn.commit()
        return cur.lastrowid

    def commit_atomic(self, op_id: int) -> bool:
        """Mark an atomic operation as committed."""
        now = _now()
        cur = self.conn.execute(
            "UPDATE atomic_operations SET status = 'committed', committed_at = ? WHERE id = ?",
            (now, op_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def rollback_atomic(self, op_id: int) -> dict:
        """
        Roll back an atomic operation using its before_state snapshot.

        Returns a dict with rollback result details.
        """
        row = self.conn.execute(
            "SELECT * FROM atomic_operations WHERE id = ?", (op_id,)
        ).fetchone()
        if row is None:
            return {'success': False, 'error': 'op_not_found'}

        op = dict(row)
        if op['status'] == 'rolled_back':
            return {'success': False, 'error': 'already_rolled_back'}

        before = json.loads(op['before_state']) if op['before_state'] else {}

        try:
            if op['op_type'] in ('file_write',):
                # Restore old blob from before_state if available
                old_blob = before.get('blob_data')
                if old_blob is not None:
                    # old_blob is stored as a hex string (from .hex()) because
                    # bytes cannot be JSON-serialized natively.  Decode with
                    # fromhex() to restore the original binary content.
                    self.store_file(
                        vfs_path=op['op_key'],
                        blob_data=bytes.fromhex(old_blob) if isinstance(old_blob, str) else old_blob,
                        file_mode=before.get('file_mode', '644'),
                    )
                else:
                    # File didn't exist before — delete
                    self.delete_file(op['op_key'])

            elif op['op_type'] == 'file_delete':
                # Re-insert the deleted file
                old_blob = before.get('blob_data')
                if old_blob is not None:
                    # Same hex-string decoding as file_write above
                    self.store_file(
                        vfs_path=op['op_key'],
                        blob_data=bytes.fromhex(old_blob) if isinstance(old_blob, str) else old_blob,
                        file_mode=before.get('file_mode', '644'),
                    )

            elif op['op_type'] == 'mod_install':
                # Disable or delete the mod
                mod_slug = op['op_key']
                mod = self.get_mod(mod_slug)
                if mod:
                    self.conn.execute(
                        "UPDATE mods SET enabled = 0 WHERE id = ?",
                        (mod['id'],)
                    )

            elif op['op_type'] == 'mod_update':
                # Restore previous mod version data
                old_version = before.get('version')
                if old_version:
                    self.conn.execute(
                        "UPDATE mods SET version = ?, file_hash = ?, file_size = ? WHERE slug = ?",
                        (old_version, before.get('file_hash'),
                         before.get('file_size'), op['op_key']),
                    )

            self.conn.execute(
                "UPDATE atomic_operations SET status = 'rolled_back' WHERE id = ?",
                (op_id,)
            )
            self.conn.commit()
            return {'success': True, 'op_type': op['op_type'], 'op_key': op['op_key']}

        except Exception as e:
            self.conn.rollback()
            return {'success': False, 'error': str(e), 'op_id': op_id}

    # -- Config / server_flags -----------------------------------------------

    def get_config(self, server_id: int, key: str,
                   default: str = None) -> str:
        """Read a runtime flag from server_flags."""
        row = self.conn.execute(
            "SELECT flag_value FROM server_flags WHERE server_id = ? AND flag_key = ?",
            (server_id, key),
        ).fetchone()
        return row['flag_value'] if row else default

    def set_config(self, server_id: int, key: str, value: str):
        """Set or update a runtime flag on server_flags."""
        now = _now()
        self.conn.execute(
            """INSERT INTO server_flags (server_id, flag_key, flag_value, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(server_id, flag_key) DO UPDATE SET
                   flag_value = excluded.flag_value,
                   updated_at = excluded.updated_at""",
            (server_id, key, value, now),
        )
        self.conn.commit()

    # -- Network rules -------------------------------------------------------

    def add_firewall_rule(self, **kwargs) -> int:
        return self.add_network_rule(**kwargs)

    def add_network_rule(self, **kwargs) -> int:
        """
        Add a network ACL rule. Required kwargs:
        server_id, rule_type.
        Returns the rule ID.
        """
        safe_keys = [
            'server_id', 'rule_type', 'direction', 'protocol',
            'port_range', 'target_host', 'target_port',
            'priority', 'enabled',
        ]
        data = {k: v for k, v in kwargs.items() if k in safe_keys and v is not None}

        if 'server_id' not in data or 'rule_type' not in data:
            raise ValueError("'server_id' and 'rule_type' are required")

        columns = ', '.join(data.keys())
        placeholders = ', '.join('?' for _ in data)
        values = list(data.values())

        cur = self.conn.execute(
            f"INSERT INTO network_rules ({columns}) VALUES ({placeholders})",
            values,
        )
        self.conn.commit()
        return cur.lastrowid

    def list_firewall_rules(self, server_id: int) -> list:
        return self.list_network_rules(server_id)

    def list_network_rules(self, server_id: int) -> list:
        """Return all network rules for a server, ordered by priority."""
        rows = self.conn.execute(
            "SELECT * FROM network_rules WHERE server_id = ? ORDER BY priority ASC",
            (server_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Virtual adapters ----------------------------------------------------

    def update_virtual_adapter(self, server_id: int, **kwargs) -> bool:
        safe_keys = [
            'virtual_ip', 'subnet', 'mac_address', 'upstream_dns',
            'bandwidth_limit', 'packet_loss', 'latency_ms', 'enabled',
        ]
        updates = {k: v for k, v in kwargs.items() if k in safe_keys}
        if not updates:
            return False
        set_clause = ', '.join('{} = ?'.format(k) for k in updates)
        values = list(updates.values()) + [server_id]
        self.conn.execute(
            'UPDATE virtual_adapters SET {} WHERE server_id = ?'.format(set_clause),
            values,
        )
        self.conn.commit()
        return True

    def get_virtual_adapter(self, server_id: int) -> dict:
        """Return the virtual NIC config for a server, or None."""
        row = self.conn.execute(
            "SELECT * FROM virtual_adapters WHERE server_id = ?",
            (server_id,)
        ).fetchone()
        return dict(row) if row else None

    def create_virtual_adapter(self, **kwargs) -> int:
        """
        Create a virtual NIC for a server. Required kwargs:
        server_id.
        Returns the adapter ID.
        """
        safe_keys = [
            'server_id', 'virtual_ip', 'subnet', 'mac_address',
            'upstream_dns', 'bandwidth_limit', 'packet_loss',
            'latency_ms', 'enabled',
        ]
        data = {k: v for k, v in kwargs.items() if k in safe_keys and v is not None}

        if 'server_id' not in data:
            raise ValueError("'server_id' is required")

        columns = ', '.join(data.keys())
        placeholders = ', '.join('?' for _ in data)
        values = list(data.values())

        cur = self.conn.execute(
            f"INSERT INTO virtual_adapters ({columns}) VALUES ({placeholders})",
            values,
        )
        self.conn.commit()
        return cur.lastrowid

    # -- Cleanup -------------------------------------------------------------

    def remove_firewall_rule(self, rule_id: int) -> bool:
        self.conn.execute('DELETE FROM network_rules WHERE id = ?', (rule_id,))
        self.conn.commit()
        return True

    def reserve_port(self, port: int, server_id: int) -> bool:
        try:
            self.conn.execute(
                'INSERT INTO server_flags (server_id, flag_key, flag_value) VALUES (?, ?, ?)',
                (server_id, 'port_{}'.format(port), str(port)),
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def release_port(self, port: int, server_id: int) -> bool:
        self.conn.execute(
            'DELETE FROM server_flags WHERE server_id = ? AND flag_key = ?',
            (server_id, 'port_{}'.format(port)),
        )
        self.conn.commit()
        return True

    def get_all_reserved_ports(self) -> list:
        rows = self.conn.execute(
            "SELECT flag_value FROM server_flags WHERE flag_key LIKE 'port_%'"
        ).fetchall()
        return [int(r['flag_value']) for r in rows if r['flag_value'].isdigit()]

    
    def query(self, sql: str, params: tuple = ()) -> list:
        """Execute a raw SQL query and return results as list of dicts."""
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
        # Remove from singleton cache
        with _db_lock:
            _db_instances.pop(self.db_path, None)
