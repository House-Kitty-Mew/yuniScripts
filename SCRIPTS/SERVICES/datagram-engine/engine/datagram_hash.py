"""
datagram_hash.py — Datagram content integrity hashing.

Provides both the Python-native hash computation and the ability to
delegate to external implementations (e.g., BouncyCastle for SHAKE256).
"""

import hashlib
from pathlib import Path
from typing import Optional, Tuple

from .datagram_types import Datagram, DatagramHash, HashAlgorithm, DatagramStatus
from .datagram_io import collect_content_for_hashing


def compute_datagram_hash(datagram: Datagram,
                          algorithm: Optional[HashAlgorithm] = None) -> DatagramHash:
    """
    Compute the content hash of a datagram.
    If algorithm is None, uses the algorithm from datagram.meta.
    """
    if algorithm is None:
        algorithm = datagram.meta.hash_algorithm

    root = Path(datagram.root_path)
    content, file_paths = collect_content_for_hashing(root)
    datagram_hash = DatagramHash.compute(algorithm, content)
    return datagram_hash


def verify_datagram_hash(datagram: Datagram) -> Tuple[bool, Optional[DatagramHash]]:
    """
    Verify a datagram's content hash against its stored hash.
    Returns (is_valid, computed_hash). If no hash is stored, returns (True, None).
    """
    stored = datagram.meta.datagram_hash
    if not stored:
        # No stored hash — can't verify
        datagram.mark_hash_valid(False)
        return False, None

    computed = compute_datagram_hash(datagram, stored.algorithm)
    is_valid = computed.hex_value == stored.hex_value
    datagram.mark_hash_valid(is_valid)
    return is_valid, computed


def update_datagram_hash(datagram: Datagram) -> DatagramHash:
    """
    Recompute and update the datagram's content hash.
    Returns the new hash and updates Base.ini.
    """
    computed = compute_datagram_hash(datagram)
    datagram.meta.datagram_hash = computed
    datagram.meta.status = DatagramStatus.HASHED
    datagram.mark_hash_valid(True)

    # Update Base.ini with new hash
    from .datagram_io import update_base_ini
    update_base_ini(datagram)

    return computed
