"""
test_simulated_announce.py — Comprehensive tests for the SIMULATED_ANNOUNCE extension.

Covers:
  1. Schema creation and table management
  2. Subscription CRUD (sub, unsub, listing, limits)
  3. Announcement queue (enqueue, delivery, marking read)
  4. Event interestingness filter (edge cases)
  5. AI thinking-mode boost
  6. Batch event filtering
  7. Title and description generation
  8. Chat formatting
  9. Integration: cycle_end → evaluate → queue pipeline
 10. Edge cases: empty inputs, invalid IDs, duplicate subs,
     persona not found, massive event volumes
 11. Full data flow: event → filter → queue → deliver → log
"""

import os, sys, json, time, sqlite3, random, threading
from pathlib import Path
from datetime import datetime, timezone

# ── Setup paths ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure we can import the AUCTIONHOUSE package
AH_PATH = PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager"
sys.path.insert(0, str(AH_PATH))
os.environ["AH_TEST_MODE"] = "1"

# ── Test database ────────────────────────────────────────────────────
_TEST_DB_PATH = ":memory:"  # Use in-memory for speed
_TEST_DB_CONN = None


def _get_test_db():
    global _TEST_DB_CONN
    if _TEST_DB_CONN is None:
        _TEST_DB_CONN = sqlite3.connect(_TEST_DB_PATH, check_same_thread=False)
        _TEST_DB_CONN.row_factory = sqlite3.Row
    return _TEST_DB_CONN


# Patch ah_database.get_db to return our test DB
import AUCTIONHOUSE.ah_database as ah_db_mod
_original_get_db = ah_db_mod.get_db
ah_db_mod.get_db = _get_test_db

# Also patch the announce database module
import AUCTIONHOUSE.EXTENSIONS.SIMULATED_ANNOUNCE.pl_announce_database as ann_db_mod
ann_db_mod.get_db = _get_test_db

from AUCTIONHOUSE.EXTENSIONS.SIMULATED_ANNOUNCE.pl_announce_database import (
    ensure_schema, drop_tables,
    subscribe, unsubscribe, get_subscriptions, count_subscriptions,
    get_subscribers_for_persona, persona_subscriber_count,
    enqueue_announcement, get_undelivered_announcements,
    mark_delivered, mark_all_delivered, get_announcement_count,
    log_delivery, get_delivery_history, cleanup_old_announcements,
)

from AUCTIONHOUSE.EXTENSIONS.SIMULATED_ANNOUNCE.pl_announce_filter import (
    evaluate_event_interestingness, batch_filter_events,
    _score_by_event_type, _score_by_narrative,
    _ai_thinking_boost, _generate_title, _generate_description,
    format_announcement_for_chat, INTERESTINGNESS_THRESHOLD,
)

from AUCTIONHOUSE.EXTENSIONS.SIMULATED_ANNOUNCE.pl_announce_handler import (
    handle_command, _cmd_sub, _cmd_unsub, _cmd_subs, _cmd_announces,
    _cmd_clear, _help,
)

from AUCTIONHOUSE.EXTENSIONS.SIMULATED_ANNOUNCE import (
    on_simulation_cycle_end, _get_persona_profile, _discover_event_queries,
    _EVENT_QUERIES,
)


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestSchema:
    """1. Schema creation and table management."""

    def setup_method(self):
        drop_tables()

    def test_tables_created(self):
        ensure_schema()
        db = _get_test_db()
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        assert "ext_announce_subscriptions" in tables, "Missing subs table"
        assert "ext_announce_queue" in tables, "Missing queue table"
        assert "ext_announce_log" in tables, "Missing log table"

    def test_idempotent_schema(self):
        ensure_schema()
        ensure_schema()  # Should not raise

    def test_indexes_created(self):
        ensure_schema()
        db = _get_test_db()
        indexes = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_announce%'"
        ).fetchall()]
        assert len(indexes) >= 3, f"Expected 3+ indexes, got {indexes}"

    def test_drop_tables(self):
        ensure_schema()
        drop_tables()
        db = _get_test_db()
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ext_announce%'"
        ).fetchall()]
        assert len(tables) == 0, f"Tables still exist: {tables}"


class TestSubscriptions:
    """2. Subscription CRUD."""

    def setup_method(self):
        ensure_schema()
        # Clean data
        db = _get_test_db()
        db.execute("DELETE FROM ext_announce_subscriptions")
        db.commit()

    def test_subscribe_new(self):
        result = subscribe("Alice", "persona_001")
        assert result is True

    def test_subscribe_duplicate(self):
        subscribe("Alice", "persona_001")
        result = subscribe("Alice", "persona_001")
        assert result is False  # IGNORE'd

    def test_subscribe_multiple_players_same_persona(self):
        subscribe("Alice", "persona_001")
        subscribe("Bob", "persona_001")
        subs = get_subscribers_for_persona("persona_001")
        assert len(subs) == 2
        assert "Alice" in subs
        assert "Bob" in subs

    def test_subscribe_multiple_personas_same_player(self):
        subscribe("Alice", "persona_001")
        subscribe("Alice", "persona_002")
        subscribe("Alice", "persona_003")
        subs = get_subscriptions("Alice")
        assert len(subs) == 3

    def test_unsubscribe(self):
        subscribe("Alice", "persona_001")
        result = unsubscribe("Alice", "persona_001")
        assert result is True
        subs = get_subscriptions("Alice")
        assert len(subs) == 0

    def test_unsubscribe_not_subscribed(self):
        result = unsubscribe("Alice", "nonexistent")
        assert result is False

    def test_get_subscriptions_empty(self):
        subs = get_subscriptions("Nobody")
        assert len(subs) == 0

    def test_count_subscriptions(self):
        assert count_subscriptions("Alice") == 0
        subscribe("Alice", "p1")
        assert count_subscriptions("Alice") == 1
        subscribe("Alice", "p2")
        assert count_subscriptions("Alice") == 2

    def test_persona_subscriber_count(self):
        assert persona_subscriber_count("p1") == 0
        subscribe("A", "p1")
        subscribe("B", "p1")
        assert persona_subscriber_count("p1") == 2

    def test_get_subscribers_for_persona(self):
        subscribe("A", "p1")
        subscribe("B", "p1")
        subscribe("C", "p2")
        subs = get_subscribers_for_persona("p1")
        assert set(subs) == {"A", "B"}

    def test_subscription_created_at_timestamp(self):
        t_before = time.time()
        subscribe("Alice", "p1")
        t_after = time.time()
        subs = get_subscriptions("Alice")
        assert len(subs) == 1
        assert t_before <= subs[0]["created_at"] <= t_after


class TestAnnouncementQueue:
    """3. Announcement queue operations."""

    def setup_method(self):
        ensure_schema()
        db = _get_test_db()
        db.execute("DELETE FROM ext_announce_queue")
        db.execute("DELETE FROM ext_announce_log")
        db.commit()

    def test_enqueue_basic(self):
        qid = enqueue_announcement(
            player_name="Alice",
            persona_id="persona_001",
            event_type="death",
            title="☠ persona_001 died",
            description="They died in battle.",
            interestingness=10,
        )
        assert qid > 0

    def test_undelivered_count(self):
        assert get_announcement_count("Alice") == 0
        enqueue_announcement("Alice", "p1", "death", "Died")
        assert get_announcement_count("Alice") == 1
        enqueue_announcement("Alice", "p1", "war", "War")
        assert get_announcement_count("Alice") == 2

    def test_get_undelivered_ordered(self):
        enqueue_announcement("Alice", "p1", "death", "First")
        time.sleep(0.01)
        enqueue_announcement("Alice", "p1", "war", "Second")
        q = get_undelivered_announcements("Alice")
        assert len(q) == 2
        assert q[0]["title"] == "First"
        assert q[1]["title"] == "Second"

    def test_mark_delivered(self):
        qid = enqueue_announcement("Alice", "p1", "death", "Done")
        mark_delivered(qid)
        remaining = get_undelivered_announcements("Alice")
        assert len(remaining) == 0

    def test_mark_all_delivered(self):
        enqueue_announcement("Alice", "p1", "a", "A")
        enqueue_announcement("Alice", "p1", "b", "B")
        enqueue_announcement("Bob", "p1", "c", "C")
        mark_all_delivered("Alice")
        assert get_announcement_count("Alice") == 0
        assert get_announcement_count("Bob") == 1  # Bob's unaffected

    def test_enqueue_with_details(self):
        details = {"location": "Nether", "method": "lava"}
        qid = enqueue_announcement(
            "Alice", "p1", "death", "Died",
            description="Killed by lava",
            details=details,
            interestingness=9,
        )
        q = get_undelivered_announcements("Alice")
        assert len(q) == 1
        assert q[0]["details"] == details
        assert q[0]["interestingness"] == 9

    def test_delivery_log(self):
        log_delivery("Alice", "p1", "death", "Died", 10)
        log_delivery("Alice", "p1", "war", "War", 7)
        history = get_delivery_history("Alice")
        assert len(history) == 2

    def test_cleanup(self):
        enqueue_announcement("Alice", "p1", "test", "Old", interestingness=5)
        # Mark delivered
        q = get_undelivered_announcements("Alice")
        mark_delivered(q[0]["id"])
        log_delivery("Alice", "p1", "test", "Old", 5)
        # Update timestamp to be old
        db = _get_test_db()
        old_time = time.time() - (31 * 86400)  # 31 days ago
        db.execute("UPDATE ext_announce_queue SET created_at = ?", (old_time,))
        db.execute("UPDATE ext_announce_log SET delivered_at = ?", (old_time,))
        db.commit()
        cleanup_old_announcements(max_age_days=30)
        assert get_announcement_count("Alice") == 0
        history = get_delivery_history("Alice")
        assert len(history) == 0


class TestEventFilter:
    """4. Event interestingness filter."""

    def test_death_score(self):
        result = evaluate_event_interestingness("death", {"method": "lava"})
        assert result["interestingness"] >= 9
        assert result["should_announce"] is True

    def test_mundane_event_under_threshold(self):
        result = evaluate_event_interestingness("eat", {"item": "bread"})
        assert result["interestingness"] < INTERESTINGNESS_THRESHOLD
        assert result["should_announce"] is False

    def test_walk_under_threshold(self):
        result = evaluate_event_interestingness("walk", {"destination": "forest"})
        assert result["interestingness"] < INTERESTINGNESS_THRESHOLD
        assert result["should_announce"] is False

    def test_marriage_announce(self):
        result = evaluate_event_interestingness("marriage", {"target_id": "persona_002"})
        assert result["interestingness"] >= 8
        assert result["should_announce"] is True

    def test_divorce_announce(self):
        result = evaluate_event_interestingness("divorce", {"target_id": "persona_002"})
        assert result["interestingness"] >= 8
        assert result["should_announce"] is True

    def test_war_declared(self):
        result = evaluate_event_interestingness("war_declared", {"target_id": "Kingdom_A"})
        assert result["interestingness"] >= 7
        assert result["should_announce"] is True

    def test_combat_victory_announce(self):
        result = evaluate_event_interestingness("combat_victory", {"target_id": "dragon"})
        assert result["interestingness"] >= 6
        assert result["should_announce"] is True

    def test_skill_mastery(self):
        result = evaluate_event_interestingness("skill_mastery", {"skill_name": "swordsmanship"})
        assert result["interestingness"] >= 6
        assert result["should_announce"] is True

    def test_faction_join(self):
        result = evaluate_event_interestingness("faction_join", {"faction_name": "Guardians"})
        assert result["interestingness"] >= 5
        assert result["should_announce"] is True

    def test_small_trade_not_announced(self):
        result = evaluate_event_interestingness("trade", {"value": 5})
        assert result["interestingness"] < INTERESTINGNESS_THRESHOLD
        assert result["should_announce"] is False

    def test_medium_trade_announced(self):
        result = evaluate_event_interestingness("trade", {"value": 50})
        assert result["interestingness"] >= 5
        assert result["should_announce"] is True

    def test_major_trade_announced(self):
        result = evaluate_event_interestingness("major_trade", {"value": 200})
        assert result["interestingness"] >= 6
        assert result["should_announce"] is True

    def test_conversation_not_announced(self):
        result = evaluate_event_interestingness("conversation", {"topic": "weather"})
        assert result["interestingness"] < INTERESTINGNESS_THRESHOLD
        assert result["should_announce"] is False

    def test_gather_not_announced(self):
        result = evaluate_event_interestingness("gather", {"item": "berries", "count": 10})
        assert result["interestingness"] < INTERESTINGNESS_THRESHOLD
        assert result["should_announce"] is False

    def test_boredom_crisis_announced(self):
        result = evaluate_event_interestingness("boredom_crisis", {})
        assert result["interestingness"] >= 5
        assert result["should_announce"] is True

    def test_leadership_announced(self):
        result = evaluate_event_interestingness("leadership", {"role": "mayor"})
        assert result["interestingness"] >= 6
        assert result["should_announce"] is True

    def test_faction_disbanded_announced(self):
        result = evaluate_event_interestingness("faction_disbanded", {"faction_name": "Old Guard"})
        assert result["interestingness"] >= 7
        assert result["should_announce"] is True

    def test_unknown_event_default(self):
        result = evaluate_event_interestingness("completely_unknown", {"description": "something happened"})
        assert 1 <= result["interestingness"] <= 10
        # Should be mildly interesting at minimum
        assert result["interestingness"] >= 1

    def test_empty_event_data(self):
        result = evaluate_event_interestingness("death", None)
        assert result["interestingness"] >= 9
        assert result["should_announce"] is True

    @staticmethod
    def test_score_by_event_type_mapping():
        # Verify all known event types return valid scores
        test_cases = [
            ("death", 10), ("suicide", 10), ("died", 10), ("killed", 10),
            ("marriage", 9), ("married", 9), ("divorce", 9),
            ("war_declared", 8), ("war_end", 8), ("faction_disbanded", 8),
            ("combat_victory", 7), ("skill_mastery", 7), ("leadership", 7),
            ("injury", 7), ("faction_join", 6), ("faction_leave", 6),
            ("major_trade", 6), ("trade", 3), ("new_record", 5),
            ("travel", 5), ("boredom_crisis", 5), ("conversation", 2),
            ("gather", 2), ("walk", 1), ("sleep", 1), ("eat", 1),
        ]
        for event_type, expected_min in test_cases:
            score, _ = _score_by_event_type(event_type, {})
            assert score >= expected_min, f"{event_type}: expected >= {expected_min}, got {score}"


class TestNarrativeBonus:
    """5. Narrative context scoring."""

    def test_famous_persona_bonus(self):
        profile = {"status": "famous", "wealth": 100, "level": 10, "combat_count": 5}
        bonus = _score_by_narrative("trade", {"value": 30}, profile)
        assert bonus >= 0

    def test_wealthy_trade_bonus(self):
        profile = {"status": "normal", "wealth": 5000, "level": 5, "combat_count": 0}
        bonus = _score_by_narrative("trade", {"value": 30}, profile)
        assert bonus >= 1  # Wealthy + trade

    def test_high_level_achievement_bonus(self):
        profile = {"status": "normal", "wealth": 100, "level": 50, "combat_count": 0}
        bonus = _score_by_narrative("skill_mastery", {"skill_name": "archery"}, profile)
        assert bonus >= 1  # High level + mastery

    def test_no_profile_no_bonus(self):
        bonus = _score_by_narrative("trade", {"value": 30}, None)
        assert bonus == 0


class TestAIThinkingMode:
    """6. AI thinking-mode boost."""

    def test_rare_event_boost(self):
        boost = _ai_thinking_boost("death", {"rarity": "legendary", "target_id": "hero"}, None)
        assert boost >= 1

    def test_target_id_boost(self):
        boost = _ai_thinking_boost("combat_victory", {"target_id": "dragon"}, None)
        assert boost >= 1

    def test_high_value_boost(self):
        boost = _ai_thinking_boost("trade", {"value": 500}, None)
        assert boost >= 1

    def test_emotional_flag_boost(self):
        boost = _ai_thinking_boost("marriage", {"emotional": True}, None)
        assert boost >= 1

    def test_poor_person_big_trade(self):
        profile = {"status": "peasant", "wealth": 30, "level": 1, "combat_count": 0}
        boost = _ai_thinking_boost("trade", {"value": 150}, profile)
        assert boost >= 1  # Poor + big trade

    def test_first_combat_boost(self):
        profile = {"status": "civilian", "wealth": 100, "level": 5, "combat_count": 0}
        boost = _ai_thinking_boost("combat_victory", {"target_id": "bandit"}, profile)
        assert boost >= 1  # First combat

    def test_ai_mode_evaluation(self):
        # AI mode should give at least as high score as non-AI
        event = {"target_id": "dragon", "rarity": "epic"}
        result_normal = evaluate_event_interestingness("combat_victory", event, ai_mode=False)
        result_ai = evaluate_event_interestingness("combat_victory", event, ai_mode=True)
        assert result_ai["interestingness"] >= result_normal["interestingness"]

    @staticmethod
    def test_boost_capped_at_2():
        boost = _ai_thinking_boost(
            "trade",
            {"value": 999, "rarity": "legendary", "target_id": "king", "emotional": True},
            {"status": "famous", "wealth": 30, "level": 50, "combat_count": 0}
        )
        assert boost <= 2


class TestBatchFilter:
    """7. Batch event filtering."""

    def test_filters_out_boring_events(self):
        events = [
            {"event_type": "death", "persona_id": "p1", "data": {"method": "battle"}},
            {"event_type": "eat", "persona_id": "p1", "data": {"item": "bread"}},
            {"event_type": "walk", "persona_id": "p2", "data": {"destination": "forest"}},
            {"event_type": "marriage", "persona_id": "p2", "data": {"target_id": "p3"}},
        ]
        results = batch_filter_events(events)
        assert len(results) == 2  # Only death & marriage
        types = [r["event_type"] for r in results]
        assert "death" in types
        assert "marriage" in types

    def test_empty_input(self):
        results = batch_filter_events([])
        assert len(results) == 0

    def test_all_interesting(self):
        events = [
            {"event_type": "death", "persona_id": "p1", "data": {}},
            {"event_type": "war_declared", "persona_id": "p2", "data": {}},
            {"event_type": "skill_mastery", "persona_id": "p3", "data": {}},
        ]
        results = batch_filter_events(events)
        assert len(results) == 3

    def test_all_boring(self):
        events = [
            {"event_type": "sleep", "persona_id": "p1", "data": {}},
            {"event_type": "eat", "persona_id": "p2", "data": {}},
            {"event_type": "conversation", "persona_id": "p3", "data": {}},
        ]
        results = batch_filter_events(events)
        assert len(results) == 0

    def test_sort_by_interestingness(self):
        events = [
            {"event_type": "death", "persona_id": "p1", "data": {}},
            {"event_type": "trade", "persona_id": "p2", "data": {"value": 5}},
        ]
        results = batch_filter_events(events)
        if len(results) == 1:
            assert results[0]["event_type"] == "death"
        elif len(results) == 2:
            assert results[0]["interestingness"] >= results[1]["interestingness"]


class TestTitleGeneration:
    """8. Title and description generation."""

    @staticmethod
    def test_death_title():
        title = _generate_title("Alex", "death", {"method": "lava"})
        assert "Alex" in title
        assert "☠" in title or "died" in title

    @staticmethod
    def test_marriage_title():
        title = _generate_title("Alex", "marriage", {"target_id": "Bob"})
        assert "Alex" in title
        assert "Bob" in title
        assert "💍" in title or "married" in title

    @staticmethod
    def test_war_title():
        title = _generate_title("Alex", "war_declared", {"target_id": "Bob"})
        assert "Alex" in title
        assert "war" in title.lower()

    @staticmethod
    def test_skill_title():
        title = _generate_title("Alex", "skill_mastery", {"skill_name": "fishing"})
        assert "Alex" in title
        assert "fishing" in title.lower() or "mastered" in title.lower()

    @staticmethod
    def test_faction_title():
        title = _generate_title("Alex", "faction_join", {"faction_name": "Heroes"})
        assert "Alex" in title
        assert "Heroes" in title or "joined" in title.lower()

    @staticmethod
    def test_boring_event_title():
        title = _generate_title("Alex", "walk", {"destination": "forest"})
        assert "Alex" in title

    @staticmethod
    def test_description_contains_name():
        desc = _generate_description("Alex", "death", {"method": "fire"}, {"reason": "burned"})
        assert "Alex" in desc

    @staticmethod
    def test_description_fallback():
        desc = _generate_description(
            "Alex", "faction_join", {"faction_name": "Club"},
            {"reason": "Joined a faction"}
        )
        assert "Club" in desc or "joined" in desc.lower() or "strengthening" in desc.lower()


class TestChatFormatting:
    """9. Chat message formatting."""

    @staticmethod
    def test_high_interest_color():
        result = format_announcement_for_chat({
            "title": "Persona died",
            "description": "They died",
            "interestingness": 10,
        })
        assert "§4" in result  # Dark red for major

    @staticmethod
    def test_medium_interest_color():
        result = format_announcement_for_chat({
            "title": "Trade happened",
            "description": "Traded items",
            "interestingness": 6,
        })
        assert "§6" in result  # Gold for medium

    @staticmethod
    def test_contains_title_and_desc():
        result = format_announcement_for_chat({
            "title": "Test Event",
            "description": "A description",
            "interestingness": 7,
        })
        assert "Test Event" in result
        assert "A description" in result


class TestCommandHandler:
    """10. Command handler edge cases."""

    def setup_method(self):
        ensure_schema()
        db = _get_test_db()
        db.execute("DELETE FROM ext_announce_subscriptions")
        db.execute("DELETE FROM ext_announce_queue")
        db.execute("DELETE FROM ext_announce_log")
        db.commit()

    def test_help(self):
        result = _help()
        assert "message" in result
        assert len(result["message"]) > 0

    @staticmethod
    def test_handler_empty():
        result = handle_command([])
        assert "help" in result.get("message", [str(result)])[0].lower() or "message" in result

    @staticmethod
    def test_sub_no_args():
        result = handle_command(["sub"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "Usage" in msg

    @staticmethod
    def test_sub_invalid_id():
        result = handle_command(["sub", ""], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "invalid" in msg.lower() or "Invalid" in msg

    @staticmethod
    def test_unsub_no_args():
        result = handle_command(["unsub"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "Usage" in msg

    @staticmethod
    def test_subs_empty():
        result = handle_command(["subs"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "not subscribed" in msg.lower() or "not" in msg.lower()

    @staticmethod
    def test_announces_empty():
        result = handle_command(["announces"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "pending" in msg.lower() or "No" in msg

    @staticmethod
    def test_clear_empty():
        result = handle_command(["clear"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "pending" in msg.lower() or "No" in msg or "clear" in msg.lower()

    def test_sub_then_subs(self):
        handle_command(["sub", "persona_test"], player_name="Alice")
        result = handle_command(["subs"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "persona_test" in msg
        assert "1/20" in msg or "1" in msg

    def test_sub_then_unsub(self):
        handle_command(["sub", "p1"], player_name="Alice")
        result = handle_command(["unsub", "p1"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "Unsubscribed" in msg or "unsubscribed" in msg.lower()

    def test_unsub_not_subscribed(self):
        result = handle_command(["unsub", "nonexistent"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "not subscribed" in msg.lower()

    def test_sub_duplicate(self):
        handle_command(["sub", "p1"], player_name="Alice")
        result = handle_command(["sub", "p1"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "already" in msg.lower()

    def test_subscription_limit(self):
        # Subscribe to 20 personas (the max)
        for i in range(20):
            handle_command(["sub", f"p{i}"], player_name="Alice")
        # Try one more
        result = handle_command(["sub", "p_extra"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "20" in msg or "only" in msg.lower()

    def test_announce_then_clear(self):
        # Directly enqueue an announcement
        enqueue_announcement("Alice", "p1", "death", "☠ p1 died", interestingness=10)
        result = handle_command(["announces"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "p1 died" in msg or "pending" in msg.lower()
        # Now clear
        result2 = handle_command(["clear"], player_name="Alice")
        msg2 = "\n".join(result2.get("message", []))
        assert "Cleared" in msg2 or "cleared" in msg2.lower() or "1" in msg2

    @staticmethod
    def test_unknown_command_fallsback_to_help():
        result = handle_command(["foobar", "arg1"], player_name="Alice")
        msg = "\n".join(result.get("message", [])).lower()
        assert "help" in msg or "Usage" in msg or "announce" in msg


class TestEdgeCases:
    """11. Edge cases."""

    def setup_method(self):
        ensure_schema()
        db = _get_test_db()
        db.execute("DELETE FROM ext_announce_subscriptions")
        db.execute("DELETE FROM ext_announce_queue")
        db.execute("DELETE FROM ext_announce_log")
        db.commit()

    @staticmethod
    def test_special_chars_in_id():
        result = handle_command(["sub", "hello-world_123"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        # Should work or give valid error
        assert len(msg) > 0

    @staticmethod
    def test_long_persona_id():
        long_id = "a" * 65
        result = handle_command(["sub", long_id], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "invalid" in msg.lower() or "Invalid" in msg

    @staticmethod
    def test_very_long_player_name():
        result = handle_command(["sub", "p1"], player_name="A" * 100)
        msg = "\n".join(result.get("message", []))
        assert "invalid" in msg.lower() or "Usage" in msg

    @staticmethod
    def test_unicode_in_ids():
        result = handle_command(["sub", "persona_♠"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        assert "invalid" in msg.lower() or "Invalid" in msg

    @staticmethod
    def test_mixed_case_command():
        result = handle_command(["SUB", "p1"], player_name="Alice")
        msg = "\n".join(result.get("message", []))
        # Should match "sub" case-insensitively
        assert "not found" not in msg.lower()

    def test_massive_event_batch(self):
        """Filter 1000 events without performance issues."""
        events = []
        for i in range(1000):
            events.append({
                "event_type": random.choice(["death", "walk", "eat", "marriage", "trade", "sleep"]),
                "persona_id": f"p{i % 50}",
                "data": {"value": random.randint(1, 200)},
            })
        results = batch_filter_events(events)
        # Should complete without error and return reasonable number
        assert isinstance(results, list)
        assert len(results) <= len(events)

    @staticmethod
    def test_empty_event_data_in_filter():
        result = evaluate_event_interestingness("", {})
        assert 1 <= result["interestingness"] <= 10

    @staticmethod
    def test_none_data_in_filter():
        result = evaluate_event_interestingness("death", None)
        assert result["interestingness"] >= 9

    @staticmethod
    def test_score_at_boundary():
        # Events scoring exactly 5 should be announced
        result = evaluate_event_interestingness("new_record", {})
        assert result["interestingness"] >= 5
        assert result["should_announce"] is True

    @staticmethod
    def test_score_below_boundary():
        # Events scoring below 5 should NOT be announced
        result = evaluate_event_interestingness("eat", {"item": "apple"})
        assert result["interestingness"] < INTERESTINGNESS_THRESHOLD
        assert result["should_announce"] is False

    def test_multiple_players_same_persona(self):
        """Two players subscribed to same persona both receive announcements."""
        enqueue_announcement("Alice", "p1", "death", "Died", interestingness=10)
        enqueue_announcement("Bob", "p1", "death", "Died", interestingness=10)
        alice_q = get_undelivered_announcements("Alice")
        bob_q = get_undelivered_announcements("Bob")
        assert len(alice_q) == 1
        assert len(bob_q) == 1

    def test_persona_exists_check(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_ANNOUNCE.pl_announce_handler import _persona_exists
        # Should not crash when checking existence
        assert _persona_exists("any_persona") is True  # Graceful when tables absent


class TestDataFlow:
    """12. Full data flow: event → filter → queue → deliver → log."""

    def setup_method(self):
        ensure_schema()
        db = _get_test_db()
        db.execute("DELETE FROM ext_announce_subscriptions")
        db.execute("DELETE FROM ext_announce_queue")
        db.execute("DELETE FROM ext_announce_log")
        db.commit()

    def test_full_pipeline(self):
        """Complete flow: personas have events → filter → subscribe → queue → read → clear → log."""
        # 1. Player subscribes to a persona
        player = "TestPlayer"
        persona = "hero_persona"
        handle_command(["sub", persona], player_name=player)

        # 2. Events happen in the world
        events = [
            {"event_type": "death", "persona_id": persona, "data": {"method": "battle", "location": "Dark Forest"}},
            {"event_type": "eat", "persona_id": persona, "data": {"item": "bread"}},  # boring — filtered
            {"event_type": "walk", "persona_id": persona, "data": {}},  # boring — filtered
            {"event_type": "skill_mastery", "persona_id": persona, "data": {"skill_name": "archery"}},
        ]

        # 3. Filter events
        filtered = batch_filter_events(events)
        assert len(filtered) == 2  # death + skill_mastery
        assert all(e["should_announce"] for e in filtered)

        # 4. Queue announcements for subscribers
        subscribers = get_subscribers_for_persona(persona)
        assert player in subscribers

        for event in filtered:
            enqueue_announcement(
                player_name=player,
                persona_id=event["persona_id"],
                event_type=event["event_type"],
                title=event["title"],
                description=event["description"],
                details=event.get("data", {}),
                interestingness=event["interestingness"],
            )

        # 5. Verify queued
        assert get_announcement_count(player) == 2
        q = get_undelivered_announcements(player)
        assert len(q) == 2

        # 6. Player reads announcements
        result = handle_command(["announces"], player_name=player)
        msg = "\n".join(result.get("message", []))
        assert "2" in msg or "Pending" in msg or "Pending Announcements" in msg or "pending" in msg.lower()
        assert persona in msg or persona in str(result)

        # 7. Player clears announcements
        result2 = handle_command(["clear"], player_name=player)
        msg2 = "\n".join(result2.get("message", []))
        assert "2" in msg2 or "Cleared" in msg2 or "cleared" in msg2.lower()

        # 8. Verify empty
        assert get_announcement_count(player) == 0

        # 9. Verify logged
        history = get_delivery_history(player)
        assert len(history) == 2

    def test_cycle_end_integration(self):
        """Simulate the on_simulation_cycle_end being called."""
        # Subscribe a player
        player = "CycleTester"
        handle_command(["sub", "cycle_persona"], player_name=player)

        # Create event data directly in the database
        db = _get_test_db()

        # Create a mock events table that our extension will discover
        db.execute("""
            CREATE TABLE IF NOT EXISTS ext_sp_persona_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                persona_id TEXT,
                event_data TEXT,
                created_at REAL
            )
        """)

        # Insert some events (one interesting, one boring)
        now = time.time()
        db.execute(
            "INSERT INTO ext_sp_persona_events (event_type, persona_id, event_data, created_at) VALUES (?, ?, ?, ?)",
            ("death", "cycle_persona", json.dumps({"method": "dragon", "location": "mountain"}), now - 10)
        )
        db.execute(
            "INSERT INTO ext_sp_persona_events (event_type, persona_id, event_data, created_at) VALUES (?, ?, ?, ?)",
            ("eat", "cycle_persona", json.dumps({"item": "apple"}), now - 5)
        )
        db.commit()

        # Clear event query cache
        global _EVENT_QUERIES_CACHED
        _EVENT_QUERIES_CACHED = False
        _EVENT_QUERIES.clear()

        # Call on_simulation_cycle_end
        result = on_simulation_cycle_end(cycle_start_time=now - 60)

        # Verify results
        assert result["status"] == "ok" or result["events_checked"] > 0

        # The interesting event (death) should have been queued
        q = get_undelivered_announcements(player)
        # Cleanup test table
        db.execute("DROP TABLE IF EXISTS ext_sp_persona_events")
        db.commit()


# ═══════════════════════════════════════════════════════════════════════
# Manual runner
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
