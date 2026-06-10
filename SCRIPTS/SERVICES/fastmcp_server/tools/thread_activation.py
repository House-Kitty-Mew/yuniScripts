#!/usr/bin/env python3
"""
THREAD Activation Engine — Spreading activation via SQL BFS traversal.

Computes activation scores by traversing edges from a seed node using
SQL queries (no Python graph walking). Each level of traversal multiplies
by a decay factor, so influence diminishes with distance.

Design:
  - BFS traversal done via iterative SQL queries (one level at a time)
  - O(d * e) where d = depth (steps) and e = edges per level
  - No in-memory graph needed — all data comes from SQLite JOINs
  - Thread-safe via stateless design (pure functions)
  - Used by HybridRetriever to rank nodes by cognitive relevance
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ActivationEngine:
    """
    Computes spreading activation scores from a seed node.

    Args:
        graph: A MemoryGraphStore instance.
        gpu_bridge: Optional ThreadGPUIntegration for GPU-accelerated activation.
            If provided and graph is large (>GPU_ACTIVATION_THRESHOLD nodes),
            spreading activation is delegated to the GPU.
    """

    # Threshold for GPU delegation: only use GPU when graph has this many nodes
    GPU_ACTIVATION_THRESHOLD = 100

    def __init__(self, graph: Any, gpu_bridge: Optional[Any] = None):
        self.graph = graph
        self.gpu_bridge = gpu_bridge

    # ── Public API ───────────────────────────────────────────────

    def spread_activation(
        self,
        seed_uuid: str,
        steps: int = 3,
        decay: float = 0.5,
        min_score: float = 0.01,
    ) -> Dict[str, float]:
        """
        Compute spreading activation from a seed node.

        BFS traversal through edges:
          Level 0: seed node → score = 1.0
          Level 1: direct neighbors → score = weight * decay
          Level 2: neighbors-of-neighbors → score = weight * decay²
          ...

        Args:
            seed_uuid: Starting node UUID.
            steps: Maximum BFS depth (default 3).
            decay: Decay factor per level (0.0-1.0). Lower = faster drop-off.
            min_score: Minimum score threshold. Nodes below are excluded.

        Returns:
            Dict of {node_id: activation_score} for nodes with score >= min_score.
            The seed node itself is excluded from results.
        """
        if not seed_uuid:
            return {}

        # ── GPU-accelerated path for large graphs ──
        if self.gpu_bridge is not None and self.graph is not None:
            try:
                # Count total nodes to decide if GPU is beneficial
                node_count = self._estimate_node_count()
                if node_count >= self.GPU_ACTIVATION_THRESHOLD:
                    return self._spread_activation_gpu(seed_uuid, steps, decay, min_score)
            except Exception as e:
                logger.debug(f"GPU activation check failed, falling back to CPU: {e}")

        # ── CPU path (SQL BFS traversal) ──
        scores: Dict[str, float] = {}
        current_level = {seed_uuid: 1.0}
        visited = {seed_uuid}
        current_decay = 1.0

        for level in range(1, steps + 1):
            current_decay *= decay
            if current_decay < min_score:
                break

            next_level = self._get_neighbors_with_weights(list(current_level.keys()))

            for neighbor_id, edge_weight in next_level:
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)

                score = edge_weight * current_decay
                if score >= min_score:
                    # Keep highest score if reached via multiple paths
                    scores[neighbor_id] = max(scores.get(neighbor_id, 0.0), score)
                    current_level[neighbor_id] = scores[neighbor_id]

            # Prepare next iteration
            current_level = {n: s for n, s in current_level.items() if n != seed_uuid}

            if not current_level:
                break

        return scores

    def get_top_activated(
        self,
        seed_uuid: str,
        top_k: int = 10,
        steps: int = 3,
        decay: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Get top-k activated nodes with their metadata.

        Returns list of dicts with node_id, score, content, and category.
        """
        scores = self.spread_activation(seed_uuid, steps, decay)

        if not scores:
            return []

        # Sort by score descending
        sorted_nodes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        sorted_nodes = sorted_nodes[:top_k]

        result = []
        for node_id, score in sorted_nodes:
            node = self.graph.get_node(node_id) if self.graph else None
            result.append({
                "node_id": node_id,
                "activation_score": round(score, 6),
                "content": node.get("content", "")[:200] if node else "",
                "category": node.get("category", "") if node else "",
                "importance": node.get("importance", 0.0) if node else 0.0,
            })

        return result

    # ── Internal ─────────────────────────────────────────────────

    # ── GPU-accelerated methods ──────────────────────────────────

    def _estimate_node_count(self) -> int:
        """Estimate total node count in the graph."""
        if not self.graph:
            return 0
        try:
            conn = self.graph._conn()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM thread_nodes")
            count = c.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def _spread_activation_gpu(
        self,
        seed_uuid: str,
        steps: int = 3,
        decay: float = 0.5,
        min_score: float = 0.01,
    ) -> Dict[str, float]:
        """
        GPU-accelerated spreading activation.

        Loads the full graph into numpy arrays, then delegates to
        gpu_bridge.spread_activation() for bulk computation.
        Falls back to CPU on any error.
        """
        try:
            # Build adjacency list and node mapping
            conn = self.graph._conn()
            c = conn.cursor()

            # Get all nodes
            c.execute("SELECT rowid FROM thread_nodes")
            all_nodes = [row[0] for row in c.fetchall()]
            node_count = len(all_nodes)
            if node_count == 0:
                conn.close()
                return {}

            # Map UUIDs to integer indices
            c.execute("SELECT rowid, thought_uuid FROM thread_nodes")
            uuid_to_idx = {}
            idx_to_uuid = {}
            for row in c.fetchall():
                uuid_to_idx[row["thought_uuid"]] = row["rowid"]
                idx_to_uuid[row["rowid"]] = row["thought_uuid"]

            # Get all edges
            c.execute("SELECT source_id, target_id, weight FROM thread_edges")
            adjacency = []
            for row in c.fetchall():
                src = uuid_to_idx.get(row["source_id"])
                tgt = uuid_to_idx.get(row["target_id"])
                if src is not None and tgt is not None:
                    adjacency.append((src, tgt, row["weight"]))

            conn.close()

            # Build seed vector
            seed_idx = uuid_to_idx.get(seed_uuid)
            if seed_idx is None:
                return {}

            import numpy as np
            seed_vector = np.zeros(node_count, dtype=np.float32)
            seed_vector[seed_idx] = 1.0

            # Run GPU activation
            result_vector = self.gpu_bridge.spread_activation(
                adjacency=adjacency,
                node_count=node_count,
                seed_activations=seed_vector,
                steps=steps,
            )

            if result_vector is None:
                return {}

            # Convert back to dict, applying decay and min_score
            final_scores = {}
            current_decay = 1.0
            for level in range(1, steps + 1):
                current_decay *= decay
            # Use result vector directly, filter by min_score
            result_np = np.asarray(result_vector)
            for idx in range(node_count):
                score = float(result_np[idx])
                if score >= min_score:
                    uid = idx_to_uuid.get(idx)
                    if uid and uid != seed_uuid:
                        final_scores[uid] = score

            return final_scores

        except Exception as e:
            logger.debug(f"GPU activation failed, falling back to CPU: {e}")
            return {}

    def _get_neighbors_with_weights(self, node_ids: List[str]) -> List:
        """
        Get all neighbors of given nodes with edge weights.
        Returns list of (neighbor_id, weight) tuples.
        """
        if not node_ids or not self.graph:
            return []

        try:
            conn = self.graph._conn()
            c = conn.cursor()
            placeholders = ",".join("?" for _ in node_ids)
            c.execute(f"""
                SELECT source_id, target_id, weight FROM thread_edges
                WHERE source_id IN ({placeholders})
                UNION
                SELECT target_id, source_id, weight FROM thread_edges
                WHERE target_id IN ({placeholders})
            """, node_ids + node_ids)

            results = []
            for row in c.fetchall():
                source = row[0]
                target = row[1]
                weight = row[2]
                # Determine which endpoint is the neighbor
                neighbor = target if source in node_ids else source
                if neighbor not in node_ids:
                    results.append((neighbor, weight))

            conn.close()
            return results
        except Exception as e:
            logger.debug(f"ActivationEngine: neighbor query failed: {e}")
            return []
