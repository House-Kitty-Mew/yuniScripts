"""
sp_memory_thread.py — THREAD-enhanced persona memory system.

Bridges the SIMULATED_PEOPLE extension memory system into the THREAD
cognitive memory architecture from sequentialthinking.

Each persona gets a private "memory space" within the THREAD graph
(namespaced by persona_uuid as session_id). Instead of flat SQL rows,
memories become typed graph nodes with:
  - Typed relationships (SHOULD_GO_TOGETHER, MIGHT_GO_TOGETHER, etc.)
  - Spreading activation for relevance-based retrieval
  - Hybrid retrieval (lexical FTS5 + graph activation fusion)
  - Forgetting curve with exponential decay
  - Event-driven memory consolidation

Graceful degradation: if THREAD modules are unavailable, falls back
to the existing flat SQL memory system.
"""

import json
import sys
import os
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

log = logging.getLogger(__name__)

# ── THREAD integration via shared plugin ────────────────────────────
# Uses EXTENSIONS/_shared/thread_plugin.py for clean importlib-based
# discovery instead of fragile sys.path manipulation.
from EXTENSIONS._shared.thread_plugin import lazy_thread

_THREAD_AVAILABLE = lazy_thread.available
_MemoryGraphStore = lazy_thread.MemoryGraphStore
_ActivationEngine = lazy_thread.ActivationEngine
_HybridRetriever = lazy_thread.HybridRetriever
_ActivationCache = lazy_thread.ActivationCache
_EventBus = lazy_thread.EventBus
_Event = lazy_thread.Event
_get_event_bus = lazy_thread.get_event_bus
_MEMORY_STORED = lazy_thread.MEMORY_STORED
_MEMORY_RETRIEVED = lazy_thread.MEMORY_RETRIEVED
_MEMORY_DECAYED = lazy_thread.MEMORY_DECAYED
_MEMORY_FORGOTTEN = lazy_thread.MEMORY_FORGOTTEN
_EDGE_ADDED = lazy_thread.EDGE_ADDED
_AdaptiveController = lazy_thread.AdaptiveController
_VALID_RELATION_TYPES = lazy_thread.VALID_RELATION_TYPES
_SHOULD_GO_TOGETHER = lazy_thread.SHOULD_GO_TOGETHER
_MIGHT_GO_TOGETHER = lazy_thread.MIGHT_GO_TOGETHER
_WENT_TOGETHER_BEFORE = lazy_thread.WENT_TOGETHER_BEFORE
_WILL_NOT_GO_TOGETHER = lazy_thread.WILL_NOT_GO_TOGETHER
_ALMOST_ABSOLUTE_REJECTION = lazy_thread.ALMOST_ABSOLUTE_REJECTION
_Database = None  # Database class from models is not part of thread_plugin

if _THREAD_AVAILABLE:
    log.info("THREAD modules imported successfully for persona memory (via thread_plugin)")
else:
    log.warning("THREAD import failed - falling back to legacy SQL memory")
    log.info("THREAD not available (persona memory using legacy SQL)")


# ── Exported constants ──────────────────────────────────────────────
# Memory type → THREAD category mapping
MEMORY_CATEGORY_MAP = {
    "purchase": "purchase",
    "sale": "sale",
    "observed_price": "price_observation",
    "missed_deal": "missed_deal",
    "life_event": "life_event",
    "social": "social",
    "world_event": "world_event",
}

# Relation type constants (re-exported for convenience)
SHOULD_GO_TOGETHER = "SHOULD_GO_TOGETHER"
MIGHT_GO_TOGETHER = "MIGHT_GO_TOGETHER"
WENT_TOGETHER_BEFORE = "WENT_TOGETHER_BEFORE"
WILL_NOT_GO_TOGETHER = "WILL_NOT_GO_TOGETHER"
ALMOST_ABSOLUTE_REJECTION = "ALMOST_ABSOLUTE_REJECTION"


# ── Global singleton ────────────────────────────────────────────────
_PERSONA_THREAD_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def get_persona_thread() -> Optional['PersonaMemoryThread']:
    """Get the global PersonaMemoryThread singleton."""
    global _PERSONA_THREAD_INSTANCE
    if _PERSONA_THREAD_INSTANCE is None:
        with _INSTANCE_LOCK:
            if _PERSONA_THREAD_INSTANCE is None:
                _PERSONA_THREAD_INSTANCE = PersonaMemoryThread()
    return _PERSONA_THREAD_INSTANCE


def is_thread_available() -> bool:
    """Check if the THREAD system is available for use."""
    return _THREAD_AVAILABLE and get_persona_thread() is not None


# ════════════════════════════════════════════════════════════════════
# PersonaMemoryThread — Main bridge class
# ════════════════════════════════════════════════════════════════════

class PersonaMemoryThread:
    """
    Bridges persona memories into the THREAD cognitive memory architecture.

    Each persona gets its own namespace via session_id = persona_uuid.
    Memories are stored as THREAD nodes with full graph capabilities.

    Graceful fallback: all public methods are safe to call even if
    THREAD modules are unavailable — they simply return None/[].
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the THREAD bridge.

        Args:
            db_path: Path to the SQLite database for THREAD storage.
                     Defaults to Documentation.db or similar shared DB.
        """
        self._thread_memory = None
        self._graph = None
        self._activation = None
        self._retriever = None
        self._cache = None
        self._event_bus = None
        self._controller = None
        self._db = None
        self._initialized = False

        if not _THREAD_AVAILABLE:
            log.info("PersonaMemoryThread: THREAD unavailable, running in legacy mode")
            return

        # Determine DB path — use dedicated persona thread DB
        if db_path is None:
            persona_cache_dir = os.path.join(
                os.path.dirname(__file__), '.thread_data'
            )
            os.makedirs(persona_cache_dir, exist_ok=True)
            db_path = os.path.join(persona_cache_dir, 'persona_thread.db')

        try:
            # Initialize Database connection (same pattern as thoughts_manager.py)
            if _Database is not None:
                self._db = _Database(db_path)
            else:
                # Fallback: use raw sqlite3
                import sqlite3
                self._db = _FallbackDB(db_path)

            # Initialize graph store
            self._graph = _MemoryGraphStore(self._db)

            # Initialize activation engine
            self._activation = _ActivationEngine(self._graph)

            # Initialize hybrid retriever
            self._retriever = _HybridRetriever(self._graph, self._activation)

            # Initialize cache
            cache_dir = os.path.join(os.path.dirname(__file__), '.thread_cache')
            os.makedirs(cache_dir, exist_ok=True)
            self._cache = _ActivationCache(
                max_size=10000, default_ttl=300, graph_store=self._graph
            )

            # Initialize event bus
            event_log_path = os.path.join(cache_dir, 'persona_thread_events.jsonl')
            if _get_event_bus is not None:
                self._event_bus = _get_event_bus(
                    db=self._db, jsonl_path=event_log_path
                )

            # Initialize adaptive controller
            if _AdaptiveController is not None:
                self._controller = _AdaptiveController(
                    event_bus=self._event_bus, window_size=1000, recompute_interval=100
                )

            self._initialized = True
            log.info(f"PersonaMemoryThread initialized (DB: {db_path})")

        except Exception as e:
            log.warning(f"PersonaMemoryThread init failed (legacy mode): {e}")

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ------------------------------------------------------------------
    # Core API: store_persona_memory
    # ------------------------------------------------------------------

    def store_persona_memory(
        self,
        persona_uuid: str,
        memory_type: str,
        item_id: Optional[str] = None,
        price: Optional[float] = None,
        detail: Optional[Any] = None,
        emotional_weight: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """
        Store a persona memory as a THREAD node.

        The memory is stored with:
          - session_id = persona_uuid (for per-persona isolation)
          - category = mapped memory type
          - importance = emotional_weight / 10.0
          - content = structured text for FTS5 search

        Also auto-creates MIGHT_GO_TOGETHER edges with semantically
        similar memories for this persona.

        Args:
            persona_uuid: The persona's UUID.
            memory_type: Type of memory ('purchase', 'observed_price', etc.).
            item_id: Minecraft item ID (e.g., 'minecraft:diamond').
            price: Price observed or paid.
            detail: Additional detail (dict or string).
            emotional_weight: Emotional significance 1-10.

        Returns:
            Dict with THREAD node metadata, or None if unavailable.
        """
        if not self._initialized or self._graph is None:
            return None

        # Build structured content for FTS5 search
        detail_str = ""
        if detail is not None:
            if isinstance(detail, dict):
                detail_str = json.dumps(detail)
            else:
                detail_str = str(detail)

        content_parts = []
        content_parts.append(f"[{memory_type}]")
        if item_id:
            content_parts.append(f"item={item_id}")
        if price is not None:
            content_parts.append(f"price={price:.2f}")
        if detail_str:
            content_parts.append(f"detail={detail_str}")
        content = " | ".join(content_parts)

        # Map emotional_weight (1-10) to THREAD importance (0.1-1.0)
        importance = max(0.1, min(1.0, emotional_weight / 10.0))

        # Map memory type to THREAD category
        category = MEMORY_CATEGORY_MAP.get(memory_type, memory_type)

        # Generate a deterministic UUID for this memory
        import uuid as _uuid
        memory_uuid = str(_uuid.uuid5(
            _uuid.NAMESPACE_DNS,
            f"persona_memory:{persona_uuid}:{content}:{datetime.now(timezone.utc).isoformat()}"
        ))

        try:
            record = self._graph.store_node(
                content=content,
                thought_uuid=memory_uuid,
                session_id=persona_uuid,
                thought_number=0,
                total_thoughts=0,
                next_thought_needed=False,
                category=category,
                keywords=[memory_type, item_id or "", str(price or "")],
                embedding=None,
                confidence=0.5,
                importance=importance,
                is_critical=(emotional_weight >= 8),
            )

            # Auto-consolidate: link to similar memories for this persona
            try:
                self._graph.auto_consolidate(
                    memory_uuid, similarity_threshold=0.75, max_links=3
                )
            except Exception:
                pass  # non-critical

            # Emit event
            if self._event_bus is not None and _MEMORY_STORED is not None:
                self._event_bus.emit(_MEMORY_STORED, {
                    "node_id": memory_uuid,
                    "session_id": persona_uuid,
                    "content": content[:200],
                    "category": category,
                    "importance": importance,
                    "memory_type": memory_type,
                    "item_id": item_id,
                    "price": price,
                })

            return record

        except Exception as e:
            log.warning(f"Failed to store persona memory in THREAD: {e}")
            return None

    # ------------------------------------------------------------------
    # Core API: recall_persona_memories
    # ------------------------------------------------------------------

    def recall_persona_memories(
        self,
        persona_uuid: str,
        query: str = "",
        max_results: int = 10,
        include_activation: bool = False,
        memory_type_filter: Optional[str] = None,
        item_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve persona memories using THREAD hybrid retrieval.

        Combines lexical FTS5 search with graph activation spreading
        from the persona's recent memories. Results are ranked by
        relevance × activation × importance.

        Args:
            persona_uuid: The persona's UUID.
            query: Search query text (optional — empty = most activated).
            max_results: Maximum memories to return.
            include_activation: Include per-signal activation scores.
            memory_type_filter: Only return specific memory type.
            item_filter: Only return memories for a specific item ID.

        Returns:
            List of enriched memory dicts, or [] if unavailable.
        """
        if not self._initialized or self._retriever is None:
            return []

        # Build an enriched query that includes persona context
        # Build search query from user params (exclude session — handled by filter below)
        search_parts = []
        if query:
            search_parts.append(query)
        if memory_type_filter:
            search_parts.append(f"[{memory_type_filter}]")
        if item_filter:
            search_parts.append(item_filter)
        full_query = " ".join(search_parts) if search_parts else query
        try:
            results = self._retriever.retrieve(
                query=full_query,
                query_embedding=None,
                top_k=max_results,
                include_activation_details=include_activation,
            )

            # Filter by session_id (persona_uuid) since THREAD may return
            # results from other sessions
            filtered = [
                r for r in results
                if r.get("session_id") == persona_uuid
            ]

            # Additional post-filter for memory type stored in content
            if memory_type_filter:
                filtered = [
                    r for r in filtered
                    if f"[{memory_type_filter}]" in r.get("content", "")
                ]

            # Parse structured content into enriched result
            enriched = []
            for r in filtered:
                content = r.get("content", "")
                entry = {
                    "memory_uuid": r.get("node_id", ""),
                    "persona_uuid": r.get("session_id", persona_uuid),
                    "content": content,
                    "memory_type": r.get("category", "unknown"),
                    "importance": r.get("importance", 0.5),
                    "confidence": r.get("confidence", 0.5),
                    "created_at": r.get("created_at", ""),
                    "activation_score": r.get("final_score", 0.0),
                }

                # Parse structured content to extract metadata
                entry.update(self._parse_memory_content(content))

                if include_activation:
                    entry["scores"] = {
                        "lexical": r.get("lexical_score"),
                        "vector": r.get("vector_score"),
                        "graph": r.get("graph_score"),
                    }

                enriched.append(entry)

            # Emit retrieval event
            if self._event_bus is not None and _MEMORY_RETRIEVED is not None:
                self._event_bus.emit(_MEMORY_RETRIEVED, {
                    "session_id": persona_uuid,
                    "query": full_query[:200],
                    "result_count": len(enriched),
                })

            # Increment access counts
            if self._graph:
                for r in filtered:
                    try:
                        self._graph.increment_access_count(r.get("node_id", ""))
                    except Exception:
                        pass

            return enriched

        except Exception as e:
            log.warning(f"THREAD recall failed for {persona_uuid}: {e}")
            return []

    # ------------------------------------------------------------------
    # Convenience: recall_price_memories
    # ------------------------------------------------------------------

    def recall_price_memories(
        self,
        persona_uuid: str,
        item_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve price memories for a specific item using THREAD retrieval.

        Uses hybrid search (lexical + graph activation) to find the most
        relevant price observations for this persona and item.

        Args:
            persona_uuid: The persona's UUID.
            item_id: Minecraft item ID.
            limit: Maximum memories to return.

        Returns:
            List of price memory dicts, or [].
        """
        return self.recall_persona_memories(
            persona_uuid=persona_uuid,
            query=f"item={item_id} price",
            max_results=limit,
            item_filter=item_id,
        )

    # ------------------------------------------------------------------
    # Association: link two persona memories
    # ------------------------------------------------------------------

    def associate_memories(
        self,
        persona_uuid: str,
        source_id: str,
        target_id: str,
        relation_type: str = MIGHT_GO_TOGETHER,
        weight: float = 1.0,
    ) -> bool:
        """
        Create a typed relationship between two persona memories.

        This allows the persona's memory graph to express causal or
        associative links (e.g., "this purchase relates to that need").

        Args:
            persona_uuid: Persona UUID (for logging/events).
            source_id: Source memory UUID.
            target_id: Target memory UUID.
            relation_type: Relationship type constant.
            weight: Edge weight.

        Returns:
            True if edge was created.
        """
        if not self._initialized or self._graph is None:
            return False

        try:
            result = self._graph.add_edge(
                source_id, target_id, relation_type, weight
            )

            if result and self._event_bus is not None:
                self._event_bus.emit(_EDGE_ADDED, {
                    "session_id": persona_uuid,
                    "source_id": source_id,
                    "target_id": target_id,
                    "relation_type": relation_type,
                    "weight": weight,
                })

            return result
        except Exception as e:
            log.warning(f"Failed to associate memories for {persona_uuid}: {e}")
            return False

    # ------------------------------------------------------------------
    # Forgetting curve: per-persona memory decay
    # ------------------------------------------------------------------

    def run_forgetting_curve(
        self,
        persona_uuid: Optional[str] = None,
        decay_factor: float = 0.95,
        archive_threshold: float = 0.1,
        delete_threshold_days: int = 90,
    ) -> Dict[str, int]:
        """
        Apply THREAD forgetting curve to persona memories.

        When persona_uuid is provided, only decays memories for that
        persona (via direct SQL targeting their session_id).
        When None, applies globally via THREAD's built-in method.

        Args:
            persona_uuid: Optional persona UUID to scope decay.
            decay_factor: Confidence decay multiplier per cycle.
            archive_threshold: Confidence below which to archive.
            delete_threshold_days: Days after which to delete archived.

        Returns:
            Stats dict: {decayed, archived, deleted}
        """
        if not self._initialized or self._graph is None:
            return {"decayed": 0, "archived": 0, "deleted": 0}

        if persona_uuid and self._db is not None:
            # Per-persona decay via direct SQL against the thoughts table
            # (THREAD's apply_forgetting_curve doesn't support session filtering)
            try:
                conn = self._db.connect()
                cursor = conn.cursor()
                now = datetime.utcnow()
                stats = {"decayed": 0, "archived": 0, "deleted": 0}

                # Get all non-archived nodes for this persona
                cursor.execute("""
                    SELECT thought_uuid, last_accessed_at, confidence, access_count
                    FROM thoughts
                    WHERE session_id = ? AND archived = 0
                """, (persona_uuid,))

                nodes = cursor.fetchall()
                for node in nodes:
                    node_id = node.get("thought_uuid") if hasattr(node, 'get') else node[0]
                    last_access = node.get("last_accessed_at") if hasattr(node, 'get') else node[1]
                    confidence = float(node.get("confidence", 0.5) if hasattr(node, 'get') else (node[2] or 0.5))

                    if last_access:
                        try:
                            last_dt = datetime.fromisoformat(str(last_access))
                        except (ValueError, TypeError):
                            last_dt = now
                    else:
                        last_dt = now

                    days_since = (now - last_dt).days
                    if days_since > 0:
                        decayed_confidence = confidence * (decay_factor ** days_since)
                        decayed_confidence = max(0.0, min(1.0, decayed_confidence))

                        cursor.execute(
                            "UPDATE thoughts SET confidence = ? WHERE thought_uuid = ?",
                            (decayed_confidence, node_id)
                        )
                        stats["decayed"] += 1

                        # Archive if below threshold
                        if decayed_confidence < archive_threshold:
                            cursor.execute(
                                "UPDATE thoughts SET archived = 1 WHERE thought_uuid = ?",
                                (node_id,)
                            )
                            stats["archived"] += 1

                # Delete archived nodes older than threshold
                from datetime import timedelta
                threshold_date = (now - timedelta(days=delete_threshold_days)).isoformat()
                cursor.execute("""
                    DELETE FROM thoughts
                    WHERE session_id = ? AND archived = 1
                    AND last_accessed_at IS NOT NULL
                    AND last_accessed_at < ?
                """, (persona_uuid, threshold_date))
                stats["deleted"] = cursor.rowcount

                conn.commit()
                log.info(f"Forgetting curve applied to {persona_uuid}: {stats}")
                return stats
            except Exception as e:
                log.warning(f"Per-persona forgetting curve failed for {persona_uuid}: {e}")
                return {"decayed": 0, "archived": 0, "deleted": 0, "error": str(e)}
        else:
            # Global: apply to all nodes via THREAD's built-in method
            try:
                stats = self._graph.apply_forgetting_curve(
                    decay_factor=decay_factor,
                    archive_threshold=archive_threshold,
                    delete_threshold_days=delete_threshold_days,
                )

                # Emit events
                if self._event_bus is not None and _MEMORY_DECAYED is not None:
                    self._event_bus.emit(_MEMORY_DECAYED, {
                        "scope": "all_personas",
                        "nodes_decayed": stats.get("decayed", 0),
                        "nodes_archived": stats.get("archived", 0),
                        "nodes_deleted": stats.get("deleted", 0),
                    })

                return stats
            except Exception as e:
                log.warning(f"Global forgetting curve failed: {e}")
                return {"decayed": 0, "archived": 0, "deleted": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # Run periodic reflect/maintenance
    # ------------------------------------------------------------------

    def reflect(self) -> Dict[str, Any]:
        """Run THREAD periodic maintenance: decay, PageRank, cache cleanup."""
        if not self._initialized:
            return {"status": "unavailable"}

        stats = {
            "forgetting_curve": self.run_forgetting_curve(),
            "page_rank": False,
            "base_activation": False,
            "cache_cleanup": 0,
        }

        if self._graph:
            try:
                self._graph.compute_page_rank_centrality(iterations=10)
                stats["page_rank"] = True
            except Exception:
                pass
            try:
                self._graph.update_all_base_activations(decay_d=0.5)
                stats["base_activation"] = True
            except Exception:
                pass

        if self._cache:
            stats["cache_cleanup"] = self._cache.cleanup_expired()

        return stats

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_persona_stats(self, persona_uuid: str) -> Dict[str, Any]:
        """Get memory statistics for a specific persona."""
        if not self._initialized or self._graph is None:
            return {"available": False}

        try:
            conn = self._graph._db.connect()
            cursor = conn.cursor()

            # Count memories via direct SQL
            cursor.execute("SELECT COUNT(*) FROM thoughts WHERE session_id = ?", (persona_uuid,))
            count_row = cursor.fetchone()
            mem_count = 0
            if count_row:
                mem_count = count_row[0] if not hasattr(count_row, "get") else (count_row.get("COUNT(*)") or 0)

            # Count by category (memory type)
            cursor.execute("""
                SELECT category, COUNT(*) as cnt FROM thoughts
                WHERE session_id = ? GROUP BY category
            """, (persona_uuid,))
            rows = cursor.fetchall()
            type_counts = {}
            for row in rows:
                cat = row.get("category") if hasattr(row, "get") else row[0]
                cnt = row.get("cnt") if hasattr(row, "get") else row[1]
                type_counts[cat] = cnt

            # Average importance
            cursor.execute("""
                SELECT AVG(importance) FROM thoughts WHERE session_id = ?
            """, (persona_uuid,))
            avg_row = cursor.fetchone()
            avg_importance = 0.0
            if avg_row:
                avg_val = avg_row[0] if not hasattr(avg_row, "get") else (avg_row.get("AVG(importance)") or 0)
                avg_importance = float(avg_val) if avg_val else 0.0

            return {
                "available": True,
                "persona_uuid": persona_uuid,
                "total_memories": mem_count,
                "by_type": type_counts,
                "avg_importance": round(avg_importance, 3),
                "avg_emotional_weight": round(avg_importance * 10, 1),
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    @staticmethod
    def _parse_memory_content(content: str) -> Dict[str, Any]:
        """Parse structured memory content back into metadata fields."""
        result = {
            "item_id": None,
            "price": None,
            "detail": None,
        }

        if not content:
            return result

        # Extract item_id
        import re
        item_match = re.search(r'item=([^\s|]+)', content)
        if item_match:
            result["item_id"] = item_match.group(1)

        # Extract price
        price_match = re.search(r'price=([\d.]+)', content)
        if price_match:
            try:
                result["price"] = float(price_match.group(1))
            except ValueError:
                pass

        # Extract detail (everything after "detail=")
        detail_match = re.search(r'detail=({.+})', content)
        if detail_match:
            try:
                result["detail"] = json.loads(detail_match.group(1))
            except (json.JSONDecodeError, ValueError):
                result["detail"] = detail_match.group(1)

        return result


# ════════════════════════════════════════════════════════════════════
# Fallback DB (when Database class from models is not available)
# ════════════════════════════════════════════════════════════════════

class _FallbackDB:
    """Minimal Database-compatible wrapper using raw sqlite3."""

    def __init__(self, db_path: str):
        import sqlite3
        self.db_path = db_path
        self.conn = None
        self._ensure_tables()

    def connect(self):
        import sqlite3
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
        return self.conn

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _ensure_tables(self):
        """Ensure THREAD tables exist with full schema for MemoryGraphStore."""
        conn = self.connect()
        cursor = conn.cursor()

        # Config table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                description TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Thoughts table with all THREAD columns
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS thoughts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thought_uuid TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                thought_number INTEGER NOT NULL,
                total_thoughts INTEGER NOT NULL,
                thought_content TEXT NOT NULL,
                next_thought_needed BOOLEAN NOT NULL,
                category TEXT,
                keywords TEXT,
                is_critical BOOLEAN DEFAULT 0,
                embedding BLOB,
                confidence REAL DEFAULT 0.5,
                importance REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                base_activation REAL DEFAULT 0.0,
                archived BOOLEAN DEFAULT 0,
                created_at TIMESTAMP,
                last_accessed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_th_thoughts_session ON thoughts(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_th_thoughts_category ON thoughts(category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_th_thoughts_critical ON thoughts(is_critical)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_th_thoughts_archived ON thoughts(archived)")

        # FTS5 index for lexical search
        try:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS thoughts_fts USING fts5(
                    thought_uuid UNINDEXED,
                    thought_content,
                    content=thoughts,
                    content_rowid=id,
                    tokenize='porter unicode61'
                )
            """)
        except Exception:
            # Fallback if FTS5 not available
            pass

        # Edges table for typed relationships
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL
                    CHECK(relation_type IN ('SHOULD_GO_TOGETHER','MIGHT_GO_TOGETHER',
                                           'WENT_TOGETHER_BEFORE','WILL_NOT_GO_TOGETHER',
                                           'ALMOST_ABSOLUTE_REJECTION')),
                weight REAL DEFAULT 1.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activated_at TIMESTAMP,
                PRIMARY KEY (source_id, target_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_th_edges_source ON edges(source_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_th_edges_target ON edges(target_id)")

        conn.commit()

    def get_config(self, key: str, default: str = "") -> str:
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT value_json FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            return row[0] if isinstance(row, tuple) else row["value_json"]
        return default

    def set_config(self, key: str, value: str, description: str = "") -> None:
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO config (key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value)
        )
        conn.commit()

    def execute(self, query: str, params: tuple = ()) -> None:
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()

    def fetch_all(self, query: str, params: tuple = ()) -> list:
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


# ════════════════════════════════════════════════════════════════════
# Convenience functions (same signatures as existing sp_database fns)
# ════════════════════════════════════════════════════════════════════

def thread_add_memory(
    persona_uuid: str,
    memory_type: str,
    item_id: Optional[str] = None,
    price: Optional[float] = None,
    detail: Optional[Any] = None,
    emotional_weight: int = 5,
) -> bool:
    """
    Thread-enabled add_memory. Returns True if stored in THREAD.

    Call this alongside the legacy add_memory() for hybrid storage.
    """
    pt = get_persona_thread()
    if pt and pt.initialized:
        result = pt.store_persona_memory(
            persona_uuid, memory_type, item_id, price, detail, emotional_weight
        )
        return result is not None
    return False


def thread_get_price_memories(
    persona_uuid: str,
    item_id: str,
    limit: int = 10,
) -> Optional[List[Dict[str, Any]]]:
    """
    Thread-enabled get_price_memories. Returns enriched memories or None.

    Returns None if THREAD unavailable (caller should fall back to SQL).
    """
    pt = get_persona_thread()
    if pt and pt.initialized:
        return pt.recall_price_memories(persona_uuid, item_id, limit)
    return None


def thread_recall_memories(
    persona_uuid: str,
    query: str = "",
    max_results: int = 10,
    memory_type_filter: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    General-purpose THREAD memory recall for personas.

    Returns None if THREAD unavailable (caller should fall back).
    """
    pt = get_persona_thread()
    if pt and pt.initialized:
        return pt.recall_persona_memories(
            persona_uuid, query, max_results,
            memory_type_filter=memory_type_filter
        )
    return None


def thread_run_reflect() -> Dict[str, Any]:
    """Run THREAD maintenance across all personas."""
    pt = get_persona_thread()
    if pt and pt.initialized:
        return pt.reflect()
    return {"status": "unavailable"}


def thread_get_persona_stats(persona_uuid: str) -> Dict[str, Any]:
    """Get THREAD memory stats for a persona."""
    pt = get_persona_thread()
    if pt and pt.initialized:
        return pt.get_persona_stats(persona_uuid)
    return {"available": False}
