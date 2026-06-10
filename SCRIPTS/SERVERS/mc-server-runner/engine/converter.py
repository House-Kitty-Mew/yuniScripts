"""
File ↔ Database (VFS) conversion module with integrity validation.

Provides compression, hashing, and integrity verification for storing
files inside the VFS database as blobs. Uses zlib (level 6) for a good
balance of compression ratio and speed, and SHA-256 for content integrity.

Architecture:
    file_to_db_blob  →  reads a real file → compresses → hashes → returns dict
    bytes_to_db_blob  → same flow from in-memory bytes
    db_blob_to_file   → decompresses → verifies hash → writes to disk
    validate_file_integrity  → reads file → checks SHA-256 against expected

This module is designed to be the single point of truth for all file↔DB
conversion so that integrity checks are consistent across the VFS system.
"""

import hashlib
import json
import zlib
import os
import shutil
from datetime import datetime
from typing import Optional, Dict, Any, Union


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COMPRESSION_LEVEL: int = 6
"""zlib compression level used throughout the module (good balance)."""


# ---------------------------------------------------------------------------
# Core compression / decompression
# ---------------------------------------------------------------------------

def compress_data(data: bytes) -> bytes:
    """Compress *data* with zlib at the module's compression level.

    Args:
        data: Raw bytes to compress.

    Returns:
        Compressed byte string.

    Raises:
        TypeError: If *data* is not bytes.
        zlib.error: If compression fails.

    Example:
        >>> compressed = compress_data(b"Hello, World!")
        >>> isinstance(compressed, bytes)
        True
    """
    if not isinstance(data, bytes):
        raise TypeError(f"Expected bytes, got {type(data).__name__}")
    return zlib.compress(data, COMPRESSION_LEVEL)


def decompress_data(compressed: bytes) -> bytes:
    """Decompress *compressed* bytes previously produced by :func:`compress_data`.

    Args:
        compressed: zlib-compressed byte string.

    Returns:
        Decompressed original bytes.

    Raises:
        TypeError: If *compressed* is not bytes.
        zlib.error: If the data is corrupt or not valid zlib data.

    Example:
        >>> decompress_data(compress_data(b"Hello")) == b"Hello"
        True
    """
    if not isinstance(compressed, bytes):
        raise TypeError(f"Expected bytes, got {type(compressed).__name__}")
    return zlib.decompress(compressed)


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    """Return the hex-encoded SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Primary conversion functions
# ---------------------------------------------------------------------------

def file_to_db_blob(file_path: str) -> Dict[str, Any]:
    """Read a real file, compress it, hash it, and return a dict for DB insertion.

    The returned dictionary contains all fields needed to store the file
    in a VFS database table.

    Args:
        file_path: Absolute or relative path to the file on disk.

    Returns:
        A dictionary with the following keys:

            - **blob_data** (bytes): zlib-compressed file content.
            - **original_size** (int): Size of the file **before** compression.
            - **compressed_size** (int): Size of the compressed blob.
            - **import_hash** (str): SHA-256 of the *original* bytes (before compression).
              Used to verify the round-trip when comparing stored files.
            - **validation_hash** (str): SHA-256 of the *compressed* blob.
              Used by :func:`db_blob_to_file` to verify integrity on extract.
            - **original_name** (str): Base name of the file (``os.path.basename``).

    Raises:
        FileNotFoundError: If *file_path* does not exist.
        IsADirectoryError: If *file_path* is a directory.
        PermissionError: If the file cannot be read.
        TypeError: If *file_path* is not a string.
        zlib.error: If compression fails.

    Example:
        >>> blob = file_to_db_blob("/tmp/test.txt")
        >>> blob["original_size"] > 0
        True
        >>> blob["compressed_size"] <= blob["original_size"] + 1024
        True
    """
    if not isinstance(file_path, str):
        raise TypeError(f"Expected str for file_path, got {type(file_path).__name__}")

    with open(file_path, "rb") as f:
        raw_bytes = f.read()

    original_size = len(raw_bytes)
    original_name = os.path.basename(file_path)

    # Hash BEFORE compression (import integrity check)
    import_hash = _sha256(raw_bytes)

    # Compress
    blob_data = zlib.compress(raw_bytes, COMPRESSION_LEVEL)
    compressed_size = len(blob_data)

    # Hash AFTER compression (extract verification)
    validation_hash = _sha256(blob_data)

    return {
        "blob_data": blob_data,
        "original_size": original_size,
        "compressed_size": compressed_size,
        "import_hash": import_hash,
        "validation_hash": validation_hash,
        "original_name": original_name,
    }


def bytes_to_db_blob(raw_bytes: bytes) -> Dict[str, Any]:
    """Convert raw bytes directly to DB blob format (for in-memory files).

    Same hash/compression logic as :func:`file_to_db_blob` but accepts
    a byte string directly rather than reading from disk.

    Args:
        raw_bytes: The raw file content as bytes.

    Returns:
        Same dictionary structure as :func:`file_to_db_blob`:

            - **blob_data** (bytes): zlib-compressed content.
            - **original_size** (int): Size before compression.
            - **compressed_size** (int): Size of compressed blob.
            - **import_hash** (str): SHA-256 of *raw_bytes*.
            - **validation_hash** (str): SHA-256 of compressed blob.
            - **original_name** (str): ``"<memory>"`` (indicates no on-disk origin).

    Raises:
        TypeError: If *raw_bytes* is not bytes.
        zlib.error: If compression fails.

    Example:
        >>> blob = bytes_to_db_blob(b"Hello")
        >>> blob["original_size"]
        5
    """
    if not isinstance(raw_bytes, bytes):
        raise TypeError(f"Expected bytes, got {type(raw_bytes).__name__}")

    original_size = len(raw_bytes)
    import_hash = _sha256(raw_bytes)
    blob_data = zlib.compress(raw_bytes, COMPRESSION_LEVEL)
    compressed_size = len(blob_data)
    validation_hash = _sha256(blob_data)

    return {
        "blob_data": blob_data,
        "original_size": original_size,
        "compressed_size": compressed_size,
        "import_hash": import_hash,
        "validation_hash": validation_hash,
        "original_name": "<memory>",
    }


def db_blob_to_file(blob_data: bytes, validation_hash: str, output_path: str) -> bool:
    """Decompress a DB blob, verify integrity against *validation_hash*,
    and write the result to *output_path*.

    Args:
        blob_data: The compressed blob bytes from the database.
        validation_hash: Expected SHA-256 hex digest of the *compressed* blob.
            This must match :func:`_sha256`\ (blob_data) exactly.
        output_path: Destination file path. Parent directories are created
            automatically (``os.makedirs``).

    Returns:
        ``True`` if the hash matched and the file was written successfully.

    Raises:
        ValueError: If ``sha256(blob_data)`` does not match *validation_hash*.
        TypeError: If *blob_data* is not bytes or *validation_hash* is not str.
        zlib.error: If decompression fails.
        OSError: If the output path cannot be written.

    Example:
        >>> blob = file_to_db_blob("/tmp/test.txt")
        >>> db_blob_to_file(blob["blob_data"], blob["validation_hash"], "/tmp/restored.txt")
        True
    """
    if not isinstance(blob_data, bytes):
        raise TypeError(
            f"Expected bytes for blob_data, got {type(blob_data).__name__}"
        )

    if not isinstance(validation_hash, str):
        raise TypeError(
            f"Expected str for validation_hash, got {type(validation_hash).__name__}"
        )

    # Verify compressed blob integrity
    actual_hash = _sha256(blob_data)
    if actual_hash != validation_hash:
        raise ValueError(
            f"Hash mismatch: expected {validation_hash}, got {actual_hash}. "
            "The compressed blob may be corrupt or from a different source."
        )

    # Decompress
    decompressed = zlib.decompress(blob_data)

    # Ensure parent directory exists
    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, exist_ok=True)

    # Write
    with open(output_path, "wb") as f:
        f.write(decompressed)

    return True


def validate_file_integrity(file_path: str, expected_hash: str) -> bool:
    """Read a file and check its SHA-256 digest against *expected_hash*.

    This is a pure integrity check — it does not modify any data.

    Args:
        file_path: Path to the file to check.
        expected_hash: The SHA-256 hex digest to compare against.

    Returns:
        ``True`` if the file's SHA-256 matches *expected_hash*.
        ``False`` if the file does not exist, cannot be read, or the
        hash does not match.

    Example:
        >>> blob = file_to_db_blob("/tmp/test.txt")
        >>> validate_file_integrity("/tmp/test.txt", blob["import_hash"])
        True
    """
    try:
        with open(file_path, "rb") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
        return False

    return _sha256(content) == expected_hash


# ---------------------------------------------------------------------------
# Audit / Record-keeping
# ---------------------------------------------------------------------------

class ConversionRecord:
    """A lightweight record for the conversion audit trail.

    Uses ``__slots__`` for memory efficiency when holding many records
    in a list or database export buffer.

    Attributes:
        vfs_path: VFS path (e.g. ``/servers/my-server/server.properties``).
        direction: ``"import"`` or ``"export"``.
        file_hash: SHA-256 hex digest (pre-compression for imports,
            post-compression for exports).
        original_size: File size before compression.
        result: ``"success"`` or ``"failure"``.
        error_msg: Human-readable error message (empty on success).
        timestamp: ISO 8601 UTC timestamp of the event.
    """

    __slots__ = (
        "vfs_path",
        "direction",
        "file_hash",
        "original_size",
        "result",
        "error_msg",
        "timestamp",
    )

    def __init__(
        self,
        vfs_path: str,
        direction: str,
        file_hash: str,
        original_size: int,
        result: str = "success",
        error_msg: str = "",
    ) -> None:
        self.vfs_path: str = vfs_path
        self.direction: str = direction
        self.file_hash: str = file_hash
        self.original_size: int = original_size
        self.result: str = result
        self.error_msg: str = error_msg
        self.timestamp: str = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this record to a plain dictionary.

        Returns:
            A dict with all slot values, suitable for JSON export.
        """
        return {
            "vfs_path": self.vfs_path,
            "direction": self.direction,
            "file_hash": self.file_hash,
            "original_size": self.original_size,
            "result": self.result,
            "error_msg": self.error_msg,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        """Return a JSON string representation of this record."""
        return json.dumps(self.to_dict(), indent=2)

    def __repr__(self) -> str:
        return (
            f"ConversionRecord(vfs_path={self.vfs_path!r}, "
            f"direction={self.direction!r}, result={self.result!r})"
        )


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

__all__ = [
    "compress_data",
    "decompress_data",
    "file_to_db_blob",
    "bytes_to_db_blob",
    "db_blob_to_file",
    "validate_file_integrity",
    "ConversionRecord",
]
