#!/usr/bin/env python3
"""
THREAD Hybrid Retriever — Fuses BM25, graph activation, and vector similarity.

Combines up to 3 signals using Reciprocal Rank Fusion (RRF):
  1. Lexical BM25 (via SQLite FTS5) — keyword matching
  2. Graph Activation (via ActivationEngine) — spreading activation from seed
  3. Vector Similarity (via cosine) — embedding-based semantic search (optional)

RRF score = Σ(1 / (60 + rank_i)) for each signal i, weighted by configurable weights.

Design:
  - Zero-dependency retrieval: all signals computed from SQLite data
  - FTS5 MATCH for BM25 — O(log n) query time
  - SQL-based BFS for activation (delegated to ActivationEngine) — O(d*e)
  - Vector cosine only computed if embeddings are available
  - Results memoized via ActivationCache (passed in constructor)
  - Thread-safe — all calls go through thread-safe sub-modules
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default RRF constant (standard value from literature)
RRF_K = 60


class HybridRetriever:
    """
    Hybrid retrieval system for the THREAD memory graph.

    Combines lexical BM25, graph activation, and optional vector similarity
    signals via Reciprocal Rank Fusion.

    Args:
        graph: A MemoryGraphStore instance.
        activation: An ActivationEngine instance.
        cache: An optional ActivationCache instance for memoization.
        gpu_bridge: Optional ThreadGPUIntegration for GPU-accelerated
            vector similarity search.
    """

    # Threshold for GPU delegation: batch size below this uses CPU
    GPU_VECTOR_THRESHOLD = 50
    # Max CPU fallback batch size to avoid slow pure-Python loops
    CPU_VECTOR_MAX = 500

    def __init__(self, graph: Any, activation: Any, cache: Any = None,
                 gpu_bridge: Optional[Any] = None):
        self.graph = graph
        self.activation = activation
        self.cache = cache
        self.gpu_bridge = gpu_bridge

    # ── Public API ───────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 5,
        weights: Optional[Dict[str, float]] = None,
        include_activation_details: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant nodes using hybrid fusion.

        Args:
            query: Free-text query string.
            query_embedding: Optional embedding vector for semantic search.
            top_k: Maximum results to return (default 5).
            weights: Signal weights dict: {'lexical': float, 'activation': float,
                     'vector': float}. Uses defaults if None.
            include_activation_details: Include per-signal breakdown in results.

        Returns:
            List of result dicts sorted by fusion score (descending).
            Each result contains: node_id, content, category, score, and optionally
            signal_scores if include_activation_details is True.
        """
        resolved_weights = self._resolve_weights(weights)

        # Step 1: Collect candidates from each signal
        lexical_results = self._lexical_search(query, top_k * 3)
        activation_results = self._activation_search(query, top_k * 3)
        vector_results = self._vector_search(query_embedding, top_k * 3)

        # Step 2: RRF fusion
        fused = self._rrf_fuse(
            lexical=lexical_results,
            activation=activation_results,
            vector=vector_results,
            weights=resolved_weights,
            top_k=top_k,
        )

        # Step 3: Enrich results with metadata
        results = self._enrich_results(fused, include_activation_details, resolved_weights)

        return results

    # ── Signal Retrieval ─────────────────────────────────────────

    def _lexical_search(self, query: str, limit: int) -> List[str]:
        """
        BM25 lexical search via FTS5.

        Returns list of node UUIDs sorted by BM25 relevance.
        """
        if not query or not self.graph:
            return []

        try:
            nodes = self.graph.search_nodes(query, limit)
            return [n.get("node_id") or n.get("thought_uuid", "") for n in nodes if n]
        except Exception as e:
            logger.debug(f"HybridRetriever: lexical search failed: {e}")
            return []

    def _activation_search(self, query: str, limit: int) -> List[str]:
        """
        Graph activation search.

        Attempts to find a node matching the query, then spreads activation
        from it. If no matching node found, returns empty.

        Returns list of node UUIDs sorted by activation score.
        """
        if not query or not self.graph or not self.activation:
            return []

        try:
            # Find most relevant node for the query
            nodes = self.graph.search_nodes(query, 1)
            if not nodes:
                return []

            seed_id = nodes[0].get("node_id") or nodes[0].get("thought_uuid", "")
            if not seed_id:
                return []

            # Spread activation from seed
            scores = self.activation.spread_activation(seed_id)
            if not scores:
                return []

            # Sort by score descending
            sorted_nodes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            return [node_id for node_id, _ in sorted_nodes[:limit]]
        except Exception as e:
            logger.debug(f"HybridRetriever: activation search failed: {e}")
            return []

    def _vector_search(
        self,
        query_embedding: Optional[List[float]],
        limit: int,
    ) -> List[str]:
        """
        Vector similarity search via cosine similarity.

        Uses GPU-accelerated batch similarity when available and the
        candidate set is large enough. Falls back to CPU for small sets
        or when GPU is unavailable.

        Only available if query_embedding is provided.
        Returns list of node UUIDs sorted by cosine similarity.
        """
        if query_embedding is None or not self.graph:
            return []

        try:
            # Get all nodes that have embeddings
            conn = self.graph._conn()
            c = conn.cursor()
            c.execute(
                "SELECT thought_uuid, embedding FROM thread_nodes WHERE embedding IS NOT NULL"
            )
            rows = c.fetchall()
            conn.close()

            if not rows:
                return []

            # ── GPU-accelerated batch path ──
            if self.gpu_bridge is not None and len(rows) >= self.GPU_VECTOR_THRESHOLD:
                try:
                    return self._vector_search_gpu(query_embedding, rows, limit)
                except Exception as e:
                    logger.debug(f"GPU vector search failed, falling back to CPU: {e}")

            # ── CPU path ──
            candidates = []
            max_cpu = min(len(rows), self.CPU_VECTOR_MAX)
            for row in rows[:max_cpu]:
                stored_emb_str = row["embedding"]
                if not stored_emb_str:
                    continue
                try:
                    import json as _json
                    stored_emb = _json.loads(stored_emb_str)
                except Exception:
                    continue
                if isinstance(stored_emb, list) and len(stored_emb) == len(query_embedding):
                    sim = self._cosine_similarity(query_embedding, stored_emb)
                    candidates.append((row["thought_uuid"], sim))

            candidates.sort(key=lambda x: x[1], reverse=True)
            return [node_id for node_id, _ in candidates[:limit]]

        except Exception as e:
            logger.debug(f"HybridRetriever: vector search failed: {e}")
            return []

    def _vector_search_gpu(
        self,
        query_embedding: List[float],
        rows: List[Any],
        limit: int,
    ) -> List[str]:
        """
        GPU-accelerated vector similarity search.

        Converts all stored embeddings to a numpy matrix, then delegates
        to gpu_bridge.similarity_search() for batch cosine similarity.
        """
        import numpy as np

        uuids = []
        vectors = []
        for row in rows:
            stored_emb_str = row["embedding"]
            if not stored_emb_str:
                continue
            try:
                import json as _json
                stored_emb = _json.loads(stored_emb_str)
            except Exception:
                continue
            if isinstance(stored_emb, list) and len(stored_emb) == len(query_embedding):
                uuids.append(row["thought_uuid"])
                vectors.append(np.array(stored_emb, dtype=np.float32))

        if not vectors:
            return []

        # Build matrix and query vector
        candidate_matrix = np.stack(vectors)
        query_np = np.array(query_embedding, dtype=np.float32)

        # Run GPU similarity search
        results = self.gpu_bridge.similarity_search(
            query_vector=query_np,
            candidate_vectors=candidate_matrix,
            top_k=limit,
            threshold=0.0,
        )

        # Results are list of (index, score) tuples
        return [uuids[idx] for idx, _ in results[:limit]]

    # ── RRF Fusion ───────────────────────────────────────────────

    def _rrf_fuse(
        self,
        lexical: List[str],
        activation: List[str],
        vector: List[str],
        weights: Dict[str, float],
        top_k: int,
    ) -> List[Tuple[str, float]]:
        """
        Fuse multiple ranked lists using weighted RRF.

        Returns list of (node_id, fusion_score) sorted by fusion score.
        """
        # Build rank maps: node_id -> rank position (0-based)
        all_nodes = {}

        def _add_signal(signal_list: List[str], weight: float):
            if weight <= 0 or not signal_list:
                return
            for rank, node_id in enumerate(signal_list):
                if node_id not in all_nodes:
                    all_nodes[node_id] = 0.0
                # RRF contribution: weight / (RRF_K + rank + 1)
                all_nodes[node_id] += weight / (RRF_K + rank + 1)

        _add_signal(lexical, weights.get("lexical", 0.5))
        _add_signal(activation, weights.get("activation", 0.3))
        _add_signal(vector, weights.get("vector", 0.2))

        # Sort by fusion score descending
        sorted_nodes = sorted(all_nodes.items(), key=lambda x: x[1], reverse=True)
        return sorted_nodes[:top_k]

    # ── Result Enrichment ────────────────────────────────────────

    def _enrich_results(
        self,
        fused: List[Tuple[str, float]],
        include_details: bool,
        weights: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """
        Add metadata to fused results.

        For each node_id in fused results, fetch node data from graph store.
        """
        results = []
        for node_id, fusion_score in fused:
            node = self.graph.get_node(node_id) if self.graph else None
            if node is None:
                continue

            result = {
                "node_id": node_id,
                "content": node.get("content", ""),
                "category": node.get("category", ""),
                "confidence": node.get("confidence", 0.5),
                "importance": node.get("importance", 0.5),
                "session_id": node.get("session_id", ""),
                "thought_number": node.get("thought_number", 0),
                "created_at": node.get("created_at", ""),
                "fusion_score": round(fusion_score, 6),
            }

            if include_details:
                result["signal_scores"] = {
                    "lexical_weight": weights.get("lexical", 0.5),
                    "activation_weight": weights.get("activation", 0.3),
                    "vector_weight": weights.get("vector", 0.2),
                    "fusion_method": "weighted_rrf",
                }

            results.append(result)

        return results

    # ── Weight Resolution ────────────────────────────────────────

    @staticmethod
    def _resolve_weights(
        weights: Optional[Dict[str, float]]
    ) -> Dict[str, float]:
        """Resolve weights with defaults and normalization."""
        if weights is None:
            return {"lexical": 0.5, "activation": 0.3, "vector": 0.2}

        resolved = {
            "lexical": weights.get("lexical", 0.5),
            "activation": weights.get("activation", 0.3),
            "vector": weights.get("vector", 0.2),
        }

        # Normalize to sum = 1.0
        total = sum(resolved.values())
        if total > 0:
            for k in resolved:
                resolved[k] = resolved[k] / total

        return resolved

    # ── Vector Similarity ────────────────────────────────────────

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
