"""
eco_config.py — Configuration for the Otters Civ economy bridge.

Loads from:
  1. ECO_BRIDGE/eco_config.json (auto-created with defaults)
  2. Environment variables (overrides)
  3. Hard-coded defaults

Database is at config/otters_civ_revived/project_ooga.db relative to
the Minecraft server directory, or any path set in eco_config.json.
"""

import json, os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from engine.config_loader import get_config_path, load_config
from engine.ports import PHOOKS_HUB_PORT

BRIDGE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = get_config_path("eco")

_DEFAULTS = {
    # Otters Civ database path (auto-detected if empty)
    "ooga_db_path": "",
    "ooga_db_fallback_paths": [
        "config/otters_civ_revived/project_ooga.db",
        "world/otters_civ_revived/project_ooga.db",
        "otters_civ_revived/project_ooga.db",
    ],
    # RCON (read from shared mc_manager config, but can override here)
    "rcon_host": "127.0.0.1",
    "rcon_port": 25575,
    "rcon_password": "",
    # Bridge behavior
    "rcon_primary": True,                # Try RCON first, DB fallback
    "rcon_timeout_seconds": 3.0,        # RCON timeout before falling back
    "log_all_operations": True,         # Log every balance change
    "enable_standalone_phooks": True,   # Register as Phooks client in standalone mode
    "standalone_phooks_port": PHOOKS_HUB_PORT,    # Phooks hub port
    # Remote bridge (eco_bridge_server on Minecraft server machine)
    "eco_bridge_host": "",               # e.g. "192.168.1.118" — set to enable remote bridge
    "eco_bridge_port": 7200,
    "eco_bridge_password": "",
    # Safety
    "max_delta_per_transaction": 100000, # Max coins per operation
    "min_balance": 0,                    # Minimum balance (0 = no negatives)
    "write_reason_prefix": "AH_BRIDGE:", # Prefix for wallet_ledger reason field
}


@dataclass
class EcoConfig:
    ooga_db_path: str = ""
    ooga_db_fallback_paths: list = None
    rcon_host: str = "127.0.0.1"
    rcon_port: int = 25575
    rcon_password: str = ""
    rcon_primary: bool = True
    rcon_timeout_seconds: float = 3.0
    log_all_operations: bool = True
    enable_standalone_phooks: bool = True
    standalone_phooks_port: int = PHOOKS_HUB_PORT
    # Remote bridge
    eco_bridge_host: str = ""
    eco_bridge_port: int = 7200
    eco_bridge_password: str = ""
    # Safety
    max_delta_per_transaction: int = 100000
    min_balance: int = 0
    write_reason_prefix: str = "AH_BRIDGE:"

    def __post_init__(self):
        if self.ooga_db_fallback_paths is None:
            self.ooga_db_fallback_paths = _DEFAULTS["ooga_db_fallback_paths"]

    def resolve_db_path(self, server_dir: Optional[Path] = None) -> Optional[Path]:
        """Resolve the actual Otters Civ database path.

        Args:
            server_dir: Minecraft server directory (e.g. Path.home() / "minecraft_server")

        Returns:
            Path to the database file, or None if not found
        """
        # 1. Explicit path
        if self.ooga_db_path:
            p = Path(self.ooga_db_path)
            if p.exists():
                return p.resolve()
            # Maybe relative to server dir
            if server_dir:
                p2 = server_dir / self.ooga_db_path
                if p2.exists():
                    return p2.resolve()

        # 2. Fallback paths relative to server dir
        if server_dir:
            for rel_path in self.ooga_db_fallback_paths:
                p = server_dir / rel_path
                if p.exists():
                    return p.resolve()

        # 3. Search broadly
        for search_root in [Path.home(), Path("/opt"), Path("/srv")]:
            for rel_path in self.ooga_db_fallback_paths:
                p = search_root / rel_path
                if p.exists():
                    return p.resolve()

        return None

    @staticmethod
    def _try_load_json(path: Path) -> Optional[dict]:
        try:
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def load(self, shared_config_path: Optional[Path] = None) -> "EcoConfig":
        """Load config from override file + optional shared mc_manager config."""
        merged = dict(_DEFAULTS)

        # Load shared mc_manager config for RCON info
        if shared_config_path and shared_config_path.exists():
            shared = self._try_load_json(shared_config_path)
            if shared:
                for k in ["rcon_host", "rcon_port", "rcon_password"]:
                    if k in shared:
                        merged[k] = shared[k]

        # Load bridge-specific override config
        override = self._try_load_json(CONFIG_PATH)
        if override:
            merged.update(override)

        # Apply to self
        for k, v in merged.items():
            if hasattr(self, k):
                setattr(self, k, v)

        # Write override file if missing
        if not CONFIG_PATH.exists():
            self._write_default()

        return self

    def _write_default(self):
        try:
            d = asdict(self)
            d.pop("rcon_password", None)  # Don't write password
            with open(CONFIG_PATH, "w") as f:
                json.dump(d, f, indent=2)
        except OSError:
            pass


# ── Global singleton ────────────────────────────────────────────────
import threading
_instance = None
_lock = threading.Lock()


def get_config(reload: bool = False) -> EcoConfig:
    global _instance
    if _instance is None or reload:
        with _lock:
            if _instance is None or reload:
                _instance = EcoConfig().load()
    return _instance
