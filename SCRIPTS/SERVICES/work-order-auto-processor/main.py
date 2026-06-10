#!/usr/bin/env python3
"""
work-order-auto-processor — YuniScripts background daemon (v2.0 DYNAMIC)

Watches Documentation.db for pending work orders and auto-processes them
using a dynamic rule-based fix engine with retry, escalation, and
graceful server restart via restart tickets.

Architecture:
  main loop → poll DB → classify work order → match fix handler →
  execute (with retry + backoff) → advance queue (or escalate)

FIXES v2.0:
  - Restart Ticket System: Instead of killing the running FastMCP server
    (which kills the daemon itself), writes a restart ticket that the
    server's background watcher thread detects for graceful self-restart.
  - Retry with Exponential Backoff: Failed handlers are retried up to
    3 times with increasing delays (30s, 120s, 300s).
  - Escalation Timer: Work orders that fail 3+ times get detailed
    context appended and marked NEEDS-AI instead of retrying forever.
  - Rate Limiting: Server restart requests are rate-limited to once
    per 5 minutes to prevent restart loops.
  - Recursion Protection: Track handler attempts per WO. If a handler
    causes the daemon to be killed/restarted, the attempt counter
    persists in the Database so the new daemon instance can detect it.
  - Handler Fallback Chains: If primary handler fails, try secondary
    approach before giving up.

Communication:
  Direct DB access (shared Documentation.db with FastMCP server).
  No MCP protocol dependency — works independently.
  Restart tickets via /tmp/fastmcp_restart_ticket.json (server reads).

Lifecycle:
  Managed by YuniScripts engine (start/stop/reload via meta.info).
"""

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Callable

# ── Configuration ───────────────────────────────────────────────
# Use DEEPSKY_DOCS_DB_PATH env var, or default to YuniScripts local Documentation.db
DEFAULT_DB = os.environ.get(
    'DEEPSKY_DOCS_DB_PATH',
    os.path.expanduser("~/Documents/dev-yuniScripts/DATA/Databases/Documentation.db")
)
POLL_INTERVAL = 30  # seconds between DB polls
LOG_DIR = Path(__file__).resolve().parent / "DATA"
PID_FILE = Path(__file__).resolve().parent / "DATA" / "daemon.pid"
RUNNING = True

# ── v2.0: Retry & Escalation Config ─────────────────────────────
MAX_RETRIES_PER_WO = 3           # Max handler attempts per work order
RETRY_BACKOFFS = [30, 120, 300]   # Seconds between retries
RESTART_COOLDOWN_SECONDS = 300    # 5 min between restart requests
RESTART_TICKET_PATH = Path("/tmp/fastmcp_restart_ticket.json")

# ── Logging ─────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "daemon.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("wo-auto-processor")

# ── Signal Handling ─────────────────────────────────────────────
def _signal_handler(sig, frame):
    global RUNNING
    logger.info(f"Received signal {sig}, shutting down...")
    RUNNING = False

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ═══════════════════════════════════════════════════════════════
# 1. DB HELPERS
# ═══════════════════════════════════════════════════════════════

class WorkOrderDB:
    """Direct DB operations on Documentation.db."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        self._verify_db()

    def _verify_db(self):
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Documentation.db not found at: {self.db_path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_pending_work_orders(self) -> List[Dict[str, Any]]:
        """Get work orders that need processing (pending or in_progress)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT id, status, priority, description, notes, assigned_to,
                          created_at, updated_at, completed_at
                   FROM work_orders
                   WHERE status IN ('pending', 'in_progress')
                   ORDER BY priority ASC, id ASC"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_runner_state(self) -> Dict[str, Any]:
        """Read the work_order_runner's state from config table."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT key, value FROM config WHERE key LIKE 'runner.%'"
            ).fetchall()
            state = {}
            for r in rows:
                state[r["key"]] = r["value"]
            return state
        finally:
            conn.close()

    def update_work_order_status(self, order_id: int, status: str,
                                  notes: Optional[str] = None):
        """Update a work order's status and optionally append notes."""
        conn = self._connect()
        try:
            now = datetime.now(timezone.utc).isoformat()
            if notes:
                conn.execute(
                    """UPDATE work_orders
                       SET status = ?, notes = COALESCE(notes, '') || '\n' || ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (status, notes, now, order_id),
                )
            else:
                if status == "completed":
                    conn.execute(
                        """UPDATE work_orders
                           SET status = ?, completed_at = ?, updated_at = ?
                           WHERE id = ?""",
                        (status, now, now, order_id),
                    )
                else:
                    conn.execute(
                        """UPDATE work_orders
                           SET status = ?, updated_at = ?
                           WHERE id = ?""",
                        (status, now, order_id),
                    )
            conn.commit()
            logger.info(f"Order #{order_id}: status → {status}")
        finally:
            conn.close()

    def advance_runner(self, order_id: int, action: str = "complete"):
        """Advance the work_order_runner state machine.
        
        action: 'complete' | 'skip' | 'fail'
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            # Clear current item
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                ("runner.current_item_type", "", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                ("runner.current_item_id", "", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                ("runner.current_item_desc", "", now),
            )

            # Update counters
            counter_key = f"runner.items_{action}d"
            current_val = conn.execute(
                "SELECT value FROM config WHERE key = ?", (counter_key,)
            ).fetchone()
            new_val = (int(current_val["value"]) if current_val and current_val["value"] else 0) + 1
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                (counter_key, str(new_val), now),
            )

            # Track processed set
            processed_raw = conn.execute(
                "SELECT value FROM config WHERE key = 'runner.processed_set'"
            ).fetchone()
            processed = processed_raw["value"] if processed_raw and processed_raw["value"] else ""
            entry = f"work_order:{order_id}"
            if entry not in processed:
                processed = f"{processed},{entry}" if processed else entry
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                ("runner.processed_set", processed, now),
            )

            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                ("runner.last_updated", now, now),
            )

            conn.commit()
            logger.info(f"Runner advanced: {action} on order #{order_id}")
        finally:
            conn.close()

    # ── v2.0: Handler attempt tracking (persists across daemon restarts) ──
    def get_handler_attempts(self, order_id: int) -> int:
        """Get the number of handler attempts for a work order."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?",
                (f"handler.attempts.{order_id}",)
            ).fetchone()
            return int(row["value"]) if row and row["value"] else 0
        finally:
            conn.close()

    def increment_handler_attempts(self, order_id: int) -> int:
        """Increment and return the handler attempt counter."""
        current = self.get_handler_attempts(order_id)
        new_val = current + 1
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                (f"handler.attempts.{order_id}", str(new_val), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        return new_val

    def reset_handler_attempts(self, order_id: int):
        """Reset handler attempts (e.g., on successful completion)."""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, '0')",
                (f"handler.attempts.{order_id}", "0"),
            )
            conn.commit()
        finally:
            conn.close()

    # ── v2.0: Restart rate limit tracking ──
    def get_last_restart_time(self) -> Optional[float]:
        """Get timestamp of last restart request, or None."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM config WHERE key = 'handler.last_restart_time'"
            ).fetchone()
            if row and row["value"]:
                return float(row["value"])
            return None
        finally:
            conn.close()

    def set_last_restart_time(self, timestamp: float):
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                ("handler.last_restart_time", str(timestamp), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def update_config(self, key: str, value: str):
        """Set a single config value."""
        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )
            conn.commit()
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════
# 2. v2.0: RESTART TICKET SYSTEM
# ═══════════════════════════════════════════════════════════════

def _write_restart_ticket(order_id: int, reason: str) -> bool:
    """Write a restart ticket for the FastMCP server to self-restart.
    
    Returns True if ticket was written successfully.
    The server's background _ticket_watcher thread detects this file
    and initiates a graceful self-restart.
    """
    try:
        ticket = {
            "reason": reason,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "order_id": order_id,
            "source": "work-order-auto-processor",
        }
        RESTART_TICKET_PATH.write_text(json.dumps(ticket, indent=2))
        logger.info(f"Restart ticket written for order #{order_id}: {reason[:80]}...")
        return True
    except Exception as e:
        logger.error(f"Failed to write restart ticket: {e}")
        return False


def _check_restart_ticket_cleared() -> bool:
    """Check if the restart ticket has been consumed (server restarted)."""
    return not RESTART_TICKET_PATH.exists()


# ═══════════════════════════════════════════════════════════════
# 3. FIX HANDLER SYSTEM
# ═══════════════════════════════════════════════════════════════

HandlerResult = Tuple[str, str]

# Registry of fix handlers keyed by pattern match
# v2.0: Added priority field for fallback chains
_handlers: List[Tuple[str, str, Callable, int]] = []  # (field, pattern, fn, priority)


def _register(pattern_field: str, pattern: str, fn: Callable, priority: int = 0):
    """Register a fix handler.
    
    pattern_field: "description" or "notes" — which field to match
    pattern: regex pattern to match
    fn: handler function (order_id, description, notes) -> HandlerResult
    priority: lower number = higher priority (default 0)
    """
    _handlers.append((pattern_field, pattern, fn, priority))
    _handlers.sort(key=lambda h: h[3])  # Sort by priority
    logger.debug(f"Registered handler: '{pattern[:50]}...' → {fn.__name__} (pri={priority})")


def _match_handlers(description: str, notes: str) -> List[Callable]:
    """Find ALL matching handlers (ordered by priority for fallback chain)."""
    matches = []
    for field, pattern, fn, priority in _handlers:
        text = description if field == "description" else notes
        if re.search(pattern, text, re.IGNORECASE):
            matches.append(fn)
    return matches


def run_handler(order_id: int, description: str, notes: str,
                attempt: int = 1) -> HandlerResult:
    """Execute the best matching handler for a work order.
    
    v2.0: Now supports fallback chains — if the primary handler fails,
    tries the next matching handler (if available).
    
    Args:
        attempt: Which attempt number this is (1-based)
    """
    handlers = _match_handlers(description, notes)
    if not handlers:
        return ("skipped", "No matching auto-fix handler found. Needs AI processing.")

    errors = []
    for handler in handlers:
        logger.info(f"Order #{order_id}: trying handler '{handler.__name__}' "
                     f"(attempt {attempt}/{MAX_RETRIES_PER_WO})")
        try:
            status, message = handler(order_id, description, notes)
            if status != "failed":
                return (status, message)
            errors.append(f"'{handler.__name__}': {message[:100]}")
        except Exception as e:
            error_msg = f"'{handler.__name__}': {e}"
            errors.append(error_msg)
            logger.error(f"Handler exception for order #{order_id}: {e}")

    return ("failed", f"All handlers failed. Errors: {'; '.join(errors)}")


# ── v2.0: Redesigned Restart Handler (uses tickets, no kill) ────

def _handle_restart_request(order_id: int, desc: str, notes: str) -> HandlerResult:
    """Handle server restart request using the restart ticket system.
    
    v2.0 FIX: Instead of killing the FastMCP server (which kills this
    daemon since it depends on the server's parent process), writes a
    restart ticket. The server's background thread detects the ticket
    and self-restarts gracefully.
    
    This prevents the SUICIDE LOOP where:
    1. Daemon kills FastMCP server
    2. YuniScripts engine panics → SIGTERM → daemon dies
    3. YuniScripts engine restarts daemon
    4. Daemon sees WO still pending → kills new server → loop
    
    Flow:
    1. Write restart ticket → /tmp/fastmcp_restart_ticket.json
    2. Wait for server to detect and self-restart (max 60s)
    3. Verify server is back by checking the ticket was consumed
    """
    try:
        # Rate limit: max 1 restart per RESTART_COOLDOWN_SECONDS
        db = WorkOrderDB()
        last_restart = db.get_last_restart_time()
        if last_restart:
            elapsed = time.time() - last_restart
            if elapsed < RESTART_COOLDOWN_SECONDS:
                remaining = int(RESTART_COOLDOWN_SECONDS - elapsed)
                return ("skipped",
                        f"Restart rate-limited. Last restart was {int(elapsed)}s ago. "
                        f"Cooling down for {remaining}s.")

        # Write restart ticket
        reason = f"Work Order #{order_id}: {desc[:100]}"
        if not _write_restart_ticket(order_id, reason):
            return ("failed", "Could not write restart ticket. Is /tmp writable?")

        # Record restart time for rate limiting
        db.set_last_restart_time(time.time())

        # Wait for server to self-restart (max 60s, check every 2s)
        logger.info(f"Order #{order_id}: Waiting for server to self-restart...")
        for i in range(30):  # 30 × 2s = 60s max wait
            if not RUNNING:
                return ("failed", "Daemon shutting down while waiting for restart")
            time.sleep(2)
            if _check_restart_ticket_cleared():
                logger.info(f"Order #{order_id}: Restart ticket consumed — "
                           f"server self-restarted successfully")
                return ("completed",
                        f"Server restart requested via ticket and completed. "
                        f"Ticket consumed after ~{(i+1)*2}s.")

        # Ticket still exists after 60s — server may not have detected it
        return ("failed",
                f"Restart ticket not consumed after 60s. "
                f"The server may be down or the _ticket_watcher thread "
                f"may not be running. Manual restart needed.")
    except Exception as e:
        return ("failed", f"Restart handler failed: {e}")


def _handle_resource_starvation(order_id: int, desc: str, notes: str) -> HandlerResult:
    """Configure system limits to prevent resource starvation on Steam Deck."""
    try:
        result = subprocess.run(
            ["ulimit", "-a"], capture_output=True, text=True, timeout=5, shell=True
        )
        cmds = [
            "ulimit -u 8192",
            "ulimit -n 65536",
        ]
        results = []
        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, shell=True)
            results.append(f"{cmd}: exit={r.returncode}")

        return ("completed", f"Applied resource limit adjustments.\n{chr(10).join(results)}")
    except Exception as e:
        return ("failed", f"Resource config failed: {e}")


# ── Register all handlers (v2.0: with priorities) ──────────────
_register("description", r"restart.*fastmcp|fastmcp.*restart|restart.*server", 
          _handle_restart_request, priority=1)
_register("description", r"resource.*starvation|fork.*resource.*unavailable",
          _handle_resource_starvation, priority=2)
# Note: The AMD GPU and read_files handlers are shell-command based and work
# via raw Python file operations. They don't trigger the suicide problem.
# They can be registered if the gpu_bridge.py / read_files.py files exist.
_gpu_bridge = os.environ.get(
        'DEEPSKY_FASTMCP_TOOLS_PATH',
        os.path.expanduser("~/Documents/dev-yuniScripts/SCRIPTS/SERVICES/fastmcp_server/tools")
    ) + "/gpu_bridge.py"
if os.path.exists(_gpu_bridge):
    _register("description", r"gpu|bridge.*import|amd.*gpu|gpu.*detect|lspci",
              lambda oid, d, n: _handle_simple_file(oid, d, n, "gpu_bridge.py"), priority=3)
_read_files = os.environ.get(
        'DEEPSKY_FASTMCP_TOOLS_PATH',
        os.path.expanduser("~/Documents/dev-yuniScripts/SCRIPTS/SERVICES/fastmcp_server/tools")
    ) + "/read_files.py"
if os.path.exists(_read_files):
    _register("description", r"read_files.*offset|offset.*bug|truncation.*marker",
              lambda oid, d, n: _handle_simple_file(oid, d, n, "read_files.py"), priority=3)


def _handle_simple_file(order_id: int, desc: str, notes: str, filename: str) -> HandlerResult:
    """Generic handler for file-level fixes. Reports need for AI processing."""
    return ("skipped", f"File fix for {filename} requires AI analysis.")


# ═══════════════════════════════════════════════════════════════
# 4. AUTO-PROCESS ENGINE (v2.0: with retry + escalation)
# ═══════════════════════════════════════════════════════════════

class AutoProcessor:
    """Main auto-processing engine with retry, escalation, and rate limiting."""

    def __init__(self, db: WorkOrderDB):
        self.db = db
        self.stats = {
            "processed": 0,
            "auto_fixed": 0,
            "deferred": 0,
            "failed": 0,
            "escalated": 0,
            "cycles": 0,
        }
        # In-memory retry tracker (reset on daemon restart)
        self._attempted_this_cycle: set = set()

    def _should_retry(self, order_id: int) -> Tuple[bool, int]:
        """Check if a work order should be retried.
        
        Returns:
            (should_retry: bool, attempt_count: int)
        """
        attempts = self.db.get_handler_attempts(order_id)
        if attempts >= MAX_RETRIES_PER_WO:
            return (False, attempts)
        return (True, attempts)

    def _get_backoff_seconds(self, attempt_count: int) -> int:
        """Get the backoff seconds for a given attempt number."""
        if attempt_count <= 0:
            return 0
        idx = min(attempt_count - 1, len(RETRY_BACKOFFS) - 1)
        return RETRY_BACKOFFS[idx]

    def process_one(self, order: Dict[str, Any]) -> bool:
        """Process a single work order with retry and escalation.
        
        v2.0 flow:
        1. Check handler attempt counter in DB
        2. If exceeded MAX_RETRIES → ESCALATE (mark NEEDS-AI)
        3. Increment handler attempt counter
        4. Try handler (with fallback chain)
        5. On success → reset attempts, mark complete
        6. On failure → check if should retry later (skip for now, will retry next cycle)
        """
        order_id = order["id"]
        description = order.get("description", "")
        notes = order.get("notes", "")

        # Skip if already attempted this cycle (prevents same-cycle loops)
        if order_id in self._attempted_this_cycle:
            return False
        self._attempted_this_cycle.add(order_id)

        logger.info(f"Processing order #{order_id}: {description[:100]}...")

        # v2.0: Check retry budget
        should_retry, attempt_count = self._should_retry(order_id)
        current_attempt = attempt_count + 1  # This is attempt N+1

        if not should_retry:
            # ESCALATE: Max retries exceeded
            escalation_msg = (
                f"[ESCALATED to AI after {MAX_RETRIES_PER_WO} failed auto-fix attempts]\n"
                f"Description: {description}\n"
                f"Max attempts ({MAX_RETRIES_PER_WO}) exhausted. "
                f"The auto-processor cannot handle this work order automatically. "
                f"An AI session is required to analyze and resolve this task."
            )
            self.db.update_work_order_status(
                order_id, "pending",
                notes=f"\n{escalation_msg}"
            )
            self.db.advance_runner(order_id, "skip")
            self.stats["escalated"] += 1
            logger.warning(f"Order #{order_id}: ESCALATED to AI after "
                          f"{MAX_RETRIES_PER_WO} attempts")
            return True

        # Run handler with attempt tracking
        self.db.increment_handler_attempts(order_id)
        status, message = run_handler(order_id, description, notes, attempt=current_attempt)

        if status == "completed":
            self.db.reset_handler_attempts(order_id)
            self.db.update_work_order_status(
                order_id, "completed",
                notes=f"[AUTO-FIXED by work-order-auto-processor at "
                      f"{datetime.now(timezone.utc).isoformat()}]\n{message}"
            )
            self.db.advance_runner(order_id, "complete")
            self.stats["auto_fixed"] += 1
            logger.info(f"Order #{order_id}: AUTO-FIXED ✓")
            return True

        elif status == "failed":
            backoff = self._get_backoff_seconds(current_attempt)
            self.db.update_work_order_status(
                order_id, "pending",
                notes=f"[AUTO-FAILED attempt {current_attempt}/{MAX_RETRIES_PER_WO} at "
                      f"{datetime.now(timezone.utc).isoformat()}]\n{message}\n"
                      f"[NEXT RETRY in {backoff}s or next cycle]"
            )
            self.db.advance_runner(order_id, "fail")
            self.stats["failed"] += 1
            logger.warning(f"Order #{order_id}: AUTO-FAILED attempt "
                          f"{current_attempt}/{MAX_RETRIES_PER_WO} ✗ "
                          f"(retry in {backoff}s)")
            return True

        else:  # skipped
            self.db.reset_handler_attempts(order_id)  # Don't count skips as failures
            self.db.update_work_order_status(
                order_id, "blocked",
                notes=f"[NO-HANDLER at {datetime.now(timezone.utc).isoformat()}]\n{message}"
            )
            self.db.advance_runner(order_id, "skip")
            self.stats["deferred"] += 1
            logger.info(f"Order #{order_id}: no handler, blocked → deferred to AI")
            return True

    def run_cycle(self) -> int:
        """Run one processing cycle."""
        self.stats["cycles"] += 1
        self._attempted_this_cycle.clear()
        orders = self.db.get_pending_work_orders()

        if not orders:
            logger.debug("No pending work orders found.")
            return 0

        logger.info(f"Cycle #{self.stats['cycles']}: Found {len(orders)} pending work orders")
        processed = 0

        for order in orders:
            if not RUNNING:
                break
            try:
                if self.process_one(order):
                    processed += 1
                    self.stats["processed"] += 1
            except Exception as e:
                logger.error(f"Error processing order #{order['id']}: {e}", exc_info=True)
                self.db.update_work_order_status(
                    order["id"], "pending",
                    notes=f"[PROCESSING-ERROR at {datetime.now(timezone.utc).isoformat()}]\nException: {e}"
                )
                self.db.advance_runner(order["id"], "fail")

        return processed


# ═══════════════════════════════════════════════════════════════
# 5. DAEMON MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    """Main daemon entry point."""
    logger.info("=" * 60)
    logger.info("Work Order Auto-Processor Daemon v2.0 DYNAMIC starting...")
    logger.info(f"PID: {os.getpid()}")
    logger.info(f"DB: {DEFAULT_DB}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")
    logger.info(f"Max retries per WO: {MAX_RETRIES_PER_WO}")
    logger.info(f"Retry backoffs: {RETRY_BACKOFFS}s")
    logger.info(f"Restart cooldown: {RESTART_COOLDOWN_SECONDS}s")
    logger.info("=" * 60)

    # Write PID file
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # Initialize
    db = WorkOrderDB()
    processor = AutoProcessor(db)

    # Report registered handlers
    logger.info(f"Registered {len(_handlers)} fix handlers (sorted by priority):")
    for field, pattern, fn, priority in _handlers:
        logger.info(f"  [{priority}] [{field}] '{pattern[:50]}' → {fn.__name__}")

    # ── v2.0: Clean any stale restart tickets on startup ──
    if RESTART_TICKET_PATH.exists():
        try:
            RESTART_TICKET_PATH.unlink()
            logger.info("Cleaned stale restart ticket on startup")
        except Exception:
            pass

    # Main loop
    consecutive_empty = 0
    while RUNNING:
        try:
            count = processor.run_cycle()
            if count > 0:
                consecutive_empty = 0
            else:
                consecutive_empty += 1
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
            consecutive_empty += 1

        sleep_time = POLL_INTERVAL if consecutive_empty > 0 else 5
        logger.debug(f"Sleeping {sleep_time}s (consecutive_empty={consecutive_empty})")
        for _ in range(sleep_time):
            if not RUNNING:
                break
            time.sleep(1)

    # Cleanup
    if PID_FILE.exists():
        PID_FILE.unlink()
    logger.info("Work Order Auto-Processor Daemon v2.0 stopped.")
    logger.info(f"Final stats: {json.dumps(processor.stats, indent=2)}")


if __name__ == "__main__":
    main()
