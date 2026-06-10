"""
ah_helper_db.py — AI Helper Database: notes & categories for persistent AI memory.

The DeepSeek AI writes categorized notes after each simulation cycle. These
notes are fed back into the next cycle's prompt so the AI can reference its
own past reasoning, track market trends, and build up context for future
events (especially Major events which require a week of build-up).

Categories:
  - market_health    — Overall market sentiment assessment
  - price_reasoning  — Why specific prices were adjusted
  - event_idea       — Potential future event ideas with reasoning
  - observation      — Noticed patterns in player behavior or market
  - recommendation   — System improvement suggestions
  - item_opinion     - Opinion on specific items or listings
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

VALID_CATEGORIES = frozenset({
    "market_health", "price_reasoning", "event_idea",
    "observation", "recommendation", "item_opinion"
})


def add_note(category: str, content: str, *, reasoning: Optional[str] = None,
             related_item_id: Optional[str] = None,
             related_event: Optional[str] = None,
             importance: int = 1,
             expires_in_days: Optional[int] = None) -> int:
    """Add a note to the AI helper database.

    Args:
        category: Must be one of VALID_CATEGORIES
        content: The note text (free-form, AI-written)
        reasoning: Why the AI made this note (optional)
        related_item_id: Item ID this note is about (optional)
        related_event: Event UUID this note relates to (optional)
        importance: 1-5, higher = more important
        expires_in_days: If set, note auto-expires after this many days

    Returns:
        The new note's ID (for immediate reference)
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category '{category}'. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    if importance < 1 or importance > 5:
        importance = max(1, min(5, importance))

    now = datetime.now(timezone.utc).isoformat()
    expires_at = None
    if expires_in_days is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()

    db = get_db()
    sql = """INSERT INTO ai_notes (category, content, reasoning, related_item_id,
             related_event, importance, created_at, expires_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
    c = db.execute(sql, (category, content, reasoning, related_item_id,
                         related_event, importance, now, expires_at))
    note_id = c.lastrowid  # Can be None for WITHOUT ROWID tables or empty inserts
    if note_id is None:
        log.warn("helper_db", "Note inserted but lastrowid is None — returning -1")
        return -1
    log.info("helper_db", f"Note added: [{category}] {content[:80]}...",
             {"note_id": note_id, "importance": importance})
    return note_id


def get_notes_for_prompt(limit: int = 50, min_importance: int = 1) -> list[dict]:
    """Fetch the most relevant non-expired notes for the AI prompt.

    Notes are sorted by importance (desc), then by creation time (desc).
    Expired notes are excluded.

    Args:
        limit: Maximum number of notes to return
        min_importance: Minimum importance level (1-5)

    Returns:
        List of dicts with category, content, reasoning, created_at
    """
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    rows = db.fetch_all("""
        SELECT category, content, reasoning, importance, created_at
        FROM ai_notes
        WHERE (expires_at IS NULL OR expires_at > ?)
          AND importance >= ?
        ORDER BY importance DESC, created_at DESC
        LIMIT ?
    """, (now, min_importance, limit))
    return rows


def get_notes_by_category(category: str, limit: int = 20) -> list[dict]:
    """Fetch notes for a specific category.

    Args:
        category: One of VALID_CATEGORIES
        limit: Maximum notes to return

    Returns:
        List of dicts with note data
    """
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    return db.fetch_all("""
        SELECT category, content, reasoning, importance, created_at
        FROM ai_notes
        WHERE category = ? AND (expires_at IS NULL OR expires_at > ?)
        ORDER BY importance DESC, created_at DESC
        LIMIT ?
    """, (category, now, limit))


def get_event_build_up_notes(event_name: str, min_days: int = 7) -> list[dict]:
    """Check how many days of build-up notes exist for a potential event.

    This is used by the AI to respect the rule that Major events need
    at least 1 week of build-up.

    Args:
        event_name: The event name to check for
        min_days: Minimum days of build-up required

    Returns:
        List of related notes (empty = not enough build-up)
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=min_days)).isoformat()
    db = get_db()
    return db.fetch_all("""
        SELECT category, content, importance, created_at
        FROM ai_notes
        WHERE (related_event LIKE ? OR content LIKE ?)
          AND created_at > ?
        ORDER BY created_at DESC
    """, (f"%{event_name}%", f"%{event_name}%", cutoff))


def clean_expired_notes() -> int:
    """Remove all expired notes from the database.

    Called periodically (e.g., weekly) to keep the notes table lean.

    Returns:
        Number of notes removed
    """
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    c = db.execute("DELETE FROM ai_notes WHERE expires_at IS NOT NULL AND expires_at < ?",
                   (now,))
    removed = c.rowcount
    if removed:
        log.info("helper_db", f"Cleaned {removed} expired notes")
    return removed


def count_notes_by_category() -> list[dict]:
    """Return a count breakdown of notes per category."""
    db = get_db()
    return db.fetch_all("""
        SELECT category, COUNT(*) as count
        FROM ai_notes
        WHERE expires_at IS NULL OR expires_at > ?
        GROUP BY category
        ORDER BY count DESC
    """, (datetime.now(timezone.utc).isoformat(),))
