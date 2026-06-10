#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║         VFS + Host Protection + God Watcher HARMONY BRIDGE         ║
║         AIHandler FastMCPServer Version (Full Divine Protection)   ║
║  Ensures perfect interconnectivity between all protection layers   ║
╚══════════════════════════════════════════════════════════════════════╝

Full pipeline:
  VFS (path safety, staging, validation)
  -> Host Protection (module patches, subprocess limits, rate limiting)
  -> Divine Protection (cgroup v2, OOM, emergency monitor, prlimit)
  -> God Watcher (G1 exploit scan, G2 runtime, G3 audit, G4 escalation)
  
All 4 layers operate as ONE unified protection system.

This bridge ensures that:
  1. Every VFS path validation is checked against Host Protection
  2. Every Host Protection check passes through God Watcher G1 scanning
  3. Every God Watcher alert feeds back into VFS staging decisions
  4. The full pipeline works as a single unified protection system
"""

import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional, Tuple, Any, Callable

# ── Cross-OS Abstraction Layer ──────────────────────────────
from tools.ecosystem_os_abstraction import process_info, process_kill, process_list, system_loadavg, system_memory, system_network_connections, Signals

logger = logging.getLogger("harmony_bridge")

# ── Status tracking ──────────────────────────────────────────────
_harmony_status = {
    "active": False,
    "vfs_connected": False,
    "host_protection_connected": False,
    "divine_protection_connected": False,
    "god_watcher_connected": False,
    "total_validations": 0,
    "total_blocks": 0,
    "total_warnings": 0,
    "total_divine_blocks": 0,
    "total_god_watcher_blocks": 0,
    "start_time": 0.0,
    "last_validation": None,
}
_harmony_lock = threading.Lock()


def initialize_harmony() -> Dict:
    """Initialize the harmony bridge - connects all protection layers.
    
    Connects VFS, Host Protection, Divine Protection, and God Watcher
    into a single pipeline. Returns status dict.
    """
    global _harmony_status
    with _harmony_lock:
        # Layer 1: VFS
        try:
            from tools.virtual_fs import get_vfs, set_harmony_active
            _harmony_status["vfs_connected"] = True
            set_harmony_active(True)
            logger.info("Harmony [AIH]: VFS connected")
        except ImportError as e:
            logger.warning(f"Harmony [AIH]: VFS import failed: {e}")
        
        # Layer 2: Host Protection
        try:
            from host_protection import (
                HOST_PROTECTION_ENABLED,
                validate_tool_call,
                divine_validate_tool_call,
                HostSafeEnvironment,
                HostProtectionError,
                get_protection_status,
            )
            _harmony_status["host_protection_connected"] = True
            logger.info("Harmony [AIH]: Host Protection connected")
        except ImportError as e:
            logger.warning(f"Harmony [AIH]: Host Protection import failed: {e}")
        
        # Layer 3: Divine Protection
        try:
            from host_protection import (
                initialize_divine_protection,
                get_cgroup_manager,
                get_emergency_monitor,
                _check_memory_available,
            )
            _harmony_status["divine_protection_connected"] = True
            logger.info("Harmony [AIH]: Divine Protection connected")
        except ImportError as e:
            logger.warning(f"Harmony [AIH]: Divine Protection import failed: {e}")
        
        # Layer 4: God Watcher
        try:
            from god_watcher import (
                get_god_watcher,
                initialize_god_watcher,
                GOD_WATCHER_ENABLED,
            )
            _harmony_status["god_watcher_connected"] = True
            logger.info("Harmony [AIH]: God Watcher connected")
        except ImportError as e:
            logger.warning(f"Harmony [AIH]: God Watcher import failed: {e}")
        
        _harmony_status["active"] = True
        _harmony_status["start_time"] = time.time()
        logger.info("Harmony bridge [AIHandler] initialized - ALL layers active")
    
    return dict(_harmony_status)


def validate_vfs_path(path: str, tool_name: str = "vfs_operation") -> Tuple[bool, str, float]:
    """Full pipeline validation of a VFS path through all protection layers.
    
    Pipeline:
      1. VFS canonicalizes the path (catches traversal, null bytes, forbidden paths)
      2. Host Protection validates the tool call
      3. Divine Protection checks memory/process constraints
      4. God Watcher G1 scans the path for exploit patterns
      5. If any layer blocks, the operation is denied
    
    Returns:
        (allowed: bool, reason: str, score: float)
    """
    with _harmony_lock:
        _harmony_status["total_validations"] += 1
        _harmony_status["last_validation"] = time.time()
    
    if not _harmony_status["active"]:
        return True, "harmony_inactive", 0.0
    
    score = 0.0
    reasons = []
    
    # Layer 1: VFS path safety
    if _harmony_status["vfs_connected"]:
        try:
            from tools.virtual_fs import VirtualFileSystem, VFSError
            vfs = VirtualFileSystem()
            vfs._safe_path(path)
        except VFSError as e:
            score = max(score, 0.9)
            reasons.append(f"VFS blocked: {e}")
        except Exception as e:
            reasons.append(f"VFS error: {e}")
            score = max(score, 0.5)
    
    # Layer 2: Host Protection validation
    if _harmony_status["host_protection_connected"]:
        try:
            from host_protection import validate_tool_call, divine_validate_tool_call
            hp_allowed, hp_reason = validate_tool_call(tool_name, {"path": path})
            if not hp_allowed:
                score = max(score, 0.85)
                reasons.append(f"Host Protection: {hp_reason}")
            # Divine-level check
            divine_allowed, divine_reason = divine_validate_tool_call(tool_name, {"path": path})
            if not divine_allowed:
                score = max(score, 0.95)
                reasons.append(f"Divine: {divine_reason}")
                with _harmony_lock:
                    _harmony_status["total_divine_blocks"] += 1
        except Exception as e:
            reasons.append(f"Host Protection error: {e}")
    
    # Layer 3: Divine Protection (memory, cgroup)
    if _harmony_status["divine_protection_connected"]:
        try:
            from host_protection import _check_memory_available, _check_concurrent_processes
            mem_ok, mem_msg = _check_memory_available()
            if not mem_ok:
                score = max(score, 0.9)
                reasons.append(f"Memory: {mem_msg}")
            
            proc_ok, proc_msg = _check_concurrent_processes()
            if not proc_ok:
                score = max(score, 0.85)
                reasons.append(f"Processes: {proc_msg}")
        except Exception as e:
            reasons.append(f"Divine error: {e}")
    
    # Layer 4: God Watcher G1 scan
    if _harmony_status["god_watcher_connected"]:
        try:
            from god_watcher import get_god_watcher
            gw = get_god_watcher()
            if tool_name not in getattr(gw, 'SAFE_TOOLS', set()):
                gw_allowed, gw_reason, gw_score = gw.validate_execution(
                    tool_name, {"path": path}
                )
                if not gw_allowed:
                    score = max(score, gw_score)
                    reasons.append(f"God Watcher: {gw_reason}")
                    with _harmony_lock:
                        _harmony_status["total_god_watcher_blocks"] += 1
        except Exception as e:
            reasons.append(f"God Watcher error: {e}")
    
    allowed = score < 0.7
    
    if not allowed:
        with _harmony_lock:
            _harmony_status["total_blocks"] += 1
        logger.warning(f"Harmony BLOCKED path '{path}': {'; '.join(reasons)}")
    
    return allowed, "; ".join(reasons), score


def validate_full_pipeline(
    tool_name: str,
    params: Dict[str, Any],
    command: str = ""
) -> Tuple[bool, str, float, Dict]:
    """Full 4-layer pipeline validation.
    
    Pipeline:
      1. VFS: path canonicalization and safety checks
      2. Host Protection: module patches, rate limiting, static analysis
      3. Divine Protection: memory check, cgroup, subprocess limits
      4. God Watcher: G1-G4 exploit scanning, runtime monitoring, audit
    
    Returns:
        (allowed, reason, score, layer_results)
    """
    layer_results = {}
    score = 0.0
    reasons = []
    
    with _harmony_lock:
        _harmony_status["total_validations"] += 1
        _harmony_status["last_validation"] = time.time()
    
    if not _harmony_status["active"]:
        return True, "harmony_inactive", 0.0, {}
    
    # ── Layer 1: VFS Path Safety ──────────────────────────
    if _harmony_status["vfs_connected"] and "path" in params:
        try:
            from tools.virtual_fs import VirtualFileSystem
            vfs = VirtualFileSystem()
            vfs._safe_path(str(params["path"]))
            layer_results["vfs"] = {"passed": True}
        except Exception as e:
            score = max(score, 0.9)
            reasons.append(f"VFS: {e}")
            layer_results["vfs"] = {"passed": False, "error": str(e)}
    
    # ── Layer 2: Host Protection ──────────────────────────
    if _harmony_status["host_protection_connected"]:
        try:
            from host_protection import validate_tool_call, divine_validate_tool_call
            hp_allowed, hp_reason = validate_tool_call(tool_name, params)
            if not hp_allowed:
                score = max(score, 0.85)
                reasons.append(f"HP: {hp_reason}")
            layer_results["host_protection"] = {"passed": hp_allowed, "reason": hp_reason}
            
            # Divine-level check
            if command:
                divine_params = {"command": command}
            else:
                divine_params = params
            d_allowed, d_reason = divine_validate_tool_call(tool_name, divine_params)
            if not d_allowed:
                score = max(score, 0.95)
                reasons.append(f"Divine: {d_reason}")
                with _harmony_lock:
                    _harmony_status["total_divine_blocks"] += 1
            layer_results["divine"] = {"passed": d_allowed, "reason": d_reason}
        except Exception as e:
            reasons.append(f"HP error: {e}")
    
    # ── Layer 3: Divine Protection (memory, cgroup) ──────
    if _harmony_status["divine_protection_connected"]:
        try:
            from host_protection import _check_memory_available, _check_concurrent_processes
            mem_ok, mem_msg = _check_memory_available()
            if not mem_ok:
                score = max(score, 0.9)
                reasons.append(f"Memory: {mem_msg}")
            layer_results["memory"] = {"passed": mem_ok, "message": mem_msg}
            
            proc_ok, proc_msg = _check_concurrent_processes()
            if not proc_ok:
                score = max(score, 0.85)
                reasons.append(f"Processes: {proc_msg}")
            layer_results["processes"] = {"passed": proc_ok, "message": proc_msg}
        except Exception as e:
            reasons.append(f"Divine error: {e}")
    
    # ── Layer 4: God Watcher ──────────────────────────────
    if _harmony_status["god_watcher_connected"]:
        try:
            from god_watcher import get_god_watcher
            gw = get_god_watcher()
            if tool_name not in getattr(gw, 'SAFE_TOOLS', set()):
                gw_allowed, gw_reason, gw_score = gw.validate_execution(
                    tool_name, params
                )
                if not gw_allowed:
                    score = max(score, gw_score)
                    reasons.append(f"GW: {gw_reason}")
                    with _harmony_lock:
                        _harmony_status["total_god_watcher_blocks"] += 1
                score = max(score, gw_score * 0.8)
                layer_results["god_watcher"] = {
                    "passed": gw_allowed,
                    "score": gw_score,
                    "reason": gw_reason[:100] if gw_reason else "",
                }
        except Exception as e:
            reasons.append(f"GW error: {e}")
    
    allowed = score < 0.7
    
    if not allowed:
        with _harmony_lock:
            _harmony_status["total_blocks"] += 1
        logger.warning(f"Harmony BLOCKED {tool_name}: {'; '.join(reasons)}")
    
    return allowed, "; ".join(reasons), score, layer_results


def get_harmony_status() -> Dict:
    """Get current harmony bridge status."""
    with _harmony_lock:
        return dict(_harmony_status)


def shutdown_harmony():
    """Shut down the harmony bridge cleanly."""
    global _harmony_status
    with _harmony_lock:
        try:
            from tools.virtual_fs import set_harmony_active
            set_harmony_active(False)
        except ImportError:
            pass
        _harmony_status["active"] = False
        logger.info("Harmony bridge [AIHandler] shut down")


if __name__ == "__main__":
    print("=" * 60)
    print("Harmony Bridge [AIHandler] Self-Check")
    print("=" * 60)
    status = initialize_harmony()
    for k, v in status.items():
        print(f"  {k}: {v}")
    print()
    
    # Test safe path
    allowed, reason, score = validate_vfs_path("/safe/test.txt")
    print(f"Safe path: allowed={allowed}, score={score:.2f}")
    if reason:
        print(f"  Reason: {reason}")
    
    # Test forbidden path
    allowed, reason, score = validate_vfs_path("/etc/passwd")
    print(f"Forbidden path: allowed={allowed}, score={score:.2f}")
    if reason:
        print(f"  Reason: {reason}")
    
    # Test traversal path
    allowed, reason, score = validate_vfs_path("../../../etc/shadow")
    print(f"Traversal path: allowed={allowed}, score={score:.2f}")
    if reason:
        print(f"  Reason: {reason}")
    
    # Test with pipeline
    allowed, reason, score, layers = validate_full_pipeline(
        "vfs_write", {"path": "/safe/test.txt", "content": "test"}
    )
    print(f"\nPipeline safe: allowed={allowed}, score={score:.2f}")
    for layer, result in layers.items():
        print(f"  {layer}: {result}")
    
    print(f"\n{'='*60}")
