#!/usr/bin/env python3
"""
Datagram Engine Module — YuniScripts Managed Entry Point.

Part of the YuniScripts ecosystem.
Converts the PowerShell-based Datagram protocol into a Python YuniScripts
Engine Module that allows scripts to load, create, and manipulate datagrams.

Key capabilities:
  - Create/load/validate datagram archives
  - Integrity hashing (SHA256, SHA3, SHAKE256)
  - Version compatibility checking (forward/backward compatible)
  - Database CRUD (SQLite, JSON backends)
  - Embedded function loading/execution
  - Phooks event integration for inter-script datagram operations
"""

import sys
import os
import json
import signal
import time
from pathlib import Path

# Add engine directory to path
ENGINE_DIR = Path(__file__).parent / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

# Local imports
from engine import (
    Datagram, DatagramMeta, DatagramVersion, DatagramHash,
    DatagramFunction, DatagramValue, DatabaseRecord,
    HashAlgorithm, EncryptionMode, DatabaseType, DatagramStatus, DataType,
    load_datagram, create_datagram, update_base_ini, update_meta_ini,
    validate_datagram_structure, parse_ini_content,
    compute_datagram_hash, verify_datagram_hash, update_datagram_hash,
    CompatibilityChecker, CompatibilityResult,
    Database, SQLiteDatabase, JSONDatabase, create_database, DatabaseError,
    FunctionRegistry, FunctionLoadError,
)


# ── Global state ────────────────────────────────────────────────────────────

_loaded_datagrams: dict = {}       # path -> Datagram
_function_registry = FunctionRegistry()
_running = True
_start_time = time.time()


# ── Phooks integration ──────────────────────────────────────────────────────

def _setup_phooks():
    """Set up Phooks client for inter-script communication."""
    try:
        from engine.phooks_client import PhooksClient
        from engine.ports import PHOOKS_HUB_PORT
        
        client = PhooksClient(
            script_id="SERVICES/datagram-engine",
            listen_events=[
                "datagram.create",
                "datagram.load",
                "datagram.meta.get",
                "datagram.hash.compute",
                "datagram.hash.verify",
                "datagram.db.connect",
                "datagram.db.insert",
                "datagram.db.select",
                "datagram.db.update",
                "datagram.db.delete",
                "datagram.compat.check",
                "datagram.func.execute",
            ],
            emit_events=[
                "datagram.response.create",
                "datagram.response.load",
                "datagram.response.meta",
                "datagram.response.hash",
                "datagram.response.db",
                "datagram.response.compat",
                "datagram.response.func",
                "datagram.notify.error",
            ],
        )
        client.register()
        return client
    except ImportError:
        print("[datagram-engine] Phooks not available — running in standalone mode")
        return None
    except Exception as e:
        print(f"[datagram-engine] Phooks setup failed: {e}")
        return None


def _handle_phooks_event(client, event: dict):
    """Handle an incoming Phooks event."""
    event_name = event.get("event", "")
    data = event.get("data", {})
    request_uuid = data.get("request_uuid", "")
    sender = event.get("sender", "unknown")
    
    try:
        if event_name == "datagram.create":
            _handle_create(client, data, request_uuid)
        elif event_name == "datagram.load":
            _handle_load(client, data, request_uuid)
        elif event_name == "datagram.meta.get":
            _handle_meta_get(client, data, request_uuid)
        elif event_name == "datagram.hash.compute":
            _handle_hash_compute(client, data, request_uuid)
        elif event_name == "datagram.hash.verify":
            _handle_hash_verify(client, data, request_uuid)
        elif event_name == "datagram.db.insert":
            _handle_db_insert(client, data, request_uuid)
        elif event_name == "datagram.db.select":
            _handle_db_select(client, data, request_uuid)
        elif event_name == "datagram.compat.check":
            _handle_compat_check(client, data, request_uuid)
        elif event_name == "datagram.func.execute":
            _handle_func_execute(client, data, request_uuid)
        else:
            _emit_error(client, request_uuid, f"Unknown event: {event_name}")
    except Exception as e:
        _emit_error(client, request_uuid, str(e))


def _emit_response(client, response_event: str, request_uuid: str, data: dict):
    """Emit a response event via Phooks."""
    if client:
        client.emit(response_event, {
            "request_uuid": request_uuid,
            "data": data,
        })


def _emit_error(client, request_uuid: str, error: str):
    """Emit an error notification."""
    _emit_response(client, "datagram.notify.error", request_uuid, {"error": error})


def _handle_create(client, data: dict, request_uuid: str):
    path = data.get("path", "")
    name = data.get("name", "Untitled Datagram")
    author = data.get("author", "Unknown")
    
    if not path:
        _emit_error(client, request_uuid, "path is required")
        return
    
    dg = create_datagram(path, name=name, author=author)
    _loaded_datagrams[path] = dg
    _emit_response(client, "datagram.response.create", request_uuid, dg.to_dict())


def _handle_load(client, data: dict, request_uuid: str):
    path = data.get("path", "")
    if not path:
        _emit_error(client, request_uuid, "path is required")
        return
    try:
        dg = load_datagram(path)
        _loaded_datagrams[path] = dg
        _emit_response(client, "datagram.response.load", request_uuid, dg.to_dict())
    except IOError as e:
        _emit_error(client, request_uuid, str(e))


def _handle_meta_get(client, data: dict, request_uuid: str):
    path = data.get("path", "")
    dg = _loaded_datagrams.get(path)
    if not dg:
        _emit_error(client, request_uuid, f"Datagram not loaded: {path}")
        return
    _emit_response(client, "datagram.response.meta", request_uuid, dg.meta.to_dict())


def _handle_hash_compute(client, data: dict, request_uuid: str):
    path = data.get("path", "")
    dg = _loaded_datagrams.get(path)
    if not dg:
        _emit_error(client, request_uuid, f"Datagram not loaded: {path}")
        return
    hash_obj = compute_datagram_hash(dg)
    _emit_response(client, "datagram.response.hash", request_uuid, {
        "hex_value": hash_obj.hex_value,
        "algorithm": hash_obj.algorithm.value,
    })


def _handle_hash_verify(client, data: dict, request_uuid: str):
    path = data.get("path", "")
    dg = _loaded_datagrams.get(path)
    if not dg:
        _emit_error(client, request_uuid, f"Datagram not loaded: {path}")
        return
    is_valid, computed = verify_datagram_hash(dg)
    _emit_response(client, "datagram.response.hash", request_uuid, {
        "valid": is_valid,
        "computed_hash": computed.hex_value if computed else "",
        "stored_hash": dg.meta.datagram_hash.hex_value if dg.meta.datagram_hash else "",
    })


def _handle_db_insert(client, data: dict, request_uuid: str):
    path = data.get("path", "")
    table = data.get("table", "default")
    record = data.get("data", {})
    dg = _loaded_datagrams.get(path)
    if not dg:
        _emit_error(client, request_uuid, f"Datagram not loaded: {path}")
        return
    db_path = Path(path) / "Databases" / "Default" / "Data" / f"{table}.db"
    db = create_database(DatabaseType.SQLITE, db_path, name=table)
    try:
        row_id = db.insert(table, record)
        _emit_response(client, "datagram.response.db", request_uuid, {
            "operation": "insert", "row_id": row_id, "table": table
        })
    finally:
        db.close()


def _handle_db_select(client, data: dict, request_uuid: str):
    path = data.get("path", "")
    table = data.get("table", "default")
    where = data.get("where", None)
    dg = _loaded_datagrams.get(path)
    if not dg:
        _emit_error(client, request_uuid, f"Datagram not loaded: {path}")
        return
    db_path = Path(path) / "Databases" / "Default" / "Data" / f"{table}.db"
    db = create_database(DatabaseType.SQLITE, db_path, name=table)
    try:
        results = db.select(table, where=where)
        _emit_response(client, "datagram.response.db", request_uuid, {
            "operation": "select", "count": len(results),
            "records": [r.to_dict() for r in results]
        })
    finally:
        db.close()


def _handle_compat_check(client, data: dict, request_uuid: str):
    dg_version_str = data.get("datagram_version", "1.0.0")
    engine_version_str = data.get("engine_version", "1.0.0")
    
    dg_version = DatagramVersion.parse(dg_version_str)
    engine_version = DatagramVersion.parse(engine_version_str)
    
    checker = CompatibilityChecker(engine_version=engine_version)
    result = checker.check_datagram_compatibility(dg_version)
    
    _emit_response(client, "datagram.response.compat", request_uuid, result.to_dict())


def _handle_func_execute(client, data: dict, request_uuid: str):
    func_name = data.get("function", "")
    args = data.get("args", [])
    kwargs = data.get("kwargs", {})
    
    try:
        result = _function_registry.execute(func_name, *args, **kwargs)
        _emit_response(client, "datagram.response.func", request_uuid, {
            "function": func_name, "result": str(result)
        })
    except (FunctionLoadError, KeyError) as e:
        _emit_error(client, request_uuid, str(e))


# ── Main entry point ────────────────────────────────────────────────────────

def main():
    """Main entry point — called by the YuniScripts engine."""
    global _running, _start_time
    
    print("[datagram-engine] Datagram Engine Module starting...")
    print(f"[datagram-engine] Version: 1.0.0")
    
    # Set up Phooks
    phooks_client = _setup_phooks()
    
    # Register built-in functions
    _function_registry.register(
        DatagramFunction(
            name="validate_structure",
            version=DatagramVersion(1, 0, 0),
            description="Validate a datagram directory structure",
        ),
        callable_obj=validate_datagram_structure,
    )
    
    _start_time = time.time()
    
    print("[datagram-engine] Datagram Engine Module ready")
    print(f"[datagram-engine] Loaded {_function_registry.function_count} built-in functions")
    
    # Main loop — process Phooks events and engine commands
    try:
        while _running:
            if phooks_client:
                event = phooks_client.receive(timeout=1.0)
                if event:
                    _handle_phooks_event(phooks_client, event)
            else:
                # Standalone mode: sleep
                time.sleep(1.0)
    except KeyboardInterrupt:
        print("[datagram-engine] Shutting down...")
    finally:
        if phooks_client:
            try:
                phooks_client.unregister()
            except Exception:
                pass
        print("[datagram-engine] Shutdown complete")
        print("SHUTDOWN_COMPLETE")


def shutdown_handler(signum, frame):
    """Handle shutdown signals."""
    global _running
    print(f"[datagram-engine] Received signal {signum}, shutting down...")
    _running = False


if __name__ == "__main__":
    # Set up signal handlers
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    
    main()
