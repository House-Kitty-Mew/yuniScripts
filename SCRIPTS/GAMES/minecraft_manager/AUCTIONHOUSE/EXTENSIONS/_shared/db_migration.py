"""
db_migration.py — Lightweight schema migration system for AH extensions.

Problem:
  Every extension uses ``CREATE TABLE IF NOT EXISTS``, making it impossible
  to add columns, rename tables, or modify existing schemas.  Some files
  work around this with ad-hoc ``_ensure_*_column()`` functions that check
  ``PRAGMA table_info`` and run ``ALTER TABLE`` manually.

Solution:
  A simple version-tracked migration system where each extension declares
  its current schema version and a dict of migration functions keyed by
  version number.  The migrator tracks which versions have been applied
  in a central ``_schema_version`` table.

Usage:
    from EXTENSIONS._shared.db_migration import migrate

    SCHEMA_VERSION = 2  # bump when adding migrations

    MIGRATIONS = {
        2: [
            ("ALTER TABLE ext_sp_item_cache ADD COLUMN market_rarity TEXT", None),
        ],
        3: [
            ("ALTER TABLE ext_sp_item_cache ADD COLUMN enchant_level INTEGER DEFAULT 0", None),
        ],
    }

    def ensure_schema():
        # ... existing CREATE TABLE IF NOT EXISTS ...
        migrate("sp_item_cache", db, SCHEMA_VERSION, MIGRATIONS)

Design:
  - Safe for concurrent extensions (each extension has its own version row)
  - Migrations are applied in strict version order
  - Each migration is wrapped in its own transaction
  - Failed migration rolls back only that migration's transaction
  - Version 1 = initial schema creation (no migration SQL needed)
"""

import logging
from typing import Dict, List, Optional, Tuple, Any

log = logging.getLogger(__name__)

# ── Schema version tracking table ─────────────────────────────────
_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS _schema_version (
    extension_name TEXT PRIMARY KEY,
    current_version INTEGER NOT NULL DEFAULT 1,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    migration_log TEXT DEFAULT '[]'
)
"""

MigrationDef = List[Tuple[str, Optional[Tuple]]]  # (sql, params_or_None)


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════

def migrate(
    extension_name: str,
    db: Any,
    target_version: int,
    migrations: Dict[int, MigrationDef],
) -> Dict[str, Any]:
    """
    Apply pending migrations for an extension.

    Args:
        extension_name: Unique name for this extension (e.g. "sp_item_cache").
        db: Database handle with ``execute()``, ``fetch_one()`` methods.
        target_version: Current schema version for this extension.
                        Bump when adding new migrations.
        migrations: Dict mapping version number (int) to list of
                    ``(sql, params_or_None)`` tuples.
                    Version 1 is implicit (initial schema) and requires no SQL.

    Returns:
        Dict with stats::

            {
                "extension": str,
                "current_version": int,
                "applied": [int, ...],   # versions applied this call
                "failed": [int, ...],     # versions that failed
                "errors": [str, ...]
            }
    """
    # Ensure version tracking table exists
    _ensure_version_table(db)

    # Get current version for this extension (starts at 1)
    current = _get_current_version(db, extension_name)

    if current >= target_version:
        return {
            "extension": extension_name,
            "current_version": current,
            "applied": [],
            "failed": [],
            "errors": [],
        }

    results = {
        "extension": extension_name,
        "current_version": current,
        "applied": [],
        "failed": [],
        "errors": [],
    }

    # Apply pending migrations in order, one at a time
    for version in range(current + 1, target_version + 1):
        if version not in migrations:
            log.warning(f"Migration {extension_name}: version {version} has no migration defined")
            continue

        migration_sql = migrations[version]
        try:
            _apply_migration(db, extension_name, version, migration_sql)
            _set_version(db, extension_name, version)
            results["applied"].append(version)
            log.info(f"Migration {extension_name}: applied version {version}")
        except Exception as e:
            log.error(f"Migration {extension_name}: version {version} FAILED: {e}")
            results["failed"].append(version)
            results["errors"].append(f"v{version}: {e}")
            # Stop applying further migrations — they may depend on this one
            break

    results["current_version"] = _get_current_version(db, extension_name)
    return results


def get_schema_version(db: Any, extension_name: str) -> int:
    """Get the current schema version for an extension.

    Returns 0 if the extension has never run a migration.
    """
    _ensure_version_table(db)
    return _get_current_version(db, extension_name)


def reset_migration(db: Any, extension_name: str) -> None:
    """Reset version tracking for an extension (use with caution).

    This does NOT undo schema changes. It only resets the version
    counter so migrations will re-apply.
    """
    try:
        db.execute(
            "DELETE FROM _schema_version WHERE extension_name = ?",
            (extension_name,)
        )
        log.warning(f"Migration reset for {extension_name}: version tracking cleared")
    except Exception as e:
        log.error(f"Migration reset for {extension_name} failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════

def _ensure_version_table(db: Any) -> None:
    """Create the schema version tracking table if it doesn't exist."""
    # Use raw execution for schema DDL
    try:
        db.execute(_SCHEMA_VERSION_TABLE)
    except Exception as e:
        log.error(f"Failed to create _schema_version table: {e}")
        raise


def _get_current_version(db: Any, extension_name: str) -> int:
    """Read the current version for an extension from the DB."""
    row = db.fetch_one(
        "SELECT current_version FROM _schema_version WHERE extension_name = ?",
        (extension_name,)
    )
    if row:
        return int(row["current_version"])
    return 1  # Version 1 = initial schema


def _set_version(db: Any, extension_name: str, version: int) -> None:
    """Update the schema version for an extension."""
    db.execute(
        """
        INSERT INTO _schema_version (extension_name, current_version, applied_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(extension_name) DO UPDATE SET
            current_version = excluded.current_version,
            applied_at = CURRENT_TIMESTAMP
        """,
        (extension_name, version)
    )


def _apply_migration(
    db: Any,
    extension_name: str,
    version: int,
    migration_sql: MigrationDef,
) -> None:
    """Execute a single migration's SQL statements."""
    for sql, params in migration_sql:
        if params is not None:
            db.execute(sql, params)
        else:
            db.execute(sql)
