"""
state_probe.py — Shared Extension State Registry Tracing.

Monitors all reads and writes to the cross-extension state_registry
to verify Phase 2C ordering, namespace isolation, and data integrity.

Usage:
    state_probe = StateProbe()
    state_probe.attach()  # Start monitoring
    
    # ... run simulation cycle ...
    
    state_probe.assert_phase2c_ordering()
    state_probe.assert_no_state_leakage("SIMULATED_SOCIAL")
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from collections import OrderedDict


# ── Expected Phase 2C ordering ──────────────────────────────────────
#
# 1. PEOPLE     → writes: active_personas, needs, finances, purchases
# 2. SOCIAL     → reads: active_personas, weather, world_events
#                 writes: social_interactions, activities_planned
# 3. RELS       → reads: social_interactions
#                 writes: relationship_changes, contentions
# 4. CHAT       → reads: relationships
#                 writes: chat_messages, interest_levels
# 5. ANNOUNCE   → reads: chat_messages, purchases, events
#                 writes: announcements, delivered
# 6. TRADE      → reads/writes: trade_routes, banditry, economy
# 7. HEALTH     → reads: persona data
#                 writes: health_stats, disease_spread

PHASE2C_ORDER = [
    "SIMULATED_PEOPLE",
    "SIMULATED_SOCIAL",
    "SIMULATED_RELATIONSHIPS",
    "SIMULATED_CHAT",
    "SIMULATED_ANNOUNCE",
    "SIMULATED_TRADE",
    "SIMULATED_HEALTH_MECHANICS",
]

# Extension → expected keys they WRITE
EXPECTED_WRITES = {
    "SIMULATED_PEOPLE": {
        "active_personas", "persona_needs", "persona_finances",
        "persona_purchases", "weather", "world_events",
    },
    "SIMULATED_SOCIAL": {
        "social_interactions", "activities_planned", "boredom_states",
        "exhaustion_states", "crisis_personas",
    },
    "SIMULATED_RELATIONSHIPS": {
        "relationship_changes", "cell_contentions", "recent_relationship_interactions",
    },
    "SIMULATED_CHAT": {
        "chat_messages", "interest_levels", "pending_notifications",
    },
    "SIMULATED_ANNOUNCE": {
        "announcements", "delivered_announcements",
    },
    "SIMULATED_TRADE": {
        "trade_events", "route_updates",
    },
    "SIMULATED_HEALTH_MECHANICS": {
        "health_stats", "disease_spread_events",
    },
}

# Extension → expected keys they READ
EXPECTED_READS = {
    "SIMULATED_PEOPLE": set(),  # PEOPLE writes first, reads nothing state-wise
    "SIMULATED_SOCIAL": {"active_personas", "weather", "world_events"},
    "SIMULATED_RELATIONSHIPS": {"social_interactions"},
    "SIMULATED_CHAT": {"relationship_changes"},
    "SIMULATED_ANNOUNCE": {"chat_messages", "persona_purchases",
                            "world_events"},
    "SIMULATED_TRADE": {"active_personas", "persona_finances",
                         "world_events"},
    "SIMULATED_HEALTH_MECHANICS": {"active_personas"},
}


@dataclass
class StateAccess:
    """A single state registry read or write."""
    key: str
    extension: str
    access_type: str  # "read" or "write"
    value_preview: str = ""
    timestamp: float = 0.0
    thread_id: int = 0


# ══════════════════════════════════════════════════════════════════════
# State Probe
# ══════════════════════════════════════════════════════════════════════

class StateProbe:
    """Monitors and verifies shared state registry access patterns."""

    def __init__(self, trace=None):
        self._accesses: list[StateAccess] = []
        self._lock = threading.Lock()
        self._attached = False
        self._original_set = None
        self._original_get = None
        self._trace_ref = trace

    # ── Lifecycle ────────────────────────────────────────────────

    def attach(self):
        """Start monitoring the state registry."""
        if self._attached:
            return
        try:
            from EXTENSIONS.state_registry import get_state
            state = get_state()
            self._original_set = state.set
            self._original_get = state.get

            def traced_set(key, value, extension):
                self._record_access(key, extension, "write",
                                    str(value)[:100])
                return self._original_set(key, value, extension)

            def traced_get(key, default=None):
                # Try to infer extension from call stack
                ext = self._infer_extension()
                self._record_access(key, ext, "read",
                                    str(default)[:100] if default else "")
                return self._original_get(key, default)

            state.set = traced_set
            state.get = traced_get
            self._attached = True
        except ImportError:
            raise RuntimeError(
                "Cannot attach StateProbe: state_registry not importable. "
                "Run from within the AH project context."
            )

    def detach(self):
        """Stop monitoring and restore original methods."""
        if not self._attached:
            return
        try:
            from EXTENSIONS.state_registry import get_state
            state = get_state()
            state.set = self._original_set
            state.get = self._original_get
            self._attached = False
        except ImportError:
            pass

    def _infer_extension(self) -> str:
        """Try to infer which extension is accessing state from the call stack."""
        try:
            import traceback

        except Exception as e:
            logger.error(f"_infer_extension failed: {e}")
            return ""
        stack = traceback.extract_stack()
        for frame in stack:
            filename = frame.filename
            if "SIMULATED_PEOPLE" in filename:
                return "SIMULATED_PEOPLE"
            elif "SIMULATED_SOCIAL" in filename:
                return "SIMULATED_SOCIAL"
            elif "SIMULATED_RELATIONSHIPS" in filename:
                return "SIMULATED_RELATIONSHIPS"
            elif "SIMULATED_CHAT" in filename:
                return "SIMULATED_CHAT"
            elif "SIMULATED_ANNOUNCE" in filename:
                return "SIMULATED_ANNOUNCE"
            elif "SIMULATED_TRADE" in filename:
                return "SIMULATED_TRADE"
            elif "SIMULATED_HEALTH_MECHANICS" in filename:
                return "SIMULATED_HEALTH_MECHANICS"
        return "UNKNOWN"

    def _record_access(self, key: str, extension: str,
                       access_type: str, value_preview: str = ""):
        """Record a state registry access."""
        access = StateAccess(
            key=key,
            extension=extension,
            access_type=access_type,
            value_preview=value_preview,
            timestamp=time.time(),
            thread_id=threading.get_ident(),
        )
        with self._lock:
            self._accesses.append(access)

        if self._trace_ref:
            self._trace_ref.record(
                f"state_registry.{access_type}",
                "accessed",
                key=key,
                extension=extension,
            )

    def clear(self):
        """Clear all recorded accesses."""
        with self._lock:
            self._accesses.clear()

    # ── Query ────────────────────────────────────────────────────

    def get_accesses(self) -> list[StateAccess]:
        """Get all recorded state registry accesses."""
        with self._lock:
            return list(self._accesses)

    def get_writes_by(self, extension: str) -> list[StateAccess]:
        """Get all writes by a specific extension."""
        return [a for a in self.get_accesses()
                if a.extension == extension and a.access_type == "write"]

    def get_reads_by(self, extension: str) -> list[StateAccess]:
        """Get all reads by a specific extension."""
        return [a for a in self.get_accesses()
                if a.extension == extension and a.access_type == "read"]

    # ── Verification ─────────────────────────────────────────────

    def assert_phase2c_ordering(self):
        """Assert that extensions accessed state in the correct Phase 2C order.

        For each pair of consecutive extensions, the first extension's writes
        must all happen before the second extension's reads.
        """
        try:
            accesses = self.get_accesses()

        except Exception as e:
            logger.error(f"assert_phase2c_order failed: {e}")
            return None
        if not accesses:
            raise AssertionError("No state accesses recorded - can't verify ordering")

        # Get timestamps for the first write of each extension
        first_write: dict[str, float] = {}
        for access in accesses:
            if access.access_type == "write":
                ext = access.extension
                if ext not in first_write:
                    first_write[ext] = access.timestamp

        # Get timestamps for the last read/write of each extension
        last_access: dict[str, float] = {}
        for access in accesses:
            last_access[access.extension] = access.timestamp

        violations = []
        for i in range(len(PHASE2C_ORDER)):
            ext = PHASE2C_ORDER[i]
            if ext not in first_write:
                violations.append(f"Extension '{ext}' never wrote to state")
                continue

        # Check that ext[i] finishes before ext[i+1] starts
        for i in range(len(PHASE2C_ORDER) - 1):
            current = PHASE2C_ORDER[i]
            next_ext = PHASE2C_ORDER[i + 1]

            if current not in last_access or next_ext not in first_write:
                continue

            if last_access.get(current, 0) > first_write.get(next_ext, float('inf')):
                violations.append(
                    f"Order violation: '{current}' last access "
                    f"({last_access[current]:.3f}) after '{next_ext}' "
                    f"first write ({first_write[next_ext]:.3f})"
                )

        if violations:
            raise AssertionError(
                "Phase 2C ordering violations:\n  " + "\n  ".join(violations)
            )

    def assert_no_state_leakage(self, extension: str):
        """Assert that an extension only read state that was written before it.

        For SIMULATED_PEOPLE (first in order), it should only read its own writes.
        For others, they should only read state written by earlier extensions.
        """
        try:
            accesses = self.get_accesses()

        except Exception as e:
            logger.error(f"assert_no_state_leak failed: {e}")
            return None
        reads = self.get_reads_by(extension)
        writes_before: dict[str, str] = {}  # key → writing extension

        # Build map of what was written before this extension started
        ext_index = PHASE2C_ORDER.index(extension) if extension in PHASE2C_ORDER else -1
        if ext_index <= 0:
            return  # First extension - no prior state to check

        earlier_exts = set(PHASE2C_ORDER[:ext_index]) | {extension}

        for access in accesses:
            if access.access_type == "write":
                writes_before[access.key] = access.extension

        # Check each read
        violations = []
        for read in reads:
            if read.key in writes_before:
                writer = writes_before[read.key]
                expected_reader_exts = PHASE2C_ORDER[:PHASE2C_ORDER.index(writer) + 1]
                if extension not in expected_reader_exts and writer != extension:
                    violations.append(
                        f"'{extension}' read '{read.key}' written by '{writer}'"
                    )

        if violations:
            raise AssertionError(
                f"State leakage detected for '{extension}':\n  "
                + "\n  ".join(violations)
            )

    def assert_expected_keys_written(self, extension: str):
        """Assert that an extension wrote all expected keys."""
        try:
            expected = EXPECTED_WRITES.get(extension, set())

        except Exception as e:
            logger.error(f"assert_expected_keys failed: {e}")
            return None
        actual = set(a.key for a in self.get_writes_by(extension))

        missing = expected - actual
        unexpected = actual - expected

        violations = []
        if missing:
            violations.append(f"Missing expected writes: {missing}")
        if unexpected:
            violations.append(f"Unexpected writes: {unexpected}")

        if violations:
            raise AssertionError(
                f"Key mismatch for '{extension}':\n  "
                + "\n  ".join(violations)
            )

    def assert_clear_between_cycles(self):
        """Assert that the state was cleared at the start of a cycle."""
        accesses = self.get_accesses()
        clears = [a for a in accesses
                  if a.key == "__clear__" or a.access_type == "clear"]
        if not clears:
            raise AssertionError(
                "State registry was never cleared between cycles"
            )

    def summary(self) -> str:
        """Return a human-readable summary of all state registry activity."""
        try:
            accesses = self.get_accesses()

        except Exception as e:
            logger.error(f"summary failed: {e}")
            return ""
        if not accesses:
            return "No state registry accesses recorded."

        # Group by extension
        ext_access: dict[str, list[StateAccess]] = {}
        for a in accesses:
            ext = a.extension
            if ext not in ext_access:
                ext_access[ext] = []
            ext_access[ext].append(a)

        lines = [
            f"=== State Registry Probe: {len(accesses)} accesses ===",
        ]

        for ext in PHASE2C_ORDER:
            if ext not in ext_access:
                continue
            writes = [a for a in ext_access[ext] if a.access_type == "write"]
            reads = [a for a in ext_access[ext] if a.access_type == "read"]
            lines.append(f"\n  {ext}:")
            lines.append(f"    Writes ({len(writes)}): "
                         f"{set(a.key for a in writes)}")
            lines.append(f"    Reads ({len(reads)}): "
                         f"{set(a.key for a in reads)}")

        return "\n".join(lines)

