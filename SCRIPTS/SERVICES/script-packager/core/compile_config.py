"""
compile_config.py — Per-script COMPILE configuration.

Defines how each script should be packaged into a datagram.
Each script can have its own compile_instructions.json that tells
the packager what to include, exclude, and how to package it.

Default configs are auto-generated for scripts that don't have one.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from copy import deepcopy


# ── Config directory ────────────────────────────────────────────────────────

def _find_project_root() -> Optional[Path]:
    """Find the yuniScripts project root by walking up parents."""
    f = Path(__file__).resolve()
    for level in range(15):
        if level >= len(f.parents):
            break
        parent = f.parents[level]
        if (parent / "engine" / "manager.py").exists() and (parent / "SCRIPTS").exists():
            return parent
    return None


def _get_configs_dir() -> Path:
    """Get the centralized configs directory for packager configs."""
    root = _find_project_root()
    if root:
        config_dir = root / "DATA" / "script_packager_configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir
    
    # Absolute fallback
    fallback = Path("/home/deck/Documents/dev-yuniScripts/DATA/script_packager_configs")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


CONFIGS_DIR = _get_configs_dir()


# ── Default compile config template ─────────────────────────────────────────

DEFAULT_COMPILE_CONFIG = {
    "script_id": "",
    "version": "1.0.0",
    "include_patterns": ["*.py", "*.json", "*.md", "*.info", "*.txt", "*.ini", "*.cfg"],
    "exclude_patterns": [
        "__pycache__", "*.pyc", ".git", "venv", ".venv",
        "__pycache__/*", "*.egg-info", ".gitignore", ".DS_Store"
    ],
    "include_databases": True,
    "include_venv": False,
    "include_configs": True,
    "config_sources": [],
    "packaging": {
        "compress": False,
        "hash_algorithm": "SHA256",
        "datagram_version": "1.0.0"
    }
}


class CompileConfig:
    """Per-script compile configuration."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.data = deepcopy(DEFAULT_COMPILE_CONFIG)
        if data:
            self._merge(data)

    def _merge(self, data: Dict[str, Any]) -> None:
        for key, value in data.items():
            if key == "packaging" and isinstance(value, dict):
                self.data["packaging"].update(value)
            else:
                self.data[key] = value

    @property
    def script_id(self) -> str:
        return self.data.get("script_id", "")

    @property
    def include_patterns(self) -> List[str]:
        return self.data.get("include_patterns", [])

    @property
    def exclude_patterns(self) -> List[str]:
        return self.data.get("exclude_patterns", [])

    @property
    def include_databases(self) -> bool:
        return self.data.get("include_databases", True)

    @property
    def include_configs(self) -> bool:
        return self.data.get("include_configs", True)

    @property
    def config_sources(self) -> List[str]:
        return self.data.get("config_sources", [])

    @property
    def packaging_config(self) -> Dict:
        return self.data.get("packaging", {})

    def to_dict(self) -> Dict[str, Any]:
        return deepcopy(self.data)

    def to_json(self) -> str:
        return json.dumps(self.data, indent=2)


def get_default_compile_config(script_id: str) -> CompileConfig:
    cfg = CompileConfig()
    cfg.data["script_id"] = script_id
    return cfg


def save_compile_config(script_id: str, config: CompileConfig) -> bool:
    safe_name = script_id.replace("/", "_").replace("\\", "_")
    path = CONFIGS_DIR / f"{safe_name}.compile.json"
    try:
        path.write_text(config.to_json(), encoding="utf-8")
        return True
    except (OSError, IOError) as e:
        print(f"[script-packager] Failed to save compile config for {script_id}: {e}")
        return False


def load_compile_config(script_id: str) -> Optional[CompileConfig]:
    safe_name = script_id.replace("/", "_").replace("\\", "_")
    path = CONFIGS_DIR / f"{safe_name}.compile.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CompileConfig(data)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[script-packager] Failed to load compile config for {script_id}: {e}")
        return None


def list_compile_configs() -> List[str]:
    configs = []
    for f in CONFIGS_DIR.glob("*.compile.json"):
        name = f.stem.replace(".compile", "")
        script_id = name.replace("_", "/", 1) if "_" in name else name
        configs.append(script_id)
    return sorted(configs)
