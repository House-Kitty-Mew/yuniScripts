"""
Datagram Engine — YuniScripts Engine Module for forward/backward compatible
data archival, loading, creation, and manipulation.

Part of the YuniScripts ecosystem.
Spec: /home/deck/Documents/dev-yuniScripts/DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md
"""

from .datagram_types import (
    Datagram, DatagramMeta, DatagramVersion, DatagramHash,
    DatagramFunction, DatagramValue, DatabaseRecord,
    HashAlgorithm, EncryptionMode, DatabaseType, DatagramStatus, DataType,
)
from .datagram_io import (
    load_datagram, create_datagram, update_base_ini, update_meta_ini,
    validate_datagram_structure, parse_ini_content,
)
from .datagram_hash import compute_datagram_hash, verify_datagram_hash, update_datagram_hash
from .datagram_compat import CompatibilityChecker, CompatibilityResult
from .datagram_db import (
    Database, SQLiteDatabase, JSONDatabase, create_database, DatabaseError
)
from .datagram_functions import FunctionRegistry, FunctionLoadError

__all__ = [
    "Datagram", "DatagramMeta", "DatagramVersion", "DatagramHash",
    "DatagramFunction", "DatagramValue", "DatabaseRecord",
    "HashAlgorithm", "EncryptionMode", "DatabaseType", "DatagramStatus", "DataType",
    "load_datagram", "create_datagram", "update_base_ini", "update_meta_ini",
    "validate_datagram_structure", "parse_ini_content",
    "compute_datagram_hash", "verify_datagram_hash", "update_datagram_hash",
    "CompatibilityChecker", "CompatibilityResult",
    "Database", "SQLiteDatabase", "JSONDatabase", "create_database", "DatabaseError",
    "FunctionRegistry", "FunctionLoadError",
]
