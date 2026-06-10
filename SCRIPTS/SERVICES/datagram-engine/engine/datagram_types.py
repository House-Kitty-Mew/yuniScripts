"""
datagram_types.py — Type system for the Datagram Engine Module.

Defines the canonical type hierarchy for datagram data storage:
  - DatagramType: The root datagram object
  - DatagramMeta: Metadata container (Base.ini + DatagramMeta.ini)
  - DatagramVersion: Semantic versioning
  - DatagramHash: Integrity hash (SHA256/SHAKE256)
  - DatagramEncryption: Encryption metadata
  - DatagramFunction: Embedded function definitions
  - DatagramDatabase: Database connection abstraction
  - DatagramValue: Typed value container (stores different data types)

Every datagram has a type-safe schema that allows:
  - Forward-compatible reads (newer datagram, older engine)
  - Backward-compatible writes (older datagram, newer engine)
  - Cross-script data exchange via Phooks events
"""

import enum
import json
import uuid
import time
from typing import Optional, Dict, List, Any, Union, Tuple


# ── Enums ───────────────────────────────────────────────────────────────────

class HashAlgorithm(enum.IntEnum):
    """Supported hash algorithms for datagram content integrity."""
    SHAKE256_1024 = 1   # Original Datagram default (BouncyCastle required)
    SHAKE256_512  = 2
    SHA3_256      = 3
    SHA3_512      = 4
    SHA256        = 5   # Pure-Python fallback, always available

    @classmethod
    def from_name(cls, name: str) -> "HashAlgorithm":
        mapping = {
            "SHAKE256-1024": cls.SHAKE256_1024,
            "SHAKE256": cls.SHAKE256_512,
            "SHA3-256": cls.SHA3_256,
            "SHA3-512": cls.SHA3_512,
            "SHA256": cls.SHA256,
        }
        normalized = name.strip().upper().replace("_", "-")
        for k, v in mapping.items():
            if k.upper() == normalized:
                return v
        return cls.SHA256  # safe default

    @property
    def display_name(self) -> str:
        return {
            1: "SHAKE256-1024",
            2: "SHAKE256-512",
            3: "SHA3-256",
            4: "SHA3-512",
            5: "SHA256",
        }.get(self.value, "UNKNOWN")

    @property
    def digest_size(self) -> int:
        """Return digest size in bytes."""
        return {
            1: 128,  # SHAKE256-1024 = 128 bytes
            2: 64,   # SHAKE256-512 = 64 bytes
            3: 32,   # SHA3-256 = 32 bytes
            4: 64,   # SHA3-512 = 64 bytes
            5: 32,   # SHA256 = 32 bytes
        }.get(self.value, 32)


class EncryptionMode(enum.IntEnum):
    """Supported encryption modes."""
    NONE        = 0
    PUBLIC_KEY  = 1
    SYMMETRIC   = 2


class DatabaseType(enum.StrEnum):
    """Supported database backends for datagram data storage."""
    SQLITE = "SQLite"
    JSON   = "JSON"
    XML    = "XML"

    @classmethod
    def from_name(cls, name: str) -> "DatabaseType":
        mapping = {v.value: v for v in cls}
        return mapping.get(name, cls.JSON)


class DatagramStatus(enum.StrEnum):
    """Lifecycle status of a datagram."""
    CREATED     = "created"
    VALIDATED   = "validated"
    HASHED      = "hashed"
    LOADED      = "loaded"
    MODIFIED    = "modified"
    ARCHIVED    = "archived"
    CORRUPTED   = "corrupted"


class DataType(enum.StrEnum):
    """Supported individual data value types for storage inside datagrams."""
    STRING    = "string"
    INTEGER   = "integer"
    FLOAT     = "float"
    BOOLEAN   = "boolean"
    BINARY    = "binary"
    JSON      = "json"
    DATETIME  = "datetime"
    UUID      = "uuid"
    NULL      = "null"


# ── Versioning ──────────────────────────────────────────────────────────────

class DatagramVersion:
    """
    Semantic version (major.minor.patch) with comparison operators.
    Matches the versioning semantics in the original Datagram spec.
    """

    __slots__ = ("major", "minor", "patch")

    def __init__(self, major: int = 1, minor: int = 0, patch: int = 0):
        self.major = int(major)
        self.minor = int(minor)
        self.patch = int(patch)

    @classmethod
    def parse(cls, version_str: str) -> "DatagramVersion":
        """Parse a dotted version string like '1.0.0' or '2'."""
        if not version_str or not version_str.strip():
            return cls(0, 0, 0)
        parts = version_str.strip().split(".")
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return cls(major, minor, patch)

    def is_compatible_with(self, required: "DatagramVersion") -> bool:
        """
        Check if this version is compatible with a required version.
        Compatibility rules (matching original Datagram spec):
          - Same major: OK if self >= required
          - Higher major: ALWAYS compatible (forward compat)
          - Lower major: NEVER compatible (breaking change)
          - Same major, higher minor: compatible
          - Same major, same minor, higher patch: compatible
        """
        if self.major > required.major:
            return True   # Higher major = forward compatible
        if self.major < required.major:
            return False  # Lower major = breaking change
        # Same major — check minor then patch
        if self.minor > required.minor:
            return True
        if self.minor < required.minor:
            return False
        return self.patch >= required.patch

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __repr__(self) -> str:
        return f"DatagramVersion({self.major}, {self.minor}, {self.patch})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, DatagramVersion):
            return (self.major == other.major and
                    self.minor == other.minor and
                    self.patch == other.patch)
        return NotImplemented

    def __lt__(self, other: "DatagramVersion") -> bool:
        if self.major != other.major:
            return self.major < other.major
        if self.minor != other.minor:
            return self.minor < other.minor
        return self.patch < other.patch

    def __le__(self, other: "DatagramVersion") -> bool:
        return self < other or self == other

    def __gt__(self, other: "DatagramVersion") -> bool:
        if self.major != other.major:
            return self.major > other.major
        if self.minor != other.minor:
            return self.minor > other.minor
        return self.patch > other.patch

    def __ge__(self, other: "DatagramVersion") -> bool:
        return self > other or self == other

    def to_dict(self) -> Dict[str, int]:
        return {"major": self.major, "minor": self.minor, "patch": self.patch}

    @classmethod
    def from_dict(cls, d: Dict[str, int]) -> "DatagramVersion":
        return cls(d.get("major", 0), d.get("minor", 0), d.get("patch", 0))


# ── Hash ────────────────────────────────────────────────────────────────────

class DatagramHash:
    """
    Represents the integrity hash of a datagram's content.
    Excludes Meta/Base.ini from hashing (as per spec) to allow
    the hash field to be updated without invalidating the hash.
    """

    __slots__ = ("algorithm", "digest", "hex_value")

    def __init__(self, algorithm: HashAlgorithm = HashAlgorithm.SHA256,
                 hex_value: str = ""):
        self.algorithm = algorithm
        self.hex_value = hex_value
        self.digest = bytes.fromhex(hex_value) if hex_value else b""

    @classmethod
    def compute(cls, algorithm: HashAlgorithm, data: bytes) -> "DatagramHash":
        """Compute a hash from raw bytes using the specified algorithm."""
        import hashlib
        if algorithm == HashAlgorithm.SHA256:
            h = hashlib.sha256(data).hexdigest()
        elif algorithm in (HashAlgorithm.SHA3_256, HashAlgorithm.SHA3_512):
            algo_name = "sha3_256" if algorithm == HashAlgorithm.SHA3_256 else "sha3_512"
            m = hashlib.new(algo_name, data)
            h = m.hexdigest()
        elif algorithm in (HashAlgorithm.SHAKE256_1024, HashAlgorithm.SHAKE256_512):
            # SHAKE256 variant — use hashlib.shake_256 if available (3.6+)
            try:
                s = hashlib.shake_256(data)
                digest_len = algorithm.digest_size
                h = s.hexdigest(digest_len)
            except AttributeError:
                # Fallback to SHA256
                import warnings
                warnings.warn(f"SHAKE256 not available, falling back to SHA256")
                h = hashlib.sha256(data).hexdigest()
        else:
            h = hashlib.sha256(data).hexdigest()
        return cls(algorithm=algorithm, hex_value=h)

    def verify(self, data: bytes) -> bool:
        """Verify data against this hash."""
        computed = DatagramHash.compute(self.algorithm, data)
        return computed.hex_value == self.hex_value

    def to_ini_value(self) -> str:
        """Format as {hex_value} for INI storage."""
        return "{" + self.hex_value + "}"

    @classmethod
    def from_ini_value(cls, algo: HashAlgorithm, ini_value: str) -> "DatagramHash":
        """Parse from {hex_value} INI format."""
        cleaned = ini_value.strip().strip("{}").strip()
        return cls(algorithm=algo, hex_value=cleaned)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm.value,
            "hex_value": self.hex_value,
        }

    def __bool__(self) -> bool:
        return bool(self.hex_value)

    def __str__(self) -> str:
        return f"{self.algorithm.display_name}:{self.hex_value[:16]}..."


# ── Meta ────────────────────────────────────────────────────────────────────

class DatagramMeta:
    """
    Datagram metadata — corresponds to Base.ini + DatagramMeta.ini.
    This is the core identity record for every datagram.
    """

    __slots__ = (
        "version", "name", "author", "hash_algorithm",
        "datagram_hash", "encryption", "encryption_key",
        "encryption_server_url", "description", "creation_date",
        "tags", "license", "status", "datagram_uuid", "custom_fields",
    )

    def __init__(self):
        self.version: DatagramVersion = DatagramVersion(1, 0, 0)
        self.name: str = "Untitled Datagram"
        self.author: str = "Unknown"
        self.hash_algorithm: HashAlgorithm = HashAlgorithm.SHA256
        self.datagram_hash: DatagramHash = DatagramHash()
        self.encryption: EncryptionMode = EncryptionMode.NONE
        self.encryption_key: str = ""
        self.encryption_server_url: str = ""
        self.description: str = ""
        self.creation_date: str = ""
        self.tags: List[str] = []
        self.license: str = ""
        self.status: DatagramStatus = DatagramStatus.CREATED
        self.datagram_uuid: str = str(uuid.uuid4())
        self.custom_fields: Dict[str, str] = {}

    def to_ini_dict(self, include_hash: bool = True) -> Dict[str, str]:
        """Convert to flat key=value dict for INI serialization."""
        result = {
            "Datagram Version": str(self.version),
            "Datagram NAME ID": self.name,
            "Datagram Author": self.author,
            "Datagram Hashing Algo": str(self.hash_algorithm.value),
            "Encryption": str(self.encryption.value),
        }
        if include_hash and self.datagram_hash:
            result["Datagram Hash UQID"] = self.datagram_hash.to_ini_value()
        if self.encryption_key:
            result["Encryption Public Key"] = "{" + self.encryption_key + "}"
        if self.encryption_server_url:
            result["Encryption Server URL"] = "{" + self.encryption_server_url + "}"
        return result

    def to_meta_dict(self) -> Dict[str, str]:
        """Convert extended metadata to dict."""
        return {
            "Description": "{" + self.description + "}",
            "Creation Date": "{" + self.creation_date + "}",
            "Tags": "{" + ", ".join(self.tags) + "}",
            "License": "{" + self.license + "}",
        }

    @classmethod
    def from_ini_dict(cls, d: Dict[str, str]) -> "DatagramMeta":
        """Parse from flat key=value dict (as read from Base.ini)."""
        meta = cls()
        if "Datagram Version" in d:
            meta.version = DatagramVersion.parse(d["Datagram Version"])
        if "Datagram NAME ID" in d:
            meta.name = d["Datagram NAME ID"]
        if "Datagram Author" in d:
            meta.author = d["Datagram Author"]
        if "Datagram Hashing Algo" in d:
            try:
                algo_val = int(d["Datagram Hashing Algo"])
                meta.hash_algorithm = HashAlgorithm(algo_val)
            except (ValueError, TypeError):
                pass
        if "Datagram Hash UQID" in d:
            meta.datagram_hash = DatagramHash.from_ini_value(
                meta.hash_algorithm, d["Datagram Hash UQID"]
            )
        if "Encryption" in d:
            try:
                meta.encryption = EncryptionMode(int(d["Encryption"]))
            except (ValueError, TypeError):
                pass
        if "Encryption Public Key" in d:
            meta.encryption_key = d["Encryption Public Key"].strip("{}").strip()
        if "Encryption Server URL" in d:
            meta.encryption_server_url = d["Encryption Server URL"].strip("{}").strip()
        return meta

    @classmethod
    def from_meta_dict(cls, d: Dict[str, str], base: "DatagramMeta" = None) -> "DatagramMeta":
        """Apply extended metadata from DatagramMeta.ini onto a base."""
        if base is None:
            meta = cls()
        else:
            meta = base
        if "Description" in d:
            meta.description = d["Description"].strip("{}").strip()
        if "Creation Date" in d:
            meta.creation_date = d["Creation Date"].strip("{}").strip()
        if "Tags" in d:
            raw = d["Tags"].strip("{}").strip()
            meta.tags = [t.strip() for t in raw.split(",") if t.strip()]
        if "License" in d:
            meta.license = d["License"].strip("{}").strip()
        return meta

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": str(self.version),
            "name": self.name,
            "author": self.author,
            "hash_algorithm": self.hash_algorithm.value,
            "hash": self.datagram_hash.hex_value if self.datagram_hash else "",
            "encryption": self.encryption.value,
            "uuid": self.datagram_uuid,
            "status": self.status.value,
            "created": self.creation_date,
            "tags": self.tags,
        }


# ── Function ────────────────────────────────────────────────────────────────

class DatagramFunction:
    """
    An embedded or referenced function within a datagram.
    Functions can be:
      - EMBEDDED: Python source code included inline
      - REFERENCE: URL/path to external function implementation
      - BUILTIN: Known function name that the engine provides
    """

    __slots__ = ("name", "version", "language", "source", "entry_point",
                 "required", "description")

    def __init__(self, name: str, version: DatagramVersion = None,
                 language: str = "python", source: str = "",
                 entry_point: str = "main", required: bool = True,
                 description: str = ""):
        self.name = name
        self.version = version or DatagramVersion(1, 0, 0)
        self.language = language
        self.source = source
        self.entry_point = entry_point
        self.required = required
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": str(self.version),
            "language": self.language,
            "source_length": len(self.source),
            "entry_point": self.entry_point,
            "required": self.required,
            "description": self.description,
        }


# ── Value ───────────────────────────────────────────────────────────────────

class DatagramValue:
    """
    A typed value for storage inside a datagram.
    Supports all DataType variants with type-safe conversion.
    """

    __slots__ = ("data_type", "value")

    def __init__(self, data_type: DataType, value: Any):
        self.data_type = data_type
        self.value = self._coerce(data_type, value)

    @staticmethod
    def _coerce(data_type: DataType, value: Any) -> Any:
        """Coerce a Python value to the specified type."""
        if value is None:
            return None
        if data_type == DataType.STRING:
            return str(value)
        elif data_type == DataType.INTEGER:
            return int(value)
        elif data_type == DataType.FLOAT:
            return float(value)
        elif data_type == DataType.BOOLEAN:
            if isinstance(value, str):
                return value.strip().lower() in ("true", "1", "yes")
            return bool(value)
        elif data_type == DataType.BINARY:
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode("utf-8")
            return bytes(value)
        elif data_type == DataType.JSON:
            if isinstance(value, (dict, list)):
                return json.dumps(value)
            return str(value)
        elif data_type == DataType.DATETIME:
            return str(value)
        elif data_type == DataType.UUID:
            return str(uuid.UUID(str(value)))
        elif data_type == DataType.NULL:
            return None
        return value

    def to_python(self) -> Any:
        """Convert to native Python type with type restoration."""
        if self.value is None:
            return None
        if self.data_type == DataType.JSON:
            try:
                return json.loads(self.value)
            except (json.JSONDecodeError, TypeError):
                return self.value
        if self.data_type == DataType.BINARY:
            if isinstance(self.value, str):
                return self.value.encode("utf-8")
            return self.value
        if self.data_type == DataType.INTEGER:
            return int(self.value)
        if self.data_type == DataType.FLOAT:
            return float(self.value)
        if self.data_type == DataType.BOOLEAN:
            if isinstance(self.value, str):
                return self.value.lower() in ("true", "1", "yes")
            return bool(self.value)
        return self.value

    def to_json_compatible(self) -> Any:
        """Convert to JSON-serializable value."""
        if self.value is None:
            return None
        if self.data_type == DataType.BINARY:
            return self.value.hex() if isinstance(self.value, bytes) else str(self.value)
        if self.data_type == DataType.DATETIME:
            return str(self.value)
        if self.data_type == DataType.UUID:
            return str(self.value)
        if self.data_type == DataType.JSON:
            return json.loads(self.value) if isinstance(self.value, str) else self.value
        return self.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.data_type.value,
            "value": self.to_json_compatible(),
        }


# ── Database Record ─────────────────────────────────────────────────────────

class DatabaseRecord:
    """
    A row/record from a datagram database operation.
    Provides typed field access.
    """

    def __init__(self, fields: Dict[str, Any]):
        self._fields = dict(fields)
        self._types: Dict[str, DataType] = {}

    def __getitem__(self, key: str) -> Any:
        return self._fields.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self._fields[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._fields

    def get(self, key: str, default: Any = None) -> Any:
        return self._fields.get(key, default)

    def keys(self):
        return self._fields.keys()

    def values(self):
        return self._fields.values()

    def items(self):
        return self._fields.items()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._fields)

    def set_type(self, field: str, data_type: DataType) -> None:
        self._types[field] = data_type

    def get_typed(self, field: str) -> Any:
        """Get a field with type coercion based on stored type info."""
        raw = self._fields.get(field)
        if raw is None or field not in self._types:
            return raw
        return DatagramValue(self._types[field], raw).to_python()

    def __repr__(self) -> str:
        return f"DatabaseRecord({self._fields})"


# ── Root Datagram ───────────────────────────────────────────────────────────

class Datagram:
    """
    Root datagram object — represents a loaded/created datagram archive.

    A Datagram bundles:
      - Metadata (identity, version, hash, encryption)
      - Managed databases (SQLite, JSON, XML)
      - Embedded functions (Python code for self-extraction)
      - Large asset references (files this datagram manages)

    This is the primary object that YuniScripts scripts interact with
    via Phooks events.
    """

    def __init__(self, root_path: str = "", meta: DatagramMeta = None):
        self.root_path: str = root_path
        self.meta: DatagramMeta = meta or DatagramMeta()
        self.databases: Dict[str, Any] = {}     # name -> database connection
        self.functions: Dict[str, DatagramFunction] = {}
        self.assets: Dict[str, str] = {}         # name -> file path
        self._loaded: bool = False
        self._hash_valid: bool = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def hash_valid(self) -> bool:
        return self._hash_valid

    def mark_loaded(self) -> None:
        self._loaded = True
        self.meta.status = DatagramStatus.LOADED

    def mark_hash_valid(self, valid: bool) -> None:
        self._hash_valid = valid

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for Phooks event transport."""
        return {
            "meta": self.meta.to_dict(),
            "root_path": self.root_path,
            "loaded": self._loaded,
            "hash_valid": self._hash_valid,
            "database_count": len(self.databases),
            "function_count": len(self.functions),
            "asset_count": len(self.assets),
            "datagram_uuid": self.meta.datagram_uuid,
        }

    def validate_schema(self) -> Tuple[bool, List[str]]:
        """
        Validate that the datagram schema is consistent and complete.
        Returns (is_valid, list_of_errors).
        """
        errors = []
        if not self.meta.name:
            errors.append("Datagram name is required")
        if not self.meta.datagram_uuid:
            errors.append("Datagram UUID is required")
        if self.meta.version.major < 1:
            errors.append("Version major must be >= 1")
        if self.meta.hash_algorithm not in HashAlgorithm:
            errors.append(f"Unknown hash algorithm: {self.meta.hash_algorithm}")
        if self.meta.encryption not in EncryptionMode:
            errors.append(f"Unknown encryption mode: {self.meta.encryption}")
        return (len(errors) == 0, errors)
