"""
hooks.py — Datagram Engine hooks for the YuniScripts engine hook system.

These hooks allow the YuniScripts engine itself to interact with datagrams
at key lifecycle events (engine start, engine stop, script start, etc.).
"""

import json
from typing import Any, Dict, Optional


# ── Internal state ──────────────────────────────────────────────────────────

_hook_registry: Dict[str, list] = {}
_default_hooks_setup = False


def register_hooks(hook_registry: dict) -> dict:
    """
    Register datagram engine hooks with the YuniScripts engine.
    Called by the engine during script discovery.
    
    Hooks provided:
      on_engine_start:  Load default datagram config
      on_engine_stop:   Clean up loaded datagrams
      on_script_start:  Make datagram module available
    """
    global _default_hooks_setup
    
    def _on_engine_start(**kwargs):
        """Called when the engine starts — initializes the datagram system."""
        print("[datagram-engine] Registering datagram engine hooks")
        return {"status": "registered"}
    
    def _on_engine_stop(**kwargs):
        """Called when the engine stops — cleans up datagram resources."""
        print("[datagram-engine] Cleaning up datagram resources")
        return {"status": "cleaned"}
    
    def _on_script_start(**kwargs):
        """Called when a script starts — makes datagram module available."""
        script_id = kwargs.get("script_id", "unknown")
        return {"script_id": script_id, "datagram_available": True}
    
    # Register hooks with the engine's registry
    if "on_engine_start" not in hook_registry:
        hook_registry["on_engine_start"] = []
    hook_registry["on_engine_start"].append(("datagram-engine", _on_engine_start))
    
    if "on_engine_stop" not in hook_registry:
        hook_registry["on_engine_stop"] = []
    hook_registry["on_engine_stop"].append(("datagram-engine", _on_engine_stop))
    
    if "on_script_start" not in hook_registry:
        hook_registry["on_script_start"] = []
    hook_registry["on_script_start"].append(("datagram-engine", _on_script_start))
    
    _default_hooks_setup = True
    print("[datagram-engine] Hooks registered successfully")
    
    return hook_registry
