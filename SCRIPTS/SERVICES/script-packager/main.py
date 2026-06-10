"""
Script Packager — Main Entry Point.
YuniScripts managed script that provides snapshot/packaging services.

Integrates with:
  - Phooks event bus for inter-script communication
  - GUI Dashboard for admin controls
  - Existing datagram-engine for core datagram types

Import strategy:
  - Local core modules: from core.xxx import yyy  (core/ package in this directory)
  - Project engine modules: from engine.xxx import yyy  (project-level engine/)
"""

import sys
import json
import signal
import time
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────────────
# Add both the project root AND this script's directory to sys.path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent  # SCRIPTS/SERVICES/script-packager -> project root

for p in [str(_SCRIPT_DIR), str(_PROJECT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Local imports (from core/ package) ─────────────────────────────────────
from core.snapshot_manager import (
    ScriptSnapshotManager, SnapshotResult, DeployPreview, DeployResult,
)
from core.compile_config import (
    CompileConfig, load_compile_config, save_compile_config,
    get_default_compile_config, list_compile_configs,
)
from core.decompile_config import (
    DecompileConfig, load_decompile_config, save_decompile_config,
    get_default_decompile_config,
)

# ── Project-level imports (from engine/ package) ───────────────────────────
from engine.phooks_client import PhooksClient
from engine.ports import PHOOKS_HUB_PORT
from engine.config_loader import get_data_dir


# ── Global state ────────────────────────────────────────────────────────────

_packager: ScriptSnapshotManager | None = None
_phooks_client: PhooksClient | None = None
_running = True
_start_time = time.time()
_gui_registered = False


# ── Phooks event handlers ───────────────────────────────────────────────────

def _handle_snapshot_create(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.snapshot.create event."""
    global _packager
    
    script_id = data.get("script_id", "")
    output_path = data.get("output_path", "")
    name = data.get("name", "")
    author = data.get("author", "Admin")
    include_databases = data.get("include_databases", None)
    include_configs = data.get("include_configs", None)
    
    if not script_id:
        _emit_error(client, request_uuid, "script_id is required")
        return
    if not output_path:
        _emit_error(client, request_uuid, "output_path is required")
        return
    
    try:
        result = _packager.create_snapshot(
            script_id=script_id,
            output_path=output_path,
            name=name,
            author=author,
            include_databases=include_databases,
            include_configs=include_configs,
        )
        _emit_response(client, "packager.response.snapshot", request_uuid, {
            "action": "create",
            "success": result.success,
            "script_id": result.script_id,
            "datagram_path": result.datagram_path,
            "file_count": result.file_count,
            "hash": result.hash,
            "size_bytes": result.size_bytes,
            "message": result.message,
            "errors": result.errors,
        })
        if result.success and client:
            client.emit("packager.notify.snapshot_created", {
                "script_id": script_id,
                "datagram_path": result.datagram_path,
                "file_count": result.file_count,
            })
    except Exception as e:
        _emit_error(client, request_uuid, f"Snapshot creation failed: {e}")


def _handle_snapshot_load(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.snapshot.load event."""
    global _packager
    
    datagram_path = data.get("datagram_path", "")
    if not datagram_path:
        _emit_error(client, request_uuid, "datagram_path is required")
        return
    
    meta = _packager.load_snapshot_meta(datagram_path)
    if meta is None:
        _emit_error(client, request_uuid, f"Datagram not found or invalid: {datagram_path}")
        return
    
    _emit_response(client, "packager.response.snapshot", request_uuid, {
        "action": "load",
        "success": True,
        "meta": meta,
    })
    
    if client:
        client.emit("packager.notify.snapshot_loaded", {
            "script_id": meta.get("script_id", "unknown"),
            "datagram_path": datagram_path,
        })


def _handle_snapshot_list(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.snapshot.list event."""
    global _packager
    
    base_dir = data.get("base_dir", None)
    snapshots = _packager.list_snapshots(base_dir)
    
    _emit_response(client, "packager.response.snapshot", request_uuid, {
        "action": "list",
        "success": True,
        "count": len(snapshots),
        "snapshots": snapshots,
    })


def _handle_snapshot_info(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.snapshot.info event."""
    global _packager
    
    datagram_path = data.get("datagram_path", "")
    if not datagram_path:
        _emit_error(client, request_uuid, "datagram_path is required")
        return
    
    meta = _packager.load_snapshot_meta(datagram_path)
    if meta is None:
        _emit_error(client, request_uuid, f"Datagram not found: {datagram_path}")
        return
    
    _emit_response(client, "packager.response.snapshot", request_uuid, {
        "action": "info",
        "success": True,
        "meta": meta,
    })


def _handle_script_list(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.script.list event."""
    global _packager
    
    scripts = _packager.discover_scripts()
    
    _emit_response(client, "packager.response.script", request_uuid, {
        "success": True,
        "count": len(scripts),
        "scripts": scripts,
    })


def _handle_script_config_get(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.script.config.get event."""
    global _packager
    
    script_id = data.get("script_id", "")
    if not script_id:
        _emit_error(client, request_uuid, "script_id is required")
        return
    
    compile_cfg = _packager.get_or_create_compile_config(script_id)
    decompile_cfg = _packager.get_or_create_decompile_config(script_id)
    
    _emit_response(client, "packager.response.config", request_uuid, {
        "success": True,
        "script_id": script_id,
        "compile_config": compile_cfg.to_dict(),
        "decompile_config": decompile_cfg.to_dict(),
    })


def _handle_script_config_set(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.script.config.set event."""
    global _packager
    
    script_id = data.get("script_id", "")
    compile_data = data.get("compile", None)
    decompile_data = data.get("decompile", None)
    
    if not script_id:
        _emit_error(client, request_uuid, "script_id is required")
        return
    
    results = {}
    if compile_data:
        ok = _packager.set_compile_config(script_id, compile_data)
        results["compile_saved"] = ok
    if decompile_data:
        ok = _packager.set_decompile_config(script_id, decompile_data)
        results["decompile_saved"] = ok
    
    _emit_response(client, "packager.response.config", request_uuid, {
        "success": True,
        "script_id": script_id,
        **results,
    })


def _handle_deploy_preview(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.deploy.preview event."""
    global _packager
    
    datagram_path = data.get("datagram_path", "")
    target_script_id = data.get("target_script_id", None)
    
    if not datagram_path:
        _emit_error(client, request_uuid, "datagram_path is required")
        return
    
    preview = _packager.preview_deploy(datagram_path, target_script_id)
    
    _emit_response(client, "packager.response.deploy", request_uuid, {
        "action": "preview",
        "success": preview.success,
        "script_id": preview.script_id,
        "source_datagram": preview.source_datagram,
        "target_path": preview.target_path,
        "file_count": preview.file_count,
        "compatibility_ok": preview.compatibility_ok,
        "port_conflicts": preview.port_conflicts,
        "would_overwrite": preview.would_overwrite,
        "post_unpack_actions": preview.post_unpack_actions,
        "message": preview.message,
    })


def _handle_deploy_execute(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.deploy.execute event."""
    global _packager
    
    datagram_path = data.get("datagram_path", "")
    target_script_id = data.get("target_script_id", None)
    auto_start = data.get("auto_start", False)
    
    if not datagram_path:
        _emit_error(client, request_uuid, "datagram_path is required")
        return
    
    result = _packager.deploy_snapshot(
        datagram_path=datagram_path,
        target_script_id=target_script_id,
        auto_start=auto_start,
        verbose=True,
    )
    
    _emit_response(client, "packager.response.deploy", request_uuid, {
        "action": "execute",
        "success": result.success,
        "script_id": result.script_id,
        "target_path": result.target_path,
        "files_restored": result.files_restored,
        "files_skipped": result.files_skipped,
        "actions_executed": result.actions_executed,
        "backup_path": result.backup_path,
        "message": result.message,
        "errors": result.errors,
    })
    
    if result.success and client:
        client.emit("packager.notify.deploy_complete", {
            "script_id": result.script_id,
            "target_path": result.target_path,
            "files_restored": result.files_restored,
        })


def _handle_gui_register(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.gui.register event — register packager tab in GUI Dashboard."""
    global _gui_registered
    _gui_registered = True
    
    _emit_response(client, "packager.response.gui", request_uuid, {
        "success": True,
        "action": "register",
        "gui_registered": True,
    })


def _handle_gui_status(client: PhooksClient, data: dict, request_uuid: str):
    """Handle packager.gui.status event — return current packager status."""
    global _packager, _start_time
    
    uptime = time.time() - _start_time
    scripts = _packager.discover_scripts()
    snapshots = _packager.list_snapshots()
    
    _emit_response(client, "packager.response.gui", request_uuid, {
        "success": True,
        "action": "status",
        "uptime_seconds": uptime,
        "script_count": len(scripts),
        "snapshot_count": len(snapshots),
        "gui_registered": _gui_registered,
    })


# ── Event dispatcher ────────────────────────────────────────────────────────

_EVENT_HANDLERS = {
    "packager.snapshot.create": _handle_snapshot_create,
    "packager.snapshot.load": _handle_snapshot_load,
    "packager.snapshot.list": _handle_snapshot_list,
    "packager.snapshot.info": _handle_snapshot_info,
    "packager.script.list": _handle_script_list,
    "packager.script.config.get": _handle_script_config_get,
    "packager.script.config.set": _handle_script_config_set,
    "packager.deploy.preview": _handle_deploy_preview,
    "packager.deploy.execute": _handle_deploy_execute,
    "packager.gui.register": _handle_gui_register,
    "packager.gui.status": _handle_gui_status,
}


def _emit_response(client, response_event: str, request_uuid: str, data: dict):
    """Emit a response event via Phooks."""
    if client:
        client.emit(response_event, {
            "request_uuid": request_uuid,
            **data,
        })


def _emit_error(client, request_uuid: str, error: str):
    """Emit an error notification."""
    _emit_response(client, "packager.notify.error", request_uuid, {"error": error})


# ── Phooks setup ────────────────────────────────────────────────────────────

def _setup_phooks() -> PhooksClient | None:
    """Set up Phooks client for inter-script communication."""
    try:
        client = PhooksClient(
            script_id="SERVICES/script-packager",
            listen_events=list(_EVENT_HANDLERS.keys()),
            emit_events=[
                "packager.response.snapshot",
                "packager.response.script",
                "packager.response.config",
                "packager.response.deploy",
                "packager.response.gui",
                "packager.notify.snapshot_created",
                "packager.notify.snapshot_loaded",
                "packager.notify.deploy_complete",
                "packager.notify.error",
            ],
        )
        client.register()
        return client
    except ImportError:
        print("[script-packager] Phooks not available — running in standalone mode")
        return None
    except Exception as e:
        print(f"[script-packager] Phooks setup failed: {e}")
        return None


# ── Main loop ───────────────────────────────────────────────────────────────

def main():
    """Main entry point — called by the YuniScripts engine."""
    global _packager, _phooks_client, _running, _start_time
    
    print("[script-packager] Script Packager starting...")
    print(f"[script-packager] Version: 1.0.0")
    
    # Create the snapshot manager
    try:
        _packager = ScriptSnapshotManager()
        scripts = _packager.discover_scripts()
        print(f"[script-packager] Discovered {len(scripts)} packageable scripts")
        for s in scripts[:10]:
            print(f"[script-packager]   {s['script_id']} ({s['name']})")
        if len(scripts) > 10:
            print(f"[script-packager]   ... and {len(scripts) - 10} more")
    except Exception as e:
        print(f"[script-packager] FATAL: Failed to initialize: {e}")
        print("[script-packager] Shutting down...")
        print("SHUTDOWN_COMPLETE")
        return
    
    # Set up Phooks
    _phooks_client = _setup_phooks()
    
    _start_time = time.time()
    print("[script-packager] Script Packager ready")
    print(f"[script-packager] Listening for {len(_EVENT_HANDLERS)} Phooks event types")
    
    # Main loop
    try:
        while _running:
            if _phooks_client:
                event = _phooks_client.receive(timeout=1.0)
                if event and isinstance(event, dict):
                    event_name = event.get("event", "")
                    data = event.get("data", {})
                    request_uuid = data.get("request_uuid", f"req_{time.time_ns()}")
                    
                    handler = _EVENT_HANDLERS.get(event_name)
                    if handler:
                        try:
                            handler(_phooks_client, data, request_uuid)
                        except Exception as e:
                            _emit_error(_phooks_client, request_uuid,
                                         f"Handler error for {event_name}: {e}")
            else:
                time.sleep(1.0)
    except KeyboardInterrupt:
        print("[script-packager] Shutting down...")
    finally:
        if _phooks_client:
            try:
                _phooks_client.unregister()
            except Exception:
                pass
        _running = False
        print("[script-packager] Shutdown complete")
        print("SHUTDOWN_COMPLETE")


def shutdown_handler(signum, frame):
    """Handle shutdown signals."""
    global _running
    print(f"[script-packager] Received signal {signum}, shutting down...")
    _running = False


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    main()
