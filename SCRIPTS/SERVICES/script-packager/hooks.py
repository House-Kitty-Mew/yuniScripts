"""
hooks.py — Script Packager hooks for the YuniScripts engine hook system.

These hooks allow the engine to interact with script snapshots at key
lifecycle events: engine start, engine stop, and script lifecycle.
"""

from pathlib import Path
from typing import Any, Dict


# ── Internal state ───────────────────────────────────────────────────

_hook_registry: Dict[str, list] = {}
_default_hooks_setup = False


def register_hooks(hook_registry: dict) -> dict:
    """Register script packager hooks with the YuniScripts engine."""
    global _default_hooks_setup
    
    def _on_engine_start(**kwargs):
        print("[script-packager] Initializing Script Packager system")
        return {"status": "ready", "version": "1.0.0"}
    
    def _on_engine_stop(**kwargs):
        print("[script-packager] Cleaning up packager resources")
        return {"status": "cleaned"}
    
    def _on_script_start(**kwargs):
        script_id = kwargs.get("script_id", "unknown")
        return {"script_id": script_id, "packager_available": True}
    
    # Register hooks with the engine's registry
    if "on_engine_start" not in hook_registry:
        hook_registry["on_engine_start"] = []
    hook_registry["on_engine_start"].append(("script-packager", _on_engine_start))
    
    if "on_engine_stop" not in hook_registry:
        hook_registry["on_engine_stop"] = []
    hook_registry["on_engine_stop"].append(("script-packager", _on_engine_stop))
    
    if "on_script_start" not in hook_registry:
        hook_registry["on_script_start"] = []
    hook_registry["on_script_start"].append(("script-packager", _on_script_start))
    
    _default_hooks_setup = True
    print("[script-packager] Hooks registered successfully")
    return hook_registry
