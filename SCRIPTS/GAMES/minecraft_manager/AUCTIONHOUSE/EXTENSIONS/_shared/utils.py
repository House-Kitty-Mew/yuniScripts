"""
utils.py — Shared decorators and helpers for simulation extensions.

Provides:
    - safe_call: Decorator that wraps functions with try/except,
      logs the error, and returns a graceful default.
"""

import functools
import traceback
import logging
from typing import Any, Callable, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def safe_call(
    default_return: Any = None,
    logger: Optional[logging.Logger] = None,
    logger_name: str = "extensions",
    re_raise: bool = False,
    message: str = "Function failed",
) -> Callable[[F], F]:
    """Decorator that wraps a function with error handling.

    Usage::

        @safe_call(default_return=[], logger=log)
        def get_some_data(...):
            ...

        @safe_call(default_return=False)
        def check_something(...):
            ...

    Args:
        default_return: Value to return if the function raises.
        logger: Logger instance. If None, uses logging.getLogger(logger_name).
        logger_name: Fallback logger name if logger is not provided.
        re_raise: If True, re-raise the exception after logging (for debug).
        message: Log message prefix.

    Returns:
        Decorated function.
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal logger
            if logger is None:
                logger = logging.getLogger(logger_name)
            try:
                return func(*args, **kwargs)
            except Exception as e:
                tb = traceback.format_exc()
                logger.error(
                    "%s — %s.%s: %s\n%s",
                    message, func.__module__, func.__qualname__, e, tb,
                )
                if re_raise:
                    raise
                return default_return
        return wrapper  # type: ignore
    return decorator
