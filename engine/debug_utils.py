"""Debug support with debugpy."""
from typing import Dict

def _is_debug_enabled(script_instance: Dict) -> bool:
    return script_instance["meta"].get("debug", False)

def inject_debug_args(script_instance: Dict, debug_port: int) -> Dict:
    if not _is_debug_enabled(script_instance):
        return script_instance
    new_meta = {**script_instance["meta"]}
    new_args = [
        "-m", "debugpy",
        "--listen", str(debug_port),
        "--wait-for-client",
    ] + new_meta.get("args", [])
    return {**script_instance, "meta": {**new_meta, "args": new_args}}