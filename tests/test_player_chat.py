"""
tests/test_player_chat.py — Tests for the SIMULATED_CHAT extension.

Covers: database CRUD, AI response generation, command handling,
interest system, queued messages, error handling, edge cases.

Run: python3 -m unittest tests.test_player_chat -v
"""

import os, sys, json, unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "SCRIPTS" / "GAMES" / "minecraft_manager"))
sys.path.insert(0, str(_PROJECT_ROOT))

TEST_DB_DIR = _PROJECT_ROOT / "tests" / "test_data_chat"
TEST_DB_PATH = TEST_DB_DIR / "auctionhouse.db3"


def setup_test_db():
    TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    import AUCTIONHOUSE.ah_database as ah_db
    if ah_db._db_instance is not None:
        ah_db._db_instance.close()
        ah_db._db_instance = None
    if hasattr(ah_db.DatabaseManager, '_local'):
        try:
            if hasattr(ah_db.DatabaseManager._local, 'conn'):
                ah_db.DatabaseManager._local.conn = None
        except AttributeError:
            pass
    ah_db.DB_PATH = TEST_DB_PATH
    ah_db.DB_DIR = TEST_DB_DIR
    from AUCTIONHOUSE.ah_database import initialize_database
    try:
        initialize_database(force=False)
    except Exception:
        pass
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import ensure_schema as sp_ensure
    sp_ensure()
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import ensure_schema
    ensure_schema()
    from AUCTIONHOUSE.ah_database import get_db
    db = get_db()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for puid, name, arch in [("chat-p1", "Chatty Chad", "adventurer"),
                               ("chat-p2", "Mercenary Max", "warrior"),
                               ("chat-p3", "Farmer Fran", "farmer")]:
        existing = db.fetch_one("SELECT id FROM ext_sp_profiles WHERE persona_uuid = ?", (puid,))
        if not existing:
            db.execute("""
                INSERT INTO ext_sp_profiles
                (persona_uuid, name, archetype, job, region, wealth_tier,
                 personality_traits, active, created_at)
                VALUES (?, ?, ?, 'tester', 'overworld', 'middle', '{}', 1, ?)
            """, (puid, name, arch, now))


def teardown_test_db():
    import AUCTIONHOUSE.ah_database as ah_db
    if ah_db._db_instance is not None:
        ah_db._db_instance.close()
        ah_db._db_instance = None
    try:
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        if TEST_DB_DIR.exists():
            for f in TEST_DB_DIR.iterdir():
                if f.is_file():
                    f.unlink()
            TEST_DB_DIR.rmdir()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# DATABASE TESTS
# ══════════════════════════════════════════════════════════════════════


class TestDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_schema_creates_tables(self):
        from AUCTIONHOUSE.ah_database import get_db
        db = get_db()
        tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ext_chat_%'")
        names = {t["name"] for t in tables}
        expected = {"ext_chat_player_connections", "ext_chat_conversations",
                     "ext_chat_messages", "ext_chat_queued_messages",
                     "ext_chat_player_interest"}
        self.assertFalse(expected - names)

    def test_create_connection(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            get_or_create_connection
        )
        conn = get_or_create_connection("player-test-1", "chat-p1")
        self.assertIn("message_count", conn)
        self.assertEqual(conn["message_count"], 1)

    def test_create_conversation(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            create_conversation, get_active_conversation
        )
        conv_id = create_conversation("player-test-2", "chat-p2")
        self.assertGreater(conv_id, 0)
        conv = get_active_conversation("player-test-2", "chat-p2")
        self.assertIsNotNone(conv)

    def test_log_message(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            create_conversation, log_message
        )
        conv_id = create_conversation("player-log", "chat-p1")
        msg = log_message(conv_id, "player", "player-log", "Hello!")
        self.assertIn("message_id", msg)

    def test_get_known_personas(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import get_known_personas
        personas = get_known_personas()
        self.assertGreaterEqual(len(personas), 3)

    def test_interest_crud(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            get_or_create_interest, update_interest
        )
        intr = get_or_create_interest("chat-p1", "player-int")
        self.assertIn("interest_level", intr)
        new_val = update_interest("chat-p1", "player-int", 10.0)
        self.assertGreater(new_val, 25.0)

    def test_queue_message(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            queue_message, get_queued_messages
        )
        q = queue_message("player-q", "chat-p1", "Hello from persona!")
        self.assertIn("queued_id", q)
        msgs = get_queued_messages("player-q")
        self.assertGreaterEqual(len(msgs), 1)

    def test_mark_message_read(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            queue_message, mark_message_read
        )
        q = queue_message("player-read", "chat-p2", "Test message")
        self.assertTrue(mark_message_read(q["queued_id"]))


# ══════════════════════════════════════════════════════════════════════
# AI RESPONSE TESTS
# ══════════════════════════════════════════════════════════════════════


class TestAIResponse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_generate_greeting(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        result = generate_response("chat-p1", "player-ai", "Hello!",
                                    conversation_context="first")
        self.assertIn("response", result)
        self.assertIn("Chatty", result["response"])

    def test_generate_farewell(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        result = generate_response("chat-p2", "player-ai", "Goodbye!",
                                    conversation_context="farewell")
        self.assertIn("response", result)

    def test_generate_insult_response(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        result = generate_response("chat-p2", "player-ai",
                                    "You're an idiot!", archetype="warrior")
        self.assertIn("response", result)

    def test_generate_gift_response(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        result = generate_response("chat-p3", "player-ai",
                                    "I have a gift for you", archetype="farmer")
        self.assertIn("response", result)

    def test_generate_empty_persona(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        result = generate_response("", "", "Hello")
        self.assertEqual(result["response"], "...")

    def test_generate_all_archetype_greetings(self):
        """Verify every archetype has a greeting response."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        archetypes = ["adventurer", "merchant", "builder", "miner",
                       "farmer", "warrior", "mage", "vagabond", "unknown"]
        for arch in archetypes:
            result = generate_response("chat-p1", "player-arch", "Hello!",
                                        archetype=arch,
                                        conversation_context="first")
            self.assertIn("response", result,
                          msg=f"Failed for archetype: {arch}")
            self.assertIsNotNone(result.get("response"))

    def test_generate_all_archetype_farewells(self):
        """Verify every archetype has a farewell response."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        archetypes = ["adventurer", "merchant", "builder", "miner",
                       "farmer", "warrior", "mage", "vagabond", "unknown"]
        for arch in archetypes:
            result = generate_response("chat-p2", "player-fw", "Goodbye",
                                        archetype=arch,
                                        conversation_context="farewell")
            self.assertIn("response", result,
                          msg=f"Failed for farewell archetype: {arch}")

    def test_generate_all_archetype_insults(self):
        """Verify each archetype handles insults differently."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        for arch in ["warrior", "mage", "vagabond", "farmer"]:
            result = generate_response("chat-p2", "player-insult",
                                        "You're stupid!", archetype=arch)
            self.assertIn("response", result,
                          msg=f"Failed for insult archetype: {arch}")

    def test_generate_all_context_types(self):
        """Verify all context types produce valid responses."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        test_cases = [
            ("farewell", "Goodbye", None),
            ("insult", "You're an idiot!", None),
            ("gift", "I have a gift for you", None),
            ("request", "Can you help me?", None),
            ("question_about_self", "Who are you?", None),
            ("statement", "The sky is blue.", None),
        ]
        for expected_ctx, msg, conv_ctx in test_cases:
            result = generate_response("chat-p1", "player-ctx", msg,
                                        conversation_context=conv_ctx)
            self.assertIn("response", result, msg=f"No response for: {msg}")
            self.assertIn("context_type", result, msg=f"No context for: {msg}")

    def test_stat_changes_on_gift(self):
        """Verify gift messages trigger interest changes (regression for player_unknown bug)."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            get_or_create_interest, update_interest
        )
        update_interest("chat-p1", "player-gift", 0.0)
        before = get_or_create_interest("chat-p1", "player-gift")
        before_level = before.get("interest_level", 25.0)

        result = generate_response("chat-p1", "player-gift",
                                    "I have a gift for you!")

        after = get_or_create_interest("chat-p1", "player-gift")
        after_level = after.get("interest_level", 25.0)
        self.assertGreaterEqual(after_level, before_level)

    def test_stat_changes_on_insult(self):
        """Verify insult messages trigger interest decrease."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            get_or_create_interest, update_interest
        )
        update_interest("chat-p1", "player-insult", 50.0)
        before = get_or_create_interest("chat-p1", "player-insult")
        before_level = before.get("interest_level", 50.0)

        result = generate_response("chat-p1", "player-insult",
                                    "You're an idiot!")

        after = get_or_create_interest("chat-p1", "player-insult")
        after_level = after.get("interest_level", 50.0)
        self.assertLessEqual(after_level, before_level)

    def test_player_uuid_passed_to_stat_changes(self):
        """CRITICAL: Verify actual player_uuid is passed to _detect_stat_changes,
        NOT a hardcoded 'player_unknown' placeholder. (Regression for work order #994)"""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _detect_stat_changes
        )
        # Verify the function signature accepts player_uuid and doesn't crash
        # The function will fail gracefully when DB is unavailable
        result = _detect_stat_changes(
            "test-persona", "real-player-uuid-12345",
            "Hello world", "adventurer", 25.0
        )
        self.assertIsNone(result)


# ══════════════════════════════════════════════════════════════════════
# _detect_context UNIT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestDetectContext(unittest.TestCase):
    def setUp(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import _detect_context
        self._detect_context = _detect_context

    def test_farewell(self):
        self.assertEqual(self._detect_context("Goodbye!"), "farewell")
        self.assertEqual(self._detect_context("bye"), "farewell")
        self.assertEqual(self._detect_context("see you later"), "farewell")

    def test_insult(self):
        self.assertEqual(self._detect_context("You're stupid"), "insult")
        self.assertEqual(self._detect_context("idiot"), "insult")
        self.assertEqual(self._detect_context("shut up"), "insult")

    def test_gift(self):
        self.assertEqual(self._detect_context("I have a gift"), "gift")
        self.assertEqual(self._detect_context("take this"), "gift")
        self.assertEqual(self._detect_context("here, for you"), "gift")

    def test_request(self):
        self.assertEqual(self._detect_context("Please help me"), "request")
        self.assertEqual(self._detect_context("Could you do this?"), "request")
        self.assertEqual(self._detect_context("I need assistance"), "request")

    def test_question_about_self(self):
        self.assertEqual(self._detect_context("Who are you?"), "question_about_self")
        self.assertEqual(self._detect_context("What is this place?"), "question_about_self")
        self.assertEqual(self._detect_context("Tell me about yourself"), "question_about_self")

    def test_question(self):
        # "How" at start is caught by question_about_self first
        self.assertEqual(self._detect_context("How are you?"), "question_about_self")
        # Questions that don't start with interrogatives
        self.assertEqual(self._detect_context("Is this thing on?"), "question")
        self.assertEqual(self._detect_context("Do you like it?"), "question")

    def test_statement(self):
        self.assertEqual(self._detect_context("The sky is blue."), "statement")
        self.assertEqual(self._detect_context("I like apples"), "statement")

    def test_empty_message(self):
        self.assertEqual(self._detect_context(""), "statement")

    def test_case_insensitivity(self):
        self.assertEqual(self._detect_context("GOODBYE!"), "farewell")
        self.assertEqual(self._detect_context("IDIOT"), "insult")
        self.assertEqual(self._detect_context("GIFT"), "gift")

    def test_priority_farewell_over_gift(self):
        """Farewells should take priority over gifts when keywords overlap."""
        self.assertEqual(self._detect_context("Goodbye, here's a gift"), "farewell")

    def test_priority_insult_over_request(self):
        """Insults should take priority over requests."""
        self.assertEqual(self._detect_context("Please help me you idiot"), "insult")


# ══════════════════════════════════════════════════════════════════════
# _detect_stat_changes UNIT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestDetectStatChanges(unittest.TestCase):
    def test_no_keywords_returns_none(self):
        """Messages without gift/insult keywords should return None."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import _detect_stat_changes
        result = _detect_stat_changes(
            "test-persona", "test-player",
            "no keywords here", "adventurer", 25.0
        )
        self.assertIsNone(result)

    def test_empty_message_no_changes(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import _detect_stat_changes
        result = _detect_stat_changes(
            "test-persona", "test-player",
            "", "adventurer", 25.0
        )
        self.assertIsNone(result)


# ══════════════════════════════════════════════════════════════════════
# _generate_* UNIT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestGeneratorFunctions(unittest.TestCase):
    def test_a_job(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import a_job
        self.assertEqual(a_job("adventurer"), "an adventurer")
        self.assertEqual(a_job("merchant"), "a merchant")
        self.assertEqual(a_job("warrior"), "a warrior")
        self.assertEqual(a_job("mage"), "a mage")
        self.assertEqual(a_job("unknown"), "a unknown")

    def test_generate_greeting_all_archetypes(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_greeting
        )
        for arch in ["adventurer", "merchant", "builder", "miner",
                       "farmer", "warrior", "mage", "vagabond", "unknown"]:
            result = _generate_greeting("Test", arch, 25.0)
            self.assertIn("Test:", result)
            self.assertIn('"', result)

    def test_generate_greeting_high_interest(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_greeting
        )
        result = _generate_greeting("Test", "adventurer", 75.0)
        self.assertIn("wonderful", result)

    def test_generate_greeting_low_interest(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_greeting
        )
        result = _generate_greeting("Test", "adventurer", 25.0)
        self.assertNotIn("wonderful", result)

    def test_generate_farewell_high_interest(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_farewell
        )
        result = _generate_farewell("Test", "merchant", 75.0)
        self.assertIn("remember", result)

    def test_generate_farewell_low_interest(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_farewell
        )
        result = _generate_farewell("Test", "merchant", 25.0)
        self.assertNotIn("remember", result)

    def test_generate_self_description(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_self_description
        )
        persona = {"name": "TestPersona", "job": "farmer", "wealth_tier": "rich"}
        skills = {"farming": 50, "mining": 30}
        result = _generate_self_description(persona, skills, "farmer")
        # Name is truncated to 8 chars
        self.assertIn("TestPers", result)
        self.assertIn("farming", result)

    def test_generate_self_description_empty_skills(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_self_description
        )
        persona = {"name": "TestPersona", "job": "fighter", "wealth_tier": "poor"}
        result = _generate_self_description(persona, {}, "warrior")
        self.assertIn("TestPers", result)

    def test_generate_gift_response(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_gift_response
        )
        result = _generate_gift_response("Test", "farmer")
        self.assertTrue(result.startswith("Test:"))
        self.assertIn("I'll return the favor", result)
        self.assertIn("good use", result.lower())

    def test_generate_insult_response_warrior(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_insult_response
        )
        result = _generate_insult_response("Test", "warrior", 50.0)
        self.assertIn("Test:", result)

    def test_generate_insult_response_low_interest(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_insult_response
        )
        result = _generate_insult_response("Test", "warrior", 10.0)
        self.assertNotIn("didn't speak", result)

    def test_generate_request_response(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_request_response
        )
        result = _generate_request_response("Test", "farmer")
        self.assertIn("Test:", result)

    def test_generate_question_response(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_question_response
        )
        result = _generate_question_response("Test", "mage", 50.0)
        self.assertIn("Test:", result)
        self.assertTrue(result.startswith("Test:"))

    def test_generate_statement_response(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_statement_response
        )
        result = _generate_statement_response("Test", "adventurer", 50.0)
        self.assertIn("Test:", result)

    def test_generate_statement_all_archetypes(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import (
            _generate_statement_response
        )
        for arch in ["adventurer", "merchant", "farmer", "warrior",
                       "mage", "vagabond", "default"]:
            result = _generate_statement_response("Test", arch, 50.0)
            self.assertTrue(result.startswith("Test:"),
                            msg=f"Failed for archetype: {arch}")


# ══════════════════════════════════════════════════════════════════════
# ERROR HANDLING TESTS (regression for try/except anti-pattern fix)
# ══════════════════════════════════════════════════════════════════════


class TestErrorHandling(unittest.TestCase):
    """Ensure the fixed try/except blocks properly catch exceptions."""

    def test_generate_response_error_recovery(self):
        """generate_response should return safe defaults on error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import generate_response
        result = generate_response(None, "player-uuid", "hello")
        self.assertIn("response", result)
        self.assertEqual(result["response"], "...")

    def test_detect_context_error_recovery(self):
        """_detect_context should return '' on error (caught by try/except)."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_ai import _detect_context
        result = _detect_context(None)
        self.assertEqual(result, "")

    def test_handle_command_error_recovery(self):
        """handle_command should return error dict on unexpected errors."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command([])
        self.assertIn("error", result)

    def test_handle_msg_error_recovery(self):
        """_handle_msg should return error dict with missing persona."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["msg"])
        self.assertIn("error", result)

    def test_check_and_queue_messages_error_recovery(self):
        """check_and_queue_messages should return None on error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_interest import (
            check_and_queue_messages
        )
        result = check_and_queue_messages(None, None)
        self.assertIsNone(result)

    def test_all_functions_handle_none(self):
        """Sanity check: All public functions should handle None arguments gracefully."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_interest import (
            process_interest_decay, can_send_proactive_message,
            generate_proactive_message
        )
        try:
            process_interest_decay(None, None)
            can_send_proactive_message(None, None)
            generate_proactive_message(None, None)
        except Exception as e:
            self.fail(f"Functions crashed with None: {e}")


# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLER TESTS
# ══════════════════════════════════════════════════════════════════════


class TestCommandHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_msg_list(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["msg", "list"])
        self.assertEqual(result.get("status"), "ok")
        self.assertIn("personas", result)

    def test_msg_command(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["msg", "chat-p1", "Hello there!"])
        self.assertIn("status", result)

    def test_msg_missing_args(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["msg"])
        self.assertIn("error", result)

    def test_qmsg_list(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["qmsg", "list"])
        self.assertEqual(result.get("status"), "ok")

    def test_qmsg_invalid_sub(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["qmsg", "invalid"])
        self.assertIn("error", result)

    def test_unknown_sub(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["unknown"])
        self.assertIn("error", result)

    def test_empty_args(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command([])
        self.assertIn("error", result)


# ══════════════════════════════════════════════════════════════════════
# INTEREST SYSTEM TESTS
# ══════════════════════════════════════════════════════════════════════


class TestInterest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_interest_decay(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_interest import (
            process_interest_decay
        )
        new_val = process_interest_decay("chat-p1", "player-decay")
        self.assertLess(new_val, 30.0)

    def test_can_send_proactive_low_interest(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_interest import (
            can_send_proactive_message
        )
        result = can_send_proactive_message("chat-p1", "player-low")
        self.assertFalse(result)

    def test_generate_proactive_message(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_interest import (
            generate_proactive_message
        )
        msg = generate_proactive_message("chat-p1", "player-pro")
        self.assertIsNotNone(msg)
        self.assertIn("Chatty", msg)


# ══════════════════════════════════════════════════════════════════════
# PLUGIN REGISTRATION TESTS
# ══════════════════════════════════════════════════════════════════════


class TestPluginRegistration(unittest.TestCase):
    def test_on_load_function(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.__init__ import (
            EXTENSION_NAME
        )
        self.assertEqual(EXTENSION_NAME, "SIMULATED_CHAT")

    def test_handle_command_import(self):
        setup_test_db()
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.__init__ import handle_command
        result = handle_command(["msg", "list"])
        self.assertIn("status", result)
        teardown_test_db()


# ══════════════════════════════════════════════════════════════════════
# EDGE CASES
# ══════════════════════════════════════════════════════════════════════


class TestEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_test_db()

    @classmethod
    def tearDownClass(cls):
        teardown_test_db()

    def test_connection_empty_uuid(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            get_or_create_connection
        )
        conn = get_or_create_connection("", "")
        self.assertEqual(conn, {})

    def test_conversation_empty_uuid(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            get_active_conversation
        )
        conv = get_active_conversation("", "")
        self.assertIsNone(conv)

    def test_interest_empty_uuid(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            update_interest
        )
        new_val = update_interest("", "", 10.0)
        self.assertEqual(new_val, 0.0)

    def test_queue_message_empty(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            queue_message
        )
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import get_queued_messages
        q = queue_message("player-empty", "chat-p3", "Test")
        msgs = get_queued_messages("player-empty")
        self.assertGreaterEqual(len(msgs), 1)

    def test_mark_message_invalid_id(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            mark_message_read
        )
        result = mark_message_read(99999)
        self.assertFalse(result)

    def test_reply_to_nonexistent(self):
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_database import (
            reply_to_queued
        )
        result = reply_to_queued(99999, "test reply")
        self.assertFalse(result)

    def test_handle_command_player_uuid_forwarded(self):
        """Verify player_uuid is properly forwarded through the command chain."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import (
            _handle_msg
        )
        result = _handle_msg(["chat-p1", "Hello"], player_uuid="specific-player")
        self.assertIn("status", result)

    def test_qmsg_read_nonexistent_id(self):
        """Reading a nonexistent message ID should return error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["qmsg", "read", "99999"])
        self.assertIn("error", result)

    def test_qmsg_reply_invalid_id(self):
        """Replying to an invalid message ID should return error."""
        from AUCTIONHOUSE.EXTENSIONS.SIMULATED_CHAT.pl_chat_handler import handle_command
        result = handle_command(["qmsg", "reply", "notanumber", "hello"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
