"""Per‑script log capture with simple rotation."""
from pathlib import Path

import io
import shutil

ENGINE_LOGS_DIR = Path(__file__).resolve().parent.parent / "engine" / "logs"


def ensure_logs_dir() -> Path:
    ENGINE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return ENGINE_LOGS_DIR


def get_log_path(script_id: str) -> Path:
    """Return the log file path for a script (id is its relative path)."""
    safe_name = script_id.replace("/", "_").replace("\\", "_")
    return ENGINE_LOGS_DIR / f"{safe_name}.log"


def create_log_file(script_id: str, max_bytes: int = 1_048_576, backup_count: int = 3):
    """Open a rotating log file in append mode and return the file object.

    Does NOT use Python's logging module — raw file for simplicity.
    We'll write directly from subprocess output.
    """
    path = get_log_path(script_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open in append mode, create if not exists
    return open(path, "a", buffering=1)  # line‑buffered


def rotate_log(log_file, max_bytes=10*1024*1024, backup_count=3):
    """Manually rotate if the file exceeds max_bytes with backup support."""
    try:
        path = Path(log_file.name) if isinstance(log_file, io.IOBase) else Path(str(log_file))
        if path.stat().st_size > max_bytes:
            for i in range(backup_count - 1, 0, -1):
                older = path.with_suffix(f'.log.{i}')
                newer = path.with_suffix(f'.log.{i-1}')
                if older.exists():
                    older.unlink()
                if newer.exists():
                    shutil.copy2(str(newer), str(older))
            shutil.copy2(str(path), str(path.with_suffix('.log.0')))
            # Truncate original
            with open(path, 'w') as f:
                f.truncate(0)
            return True
    except (OSError, AttributeError, ValueError):
        pass
    return False