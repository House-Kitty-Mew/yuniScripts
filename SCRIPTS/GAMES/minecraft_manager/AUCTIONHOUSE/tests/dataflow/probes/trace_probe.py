"""
trace_probe.py — Data Flow Tracing System.

Records every operation in a data flow path with timestamps, thread IDs,
and metadata.  Used to verify that the expected code paths were taken
during a test and that no unexpected paths were triggered.

Usage:
    trace = DataFlowTrace()
    with trace.path("core.listing"):
        result = list_item(...)
    trace.verify_path_taken(["core.listing", "db.insert"])
    trace.verify_path_not_taken(["core.bidding"])
"""

import time
import threading
import traceback
from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════════════
# Data Flow Path Constants
# ══════════════════════════════════════════════════════════════════════

# ── Core AH paths ─────────────────────────────────────────────────
PATH_LISTING = "core.listing"
PATH_BIDDING = "core.bidding"
PATH_BUYNOW = "core.buynow"
PATH_CANCEL = "core.cancel"
PATH_EXPIRY = "core.expiry"
PATH_QUERY = "core.query"
PATH_BALANCE = "core.balance"
PATH_SYNC = "core.balance_sync"

# ── AI Engine paths ──────────────────────────────────────────────
PATH_AI_CONTEXT = "ai.context_gathering"
PATH_AI_API_CALL = "ai.api_call"
PATH_AI_PARSE = "ai.parse_response"
PATH_AI_PRICE_ADJUST = "ai.price_adjustment"
PATH_AI_STOCK_ADJUST = "ai.stock_adjustment"
PATH_AI_EVENT = "ai.event_trigger"
PATH_AI_RARE_ITEM = "ai.rare_item_gen"
PATH_AI_STALE_FIX = "ai.stale_recommendation"
PATH_AI_SNAPSHOT = "ai.price_snapshot"
PATH_AI_NOTES = "ai.notes_save"

# ── Extension paths ──────────────────────────────────────────────
PATH_PEOPLE_BEHAVIOR = "people.behavior_tick"
PATH_PEOPLE_FINANCE = "people.finance_tick"
PATH_PEOPLE_NEEDS = "people.needs_check"
PATH_PEOPLE_PURCHASE = "people.purchase"

PATH_SOCIAL_BOREDOM = "social.boredom_tick"
PATH_SOCIAL_EXHAUSTION = "social.exhaustion_tick"
PATH_SOCIAL_ACTIVITY = "social.activity_planning"
PATH_SOCIAL_CRISIS = "social.crisis_mode"

PATH_RELS_CONTENTION = "rels.cell_contention"
PATH_RELS_SKILL_DECAY = "rels.skill_decay"
PATH_RELS_MARRIAGE = "rels.marriage_check"
PATH_RELS_SITUATIONSHIP = "rels.situationship"

PATH_CHAT_MESSAGE = "chat.message_processing"
PATH_CHAT_AI_RESPONSE = "chat.ai_response_gen"
PATH_CHAT_INTEREST = "chat.interest_tracking"

PATH_ANNOUNCE_FILTER = "announce.ai_filter"
PATH_ANNOUNCE_DELIVERY = "announce.delivery"

PATH_TRADE_ROUTES = "trade.route_processing"
PATH_TRADE_BANDITRY = "trade.banditry"
PATH_TRADE_ECONOMY = "trade.economy_tick"
PATH_TRADE_WORLD_EVENT = "trade.world_event_impact"

PATH_HEALTH_BLOOD = "health.blood_tick"
PATH_HEALTH_MUSCLE = "health.muscle_tick"
PATH_HEALTH_DISEASE = "health.disease_tick"
PATH_HEALTH_IMMUNE = "health.immune_tick"
PATH_HEALTH_HYGIENE = "health.hygiene_tick"
PATH_HEALTH_PAIN = "health.pain_tick"
PATH_HEALTH_COMBAT = "health.combat_skill_tick"
PATH_HEALTH_CRITICAL = "health.critical_decision"
PATH_HEALTH_GENETICS = "health.genetics_tick"
PATH_HEALTH_ANATOMY = "health.anatomy_tick"

# ── Economy paths ────────────────────────────────────────────────
PATH_ECO_BRIDGE_CHECK = "economy.bridge_check"
PATH_ECO_BRIDGE_DEDUCT = "economy.bridge_deduct"
PATH_ECO_BRIDGE_CREDIT = "economy.bridge_credit"
PATH_ECO_FALLBACK = "economy.fallback_table"
PATH_ECO_RCON_FALLBACK = "economy.rcon_fallback"

# ── Registry paths ───────────────────────────────────────────────
PATH_STATE_REGISTRY_WRITE = "state_registry.write"
PATH_STATE_REGISTRY_READ = "state_registry.read"
PATH_STATE_REGISTRY_CLEAR = "state_registry.clear"

# ── Hook paths ──────────────────────────────────────────────────
PATH_HOOK_FIRE = "hook.fire"
PATH_HOOK_EXTENSION_OK = "hook.extension_ok"
PATH_HOOK_EXTENSION_FAIL = "hook.extension_fail"

# ── DB paths ─────────────────────────────────────────────────────
PATH_DB_INSERT = "db.insert"
PATH_DB_UPDATE = "db.update"
PATH_DB_DELETE = "db.delete"
PATH_DB_SELECT = "db.select"
PATH_DB_ATOMIC_UPDATE = "db.atomic_update"
PATH_DB_TRANSACTION = "db.transaction"


# ══════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════

@dataclass
class FlowStep:
    """A single step in a data flow path trace."""
    path: str
    action: str
    metadata: dict = field(default_factory=dict)
    timestamp: float = 0.0
    thread_id: int = 0
    call_stack: str = ""


# ══════════════════════════════════════════════════════════════════════
# Data Flow Trace
# ══════════════════════════════════════════════════════════════════════

class DataFlowTrace:
    """Records every data flow step with timestamps for verification.

    Thread-safe: uses per-thread buffers merged on read.
    """

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._local = threading.local()
        self._lock = threading.Lock()
        self._merged_trace: list[FlowStep] = []

    @property
    def _buffer(self) -> list[FlowStep]:
        """Get or create the per-thread buffer."""
        if not hasattr(self._local, 'buffer'):
            self._local.buffer = []
        return self._local.buffer

    def record(self, path: str, action: str, **metadata) -> None:
        """Record a flow step.

        Args:
            path: Dot-separated path string (use PATH_* constants)
            action: Verb describing the action (e.g. 'start', 'success', 'fail')
            **metadata: Arbitrary key-value pairs to attach
        """
        if not self._enabled:
            return
        step = FlowStep(
            path=path,
            action=action,
            metadata=metadata,
            timestamp=time.time(),
            thread_id=threading.get_ident(),
            call_stack=''.join(traceback.format_stack(limit=6)[:-1])
        )
        self._buffer.append(step)

    def flush(self) -> None:
        """Merge per-thread buffers into the global trace list."""
        with self._lock:
            for step in self._buffer:
                self._merged_trace.append(step)
            self._buffer.clear()

    def get_trace(self) -> list[FlowStep]:
        """Get all recorded steps (flushes first)."""
        self.flush()
        with self._lock:
            return list(self._merged_trace)

    def clear(self) -> None:
        """Clear all recorded traces."""
        self.flush()
        with self._lock:
            self._merged_trace.clear()
        if hasattr(self._local, 'buffer'):
            self._local.buffer.clear()

    def get_paths_taken(self) -> list[str]:
        """Return sorted unique paths that were recorded."""
        return sorted(set(s.path for s in self.get_trace()))

    def get_paths_by_prefix(self, prefix: str) -> list[str]:
        """Return unique paths starting with a given prefix."""
        return sorted(set(
            s.path for s in self.get_trace()
            if s.path.startswith(prefix)
        ))

    # ── Context manager ──────────────────────────────────────────

    def path(self, path: str, **extra_meta):
        """Context manager that records path entry and exit.

        Usage:
            with trace.path("core.listing", seller="Alice"):
                result = list_item(...)
        """
        return _PathContext(self, path, extra_meta)

    # ── Verification ─────────────────────────────────────────────

    def verify_path_taken(self, expected_paths: list[str],
                          description: str = "") -> None:
        """Assert that all expected paths were taken.

        Args:
            expected_paths: List of path strings that MUST appear
            description: Optional assertion description

        Raises:
            AssertionError: If any expected path was not taken
        """
        taken = self.get_paths_taken()
        missing = [p for p in expected_paths if p not in taken]
        if missing:
            msg = f"Missing expected paths: {missing}"
            if description:
                msg += f" ({description})"
            msg += f"\nPaths taken: {taken}"
            raise AssertionError(msg)

    def verify_path_not_taken(self, forbidden_paths: list[str],
                              description: str = "") -> None:
        """Assert that forbidden paths were NOT taken.

        Args:
            forbidden_paths: List of path strings that MUST NOT appear
            description: Optional assertion description

        Raises:
            AssertionError: If any forbidden path was found
        """
        taken = self.get_paths_taken()
        found = [p for p in forbidden_paths if p in taken]
        if found:
            msg = f"Forbidden paths were taken: {found}"
            if description:
                msg += f" ({description})"
            msg += f"\nPaths taken: {taken}"
            raise AssertionError(msg)

    def verify_path_order(self, expected_sequence: list[str],
                          description: str = "") -> None:
        """Assert that paths appear in a specific order in the trace.

        Args:
            expected_sequence: Ordered list of paths that must appear
                              in this relative order (other paths may
                              interleave)
            description: Optional assertion description

        Raises:
            AssertionError: If paths don't appear in expected order
        """
        trace = self.get_trace()
        trace_paths = [s.path for s in trace]
        idx = 0
        for expected in expected_sequence:
            try:
                idx = trace_paths.index(expected, idx) + 1
            except ValueError:
                msg = f"Path '{expected}' not found after position {idx}"
                if description:
                    msg += f" ({description})"
                raise AssertionError(msg)

    def get_step_count(self, path: str = None) -> int:
        """Count steps, optionally filtered by path."""
        if path is None:
            return len(self.get_trace())
        return sum(1 for s in self.get_trace() if s.path == path)

    def first_step(self, path: str) -> FlowStep:
        """Get the first recorded step for a given path."""
        for s in self.get_trace():
            if s.path == path:
                return s
        raise KeyError(f"No steps recorded for path: {path}")

    def summary(self) -> str:
        """Return a human-readable summary of all recorded paths."""
        traces = self.get_trace()
        if not traces:
            return "No data flow traces recorded."

        lines = [f"=== Data Flow Trace: {len(traces)} steps ==="]
        for step in traces:
            meta_str = ", ".join(f"{k}={v}" for k, v in step.metadata.items())
            lines.append(
                f"  [{step.action:8s}] {step.path}"
                f"{' | ' + meta_str if meta_str else ''}"
                f"  (t={step.thread_id})"
            )

        # Summary by path
        from collections import Counter
        path_counts = Counter(s.path for s in traces)
        lines.append(f"\n--- Path Summary ({len(path_counts)} unique) ---")
        for path, count in sorted(path_counts.items()):
            lines.append(f"  {path}: {count}x")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Path context manager
# ══════════════════════════════════════════════════════════════════════

class _PathContext:
    """Context manager for tracing path entry/exit."""

    def __init__(self, trace: DataFlowTrace, path: str,
                 extra_meta: dict = None):
        self._trace = trace
        self._path = path
        self._extra = extra_meta or {}

    def __enter__(self):
        self._trace.record(self._path, "enter", **self._extra)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._trace.record(
                self._path, "exit_error",
                error=str(exc_val),
                error_type=exc_type.__name__
            )
        else:
            self._trace.record(self._path, "exit_ok")
        return False  # Don't suppress exceptions


# ── Global singleton for test sessions ──────────────────────────────
_global_trace: Optional[DataFlowTrace] = None


def get_trace() -> DataFlowTrace:
    """Get or create the global DataFlowTrace singleton."""
    global _global_trace
    if _global_trace is None:
        _global_trace = DataFlowTrace()
    return _global_trace


def reset_trace() -> None:
    """Clear and reset the global trace."""
    global _global_trace
    if _global_trace is not None:
        _global_trace.clear()
    _global_trace = DataFlowTrace()
