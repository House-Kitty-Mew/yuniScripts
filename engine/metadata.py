"""Pure functions for script metadata parsing – now includes ports."""
import sys
from pathlib import Path
import configparser

def _default_python() -> str:
    """Return a sensible default Python path for the current platform."""
    if sys.platform == "win32":
        # On Windows, rely on PATH — "python" or "python3" depending on install
        return "python"
    return "/usr/bin/python3"

def default_meta():
    return {
        "name": "Unnamed Script",
        "version": "0.0.0",
        "description": "",
        "category": "uncategorized",
        "entry_point": "main.py",
        "enabled": True,
        "restart_policy": "always",
        "server_type": "normal",      # normal | long_running | critical
        "shutdown_timeout": 5.0,      # seconds before force-kill
        "watch_patterns": ["*.py"],
        "dependencies": [],
        "python_path": _default_python(),
        "args": [],
        "debug": False,
        "requirements_file": "requirements.txt",
        "ports": []
    }

def _split_list(raw: str) -> list:
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]

def _parse_ports(raw: str) -> list:
    ports = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                start, end = part.split('-', 1)
                ports.extend(range(int(start), int(end)+1))
            except Exception:
                print(f"Warning: invalid port range '{part}'")
        else:
            try:
                ports.append(int(part))
            except Exception:
                print(f"Warning: invalid port '{part}'")
    return ports

def parse_meta_info(file_path: Path) -> dict:
    config = configparser.ConfigParser()
    config.read(file_path)
    if "script" not in config:
        return default_meta()
    section = config["script"]
    meta = default_meta()
    ports_raw = section.get("ports", "")
    return {
        **meta,
        "name": section.get("name", meta["name"]),
        "version": section.get("version", meta["version"]),
        "description": section.get("description", meta["description"]),
        "category": section.get("category", meta["category"]),
        "entry_point": section.get("entry_point", meta["entry_point"]),
        "enabled": section.getboolean("enabled", meta["enabled"]),
        "restart_policy": section.get("restart_policy", meta["restart_policy"]),
        "server_type": section.get("server_type", meta["server_type"]),
        "shutdown_timeout": section.getfloat("shutdown_timeout", meta["shutdown_timeout"]),
        "watch_patterns": _split_list(section.get("watch_patterns", "")) or meta["watch_patterns"],
        "dependencies": _split_list(section.get("dependencies", "")),
        "python_path": section.get("python_path", meta["python_path"]),
        "args": _split_list(section.get("args", "")),
        "debug": section.getboolean("debug", meta["debug"]),
        "requirements_file": section.get("requirements_file", meta["requirements_file"]),
        "ports": _parse_ports(ports_raw) if ports_raw else []
    }

def parse_meta_json(file_path: Path) -> dict:
    import json
    meta = default_meta()
    data = json.loads(file_path.read_text())
    return {
        **meta,
        "name": data.get("name", meta["name"]),
        "version": data.get("version", meta["version"]),
        "description": data.get("description", meta["description"]),
        "category": data.get("category", meta["category"]),
        "entry_point": data.get("entry_point", meta["entry_point"]),
        "enabled": data.get("enabled", meta["enabled"]),
        "restart_policy": data.get("restart_policy", meta["restart_policy"]),
        "server_type": data.get("server_type", meta["server_type"]),
        "shutdown_timeout": data.get("shutdown_timeout", meta["shutdown_timeout"]),
        "watch_patterns": data.get("watch_patterns", meta["watch_patterns"]),
        "dependencies": data.get("dependencies", meta["dependencies"]),
        "python_path": data.get("python_path", meta["python_path"]),
        "args": data.get("args", meta["args"]),
        "debug": data.get("debug", meta["debug"]),
        "requirements_file": data.get("requirements_file", meta["requirements_file"]),
        "ports": data.get("ports", [])
    }

def load_meta(script_dir: Path) -> dict | None:
    meta_info = script_dir / "meta.info"
    meta_json = script_dir / "meta.json"
    if meta_info.exists():
        return parse_meta_info(meta_info)
    if meta_json.exists():
        return parse_meta_json(meta_json)
    return None