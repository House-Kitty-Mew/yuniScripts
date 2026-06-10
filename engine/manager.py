"""Pure functional script registry management with validation."""
from pathlib import Path
import json
import sys
import importlib.util
from .metadata import load_meta

def _script_id(scripts_root: Path, script_dir: Path) -> str:
    """Return a script ID with forward slashes, safe across all platforms."""
    rel = script_dir.relative_to(scripts_root)
    return str(rel.as_posix())

def validate_script(script_dir: Path):
    hooks_file = script_dir / "hooks.py"
    main_file = script_dir / "main.py"
    if not hooks_file.exists():
        if main_file.exists():
            content = main_file.read_text()
            if "register_hooks" not in content:
                print(f"FATAL: {script_dir} is missing hooks.py and does not define register_hooks in main.py")
                sys.exit(1)
        else:
            print(f"FATAL: {script_dir} has no main.py")
            sys.exit(1)

    phooks_file = script_dir / "Phooks.py"
    if phooks_file.exists():
        try:
            spec = importlib.util.spec_from_file_location("_phooks", phooks_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not hasattr(module, "PHOOKS_EVENTS_LISTEN") or not isinstance(module.PHOOKS_EVENTS_LISTEN, list):
                print(f"FATAL: {script_dir}/Phooks.py must define PHOOKS_EVENTS_LISTEN as a list")
                sys.exit(1)
            if not hasattr(module, "PHOOKS_EVENTS_EMIT") or not isinstance(module.PHOOKS_EVENTS_EMIT, list):
                print(f"FATAL: {script_dir}/Phooks.py must define PHOOKS_EVENTS_EMIT as a list")
                sys.exit(1)
        except Exception as e:
            print(f"FATAL: error loading Phooks.py in {script_dir}: {e}")
            sys.exit(1)
    else:
        print(f"FATAL: {script_dir} is missing Phooks.py")
        sys.exit(1)

    api_file = script_dir / "API.md"
    if not api_file.exists():
        print(f"FATAL: {script_dir} is missing API.md documentation file.")
        sys.exit(1)

def discover_scripts(scripts_root: Path) -> dict:
    try:
        registry = {}
        for directory in scripts_root.rglob("*"):
            if directory.is_dir():
                entry = directory / "main.py"
                meta_info = directory / "meta.info"
                meta_json = directory / "meta.json"
                if entry.exists() and (meta_info.exists() or meta_json.exists()):
                    validate_script(directory)
                    meta = load_meta(directory)
                    if meta is None:
                        continue
                    sid = _script_id(scripts_root, directory)
                    registry[sid] = {
                        "id": sid,
                        "path": directory,
                        "meta": meta,
                    }
        return registry
    except Exception as e:
        logger.error(f"discover_scripts failed: {e}")
        return {}

def apply_overrides(registry: dict, overrides_path: Path) -> dict:
    try:
        if not overrides_path.exists():
            return registry
        overrides = json.loads(overrides_path.read_text())
        new_registry = {}
        for sid, instance in registry.items():
            if sid in overrides:
                new_meta = {**instance["meta"]}
                settings = overrides[sid]
                for key in ("enabled", "args", "restart_policy", "watch_patterns", "debug", "ports"):
                    if key in settings:
                        new_meta[key] = settings[key]
                new_registry[sid] = {
                    "id": instance["id"],
                    "path": instance["path"],
                    "meta": new_meta,
                }
            else:
                new_registry[sid] = instance
        return new_registry
    except Exception as e:
        logger.error(f"apply_overrides failed: {e}")
        return {}

def get_enabled_scripts(registry: dict) -> list:
    return [sid for sid, inst in registry.items() if inst["meta"]["enabled"]]

def check_port_conflicts(registry: dict):
    try:
        used_ports = {}
        for sid, inst in registry.items():
            if not inst["meta"]["enabled"]:
                continue
            ports = inst["meta"].get("ports", [])
            for port in ports:
                if port in used_ports:
                    print(f"FATAL: Port conflict – {sid} and {used_ports[port]} both use port {port}")
                    sys.exit(1)
                used_ports[port] = sid
        print(f"Port usage verified: {len(used_ports)} ports across scripts.")
    except Exception as e:
        logger.error(f"check_port_conflicts failed: {e}")
        return None
