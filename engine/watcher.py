"""Functional file watcher with recursive ignore & debounce.

Supports two backends:
  1. watchdog (preferred) — efficient OS-level file notifications
  2. Polling fallback — periodic stat() checks when watchdog is unavailable
"""
import time
import os
from queue import Queue
from pathlib import Path
from typing import Dict, Optional, Callable

# ── Backend selection ───────────────────────────────────────────────
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _HAVE_WATCHDOG = True
except ImportError:
    _HAVE_WATCHDOG = False
    Observer = None
    FileSystemEventHandler = None


IGNORED_DIR_NAMES = {"__pycache__", "venv", ".git", ".mypy_cache", "__pypackages__",
                      "node_modules", ".tox", ".eggs"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".pid", ".swp", ".swx", ".bak"}


def _is_ignored(event_path: Path, script_root: Path) -> bool:
    """Check if a path should be ignored based on directory/extension rules."""
    try:
        rel = event_path.relative_to(script_root)
    except ValueError:
        return True
    for part in rel.parts:
        if part in IGNORED_DIR_NAMES:
            return True
    if event_path.name.startswith("."):
        return True
    if event_path.suffix in IGNORED_SUFFIXES:
        return True
    return False


def _walk_script_files(script_path: Path, watch_patterns: list) -> Dict[Path, float]:
    """Walk script_path and return {Path: mtime} for matching files."""
    result = {}
    try:
        for root, dirs, files in os.walk(str(script_path)):
            root_path = Path(root)
            # Prune ignored dirs in-place (affects os.walk behavior)
            dirs[:] = [d for d in dirs if d not in IGNORED_DIR_NAMES and not d.startswith(".")]
            for fname in files:
                fpath = root_path / fname
                if fname.startswith(".") or fpath.suffix in IGNORED_SUFFIXES:
                    continue
                # Check against watch patterns
                for pattern in watch_patterns:
                    if fpath.match(pattern) or fpath.match(f"**/{pattern}"):
                        try:
                            result[fpath] = fpath.stat().st_mtime
                        except OSError:
                            pass
                        break
    except (PermissionError, OSError):
        pass
    return result


# ══════════════════════════════════════════════════════════════════════
# Watchdog backend (preferred)
# ══════════════════════════════════════════════════════════════════════

if _HAVE_WATCHDOG:

    class _WatchdogHandler(FileSystemEventHandler):
        def __init__(self, script_id: str, script_path: Path,
                     watch_patterns: list, queue: Queue):
            self.script_id = script_id
            self.script_path = script_path
            self.watch_patterns = watch_patterns
            self.queue = queue

        def on_modified(self, event):
            self._handle(event)

        def on_created(self, event):
            self._handle(event)

        def _handle(self, event):
            try:
                if event.is_directory:
                    return
                src_path = Path(event.src_path)
                if _is_ignored(src_path, self.script_path):
                    return
                for pattern in self.watch_patterns:
                    if src_path.match(pattern) or src_path.match(f"**/{pattern}"):
                        self.queue.put((self.script_id, str(src_path)))
                        break
            except Exception as e:
                logger.error(f"_handle failed: {e}")
                return None


def _create_watchdog_watcher(script_instance: Dict, queue: Queue) -> Dict:
    """Create a watchdog-based watcher."""
    sid = script_instance["id"]
    path = script_instance["path"]
    patterns = script_instance["meta"]["watch_patterns"]
    observer = Observer()
    handler = _WatchdogHandler(sid, path, patterns, queue)
    observer.schedule(handler, str(path), recursive=True)
    return {
        "script_id": sid,
        "observer": observer,
        "handler": handler,
        "active": False,
        "last_reload_time": 0.0,
        "backend": "watchdog",
    }


# ══════════════════════════════════════════════════════════════════════
# Polling backend (fallback — works on all platforms without watchdog)
# ══════════════════════════════════════════════════════════════════════

def _create_polling_watcher(script_instance: Dict, queue: Queue) -> Dict:
    """Create a poll-based watcher (stat() polling as fallback).

    Polls every ``poll_interval`` seconds for file modifications.
    """
    sid = script_instance["id"]
    path = script_instance["path"]
    patterns = script_instance["meta"]["watch_patterns"]
    poll_interval = script_instance["meta"].get("watch_poll_interval", 2.0)

    # Snapshot current file mtimes
    snapshot = _walk_script_files(path, patterns)

    return {
        "script_id": sid,
        "script_path": path,
        "watch_patterns": patterns,
        "queue": queue,
        "poll_interval": poll_interval,
        "snapshot": snapshot,
        "active": False,
        "last_reload_time": 0.0,
        "_poll_thread": None,
        "backend": "polling",
    }


def _polling_loop(watcher_state: Dict):
    """Background loop that polls files and pushes changes to the queue."""
    sid = watcher_state["script_id"]
    path = watcher_state["script_path"]
    patterns = watcher_state["watch_patterns"]
    queue = watcher_state["queue"]
    interval = watcher_state["poll_interval"]

    while watcher_state.get("active", False):
        time.sleep(interval)
        try:
            current = _walk_script_files(path, patterns)
            old = watcher_state.get("snapshot", {})
            # Check for new or modified files
            for fpath, mtime in current.items():
                old_mtime = old.get(fpath)
                if old_mtime is None or mtime > old_mtime:
                    queue.put((sid, str(fpath)))
            # Check for deleted files (snapshot has it but current doesn't)
            for fpath in old:
                if fpath not in current:
                    queue.put((sid, str(fpath)))
            watcher_state["snapshot"] = current
        except Exception:
            pass  # Silently recover on next poll


# ══════════════════════════════════════════════════════════════════════
# Unified API
# ══════════════════════════════════════════════════════════════════════

def create_watcher(script_instance: Dict, queue: Queue) -> Dict:
    """Create a file watcher, preferring watchdog with polling fallback."""
    if _HAVE_WATCHDOG:
        return _create_watchdog_watcher(script_instance, queue)
    else:
        return _create_polling_watcher(script_instance, queue)


def start_watcher(watcher_state: Dict) -> Dict:
    try:
        """Start the watcher (watchdog observer or polling thread)."""
        if watcher_state["active"]:
            return watcher_state
    
        backend = watcher_state.get("backend", "watchdog")
        if backend == "watchdog" and _HAVE_WATCHDOG:
            watcher_state["observer"].start()
            watcher_state["active"] = True
        elif backend == "polling":
            import threading
            watcher_state["active"] = True
            t = threading.Thread(target=_polling_loop, args=(watcher_state,),
                                 daemon=True, name=f"poll-watcher-{watcher_state['script_id']}")
            watcher_state["_poll_thread"] = t
            t.start()
    
        return {**watcher_state, "active": True}
    except Exception as e:
        logger.error(f"start_watcher failed: {e}")
        return None


def stop_watcher(watcher_state: Dict) -> Dict:
    try:
        """Stop the watcher."""
        if not watcher_state["active"]:
            return watcher_state
    
        backend = watcher_state.get("backend", "watchdog")
        if backend == "watchdog" and _HAVE_WATCHDOG:
            watcher_state["observer"].stop()
            watcher_state["observer"].join()
        elif backend == "polling":
            watcher_state["active"] = False
            # Thread will exit on next poll interval check
    
        return {**watcher_state, "active": False}
    except Exception as e:
        logger.error(f"stop_watcher failed: {e}")
        return None


def reload_script(registry: Dict, running: Dict, script_id: str,
                  watchers: Dict, debounce_seconds: float = 2.0,
                  log_base_dir: Path = None) -> tuple:
    try:
        """Reload a script by stopping and re-starting it, with debounce."""
        now = time.time()
        if script_id in watchers:
            last = watchers[script_id].get("last_reload_time", 0.0)
            if now - last < debounce_seconds:
                return registry, running, watchers, False
    
        if script_id not in registry:
            return registry, running, watchers, False
    
        inst = registry[script_id]
        proc_info = running.get(script_id)
    
        from engine.process_wrapper import stop_script, start_script
    
        if proc_info and proc_info.get("status") in ("running", "starting"):
            proc_info = stop_script(proc_info)
            running[script_id] = proc_info
    
        new_proc = start_script(inst, log_base_dir=log_base_dir)
        running[script_id] = new_proc
    
        if script_id in watchers:
            watchers[script_id] = {**watchers[script_id], "last_reload_time": now}
    
        return registry, running, watchers, True
    except Exception as e:
        logger.error(f"reload_script failed: {e}")
        return ()

