"""
tool_queue.py - Prioritized Async Tool Queue with AI Feedback

Replaces the simple semaphore-based concurrency limiter with a proper
prioritized async queue that:
1. Assigns priority levels to tool calls
2. Tracks queue position and estimates wait time
3. Emits periodic feedback messages so the AI knows what's queued
4. Caps concurrency to prevent host overloading
5. Supports fair ordering within equal-priority tools
"""

import asyncio
import time
import logging
import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any, Tuple
from enum import IntEnum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Priority levels
# ---------------------------------------------------------------------------
class ToolPriority(IntEnum):
    CRITICAL   = 1
    HIGH       = 2
    NORMAL     = 3
    LOW        = 4
    BACKGROUND = 5

# ---------------------------------------------------------------------------
# Tool -> priority mapping
# ---------------------------------------------------------------------------
# ── Stale item expiry ─────────────────────────────────────────────────
# When a client disconnects mid-request, its QueueItem stays in the heap
# forever, blocking ALL subsequent tools. This expiry prevents that deadlock.
MAX_QUEUE_WAIT_SECONDS = 300  # 5 minutes max wait in queue

_TOOL_PRIORITY_MAP: Dict[str, ToolPriority] = {
    "process_monitor":         ToolPriority.CRITICAL,
    "process_cleanup":         ToolPriority.CRITICAL,
    "process_watchdog_status": ToolPriority.CRITICAL,
    "god_watcher_admin":       ToolPriority.CRITICAL,
    "cpu_guard":               ToolPriority.CRITICAL,
    "backup_undo":             ToolPriority.CRITICAL,
    "sequentialthinking":      ToolPriority.HIGH,
    "thinking":                ToolPriority.HIGH,
    "read_files":              ToolPriority.HIGH,
    "read_big_file":           ToolPriority.HIGH,
    "write_file":              ToolPriority.HIGH,
    "write_files":             ToolPriority.HIGH,
    "write_big_file":          ToolPriority.HIGH,
    "edit_text":               ToolPriority.HIGH,
    "text_replace":            ToolPriority.HIGH,
    "execute_command":         ToolPriority.HIGH,
    "execute_commands":        ToolPriority.HIGH,
    "execute_python":          ToolPriority.HIGH,
    "execute_protected":       ToolPriority.HIGH,
    "find_for_me":             ToolPriority.NORMAL,
    "grep_search":             ToolPriority.NORMAL,
    "file_search":             ToolPriority.NORMAL,
    "search_files":            ToolPriority.NORMAL,
    "search_conversations":    ToolPriority.NORMAL,
    "search_messages":         ToolPriority.NORMAL,
    "fetch":                   ToolPriority.NORMAL,
    "get_web_info":            ToolPriority.NORMAL,
    "documentation_search":    ToolPriority.NORMAL,
    "query_docs":              ToolPriority.NORMAL,
    "query_db":                ToolPriority.NORMAL,
    "exec_on_db":              ToolPriority.NORMAL,
    "database_query":          ToolPriority.NORMAL,
    "database_execute":        ToolPriority.NORMAL,
    "database_list_tables":    ToolPriority.NORMAL,
    "database_table_schema":   ToolPriority.NORMAL,
    "database_create":         ToolPriority.NORMAL,
    "create_work_order":       ToolPriority.NORMAL,
    "work_order_runner":       ToolPriority.NORMAL,
    "github_upload":           ToolPriority.LOW,
    "gpu_bridge":              ToolPriority.LOW,
    "veracrypt_admin":         ToolPriority.LOW,
    "sync_tool_registry":      ToolPriority.LOW,
    "reload_tool_definitions": ToolPriority.LOW,
}

def get_tool_priority(tool_name: str) -> ToolPriority:
    return _TOOL_PRIORITY_MAP.get(tool_name, ToolPriority.NORMAL)

# ---------------------------------------------------------------------------
# Per-priority capacity limits
# ---------------------------------------------------------------------------
_PRIORITY_CAPACITY: Dict[ToolPriority, int] = {
    ToolPriority.CRITICAL:   4,
    ToolPriority.HIGH:       3,
    ToolPriority.NORMAL:     2,
    ToolPriority.LOW:        1,
    ToolPriority.BACKGROUND: 1,
}

_PRIORITY_SEMAPHORES: Dict[ToolPriority, "asyncio.Semaphore"] = {}
_SEMAPHORE_LOCK = asyncio.Lock()

async def _get_priority_semaphore(priority: ToolPriority) -> "asyncio.Semaphore":
    if priority not in _PRIORITY_SEMAPHORES:
        async with _SEMAPHORE_LOCK:
            if priority not in _PRIORITY_SEMAPHORES:
                _PRIORITY_SEMAPHORES[priority] = asyncio.Semaphore(
                    _PRIORITY_CAPACITY.get(priority, 2)
                )
    return _PRIORITY_SEMAPHORES[priority]

# ---------------------------------------------------------------------------
# Queue item
# ---------------------------------------------------------------------------
@dataclass
class QueueItem:
    priority: int
    enqueue_time: float
    tool_name: str = field(compare=False)
    tool_args: Dict[str, Any] = field(compare=False, default_factory=dict)
    job_id: str = field(compare=False, default="")
    sequence: int = field(compare=False, default=0)

    def __lt__(self, other):
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.enqueue_time < other.enqueue_time

# ---------------------------------------------------------------------------
# Queue Manager
# ---------------------------------------------------------------------------
class ToolQueueManager:
    def __init__(self):
        self._queue: List[QueueItem] = []
        self._sequence: int = 0
        self._running: Dict[str, int] = {}
        self._running_total: int = 0
        self._lock = asyncio.Lock()
        self._feedback_callback: Optional[Callable] = None
        self._recent_runtimes: Dict[str, List[float]] = {}

    def set_feedback_callback(self, callback: Optional[Callable]):
        self._feedback_callback = callback

    def _emit_feedback(self, message: str, level: str = "info", context: str = ""):
        if self._feedback_callback:
            try:
                self._feedback_callback(message, level, context)
            except Exception:
                pass

    def record_runtime(self, tool_name: str, duration: float):
        if tool_name not in self._recent_runtimes:
            self._recent_runtimes[tool_name] = []
        runtimes = self._recent_runtimes[tool_name]
        runtimes.append(duration)
        if len(runtimes) > 10:
            runtimes.pop(0)

    def _estimate_wait_time(self, tool_name: str) -> float:
        avg_runtime = 10.0
        if tool_name in self._recent_runtimes and self._recent_runtimes[tool_name]:
            runtimes = self._recent_runtimes[tool_name]
            avg_runtime = sum(runtimes) / len(runtimes)
        return avg_runtime * 0.5

    async def enqueue(self, tool_name: str, tool_args: Dict[str, Any], job_id: str = "") -> Tuple[int, float]:
        # Expire stale items on every enqueue call to keep queue clean
        async with self._lock:
            self._expire_stale_items()

        priority = get_tool_priority(tool_name)
        async with self._lock:
            self._sequence += 1
            item = QueueItem(
                priority=priority.value,
                enqueue_time=time.monotonic(),
                tool_name=tool_name,
                tool_args=tool_args,
                job_id=job_id,
                sequence=self._sequence,
            )
            heapq.heappush(self._queue, item)
            position = len(self._queue)
            wait_estimate = self._estimate_wait_time(tool_name)

        self._emit_feedback(
            f"[QUEUE] '{tool_name}' enqueued at position #{position} "
            f"(priority={priority.name}, ~{wait_estimate:.0f}s estimated wait)",
            level="info",
            context=f"queue:{job_id}"
        )
        return position, wait_estimate

    def _expire_stale_items(self) -> int:
        """Remove queue items abandoned by disconnected clients.

        Scans the heap for items whose enqueue_time is older than
        MAX_QUEUE_WAIT_SECONDS and removes them.  Returns the count of
        expired items.
        """
        now = time.monotonic()
        new_queue = []
        expired = 0
        for item in self._queue:
            age = now - item.enqueue_time
            if age > MAX_QUEUE_WAIT_SECONDS:
                expired += 1
                self._emit_feedback(
                    f"[QUEUE] Expired stale '{item.tool_name}' (job={item.job_id}, "
                    f"waited {age:.0f}s)",
                    level="warning",
                    context="queue:expiry"
                )
            else:
                new_queue.append(item)
        self._queue = new_queue
        heapq.heapify(self._queue)
        return expired

    async def acquire_slot(self, tool_name: str, job_id: str = "") -> bool:
        while True:
            # ── Expire stale items that may be blocking the queue ─────
            async with self._lock:
                expired = self._expire_stale_items()
                if expired:
                    self._emit_feedback(
                        f"[QUEUE] Expired {expired} stale item(s), queue now has {len(self._queue)} items",
                        level="info",
                        context="queue:expiry"
                    )

            our_item: Optional[QueueItem] = None
            async with self._lock:
                if not self._queue:
                    return True
                front = self._queue[0]
                if front.tool_name == tool_name and front.job_id == job_id:
                    our_item = heapq.heappop(self._queue)

            if our_item:
                priority = get_tool_priority(tool_name)
                sem = await _get_priority_semaphore(priority)
                try:
                    await asyncio.wait_for(sem.acquire(), timeout=120)
                except asyncio.TimeoutError:
                    self._emit_feedback(
                        f"[QUEUE] '{tool_name}' timed out waiting for slot",
                        level="warning",
                        context=f"queue:{job_id}"
                    )
                    async with self._lock:
                        heapq.heappush(self._queue, our_item)
                    return False

                async with self._lock:
                    self._running[tool_name] = self._running.get(tool_name, 0) + 1
                    self._running_total += 1
                self._emit_feedback(
                    f"[QUEUE] '{tool_name}' started (running: {self._running_total})",
                    level="info",
                    context=f"queue:{job_id}"
                )
                return True

            async with self._lock:
                position = next(
                    (i for i, item in enumerate(self._queue)
                     if item.tool_name == tool_name and item.job_id == job_id),
                    -1
                )
                if position >= 0:
                    wait_estimate = self._estimate_wait_time(tool_name)
                    self._emit_feedback(
                        f"[QUEUE] '{tool_name}' waiting at position #{position + 1} "
                        f"(~{wait_estimate:.0f}s estimated)",
                        level="info",
                        context=f"queue:{job_id}"
                    )
            await asyncio.sleep(1.0)

    def release_slot(self, tool_name: str):
        priority = get_tool_priority(tool_name)
        sem = _PRIORITY_SEMAPHORES.get(priority)
        if sem:
            try:
                sem.release()
            except (ValueError, RuntimeError):
                pass
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._decrement_running(tool_name))
        except RuntimeError:
            pass

    async def _decrement_running(self, tool_name: str):
        async with self._lock:
            self._running[tool_name] = max(0, self._running.get(tool_name, 0) - 1)
            self._running_total = max(0, self._running_total - 1)

    def get_queue_status(self) -> Dict[str, Any]:
        return {
            "queue_depth": len(self._queue),
            "running_total": self._running_total,
            "running_by_tool": dict(self._running),
        }


# ===== Global singleton =====
_global_queue_manager: Optional["ToolQueueManager"] = None

def get_queue_manager() -> "ToolQueueManager":
    global _global_queue_manager
    if _global_queue_manager is None:
        _global_queue_manager = ToolQueueManager()
    return _global_queue_manager
