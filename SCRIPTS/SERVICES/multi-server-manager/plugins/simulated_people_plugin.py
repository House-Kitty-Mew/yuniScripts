"""
Simulated People — SubsystemPlugin implementation.

Manages simulated player entities with config-driven behaviors,
social relationships, and status announcements.

Provides the social simulation layer for the Multi-Server ecosystem.
"""

import json
import logging
import os
import sqlite3
import random
import string
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine.plugin_registry import (
    SubsystemPlugin,
    PluginRegistry,
    PluginHealth,
    PluginState,
    PluginError,
)

logger = logging.getLogger("plugins.simulated_people")


# ──────────────────────────────────────────────────────────────────────────────
# SQL Schema
# ──────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS people (
    person_id       TEXT PRIMARY KEY,
    server_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    persona         TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'online',
    last_active     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    metadata_json   TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS relationships (
    rel_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       TEXT NOT NULL,
    person_a        TEXT NOT NULL REFERENCES people(person_id),
    person_b        TEXT NOT NULL REFERENCES people(person_id),
    relationship    TEXT NOT NULL DEFAULT 'neutral',
    strength        REAL NOT NULL DEFAULT 0.0,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       TEXT NOT NULL,
    person_id       TEXT NOT NULL REFERENCES people(person_id),
    activity_type   TEXT NOT NULL,
    description     TEXT,
    targets_json    TEXT DEFAULT '[]',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    duration_minutes INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS announcements (
    announcement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       TEXT NOT NULL,
    person_id       TEXT NOT NULL REFERENCES people(person_id),
    message         TEXT NOT NULL,
    announcement_type TEXT NOT NULL DEFAULT 'chat',
    created_at      TEXT NOT NULL,
    is_read         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_people_server ON people(server_id);
CREATE INDEX IF NOT EXISTS idx_rels_person ON relationships(person_a);
CREATE INDEX IF NOT EXISTS idx_acts_person ON activities(person_id);
CREATE INDEX IF NOT EXISTS idx_annc_server ON announcements(server_id);
"""

# ──────────────────────────────────────────────────────────────────────────────
# Default Personas (config-driven)
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_PERSONAS = [
    {
        "name": "Alex",
        "persona": "Friendly builder who loves cooperative projects",
        "default_status": "online",
    },
    {
        "name": "Bailey",
        "persona": "Resource trader always looking for good deals",
        "default_status": "online",
    },
    {
        "name": "Casey",
        "persona": "Explorer who ventures to distant lands",
        "default_status": "online",
    },
    {
        "name": "Dakota",
        "persona": "Redstone engineer and automation specialist",
        "default_status": "online",
    },
    {
        "name": "Ellis",
        "persona": "Friendly neighbor who helps new players",
        "default_status": "online",
    },
    {
        "name": "Finley",
        "persona": "Merchant who runs a bustling shop",
        "default_status": "online",
    },
    {
        "name": "Grayson",
        "persona": "Adventurer always seeking the next challenge",
        "default_status": "online",
    },
    {
        "name": "Harper",
        "persona": "Farmer who cultivates vast fields",
        "default_status": "online",
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Plugin
# ──────────────────────────────────────────────────────────────────────────────

class SimulatedPeoplePlugin(SubsystemPlugin):
    """
    SubsystemPlugin for Simulated People.
    
    Creates and manages simulated player entities that interact
    with the game world, form relationships, announce activities,
    and provide a living-world feel to the server.
    """

    name = "simulated_people"
    version = "1.0.0"
    description = "Simulated People — living-world NPC social simulation"
    dependencies = []
    optional_dependencies = ["economy_bridge"]
    tags = ["simulation", "social", "npc"]
    author = "Multi-Server Manager Team"

    # ── Lifecycle Hooks ──────────────────────────────────────────────

    async def on_init(self, server_id: str, config: Dict[str, Any]) -> None:
        """
        Initialize the Simulated People plugin for a server.
        
        Creates VFS-backed database with schema, seeds initial
        simulated people from config or defaults.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            raise PluginError(
                f"SimulatedPeople: VFS database not available for server '{server_id}'"
            )
        
        try:
            db.execute(SCHEMA_SQL)
            db.commit()
            logger.info(
                "SimulatedPeople: Schema initialized for server '%s'", server_id
            )
        except sqlite3.Error as e:
            db.rollback()
            raise PluginError(
                f"SimulatedPeople: Schema creation failed for server '{server_id}': {e}"
            )
        
        # Seed initial people from config or defaults
        count = self._seed_people(server_id, config)
        logger.info(
            "SimulatedPeople: Seeded %d people for server '%s'", count, server_id
        )

    async def on_shutdown(self, server_id: str) -> None:
        """Gracefully shut down for a server."""
        logger.info("SimulatedPeople: Shutting down server '%s'", server_id)
        try:
            # Mark all as offline
            db = self._get_vfs_db(server_id)
            if db and hasattr(db, 'is_open') and db.is_open():
                now = datetime.now(timezone.utc).isoformat()
                db.execute(
                    "UPDATE people SET status='offline', last_active=? WHERE server_id=?",
                    (now, server_id)
                )
                db.commit()
                db.close()
        except Exception as e:
            logger.warning(
                "SimulatedPeople: Error during shutdown for '%s': %s", server_id, e
            )

    async def on_health_check(self, server_id: str) -> PluginHealth:
        """Perform a health check."""
        try:
            db = self._get_vfs_db(server_id)
            if db is None or not hasattr(db, 'is_open') or not db.is_open():
                return PluginHealth.UNHEALTHY
            
            rows = db.query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='people'"
            )
            if not rows:
                return PluginHealth.DEGRADED
            
            db.query("SELECT COUNT(*) FROM people WHERE server_id=?", (server_id,))
            return PluginHealth.HEALTHY
            
        except Exception as e:
            logger.error("SimulatedPeople: Health check failed for '%s': %s", server_id, e)
            return PluginHealth.UNHEALTHY

    # ── Public API ───────────────────────────────────────────────────

    def get_all_people(
        self,
        server_id: str,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all simulated people on a server, optionally filtered by status.
        
        Returns:
            List of person dicts.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            if status:
                rows = db.query(
                    "SELECT * FROM people WHERE server_id=? AND status=? ORDER BY name",
                    (server_id, status)
                )
            else:
                rows = db.query(
                    "SELECT * FROM people WHERE server_id=? ORDER BY name",
                    (server_id,)
                )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_person(self, server_id: str, person_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific simulated person by ID."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        try:
            rows = db.query(
                "SELECT * FROM people WHERE person_id=? AND server_id=?",
                (person_id, server_id)
            )
            return dict(rows[0]) if rows else None
        except sqlite3.Error:
            return None

    def get_person_by_name(self, server_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Find a person by name."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        try:
            rows = db.query(
                "SELECT * FROM people WHERE name=? AND server_id=?",
                (name, server_id)
            )
            return dict(rows[0]) if rows else None
        except sqlite3.Error:
            return None

    def create_person(
        self,
        server_id: str,
        name: str,
        persona: str = "",
        status: str = "online",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new simulated person.
        
        Returns:
            Created person dict, or None on failure.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        import uuid
        person_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        try:
            db.execute(
                """INSERT INTO people
                   (person_id, server_id, name, persona, status, last_active, created_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (person_id, server_id, name, persona, status, now, now,
                 json.dumps(metadata or {}))
            )
            db.commit()
            logger.info(
                "SimulatedPeople: Created person '%s' (%s) on '%s'",
                name, person_id[:8], server_id
            )
            return {
                "person_id": person_id,
                "name": name,
                "persona": persona,
                "status": status,
                "server_id": server_id,
                "created_at": now,
            }
        except sqlite3.Error as e:
            db.rollback()
            logger.error("SimulatedPeople: Failed to create person: %s", e)
            return None

    def update_status(
        self,
        server_id: str,
        person_id: str,
        status: str,
    ) -> bool:
        """Update a person's status (online/offline/away/busy)."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return False
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            db.execute(
                "UPDATE people SET status=?, last_active=? WHERE person_id=? AND server_id=?",
                (status, now, person_id, server_id)
            )
            db.commit()
            return db.query("SELECT changes()")[0][0] > 0
        except sqlite3.Error:
            return False

    def create_relationship(
        self,
        server_id: str,
        person_a_id: str,
        person_b_id: str,
        relationship: str = "neutral",
        strength: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Create or update a relationship between two simulated people.
        
        Args:
            server_id: Target server
            person_a_id: First person
            person_b_id: Second person
            relationship: "friend", "neutral", "rival", "ally"
            strength: -1.0 (hostile) to 1.0 (close)
        
        Returns:
            Relationship dict, or None on failure.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            # Normalize order to avoid duplicates
            a, b = sorted([person_a_id, person_b_id])
            
            existing = db.query(
                "SELECT * FROM relationships WHERE server_id=? AND person_a=? AND person_b=?",
                (server_id, a, b)
            )
            
            if existing:
                db.execute(
                    "UPDATE relationships SET relationship=?, strength=?, updated_at=? WHERE rel_id=?",
                    (relationship, strength, now, existing[0][0])
                )
            else:
                db.execute(
                    "INSERT INTO relationships (server_id, person_a, person_b, relationship, strength, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (server_id, a, b, relationship, strength, now)
                )
            
            db.commit()
            return {
                "server_id": server_id,
                "person_a": a,
                "person_b": b,
                "relationship": relationship,
                "strength": strength,
            }
        except sqlite3.Error as e:
            db.rollback()
            logger.error("SimulatedPeople: Relationship error: %s", e)
            return None

    def get_relationships(
        self,
        server_id: str,
        person_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get relationships, optionally filtered by person."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            if person_id:
                rows = db.query(
                    "SELECT * FROM relationships WHERE server_id=? AND (person_a=? OR person_b=?)",
                    (server_id, person_id, person_id)
                )
            else:
                rows = db.query(
                    "SELECT * FROM relationships WHERE server_id=?"
                )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def add_announcement(
        self,
        server_id: str,
        person_id: str,
        message: str,
        announcement_type: str = "chat",
    ) -> bool:
        """
        Add an announcement from a simulated person.
        
        Returns:
            True if successful.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return False
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            db.execute(
                "INSERT INTO announcements (server_id, person_id, message, announcement_type, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (server_id, person_id, message, announcement_type, now)
            )
            db.commit()
            return True
        except sqlite3.Error:
            return False

    def get_announcements(
        self,
        server_id: str,
        unread_only: bool = True,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get announcements for a server."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return []
        
        try:
            if unread_only:
                rows = db.query(
                    "SELECT * FROM announcements WHERE server_id=? AND is_read=0 ORDER BY created_at DESC LIMIT ?",
                    (server_id, limit)
                )
            else:
                rows = db.query(
                    "SELECT * FROM announcements WHERE server_id=? ORDER BY created_at DESC LIMIT ?",
                    (server_id, limit)
                )
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def mark_announcement_read(self, server_id: str, announcement_id: int) -> bool:
        """Mark an announcement as read."""
        db = self._get_vfs_db(server_id)
        if db is None:
            return False
        
        try:
            db.execute(
                "UPDATE announcements SET is_read=1 WHERE announcement_id=? AND server_id=?",
                (announcement_id, server_id)
            )
            db.commit()
            return True
        except sqlite3.Error:
            return False

    def log_activity(
        self,
        server_id: str,
        person_id: str,
        activity_type: str,
        description: str,
        targets: Optional[List[str]] = None,
        duration_minutes: int = 0,
    ) -> Optional[int]:
        """
        Log an activity for a simulated person.
        
        Returns:
            Activity ID, or None on failure.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return None
        
        now = datetime.now(timezone.utc).isoformat()
        try:
            db.execute(
                """INSERT INTO activities
                   (server_id, person_id, activity_type, description, targets_json, started_at, duration_minutes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (server_id, person_id, activity_type, description,
                 json.dumps(targets or []), now, duration_minutes)
            )
            db.commit()
            result = db.query("SELECT last_insert_rowid() as id")
            return result[0][0] if result else None
        except sqlite3.Error:
            return None

    # ── Internal Helpers ─────────────────────────────────────────────

    def _seed_people(
        self,
        server_id: str,
        config: Dict[str, Any],
    ) -> int:
        """
        Seed initial simulated people from config or defaults.
        
        Respects config's "personas" list, falling back to DEFAULT_PERSONAS.
        """
        db = self._get_vfs_db(server_id)
        if db is None:
            return 0
        
        # Check if already seeded
        try:
            count = db.query(
                "SELECT COUNT(*) FROM people WHERE server_id=?", (server_id,)
            )[0][0]
            if count > 0:
                return count  # Already seeded
        except sqlite3.Error:
            pass
        
        personas = config.get("personas", DEFAULT_PERSONAS)
        created = 0
        now = datetime.now(timezone.utc).isoformat()
        import uuid
        
        for pdata in personas:
            try:
                person_id = str(uuid.uuid4())
                name = pdata.get("name", f"Person_{random.randint(1000, 9999)}")
                persona = pdata.get("persona", "")
                status = pdata.get("default_status", "online")
                
                db.execute(
                    "INSERT INTO people (person_id, server_id, name, persona, status, last_active, created_at, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (person_id, server_id, name, persona, status, now, now, "{}")
                )
                created += 1
            except sqlite3.Error:
                continue
        
        try:
            db.commit()
        except sqlite3.Error:
            return 0
        
        return created

    # ── VFS Integration ──────────────────────────────────────────────

    def _get_vfs_db(self, server_id: str) -> Any:
        """Get the VFS-backed database for this plugin+server combo."""
        try:
            from engine.vfs_db_isolation import VFSDatabaseManager
            
            mgr = VFSDatabaseManager(data_root="DATA/vfs")
            db = mgr.get_database(self.name, server_id)
            if db and not (hasattr(db, 'is_open') and db.is_open()):
                db.open()
            return db
        except Exception as e:
            logger.error("SimulatedPeople: VFS DB error for '%s': %s", server_id, e)
            return None
