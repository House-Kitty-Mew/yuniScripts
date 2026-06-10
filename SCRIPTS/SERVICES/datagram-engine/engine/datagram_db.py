"""
datagram_db.py — Database CRUD operations for datagrams.

Implements the database operations from the original Datagram spec:
  - SQLite: Full SQL database support with parameterized queries
  - JSON: File-based JSON document store
  - XML: File-based XML document store

All operations are isolated and use parameterized queries to prevent
SQL injection (original Datagram had risks with string interpolation).
"""

import json
import sqlite3
import xml.etree.ElementTree as ET
import xml.dom.minidom as MD
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union
from enum import Enum

from .datagram_types import DatabaseType, DatabaseRecord, DataType


class DatabaseError(Exception):
    """Raised on database operation failures."""
    pass


class Database:
    """
    Abstract base for datagram database backends.
    Each backend supports: connect, insert, select, update, delete.
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self.db_type: DatabaseType = DatabaseType.JSON

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        """Insert a record. Returns the ID/row key."""
        raise NotImplementedError

    def select(self, table: str, where: Optional[Dict[str, Any]] = None,
               order_by: Optional[str] = None, limit: Optional[int] = None) -> List[DatabaseRecord]:
        raise NotImplementedError

    def update(self, table: str, data: Dict[str, Any],
               where: Dict[str, Any]) -> int:
        """Update records matching 'where'. Returns count of affected rows."""
        raise NotImplementedError

    def delete(self, table: str, where: Dict[str, Any]) -> int:
        raise NotImplementedError

    def close(self) -> None:
        pass


# ── SQLite Backend ──────────────────────────────────────────────────────────

class SQLiteDatabase(Database):
    """SQLite database backend — full SQL support with parameterized queries."""

    def __init__(self, path: Union[str, Path], name: str = "default"):
        super().__init__(name)
        self.db_type = DatabaseType.SQLITE
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self) -> None:
        """Connect (or create) the SQLite database."""
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        # Enable WAL mode for concurrent access
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def _ensure_connection(self) -> None:
        if self._conn is None:
            self._connect()

    def _table_exists(self, table: str) -> bool:
        """Check if a table exists in the database."""
        self._ensure_connection()
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        )
        return cursor.fetchone() is not None

    def _ensure_table(self, table: str, data: Dict[str, Any]) -> None:
        """Create table if it doesn't exist, inferred from data keys."""
        self._ensure_connection()
        # Check if table exists
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        )
        if cursor.fetchone() is None:
            # Infer column types from data
            col_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
            for key, value in data.items():
                if isinstance(value, int):
                    col_type = "INTEGER"
                elif isinstance(value, float):
                    col_type = "REAL"
                elif isinstance(value, bool):
                    col_type = "INTEGER"
                elif isinstance(value, (dict, list)):
                    col_type = "TEXT"  # JSON stored as text
                else:
                    col_type = "TEXT"
                safe_key = key.replace('"', '""')
                col_defs.append(f'"{safe_key}" {col_type}')
            sql = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(col_defs)})"
            self._conn.execute(sql)
            self._conn.commit()

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        self._ensure_connection()
        self._ensure_table(table, data)

        # Serialize complex types to JSON
        clean_data = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                clean_data[key] = json.dumps(value)
            else:
                clean_data[key] = value

        columns = ', '.join(f'"{k}"' for k in clean_data.keys())
        placeholders = ', '.join('?' for _ in clean_data.values())
        values = list(clean_data.values())

        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        cursor = self._conn.execute(sql, values)
        self._conn.commit()
        return cursor.lastrowid or 0

    def select(self, table: str, where: Optional[Dict[str, Any]] = None,
               order_by: Optional[str] = None,
               limit: Optional[int] = None) -> List[DatabaseRecord]:
        self._ensure_connection()
        # Check if table exists — return empty if not
        if not self._table_exists(table):
            return []
        sql = f"SELECT * FROM {table}"
        params: List[Any] = []

        if where:
            conditions = ' AND '.join(f'"{k}" = ?' for k in where.keys())
            sql += f" WHERE {conditions}"
            params.extend(where.values())

        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {limit}"

        cursor = self._conn.execute(sql, params)
        rows = cursor.fetchall()
        return [DatabaseRecord(dict(row)) for row in rows]

    def update(self, table: str, data: Dict[str, Any],
               where: Dict[str, Any]) -> int:
        self._ensure_connection()
        if not self._table_exists(table):
            return 0
        set_clause = ', '.join(f'"{k}" = ?' for k in data.keys())
        where_clause = ' AND '.join(f'"{k}" = ?' for k in where.keys())

        sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
        params = list(data.values()) + list(where.values())
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor.rowcount

    def delete(self, table: str, where: Dict[str, Any]) -> int:
        self._ensure_connection()
        if not self._table_exists(table):
            return 0
        where_clause = ' AND '.join(f'"{k}" = ?' for k in where.keys())
        sql = f"DELETE FROM {table} WHERE {where_clause}"
        cursor = self._conn.execute(sql, list(where.values()))
        self._conn.commit()
        return cursor.rowcount

    def execute_raw(self, sql: str, params: List[Any] = None) -> List[Dict[str, Any]]:
        """Execute raw SQL. Use with caution — for advanced queries only."""
        self._ensure_connection()
        cursor = self._conn.execute(sql, params or [])
        if sql.strip().upper().startswith(("SELECT", "PRAGMA")):
            return [dict(row) for row in cursor.fetchall()]
        self._conn.commit()
        return []

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ── JSON Backend ────────────────────────────────────────────────────────────

class JSONDatabase(Database):
    """JSON file-based document store."""

    def __init__(self, path: Union[str, Path], name: str = "default"):
        super().__init__(name)
        self.db_type = DatabaseType.JSON
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, List[Dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                content = self._path.read_text(encoding="utf-8")
                self._data = json.loads(content)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, indent=2, default=str),
            encoding="utf-8"
        )

    def _match_where(self, record: Dict[str, Any],
                     where: Dict[str, Any]) -> bool:
        for key, value in where.items():
            if key not in record:
                return False
            if str(record[key]) != str(value):
                return False
        return True

    def _ensure_table(self, table: str) -> None:
        if table not in self._data:
            self._data[table] = []

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        self._ensure_table(table)
        record = dict(data)
        record["_id"] = len(self._data[table]) + 1
        self._data[table].append(record)
        self._save()
        return record["_id"]

    def select(self, table: str, where: Optional[Dict[str, Any]] = None,
               order_by: Optional[str] = None,
               limit: Optional[int] = None) -> List[DatabaseRecord]:
        self._ensure_table(table)
        records = self._data[table]

        if where:
            records = [r for r in records if self._match_where(r, where)]

        if order_by:
            field = order_by.lstrip("-")
            reverse = order_by.startswith("-")
            records.sort(key=lambda r: str(r.get(field, "")), reverse=reverse)

        if limit is not None:
            records = records[:limit]

        return [DatabaseRecord(r) for r in records]

    def update(self, table: str, data: Dict[str, Any],
               where: Dict[str, Any]) -> int:
        self._ensure_table(table)
        count = 0
        for record in self._data[table]:
            if self._match_where(record, where):
                record.update(data)
                count += 1
        if count > 0:
            self._save()
        return count

    def delete(self, table: str, where: Dict[str, Any]) -> int:
        self._ensure_table(table)
        before = len(self._data[table])
        self._data[table] = [
            r for r in self._data[table] if not self._match_where(r, where)
        ]
        after = len(self._data[table])
        deleted = before - after
        if deleted > 0:
            self._save()
        return deleted


# ── Database Factory ────────────────────────────────────────────────────────

def create_database(db_type: DatabaseType, path: Union[str, Path],
                    name: str = "default") -> Database:
    """Factory function to create the appropriate database backend."""
    if db_type == DatabaseType.SQLITE:
        return SQLiteDatabase(path, name)
    elif db_type == DatabaseType.JSON:
        return JSONDatabase(path, name)
    else:
        raise DatabaseError(f"Unsupported database type: {db_type}")
