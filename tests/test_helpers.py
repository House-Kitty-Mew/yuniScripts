#!/usr/bin/env python3
"""
test_helpers.py — Shared test framework for YuniScripts custom inline tests.

Provides Unicode-safe section headers, test reporting, and color constants
that work on ALL platforms (including Windows GitHub runners with cp1252).

Usage:
    from tests.test_helpers import *

    section("1. My Test Section")
    test("description", condition)
    skip("reason")
    report()

Design:
    - The section() function detects stdout encoding and uses ASCII dashes
      when Unicode box-drawing chars are unsupported (e.g., Windows cp1252).
    - The test() function has __test__ = False so pytest won't collect it.
    - Add __test__ = False at module level in any test file using this framework
      to prevent pytest from collecting the entire file (these are run directly
      with `python tests/test_*.py`, NOT via pytest).
    - All print() calls go through _safe_print() which handles encoding errors
      on legacy Windows consoles (cp1252 can't encode →, —, etc.).
"""

import sys
import time

# ── Encoding-safe Unicode detection ──────────────────────────────────
_CAN_USE_UNICODE = False
try:
    encoding = getattr(sys.stdout, "encoding", None) or ""
    # UTF-8, UTF-16, UTF-32 all support box-drawing chars (U+2500)
    _CAN_USE_UNICODE = encoding.lower().replace("-", "").replace("_", "") in (
        "utf8", "utf16", "utf32"
    )
except Exception:
    pass

_SEP = "\u2500" if _CAN_USE_UNICODE else "-"  # ─ or -


# ── Encoding-safe print wrapper ─────────────────────────────────────
def _safe_print(*args, **kwargs):
    """Print with encoding error handling for legacy Windows consoles.

    On Windows GitHub Actions runners, stdout encoding is often cp1252,
    which cannot represent Unicode characters like -> or --.
    This wrapper encodes the output string to stdout's encoding with
    'replace' error handler, preventing UnicodeEncodeError crashes.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    text = " ".join(str(a) for a in args)
    try:
        safe_text = text.encode(encoding, errors="replace").decode(encoding)
    except (LookupError, ValueError):
        safe_text = text.encode("utf-8", errors="replace").decode("utf-8")
    print(safe_text, **kwargs)


# ── ANSI color constants (safe on all platforms) ─────────────────────
_G = "\033[92m"  # green
_Y = "\033[93m"  # yellow
_R = "\033[91m"  # red
_C = "\033[96m"  # cyan
_N = "\033[0m"   # reset

PASS = f"{_G}PASS{_N}"
FAIL = f"{_R}FAIL{_N}"
SKIP = f"{_Y}SKIP{_N}"

# ── Global counters ─────────────────────────────────────────────────
_tests_run = 0
_tests_pass = 0
_tests_fail = 0
_tests_skip = 0


def safe_sleep(seconds: float, interval: float = 0.01) -> None:
    """Sleep for `seconds` while handling KeyboardInterrupt gracefully.

    On Windows GitHub Actions runners, time.sleep() can receive spurious
    Ctrl+C / SIGINT signals from the process group or console, raising
    KeyboardInterrupt.  This wrapper breaks the sleep into tiny intervals
    and catches KeyboardInterrupt silently so the test can continue.

    Args:
        seconds: Total time to sleep, in seconds.
        interval: Check interval (default 0.01s = 10ms).
    """
    elapsed = 0.0
    while elapsed < seconds:
        try:
            time.sleep(min(interval, seconds - elapsed))
        except KeyboardInterrupt:
            # Spurious Ctrl+C on Windows CI -- ignore and continue
            pass
        elapsed += interval


def test(name: str, condition: bool, detail: str = ""):
    """Record a single test result -- PASS or FAIL with optional detail."""
    global _tests_run, _tests_pass, _tests_fail
    _tests_run += 1
    if condition:
        _tests_pass += 1
        _safe_print(f"  {PASS}  {name}")
    else:
        _tests_fail += 1
        _safe_print(f"  {FAIL}  {name}")
        if detail:
            _safe_print(f"         {_Y}{detail}{_N}")


test.__test__ = False  # tell pytest not to collect this function


def skip(name: str, reason: str = ""):
    """Record a skipped test."""
    global _tests_skip
    _tests_skip += 1
    msg = f"  {SKIP}  {name}"
    if reason:
        msg += f"  ({_Y}{reason}{_N})"
    _safe_print(msg)


def section(title: str):
    """Print a section header with Unicode-safe box-drawing characters.

    On terminals that support UTF-8 (Linux, macOS, Windows Terminal):
        --- Section Title ---
        ---------------------

    On legacy Windows consoles (cp1252):
        --- Section Title ---
        ---------------------
    """
    _safe_print(f"\n{_C}{_SEP * 3} {title} {_SEP * 3}{_N}")
    _safe_print(f"{_C}{_SEP * (len(title) + 8)}{_N}")


def report():
    """Print final results summary and exit with appropriate code.

    Wraps sys.exit() in a try/except to handle KeyboardInterrupt gracefully
    on platforms where exit signals can be interrupted (e.g. Windows CI).
    """
    _safe_print(f"  {_G}{_tests_pass} passed{_N}")
    if _tests_fail:
        _safe_print(f"  {_R}{_tests_fail} failed{_N}")
    if _tests_skip:
        _safe_print(f"  {_Y}{_tests_skip} skipped{_N}")
    _safe_print(f"  {_C}{_tests_run} total{_N}")

    if _tests_fail:
        _safe_print(f"\n  {_R}Some tests FAILED -- review above.{_N}")
        try:
            sys.exit(1)
        except KeyboardInterrupt:
            # Spurious Ctrl+C on Windows CI -- still exit with error code
            sys.exit(1)
    else:
        _safe_print(f"\n  {_G}All tests passed.{_N}")
