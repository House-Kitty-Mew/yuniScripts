#!/usr/bin/env python3
"""
THREAD Memory Graph Store — SQLite-backed node/edge storage with FTS5.

Persistence layer for the THREAD cognitive memory system.
All node and edge data lives in SQLite — only metadata is cached in memory.

Schema:
  - thread_nodes: node storage with embedding for thought metadata
  - thread_edges: typed directed edges between nodes
  - thread_meta: key-value store for graph version counters
  - thread_nodes_fts: FTS5 virtual table for full-text search

Design:
  - SQLite-native: data stored in flat relational tables, not in-memory graph
  - WAL mode for concurrent reads during write operations
  - Thread-safe: threading.Lock around all write paths
  - PageRank approximation: weighted by access_count and recency
  - Forgetting curve: exponential decay on confidence, age-based deletion
"""

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Relation Type Constants ───────────────────────────────────────
SHOULD_GO_TOGETHER = "SHOULD_GO_TOGETHER"
MIGHT_GO_TOGETHER = "MIGHT_GO_TOGETHER"
WENT_TOGETHER_BEFORE = "WENT_TOGETHER_BEFORE"
WILL_NOT_GO_TOGETHER = "WILL_NOT_GO_TOGETHER"
ALMOST_ABSOLUTE_REJECTION = "ALMOST_ABSOLUTE_REJECTION"

VALID_RELATION_TYPES = frozenset({
    SHOULD_GO_TOGETHER,
    MIGHT_GO_TOGETHER,
    WENT_TOGETHER_BEFORE,
    WILL_NOT_GO_TOGETHER,
    ALMOST_ABSOLUTE_REJECTION,
})

# ── Default database path ─────────────────────────────────────────
DEFAULT_DB_DIR = Path.home() / ".local_mcp"
DEFAULT_DB_PATH = str(DEFAULT_DB_DIR / "thread_graph.db")


# ── MemoryGraphStore ──────────────────────────────────────────────

class MemoryGraphStore:
    """
    SQLite-backed node/edge store for the THREAD cognitive memory system.

    Args:
        db_or_path: A Database instance, a path string, or None (uses default).
                    If a Database instance, uses its connection.
                    If None, uses ~/.local_mcp/thread_graph.db.
    """

    def __init__(self, db_or_path=None):
        self._lock = threading.Lock()
        self._db_path: str
        self._external_db = False  # True if given a Database instance

        if db_or_path is None:
            self._db_path = DEFAULT_DB_PATH
            os.makedirs(DEFAULT_DB_DIR, exist_ok=True)
        elif isinstance(db_or_path, str):
            self._db_path = db_or_path
        else:
            # Assume it's a Database instance — extract db_path from it
            self._db_path = getattr(db_or_path, 'db_path', DEFAULT_DB_PATH)
            self._external_db = True

        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        """Get a new SQLite connection (thread-safe — each call gets its own)."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self):
        """Create tables if they don't exist."""
        conn = self._conn()
        try:
            c = conn.cursor()
            c.executescript("""
                CREATE TABLE IF NOT EXISTS thread_nodes (
                    thought_uuid TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    session_id TEXT DEFAULT '',
                    thought_number INTEGER DEFAULT 0,
                    total_thoughts INTEGER DEFAULT 0,
                    next_thought_needed INTEGER DEFAULT 1,
                    category TEXT DEFAULT 'general',
                    keywords TEXT DEFAULT '[]',
                    embedding TEXT DEFAULT NULL,
                    confidence REAL DEFAULT 0.5,
                    importance REAL DEFAULT 0.5,
                    is_critical INTEGER DEFAULT 0,
                    access_count INTEGER DEFAULT 0,
                    graph_version INTEGER DEFAULT 1,
                    activation_score REAL DEFAULT 0.0,
                    page_rank_score REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS thread_edges (
                    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES thread_nodes(thought_uuid) ON DELETE CASCADE,
                    target_id TEXT NOT NULL REFERENCES thread_nodes(thought_uuid) ON DELETE CASCADE,
                    relation_type TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_id, target_id, relation_type)
                );

                CREATE TABLE IF NOT EXISTS thread_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_thread_nodes_session
                    ON thread_nodes(session_id);
                CREATE INDEX IF NOT EXISTS idx_thread_nodes_category
                    ON thread_nodes(category);
                CREATE INDEX IF NOT EXISTS idx_thread_nodes_importance
                    ON thread_nodes(importance);
                CREATE INDEX IF NOT EXISTS idx_thread_nodes_modified
                    ON thread_nodes(modified_at);
                CREATE INDEX IF NOT EXISTS idx_thread_edges_source
                    ON thread_edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_thread_edges_target
                    ON thread_edges(target_id);
                CREATE INDEX IF NOT EXISTS idx_thread_edges_type
                    ON thread_edges(relation_type);
            """)

            # FTS5 virtual table for full-text search
            try:
                c.executescript("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS thread_nodes_fts USING fts5(
                        content, category, keywords,
                        content='thread_nodes', content_rowid='rowid'
                    );
                """)
            except sqlite3.OperationalError as e:
                # FTS5 might not be available in all SQLite builds
                logger.warning(f"FTS5 not available (full-text search disabled): {e}")

            # Initialize graph version counter
            c.execute(
                "INSERT OR IGNORE INTO thread_meta (key, value) VALUES ('graph_version', '1')"
            )
            c.execute(
                "INSERT OR IGNORE INTO thread_meta (key, value) VALUES ('node_count', '0')"
            )
            c.execute(
                "INSERT OR IGNORE INTO thread_meta (key, value) VALUES ('edge_count', '0')"
            )

            conn.commit()
        except Exception as e:
            logger.error(f"MemoryGraphStore: schema creation failed: {e}")
            raise
        finally:
            conn.close()

    # ── Node Operations ──────────────────────────────────────────

    def store_node(
        self,
        content: str,
        thought_uuid: str,
        session_id: str = "",
        thought_number: int = 0,
        total_thoughts: int = 0,
        next_thought_needed: bool = True,
        category: str = "general",
        keywords: Optional[List[str]] = None,
        embedding: Optional[List[float]] = None,
        confidence: float = 0.5,
        importance: float = 0.5,
        is_critical: bool = False,
    ) -> Dict[str, Any]:
        """
        Store a thinking step node. Returns the created/updated record dict.
        Thread-safe.
        """
        now = datetime.now(timezone.utc).isoformat()
        keywords_json = json.dumps(keywords or [])
        embedding_json = json.dumps(embedding) if embedding else None
        is_critical_int = 1 if is_critical else 0
        next_int = 1 if next_thought_needed else 0

        with self._lock:
            conn = self._conn()
            try:
                c = conn.cursor()

                # Check if node already exists
                c.execute("SELECT graph_version, access_count FROM thread_nodes WHERE thought_uuid = ?",
                          (thought_uuid,))
                existing = c.fetchone()

                is_new = existing is None
                if is_new:
                    c.execute("""
                        INSERT INTO thread_nodes
                            (thought_uuid, content, session_id, thought_number,
                             total_thoughts, next_thought_needed, category,
                             keywords, embedding, confidence, importance,
                             is_critical, access_count, graph_version,
                             created_at, modified_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                    """, (thought_uuid, content, session_id, thought_number,
                          total_thoughts, next_int, category,
                          keywords_json, embedding_json, confidence, importance,
                          is_critical_int, now, now))

                    # Increment meta counter
                    c.execute("UPDATE thread_meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'node_count'")
                else:
                    # Update existing node
                    current_version = existing["graph_version"]
                    new_version = current_version + 1
                    c.execute("""
                        UPDATE thread_nodes SET
                            content = ?, session_id = ?, thought_number = ?,
                            total_thoughts = ?, next_thought_needed = ?,
                            category = ?, keywords = ?, embedding = ?,
                            confidence = ?, importance = ?,
                            is_critical = ?, graph_version = ?,
                            modified_at = ?
                        WHERE thought_uuid = ?
                    """, (content, session_id, thought_number,
                          total_thoughts, next_int, category,
                          keywords_json, embedding_json, confidence, importance,
                          is_critical_int, new_version, now, thought_uuid))

                    # Increment graph version
                    c.execute("UPDATE thread_meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'graph_version'")

                conn.commit()

            except Exception as e:
                conn.rollback()
                logger.warning(f"MemoryGraphStore.store_node failed: {e}")
                raise
            finally:
                conn.close()

        # Build return record
        record = self._build_node_record(
            thought_uuid, content, session_id, thought_number,
            total_thoughts, next_thought_needed, category,
            keywords or [], confidence, importance, is_critical,
            is_new=is_new
        )
        return record

    def get_node(self, thought_uuid: str) -> Optional[Dict[str, Any]]:
        """Get a node by UUID. Returns None if not found."""
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM thread_nodes WHERE thought_uuid = ?", (thought_uuid,))
            row = c.fetchone()
            if row is None:
                return None
            return self._row_to_node_dict(row)
        except Exception as e:
            logger.warning(f"MemoryGraphStore.get_node failed: {e}")
            return None
        finally:
            conn.close()

    def get_node_count(self) -> int:
        """Get total number of nodes."""
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute("SELECT value FROM thread_meta WHERE key = 'node_count'")
            row = c.fetchone()
            return int(row["value"]) if row else 0
        except Exception:
            return 0
        finally:
            conn.close()

    def get_edge_count(self) -> int:
        """Get total number of edges."""
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute("SELECT value FROM thread_meta WHERE key = 'edge_count'")
            row = c.fetchone()
            return int(row["value"]) if row else 0
        except Exception:
            return 0
        finally:
            conn.close()

    def get_graph_version(self) -> int:
        """Get current graph version (incremented on mutations)."""
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute("SELECT value FROM thread_meta WHERE key = 'graph_version'")
            row = c.fetchone()
            return int(row["value"]) if row else 1
        except Exception:
            return 1
        finally:
            conn.close()

    def increment_access_count(self, node_id: str) -> None:
        """Increment the access counter for a node. Thread-safe."""
        with self._lock:
            conn = self._conn()
            try:
                c = conn.cursor()
                now = datetime.now(timezone.utc).isoformat()
                c.execute("""
                    UPDATE thread_nodes SET
                        access_count = access_count + 1,
                        modified_at = ?
                    WHERE thought_uuid = ?
                """, (now, node_id))
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

    # ── Edge Operations ──────────────────────────────────────────

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        weight: float = 1.0,
    ) -> bool:
        """
        Create a typed edge between two nodes.
        Returns True if edge was created, False if it already exists.
        Thread-safe.
        """
        if relation_type not in VALID_RELATION_TYPES:
            logger.warning(f"Invalid relation type: {relation_type}")
            return False

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._conn()
            try:
                c = conn.cursor()
                c.execute("""
                    INSERT OR IGNORE INTO thread_edges
                        (source_id, target_id, relation_type, weight, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (source_id, target_id, relation_type, weight, now))

                if c.rowcount > 0:
                    c.execute("UPDATE thread_meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'edge_count'")
                    c.execute("UPDATE thread_meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'graph_version'")
                    conn.commit()
                    return True
                return False
            except Exception as e:
                conn.rollback()
                logger.warning(f"MemoryGraphStore.add_edge failed: {e}")
                return False
            finally:
                conn.close()

    def get_node_edges(
        self,
        node_id: str,
        direction: str = "both",
    ) -> List[Dict[str, Any]]:
        """Get all edges connected to a node. direction: 'out', 'in', or 'both'."""
        conn = self._conn()
        try:
            c = conn.cursor()
            if direction == "out":
                c.execute("SELECT * FROM thread_edges WHERE source_id = ?", (node_id,))
            elif direction == "in":
                c.execute("SELECT * FROM thread_edges WHERE target_id = ?", (node_id,))
            else:
                c.execute(
                    "SELECT * FROM thread_edges WHERE source_id = ? OR target_id = ?",
                    (node_id, node_id)
                )
            return [dict(row) for row in c.fetchall()]
        except Exception:
            return []
        finally:
            conn.close()

    # ── Auto-Consolidation ───────────────────────────────────────

    def auto_consolidate(
        self,
        thought_uuid: str,
        similarity_threshold: float = 0.85,
        max_links: int = 5,
    ) -> int:
        """
        Find similar nodes and create MIGHT_GO_TOGETHER edges.
        Similarity based on category match + keyword overlap.

        Args:
            thought_uuid: Source node to find similar nodes for.
            similarity_threshold: Minimum Jaccard similarity (0.0-1.0).
            max_links: Maximum edges to create.

        Returns:
            Number of edges created.
        """
        source = self.get_node(thought_uuid)
        if source is None:
            return 0

        source_cat = source.get("category", "general")
        source_keywords = set(source.get("keywords", []))

        # Find candidates by category match
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT thought_uuid, category, keywords FROM thread_nodes WHERE thought_uuid != ?",
                (thought_uuid,)
            )
            candidates = []
            for row in c.fetchall():
                target_keywords = set(json.loads(row["keywords"]))
                # Skip if already connected
                c2 = conn.cursor()
                c2.execute(
                    "SELECT 1 FROM thread_edges WHERE source_id = ? AND target_id = ?",
                    (thought_uuid, row["thought_uuid"])
                )
                if c2.fetchone():
                    continue

                # Calculate similarity
                similarity = self._calc_similarity(
                    source_cat, source_keywords,
                    row["category"], target_keywords
                )

                if similarity >= similarity_threshold:
                    candidates.append((row["thought_uuid"], similarity))

            # Sort by similarity, take top max_links
            candidates.sort(key=lambda x: x[1], reverse=True)
            candidates = candidates[:max_links]

            # Create edges
            links_created = 0
            now = datetime.now(timezone.utc).isoformat()
            with self._lock:
                for target_uuid, sim in candidates:
                    c = conn.cursor()
                    c.execute("""
                        INSERT OR IGNORE INTO thread_edges
                            (source_id, target_id, relation_type, weight, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (thought_uuid, target_uuid, MIGHT_GO_TOGETHER, sim, now))
                    if c.rowcount > 0:
                        links_created += 1

                if links_created > 0:
                    c = conn.cursor()
                    c.execute("UPDATE thread_meta SET value = CAST(CAST(value AS INTEGER) + ? AS TEXT) WHERE key = 'edge_count'",
                              (links_created,))
                    c.execute("UPDATE thread_meta SET value = CAST(CAST(value AS INTEGER) + ? AS TEXT) WHERE key = 'graph_version'",
                              (links_created,))
                    conn.commit()

            return links_created

        except Exception as e:
            logger.warning(f"MemoryGraphStore.auto_consolidate failed: {e}")
            return 0
        finally:
            conn.close()

    # ── Forgetting Curve ─────────────────────────────────────────

    def apply_forgetting_curve(
        self,
        decay_factor: float = 0.95,
        archive_threshold: float = 0.1,
        delete_threshold_days: float = 90,
    ) -> Dict[str, int]:
        """
        Apply forgetting curve to all nodes.

        1. Decay confidence: confidence *= decay_factor for non-critical nodes
        2. Archive: mark low-confidence nodes as non-critical (removes protection)
        3. Delete: remove old non-critical nodes + their edges

        Returns dict with decayed, archived, deleted counts.
        """
        result = {"decayed": 0, "archived": 0, "deleted": 0}

        with self._lock:
            conn = self._conn()
            try:
                c = conn.cursor()
                now_iso = datetime.now(timezone.utc).isoformat()

                # 1. Decay confidence (only non-critical nodes)
                c.execute("""
                    UPDATE thread_nodes
                    SET confidence = ROUND(confidence * ?, 4),
                        modified_at = ?
                    WHERE is_critical = 0 AND confidence > 0.01
                """, (decay_factor, now_iso))
                result["decayed"] = c.rowcount

                # 2. Archive: unmark critical status for very low confidence
                c.execute("""
                    UPDATE thread_nodes
                    SET is_critical = 0, modified_at = ?
                    WHERE confidence < ? AND is_critical = 1
                """, (now_iso, archive_threshold))
                result["archived"] = c.rowcount

                # 3. Delete old non-critical nodes
                cutoff = datetime.now(timezone.utc).timestamp() - (delete_threshold_days * 86400)
                cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
                c.execute("""
                    SELECT thought_uuid FROM thread_nodes
                    WHERE is_critical = 0 AND modified_at < ?
                """, (cutoff_iso,))
                to_delete = [row["thought_uuid"] for row in c.fetchall()]

                if to_delete:
                    placeholders = ",".join("?" for _ in to_delete)
                    # Delete edges first (CASCADE should handle this, but be explicit)
                    c.execute(f"DELETE FROM thread_edges WHERE source_id IN ({placeholders})", to_delete)
                    c.execute(f"DELETE FROM thread_edges WHERE target_id IN ({placeholders})", to_delete)
                    # Delete nodes
                    c.execute(f"DELETE FROM thread_nodes WHERE thought_uuid IN ({placeholders})", to_delete)
                    # Update FTS (delete + re-insert) — handled by content sync on next store
                    result["deleted"] = len(to_delete)

                    # Update meta counters
                    c.execute("UPDATE thread_meta SET value = CAST(MAX(0, CAST(value AS INTEGER) - ?) AS TEXT) WHERE key = 'node_count'",
                              (result["deleted"],))
                    c.execute("UPDATE thread_meta SET value = CAST(MAX(0, CAST(value AS INTEGER) - ?) AS TEXT) WHERE key = 'edge_count'",
                              (result["deleted"] * 2,))  # approximate

                # Increment graph version
                c.execute("UPDATE thread_meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'graph_version'")
                conn.commit()

            except Exception as e:
                conn.rollback()
                logger.warning(f"MemoryGraphStore.apply_forgetting_curve failed: {e}")
            finally:
                conn.close()

        return result

    # ── PageRank Approximation ───────────────────────────────────

    def compute_page_rank_centrality(self, iterations: int = 10) -> None:
        """
        Approximate PageRank using access_count and recency.
        This is NOT true PageRank — it's a useful heuristic that's O(n) instead of O(n²).

        score = (access_count / max_access) * recency_weight
        where recency_weight = 1 - (days_since_last_access / 90), clamped to [0, 1]
        """
        conn = self._conn()
        try:
            c = conn.cursor()
            now_ts = datetime.now(timezone.utc).timestamp()

            c.execute("""
                SELECT thought_uuid, access_count, modified_at
                FROM thread_nodes
            """)
            rows = c.fetchall()

            if not rows:
                return

            max_access = max((r["access_count"] for r in rows), default=1)
            if max_access == 0:
                max_access = 1

            updates = []
            for row in rows:
                try:
                    mod_ts = datetime.fromisoformat(row["modified_at"]).timestamp()
                except (ValueError, TypeError):
                    mod_ts = now_ts
                days_since = max(0, (now_ts - mod_ts) / 86400)
                recency = max(0.0, min(1.0, 1.0 - (days_since / 90.0)))
                score = (row["access_count"] / max_access) * recency
                updates.append((round(score, 6), row["thought_uuid"]))

            c.executemany(
                "UPDATE thread_nodes SET page_rank_score = ? WHERE thought_uuid = ?",
                updates
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"MemoryGraphStore.compute_page_rank_centrality failed: {e}")
        finally:
            conn.close()

    # ── Base-Level Activation ────────────────────────────────────

    def update_all_base_activations(self, decay_d: float = 0.5) -> None:
        """
        Update base-level activation for all nodes using ACT-R formula.

        activation = ln(access_count + 1) * e^(-decay_d * days_since_last_access)
        """
        conn = self._conn()
        try:
            c = conn.cursor()
            now_ts = datetime.now(timezone.utc).timestamp()

            c.execute("SELECT thought_uuid, access_count, created_at, modified_at FROM thread_nodes")
            rows = c.fetchall()

            import math
            updates = []
            for row in rows:
                try:
                    mod_ts = datetime.fromisoformat(row["modified_at"]).timestamp()
                except (ValueError, TypeError):
                    mod_ts = now_ts
                days_since = max(0, (now_ts - mod_ts) / 86400)
                activation = math.log(row["access_count"] + 1) * math.exp(-decay_d * days_since)
                updates.append((round(activation, 6), row["thought_uuid"]))

            c.executemany(
                "UPDATE thread_nodes SET activation_score = ? WHERE thought_uuid = ?",
                updates
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"MemoryGraphStore.update_all_base_activations failed: {e}")
        finally:
            conn.close()

    # ── Query Helpers ────────────────────────────────────────────

    def search_nodes(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Full-text search on node content using FTS5.
        Falls back to LIKE search if FTS is unavailable.
        """
        conn = self._conn()
        try:
            c = conn.cursor()
            # Try FTS5 first
            try:
                c.execute("""
                    SELECT n.* FROM thread_nodes n
                    JOIN thread_nodes_fts fts ON n.rowid = fts.rowid
                    WHERE thread_nodes_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, limit))
                rows = c.fetchall()
                if rows:
                    return [self._row_to_node_dict(r) for r in rows]
            except (sqlite3.OperationalError, sqlite3.ProgrammingError):
                pass

            # Fallback: LIKE search
            like = f"%{query}%"
            c.execute("""
                SELECT * FROM thread_nodes
                WHERE content LIKE ? OR category LIKE ? OR keywords LIKE ?
                ORDER BY importance DESC
                LIMIT ?
            """, (like, like, like, limit))
            return [self._row_to_node_dict(r) for r in c.fetchall()]
        except Exception as e:
            logger.warning(f"MemoryGraphStore.search_nodes failed: {e}")
            return []
        finally:
            conn.close()

    def list_recent_nodes(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Get most recently modified nodes."""
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute("""
                SELECT * FROM thread_nodes
                ORDER BY modified_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            return [self._row_to_node_dict(r) for r in c.fetchall()]
        except Exception:
            return []
        finally:
            conn.close()

    # ── Internal Helpers ─────────────────────────────────────────

    def _build_node_record(
        self, thought_uuid, content, session_id, thought_number,
        total_thoughts, next_thought_needed, category,
        keywords, confidence, importance, is_critical, is_new=False
    ) -> Dict[str, Any]:
        """Build a standardized node record dict."""
        now = datetime.now(timezone.utc).isoformat()
        return {
            "thought_uuid": thought_uuid,
            "session_id": session_id,
            "thought_number": thought_number,
            "total_thoughts": total_thoughts,
            "thought_content": content,
            "next_thought_needed": next_thought_needed,
            "category": category,
            "keywords": keywords,
            "is_critical": is_critical,
            "confidence": confidence,
            "importance": importance,
            "access_count": 0 if is_new else None,
            "created_at": now if is_new else None,
            "node_id": thought_uuid,
            "content": content,
        }

    @staticmethod
    def _row_to_node_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a SQLite row to a standardized dict."""
        try:
            keywords = json.loads(row["keywords"]) if row["keywords"] else []
        except (json.JSONDecodeError, TypeError):
            keywords = []
        return {
            "node_id": row["thought_uuid"],
            "thought_uuid": row["thought_uuid"],
            "content": row["content"],
            "session_id": row["session_id"],
            "thought_number": row["thought_number"],
            "total_thoughts": row["total_thoughts"],
            "next_thought_needed": bool(row["next_thought_needed"]),
            "category": row["category"],
            "keywords": keywords,
            "embedding": row["embedding"],
            "confidence": row["confidence"],
            "importance": row["importance"],
            "is_critical": bool(row["is_critical"]),
            "access_count": row["access_count"],
            "graph_version": row["graph_version"],
            "activation_score": row["activation_score"],
            "page_rank_score": row["page_rank_score"],
            "created_at": row["created_at"],
            "modified_at": row["modified_at"],
        }

    @staticmethod
    def _calc_similarity(
        cat1: str, keywords1: set,
        cat2: str, keywords2: set,
    ) -> float:
        """Calculate Jaccard similarity between two nodes."""
        cat_score = 1.0 if cat1 == cat2 else 0.0
        if not keywords1 or not keywords2:
            return cat_score * 0.3  # small base if no keywords

        intersection = keywords1 & keywords2
        union = keywords1 | keywords2
        kw_score = len(intersection) / len(union) if union else 0.0

        # Weighted: 30% category match, 70% keyword overlap
        return (cat_score * 0.3) + (kw_score * 0.7)
