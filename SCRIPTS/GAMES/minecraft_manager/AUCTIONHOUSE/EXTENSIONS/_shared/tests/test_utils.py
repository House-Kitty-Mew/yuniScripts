"""
test_utils.py — Unit tests for the _shared/utils module.

Tests the safe_call decorator with various scenarios.
"""

import unittest
import logging
from unittest.mock import patch, MagicMock
from EXTENSIONS._shared.utils import safe_call


class TestSafeCallDecorator(unittest.TestCase):
    """Tests for the safe_call decorator."""

    def setUp(self):
        self.log = logging.getLogger("test_safe_call")
        self.log.handlers = []  # prevent test output pollution

    # ── Basic success/failure ─────────────────────────────────────────

    def test_successful_function_returns_result(self):
        """A function that succeeds should return its normal result."""
        @safe_call(default_return=None)
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        result = greet("World")
        self.assertEqual(result, "Hello, World!")

    def test_failing_function_returns_default(self):
        """A function that raises should return the default_return."""
        @safe_call(default_return=42, logger=self.log)
        def crash() -> int:
            raise ValueError("boom")

        result = crash()
        self.assertEqual(result, 42)

    def test_failing_function_logs_error(self):
        """A function that raises should log the error."""
        mock_log = MagicMock()

        @safe_call(default_return=None, logger=mock_log)
        def crash() -> int:
            raise RuntimeError("kaboom")

        crash()
        mock_log.error.assert_called_once()
        args = mock_log.error.call_args[0]
        self.assertIn("kaboom", str(args))

    # ── Default return types ──────────────────────────────────────────

    def test_default_return_none(self):
        """Default return of None should work."""
        @safe_call(default_return=None)
        def crash() -> None:
            raise ValueError("nope")

        self.assertIsNone(crash())

    def test_default_return_empty_list(self):
        """Default return of [] should work."""
        @safe_call(default_return=[])
        def crash() -> list:
            raise ValueError("nope")

        self.assertEqual(crash(), [])

    def test_default_return_false(self):
        """Default return of False should work."""
        @safe_call(default_return=False)
        def crash() -> bool:
            raise ValueError("nope")

        self.assertFalse(crash())

    def test_default_return_dict(self):
        """Default return of {'ok': False} should work."""
        @safe_call(default_return={"ok": False, "error": "unknown"})
        def crash() -> dict:
            raise ValueError("nope")

        result = crash()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown")

    # ── Logging behavior ──────────────────────────────────────────────

    def test_default_logger_created(self):
        """If no logger is passed, a default logger should be used."""
        @safe_call(default_return="fallback")
        def crash() -> str:
            raise ValueError("log test")

        # Should not crash — uses default logging.getLogger
        result = crash()
        self.assertEqual(result, "fallback")

    def test_custom_logger_name(self):
        """Custom logger_name should create a logger with that name."""
        @safe_call(default_return=None, logger_name="my_custom_logger")
        def crash() -> None:
            raise ValueError("name test")

        with patch("EXTENSIONS._shared.utils.logging.getLogger") as mock_get:
            @safe_call(default_return=None, logger_name="custom_name")
            def crash2() -> None:
                raise ValueError("name test2")

            crash2()
            mock_get.assert_called_with("custom_name")

    # ── Re-raise mode ─────────────────────────────────────────────────

    def test_re_raise_mode(self):
        """With re_raise=True, the exception should propagate."""
        @safe_call(default_return=None, re_raise=True)
        def crash() -> None:
            raise ValueError("re-raise test")

        with self.assertRaises(ValueError):
            crash()

    # ── Function metadata preservation ────────────────────────────────

    def test_function_name_preserved(self):
        """The decorated function should preserve its __name__."""
        @safe_call(default_return=None)
        def my_function() -> None:
            """My docstring."""
            pass

        self.assertEqual(my_function.__name__, "my_function")
        self.assertEqual(my_function.__doc__, "My docstring.")

    # ── Argument passthrough ──────────────────────────────────────────

    def test_arguments_passed_through(self):
        """Decorator should pass args and kwargs through unchanged."""
        @safe_call(default_return=None)
        def adder(a: int, b: int) -> int:
            return a + b

        self.assertEqual(adder(3, 4), 7)
        self.assertEqual(adder(a=10, b=20), 30)
        self.assertEqual(adder(5, b=6), 11)


if __name__ == "__main__":
    unittest.main()
