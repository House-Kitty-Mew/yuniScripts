"""
SIMULATED_ANNOUNCE — Major event announcement system.

Notifies subscribed players when "interesting" events happen to their
favorite simulated personas.  Uses AI thinking-mode evaluation to filter
out boring events (berry picking, casual walking) and only announce
events that are actually worth knowing about.

Features:
  - Subscribe to personas via \\ah sub <persona_id>
  - AI evaluation of event interestingness (1-10 scale)
  - Only events scoring >= 5 are announced
  - Queued delivery via the chat message system
  - Historical log of delivered announcements

Architecture:
  pl_announce_database.py  — Schema & queries
  pl_announce_filter.py    — AI thinking-mode event filter
  pl_announce_handler.py   — Command handler for \\ah sub/unsub/subs/announces
"""

from AUCTIONHOUSE.ah_logger import get_logger
from typing import Optional
from time import time
import json

log = get_logger()
EXTENSION_NAME = "SIMULATED_ANNOUNCE"
EXT_NAME = "pl_announce"

# ── Event probing helpers ────────────────────────────────────────────

# Cache for which event tables the sp_behavior and sp_world_events expose
_EVENT_QUERIES_CACHED = False
_EVENT_QUERIES = []


def _discover_event_queries():
    """Discover how to query recent events from SIMULATED_PEOPLE.

    We probe the database for tables that contain event history we can
    use for announcement generation on each simulation cycle.
    """
    global _EVENT_QUERIES_CACHED, _EVENT_QUERIES
    if _EVENT_QUERIES_CACHED:
        return

    from AUCTIONHOUSE.ah_database import get_db
    db = get_db()

    queries = []

    # Check for SIMULATED_PEOPLE ext_sp_persona_events table
    try:
        db.execute("SELECT 1 FROM ext_sp_persona_events LIMIT 0")
        queries.append({
            "table": "ext_sp_persona_events",
            "query": """
                SELECT event_type, persona_id, event_data, created_at
                FROM ext_sp_persona_events
                WHERE created_at > ?
                ORDER BY created_at DESC
            """,
            "type_col": "event_type",
            "persona_col": "persona_id",
            "data_col": "event_data",
        })
        log.info(EXT_NAME, "Discovered event source: ext_sp_persona_events")
    except Exception:
        pass

    # Check for SIMULATED_CHAT ext_chat_events
    try:
        db.execute("SELECT 1 FROM ext_chat_events LIMIT 0")
        queries.append({
            "table": "ext_chat_events",
            "query": """
                SELECT event_type, persona_id, event_data, created_at
                FROM ext_chat_events
                WHERE created_at > ?
                ORDER BY created_at DESC
            """,
            "type_col": "event_type",
            "persona_col": "persona_id",
            "data_col": "event_data",
        })
        log.info(EXT_NAME, "Discovered event source: ext_chat_events")
    except Exception:
        pass

    # Check for generic sp_persona_events
    try:
        db.execute("SELECT 1 FROM sp_persona_events LIMIT 0")
        queries.append({
            "table": "sp_persona_events",
            "query": """
                SELECT event_type, persona_id, event_data, created_at
                FROM sp_persona_events
                WHERE created_at > ?
                ORDER BY created_at DESC
            """,
            "type_col": "event_type",
            "persona_col": "persona_id",
            "data_col": "event_data",
        })
        log.info(EXT_NAME, "Discovered event source: sp_persona_events")
    except Exception:
        pass

    # Check for sp_behavior_event_log
    try:
        db.execute("SELECT 1 FROM sp_behavior_event_log LIMIT 0")
        queries.append({
            "table": "sp_behavior_event_log",
            "query": """
                SELECT event_type, persona_id, event_data AS event_data, timestamp AS created_at
                FROM sp_behavior_event_log
                WHERE timestamp > ?
                ORDER BY timestamp DESC
            """,
            "type_col": "event_type",
            "persona_col": "persona_id",
            "data_col": "event_data",
        })
        log.info(EXT_NAME, "Discovered event source: sp_behavior_event_log")
    except Exception:
        pass

    _EVENT_QUERIES = queries
    _EVENT_QUERIES_CACHED = True


# ── on_load: Called by plugin registry ───────────────────────────────

def on_load(registry):
    """Register hooks with the AH plugin registry."""
    from .pl_announce_database import ensure_schema
    ensure_schema()
    log.info(EXT_NAME,
             f"Extension '{EXTENSION_NAME}' loaded — "
             f"persona event announcement system active")


# ── on_simulation_cycle_end: Hook called after persona tick + AI cycle ─

def on_simulation_cycle_end(**kwargs) -> dict:
    """Called after the simulation cycle completes.

    We scan for new events that happened to personas and queue
    interesting ones for subscribed players.
    """
    from .pl_announce_database import (
        get_subscribers_for_persona, enqueue_announcement
    )
    from .pl_announce_filter import evaluate_event_interestingness, _generate_title, _generate_description

    # Track when we last checked (passed via kwargs)
    # Use timestamp from kwargs or default to ~1 hour ago
    cycle_start_time = kwargs.get("cycle_start_time", time() - 3600)

    # Discover event sources
    _discover_event_queries()
    if not _EVENT_QUERIES:
        return {"status": "no_event_sources", "announcements_queued": 0}

    # Collect recent events from all sources
    events_to_check = []
    seen_events = set()  # dedup by (persona_id, event_type, data_hash)

    from AUCTIONHOUSE.ah_database import get_db
    db = get_db()

    for source in _EVENT_QUERIES:
        try:
            rows = db.execute(source["query"], (cycle_start_time,)).fetchall()
            for row in rows:
                try:
                    event_type = row[0] if isinstance(row[0], str) else str(row[0])
                    persona_id = str(row[1])

                    # Parse event_data — might be JSON string or already dict
                    event_data_raw = row[2]
                    if isinstance(event_data_raw, str):
                        try:
                            event_data = json.loads(event_data_raw)
                        except (json.JSONDecodeError, TypeError):
                            event_data = {"description": event_data_raw}
                    elif isinstance(event_data_raw, dict):
                        event_data = event_data_raw
                    else:
                        event_data = {}

                    # Dedup
                    dedup_key = f"{persona_id}:{event_type}:{hash(str(event_data)) % 10000}"
                    if dedup_key in seen_events:
                        continue
                    seen_events.add(dedup_key)

                    events_to_check.append({
                        "event_type": event_type,
                        "persona_id": persona_id,
                        "data": event_data,
                    })
                except Exception:
                    continue
        except Exception:
            continue

    if not events_to_check:
        return {"status": "no_new_events", "events_checked": 0, "announcements_queued": 0}

    # For each event, evaluate interestingness and queue if worthy
    total_queued = 0
    events_checked = 0
    events_announced = 0
    announced_details = []

    for event in events_to_check:
        events_checked += 1
        try:
            persona_id = event["persona_id"]
            event_type = event["event_type"]
            event_data = event["data"]

            # Try to get persona profile for context-aware scoring
            persona_profile = _get_persona_profile(persona_id)

            # Evaluate interestingness (use AI thinking-mode = True)
            eval_result = evaluate_event_interestingness(
                event_type=event_type,
                event_data=event_data,
                persona_profile=persona_profile,
                ai_mode=True,
            )

            if eval_result["should_announce"]:
                # Find subscribers for this persona
                subscribers = get_subscribers_for_persona(persona_id)

                if subscribers:
                    title = _generate_title(persona_id, event_type, event_data)
                    description = _generate_description(
                        persona_id, event_type, event_data, eval_result
                    )

                    for player_name in subscribers:
                        queue_id = enqueue_announcement(
                            player_name=player_name,
                            persona_id=persona_id,
                            event_type=event_type,
                            title=title,
                            description=description,
                            details=event_data,
                            interestingness=eval_result["interestingness"],
                        )
                        total_queued += 1

                    events_announced += 1
                    announced_details.append({
                        "persona_id": persona_id,
                        "event_type": event_type,
                        "interestingness": eval_result["interestingness"],
                        "subscribers": len(subscribers),
                    })
        except Exception:
            continue

    # Log summary
    if total_queued > 0:
        log.info(EXT_NAME,
                 f"Cycle end: checked {events_checked} events, "
                 f"announced {events_announced} events to {total_queued} subscribers")

    return {
        "status": "ok",
        "events_checked": events_checked,
        "events_announced": events_announced,
        "announcements_queued": total_queued,
        "announced_details": announced_details,
    }


# ── Persona profile lookup ───────────────────────────────────────────

def _get_persona_profile(persona_id: str) -> Optional[dict]:
    """Try to get a persona profile for context-aware scoring."""
    try:
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()

        # Try SIMULATED_PEOPLE profiles table
        row = db.execute(
            "SELECT persona_id, status, wealth, level, combat_count FROM ext_sp_profiles "
            "WHERE persona_id = ? LIMIT 1",
            (persona_id,)
        ).fetchone()
        if row:
            return {
                "persona_id": row[0],
                "status": row[1] if len(row) > 1 else None,
                "wealth": row[2] if len(row) > 2 else 0,
                "level": row[3] if len(row) > 3 else 0,
                "combat_count": row[4] if len(row) > 4 else 0,
            }

        # Try ext_sp_personas
        row = db.execute(
            "SELECT persona_id FROM ext_sp_personas WHERE persona_id = ? LIMIT 1",
            (persona_id,)
        ).fetchone()
        if row:
            return {"persona_id": row[0], "status": None, "wealth": 0, "level": 0, "combat_count": 0}

    except Exception:
        pass
    return None


# ── Direct command handler (called from minescript) ──────────────────

def handle_command(parts: list[str], player_name: str = "") -> dict:
    """Handle \\ah announce commands from the minescript client.

    Args:
        parts: Command parts after 'ah announce' or routing prefix
        player_name: The player issuing the command

    Returns:
        Response dict for the minescript client
    """
    from .pl_announce_handler import handle_command as handler
    return handler(parts, player_name=player_name)



