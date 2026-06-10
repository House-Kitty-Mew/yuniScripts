"""
datagram_io.py — Datagram format reading, writing, and parsing.

Handles:
  - INI file reading/writing (Base.ini, DatagramMeta.ini)
  - Datagram file detection and loading
  - Directory structure validation
  - Content collection for hashing
  - Datagram creation from scratch
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime, timezone

from .datagram_types import (
    Datagram, DatagramMeta, DatagramVersion, DatagramHash,
    DatagramFunction, HashAlgorithm, DatagramStatus,
)


# ── INI Parsing ─────────────────────────────────────────────────────────────

def parse_ini_content(content: str) -> Dict[str, str]:
    """
    Parse Datagram INI format: [Key]=value and [Key]={value}
    
    Rules:
      - Lines starting with # after trim are comments (skipped)
      - Empty lines are skipped
      - [Key]=value format: key is everything inside [], value is after =
      - [Key]={value} format: value is inside braces (braces stripped)
      - Values can contain = signs (only first = separates key from value)
    """
    result: Dict[str, str] = {}
    for line in content.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        # Match [key]=value or [key]={value}
        match = re.match(r"^\[([^\]]+)\]=(.*)$", trimmed)
        if match:
            key = match.group(1).strip()
            value = match.group(2)
            result[key] = value
    return result


def serialize_ini(data: Dict[str, str]) -> str:
    """Serialize a dict to Datagram INI format."""
    lines = []
    for key, value in data.items():
        lines.append(f"[{key}]={value}")
    return "\n".join(lines) + "\n"


def serialize_ini_braced(data: Dict[str, str]) -> str:
    """Serialize to INI format with {value} braces for multi-line values."""
    lines = []
    for key, value in data.items():
        if value.startswith("{") and value.endswith("}"):
            lines.append(f"[{key}]={value}")
        else:
            lines.append(f"[{key}]={{{value}}}")
    return "\n".join(lines) + "\n"


# ── File Reading ────────────────────────────────────────────────────────────

def read_ini_file(path: Path) -> Optional[Dict[str, str]]:
    """Read an INI file and parse it. Returns None if file not found."""
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        return parse_ini_content(content)
    except (OSError, UnicodeDecodeError) as e:
        raise IOError(f"Failed to read {path}: {e}")


def write_ini_file(path: Path, data: Dict[str, str], 
                   braced: bool = False) -> None:
    """Write parsed data to an INI file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if braced:
        content = serialize_ini_braced(data)
    else:
        content = serialize_ini(data)
    path.write_text(content, encoding="utf-8")


# ── Content Collection for Hashing ──────────────────────────────────────────

def collect_content_for_hashing(root: Path) -> Tuple[bytes, List[str]]:
    """
    Collect all file contents for datagram content hashing.
    
    Excludes Meta/Base.ini (as per spec) to allow the hash field
    to be updated without invalidating the hash.
    Uses relative paths sorted by path for deterministic ordering.
    """
    base_ini_rel = Path("Meta") / "Base.ini"
    collected_parts: List[bytes] = []
    file_paths: List[str] = []

    # Walk the directory tree and collect files
    all_files: List[Path] = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(root)
            # Exclude Meta/Base.ini
            if rel == base_ini_rel:
                continue
            # Skip __pycache__ and .pyc
            if "__pycache__" in rel.parts or fn.endswith(".pyc"):
                continue
            all_files.append(full)

    # Sort by relative path for deterministic ordering
    all_files.sort(key=lambda p: str(p.relative_to(root)))

    # Collect: path as UTF-8 bytes + file content as raw bytes
    for full_path in all_files:
        rel = str(full_path.relative_to(root))
        rel_bytes = rel.encode("utf-8")
        try:
            content = full_path.read_bytes()
        except (OSError, PermissionError):
            continue
        collected_parts.append(rel_bytes)
        collected_parts.append(b"\x00")  # Separator
        collected_parts.append(content)
        collected_parts.append(b"\x00")
        file_paths.append(rel)

    return b"".join(collected_parts), file_paths


# ── Datagram Loading ────────────────────────────────────────────────────────

def load_datagram(root_path: str) -> Datagram:
    """
    Load a datagram from a directory path.
    
    Expected structure:
      <root>/
        Meta/Base.ini          (required)
        Meta/DatagramMeta.ini  (optional)
    
    Returns a Datagram object. Does NOT verify hash (call verify_datagram_hash separately).
    Raises IOError if Base.ini is missing or unreadable.
    """
    root = Path(root_path).resolve()
    if not root.exists():
        raise IOError(f"Datagram path does not exist: {root}")
    if not root.is_dir():
        raise IOError(f"Datagram path is not a directory: {root}")

    # Read Base.ini
    base_ini_path = root / "Meta" / "Base.ini"
    if not base_ini_path.exists():
        raise IOError(f"Missing Meta/Base.ini in datagram: {root}")

    base_data = read_ini_file(base_ini_path)
    if base_data is None:
        raise IOError(f"Failed to parse Meta/Base.ini in: {root}")

    meta = DatagramMeta.from_ini_dict(base_data)

    # Read DatagramMeta.ini if present
    meta_ini_path = root / "Meta" / "DatagramMeta.ini"
    if meta_ini_path.exists():
        meta_data = read_ini_file(meta_ini_path)
        if meta_data:
            meta = DatagramMeta.from_meta_dict(meta_data, base=meta)

    datagram = Datagram(root_path=str(root), meta=meta)
    datagram.mark_loaded()
    return datagram


def create_datagram(root_path: str, name: str = "Untitled Datagram",
                    author: str = "Unknown",
                    version: DatagramVersion = None,
                    description: str = "") -> Datagram:
    """
    Create a new datagram at the specified path.
    Creates the directory structure and Base.ini.
    """
    root = Path(root_path).resolve()

    # Create directory structure
    (root / "Meta").mkdir(parents=True, exist_ok=True)
    (root / "Databases" / "Default" / "Data").mkdir(parents=True, exist_ok=True)
    (root / "LargeAssets").mkdir(parents=True, exist_ok=True)
    (root / "PreLoad" / "Gui").mkdir(parents=True, exist_ok=True)
    (root / "PreLoad" / "Intil").mkdir(parents=True, exist_ok=True)
    (root / "Functions").mkdir(parents=True, exist_ok=True)

    # Create metadata
    meta = DatagramMeta()
    meta.name = name
    meta.author = author
    if version:
        meta.version = version
    meta.description = description
    meta.creation_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta.status = DatagramStatus.CREATED

    # Write Base.ini
    base_data = meta.to_ini_dict(include_hash=False)
    write_ini_file(root / "Meta" / "Base.ini", base_data)

    # Write DatagramMeta.ini
    meta_data = meta.to_meta_dict()
    write_ini_file(root / "Meta" / "DatagramMeta.ini", meta_data, braced=True)

    # Create Datagram object
    datagram = Datagram(root_path=str(root), meta=meta)
    return datagram


def update_base_ini(datagram: Datagram) -> None:
    """Update the Base.ini file for an in-memory datagram change."""
    root = Path(datagram.root_path)
    base_data = datagram.meta.to_ini_dict(include_hash=bool(datagram.meta.datagram_hash))
    write_ini_file(root / "Meta" / "Base.ini", base_data)
    datagram.meta.status = DatagramStatus.MODIFIED


def update_meta_ini(datagram: Datagram) -> None:
    """Update the DatagramMeta.ini file."""
    root = Path(datagram.root_path)
    meta_data = datagram.meta.to_meta_dict()
    write_ini_file(root / "Meta" / "DatagramMeta.ini", meta_data, braced=True)


# ── Validation ──────────────────────────────────────────────────────────────

def validate_datagram_structure(root_path: str) -> Tuple[bool, List[str]]:
    """
    Validate that a directory has proper datagram structure.
    Returns (is_valid, list_of_errors_or_warnings).
    """
    root = Path(root_path).resolve()
    errors: List[str] = []
    warnings: List[str] = []

    if not root.exists():
        errors.append(f"Path does not exist: {root}")
        return False, errors

    if not root.is_dir():
        errors.append(f"Path is not a directory: {root}")
        return False, errors

    # Check required files
    base_ini = root / "Meta" / "Base.ini"
    if not base_ini.exists():
        errors.append("Missing required Meta/Base.ini")

    # Check recommended directories
    expected_dirs = [
        ("Meta", True),
        ("Databases", False),
        ("Databases/Default/Data", False),
        ("LargeAssets", False),
        ("PreLoad", False),
        ("PreLoad/Gui", False),
        ("PreLoad/Intil", False),
        ("Functions", False),
    ]
    for dir_rel, required in expected_dirs:
        d = root / dir_rel
        if not d.exists():
            msg = f"Missing directory: {dir_rel}"
            if required:
                errors.append(msg)
            else:
                warnings.append(msg)
        elif not d.is_dir():
            errors.append(f"Path exists but is not a directory: {dir_rel}")

    return (len(errors) == 0, errors + warnings)
