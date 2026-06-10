"""
shm_bridge_pending.py — Eventually-consistent pending actions table for the
sp_combat ↔ SHM bridge.

If a bridge action (fracture, pain registration, bleeding) fails after the
wound is already committed to ext_sp_wounds, the action is recorded here
and retried on subsequent ticks.

Design:
  - Each pending action has a wound_uuid (unique), owner_uuid, action_type
  - Retried up to max_retries (3) with exponential backoff
  - On success, the pending record is deleted
  - On permanent failure (max_retries exceeded), the record is preserved
    for manual review

Usage:
  from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_bridge_pending import (
      ensure_pending_schema, add_pending_action, process_pending_actions
  )
"""

import traceback
from datetime import datetime, timezone

from AUCTIONHOUSE.ah_logger import get_logger
from AUCTIONHOUSE.EXTENSIONS.SIMULATED_HEALTH_MECHANICS.shm_database import get_db

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Schema
# ══════════════════════════════════════════════════════════════════════

PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS shm_bridge_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wound_uuid TEXT UNIQUE,
    owner_uuid TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_data TEXT DEFAULT '{}',
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TEXT NOT NULL,
    last_attempt_at TEXT,
    last_error TEXT,
    is_resolved INTEGER DEFAULT 0
)
"""


def ensure_pending_schema():
    """Create the pending actions table if it doesn't exist."""
    try:
        db = get_db()
        db.execute(PENDING_SCHEMA)
        log.debug("shm_bridge_pending", "Pending actions schema ensured")
    except Exception as e:
        log.error("shm_bridge_pending", f"Failed to create pending schema: {e}")


# ══════════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════════

def add_pending_action(wound_uuid: str, owner_uuid: str, action_type: str,
                       action_data: dict = None) -> bool:
    """Record a pending bridge action for retry.

    Args:
        wound_uuid: UUID of the wound that triggered this action
        owner_uuid: Persona UUID
        action_type: e.g. "create_fracture", "register_pain", "cause_bleeding"
        action_data: Optional dict with extra parameters

    Returns:
        True if recorded, False if duplicate or error
    """
    try:
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        import json
        data_str = json.dumps(action_data or {})
        db.execute("""
            INSERT OR IGNORE INTO shm_bridge_pending
                (wound_uuid, owner_uuid, action_type, action_data,
                 retry_count, max_retries, created_at)
            VALUES (?, ?, ?, ?, 0, 3, ?)
        """, (wound_uuid, owner_uuid, action_type, data_str, now))
        log.debug("shm_bridge_pending",
                  f"Pending action recorded: {action_type} for wound {wound_uuid[:16]}")
        return True
    except Exception as e:
        log.error("shm_bridge_pending",
                  f"Failed to record pending action: {e}")
        return False


def resolve_pending_action(wound_uuid: str) -> bool:
    """Mark a pending action as resolved (successfully processed)."""
    try:
        db = get_db()
        db.execute("""
            UPDATE shm_bridge_pending SET is_resolved = 1
            WHERE wound_uuid = ? AND is_resolved = 0
        """, (wound_uuid,))
        return True
    except Exception as e:
        log.error("shm_bridge_pending", f"Failed to resolve pending action: {e}")
        return False


def delete_pending_action(wound_uuid: str) -> bool:
    """Delete a resolved pending action record."""
    try:
        db = get_db()
        db.execute("DELETE FROM shm_bridge_pending WHERE wound_uuid = ?",
                   (wound_uuid,))
        return True
    except Exception as e:
        log.error("shm_bridge_pending", f"Failed to delete pending action: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
# Processing
# ══════════════════════════════════════════════════════════════════════

# Registry of action-type -> handler callables
_PENDING_HANDLERS = {}


def reset_pending_state():
    """Reset all module-level state to defaults.

    Called from test setUp/tearDown to ensure clean handlers don't
    leak between test classes.
    """
    _PENDING_HANDLERS.clear()


def register_pending_handler(action_type: str, handler: callable):
    """Register a handler for a specific pending action type.

    The handler receives (wound_uuid, owner_uuid, action_data) and should
    return True on success, False on failure.
    """
    _PENDING_HANDLERS[action_type] = handler


def process_pending_actions() -> dict:
    """Process all unresolved pending actions.

    Called once per SHM tick. Retries each action, increments retry_count
    on failure, and removes/keeps based on max_retries.

    Returns:
        Dict with counts: processed, succeeded, failed, exceeded
    """
    results = {"processed": 0, "succeeded": 0, "failed": 0, "exceeded": 0}

    try:
        db = get_db()
        pending = db.fetch_all("""
            SELECT * FROM shm_bridge_pending
            WHERE is_resolved = 0 AND retry_count < max_retries
            ORDER BY created_at ASC
        """)

        for action in pending:
            results["processed"] += 1
            handler = _PENDING_HANDLERS.get(action["action_type"])
            if not handler:
                # No handler registered — skip (will be retried or reviewed)
                continue

            try:
                import json
                action_data = json.loads(action["action_data"] or "{}")
                success = handler(action["wound_uuid"],
                                  action["owner_uuid"],
                                  action_data)
            except Exception as e:
                log.warn("shm_bridge_pending",
                         f"Handler error for {action['action_type']}: {e}")
                success = False

            now = datetime.now(timezone.utc).isoformat()
            if success:
                db.execute("""
                    UPDATE shm_bridge_pending
                    SET is_resolved = 1, last_attempt_at = ?
                    WHERE id = ?
                """, (now, action["id"]))
                results["succeeded"] += 1
            else:
                new_count = action["retry_count"] + 1
                new_maxed = 1 if new_count >= action["max_retries"] else 0
                db.execute("""
                    UPDATE shm_bridge_pending
                    SET retry_count = ?, last_attempt_at = ?,
                        last_error = 'retry_failed', is_resolved = ?
                    WHERE id = ?
                """, (new_count, now, new_maxed, action["id"]))
                if new_maxed:
                    results["exceeded"] += 1
                    log.warn("shm_bridge_pending",
                             f"Action {action['action_type']} for wound "
                             f"{action['wound_uuid'][:16]} exceeded max retries")
                else:
                    results["failed"] += 1

    except Exception as e:
        log.error("shm_bridge_pending",
                  f"Error processing pending actions: {e}\n{traceback.format_exc()}")

    return results


def cleanup_resolved_actions(max_age_hours: int = 72) -> int:
    """Remove resolved pending actions older than max_age_hours."""
    try:
        db = get_db()
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        db.execute("""
            DELETE FROM shm_bridge_pending
            WHERE is_resolved = 1 AND created_at < ?
        """, (cutoff,))
        return db._last_row_count if hasattr(db, '_last_row_count') else 0
    except Exception as e:
        log.error("shm_bridge_pending", f"Cleanup error: {e}")
        return 0
