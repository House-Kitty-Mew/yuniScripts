#!/usr/bin/env python3
"""
Config validation helpers for config-updater.

Provides reusable validation functions for INI/JSON config files.
"""

import os
from pathlib import Path


def validate_file_exists(path):
    """Check that a file exists and is readable."""
    p = Path(path)
    if not p.exists():
        return False, f"File not found: {path}"
    if not os.access(str(p), os.R_OK):
        return False, f"File not readable: {path}"
    return True, str(p.resolve())


def validate_directory_exists(path):
    """Check that a directory exists and is accessible."""
    p = Path(path)
    if not p.exists():
        return False, f"Directory not found: {path}"
    if not p.is_dir():
        return False, f"Not a directory: {path}"
    return True, str(p.resolve())


def validate_in_range(value, min_val, max_val, label="value"):
    """Check a numeric value is within [min_val, max_val]."""
    try:
        v = float(value)
        if min_val <= v <= max_val:
            return True, v
        return False, f"{label} {v} is out of range [{min_val}, {max_val}]"
    except (ValueError, TypeError):
        return False, f"{label} is not a number: {value}"


def validate_regex_match(value, pattern, label="value"):
    """Check a string matches a regex pattern."""
    import re
    if re.match(pattern, value):
        return True, value
    return False, f"{label} does not match pattern: {pattern}"


def validate_choice(value, choices, label="value"):
    """Check a value is one of the allowed choices."""
    if value in choices:
        return True, value
    return False, f"{label} must be one of: {', '.join(map(str, choices))}"
