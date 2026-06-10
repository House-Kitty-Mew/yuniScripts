"""
decompile_config.py — Per-script DECOMPILE configuration.

Defines how each script datagram should be unpacked on a target
yuniScripts engine. Each script can have its own decompile_instructions.json
that tells the packager where to put files, what actions to take after
unpacking, and compatibility requirements.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from copy import deepcopy


# ── Config directory (shared with compile_config) ───────────────────────────

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
    
    fallback = Path("/home/deck/Documents/dev-yuniScripts/DATA/script_packager_configs")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


CONFIGS_DIR = _get_configs_dir()


# ── Default decompile config template ───────────────────────────────────────

DEFAULT_DECOMPILE_CONFIG = {
    "script_id": "",
    "target_path": "",
    "post_unpack_actions": [
        {"type": "register_script", "enabled": True},
        {"type": "install_dependencies", "requirements_file": "requirements.txt"},
        {"type": "restore_configs"},
        {"type": "start_script", "delay_seconds": 2}
    ],
    "compatibility": {
        "min_engine_version": "1.0.0",
        "required_ports": []
    },
    "unpackaging": {
        "extract_method": "directory_copy",
        "overwrite_existing": False,
        "create_backup": True,
        "on_conflict": "skip"
    }
}


class DecompileConfig:
    """Per-script decompile configuration."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.data = deepcopy(DEFAULT_DECOMPILE_CONFIG)
        if data:
            self._merge(data)

    def _merge(self, data: Dict[str, Any]) -> None:
        for key, value in data.items():
            if key == "post_unpack_actions" and isinstance(value, list):
                self.data["post_unpack_actions"] = value
            elif key == "compatibility" and isinstance(value, dict):
                self.data["compatibility"].update(value)
            elif key == "unpackaging" and isinstance(value, dict):
                self.data["unpackaging"].update(value)
            else:
                self.data[key] = value

    @property
    def script_id(self) -> str:
        return self.data.get("script_id", "")

    @property
    def target_path(self) -> str:
        return self.data.get("target_path", "")

    @property
    def post_unpack_actions(self) -> List[Dict]:
        return self.data.get("post_unpack_actions", [])

    @property
    def min_engine_version(self) -> str:
        return self.data.get("compatibility", {}).get("min_engine_version", "1.0.0")

    @property
    def required_ports(self) -> List[int]:
        return self.data.get("compatibility", {}).get("required_ports", [])

    @property
    def overwrite_existing(self) -> bool:
        return self.data.get("unpackaging", {}).get("overwrite_existing", False)

    @property
    def create_backup(self) -> bool:
        return self.data.get("unpackaging", {}).get("create_backup", True)

    @property
    def on_conflict(self) -> str:
        return self.data.get("unpackaging", {}).get("on_conflict", "skip")

    def to_dict(self) -> Dict[str, Any]:
        return deepcopy(self.data)

    def to_json(self) -> str:
        return json.dumps(self.data, indent=2)


def get_default_decompile_config(script_id: str) -> DecompileConfig:
    target = script_id
    cfg = DecompileConfig()
    cfg.data["script_id"] = script_id
    cfg.data["target_path"] = target
    return cfg


def save_decompile_config(script_id: str, config: DecompileConfig) -> bool:
    safe_name = script_id.replace("/", "_").replace("\\", "_")
    path = CONFIGS_DIR / f"{safe_name}.decompile.json"
    try:
        path.write_text(config.to_json(), encoding="utf-8")
        return True
    except (OSError, IOError) as e:
        print(f"[script-packager] Failed to save decompile config for {script_id}: {e}")
        return False


def load_decompile_config(script_id: str) -> Optional[DecompileConfig]:
    safe_name = script_id.replace("/", "_").replace("\\", "_")
    path = CONFIGS_DIR / f"{safe_name}.decompile.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DecompileConfig(data)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[script-packager] Failed to load decompile config for {script_id}: {e}")
        return None
